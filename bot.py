"""
bot.py — Main Application Entry Point
Voice Chat Bridge Bot — Production Ready

Orchestrates all subsystems:
  - Config, Database, AudioProcessor, AudioBridge
  - VCManager, Handlers
  - Pyrogram Bot Client + User Client
  - PyTgCalls (native WebRTC)

Usage:
    python bot.py

Requires Python 3.11+
"""
import asyncio
import os
import signal
import sys
import time

# Enforce Python 3.11+
if sys.version_info < (3, 11):
    print("[ERROR] Python 3.11 or higher is required.")
    sys.exit(1)

from pyrogram import Client
from pytgcalls import PyTgCalls
from loguru import logger

from config import Config
from database import Database
from audio_processor import AudioProcessor
from audio_bridge import AudioBridge
from vc_manager import VCManager
from handlers import Handlers


class VCBridgeBot:
    """
    Main application class.
    Instantiates and wires all subsystems, then runs the event loop.
    """

    def __init__(self):
        # --- Core config & DB -------------------------------------------
        self.cfg = Config()
        self.db  = Database(self.cfg.MONGO_DB_URI)

        # --- Audio pipeline ---------------------------------------------
        self.proc   = AudioProcessor()
        self.bridge = AudioBridge(self.proc)

        # --- Pyrogram bot client (handles bot commands) -----------------
        self.bot = Client(
            name="vcbridge_bot",
            api_id=self.cfg.API_ID,
            api_hash=self.cfg.API_HASH,
            bot_token=self.cfg.BOT_TOKEN,
        )

        # --- Pyrogram user client (joins VCs) ---------------------------
        self.user = Client(
            name="vcbridge_user",
            api_id=self.cfg.API_ID,
            api_hash=self.cfg.API_HASH,
            session_string=self.cfg.STRING_SESSION,
        )

        # --- PyTgCalls on the user client (native WebRTC) ---------------
        self.calls = PyTgCalls(self.user)

        # --- VC manager -------------------------------------------------
        self.vc = VCManager(
            user_client=self.user,
            calls=self.calls,
            bridge=self.bridge,
            db=self.db,
            cfg=self.cfg,
        )

        # Timestamp for uptime tracking
        self._start_time = time.monotonic()

        # --- Register all bot commands & callbacks ----------------------
        self.handlers = Handlers(
            bot=self.bot,
            vc=self.vc,
            bridge=self.bridge,
            proc=self.proc,
            db=self.db,
            cfg=self.cfg,
            bot_start=self._start_time,
        )

    # ------------------------------------------------------------------ #
    #  PyTgCalls event binding                                             #
    # ------------------------------------------------------------------ #

    def _bind_call_events(self):
        """
        Wire raw-audio and call-left events from PyTgCalls.
        on_raw_update delivers decoded PCM frames from source VC.
        on_left fires when any call is ended/kicked.
        """

        @self.calls.on_raw_update()
        async def _on_raw_update(client, update, *args):
            """
            Intercept raw audio frames from the source VC.
            The update payload varies across PyTgCalls versions;
            we check for common attribute shapes.
            """
            try:
                # PyTgCalls >= 4.x delivers frames via update.payload.audio
                if hasattr(update, "chat_id") and hasattr(update, "payload"):
                    payload = update.payload
                    if hasattr(payload, "audio") and payload.audio:
                        self.vc.on_raw_audio(update.chat_id, payload.audio)
                    elif hasattr(payload, "data") and payload.data:
                        # Fallback for alternative payload shapes
                        self.vc.on_raw_audio(update.chat_id, payload.data)
            except Exception as exc:
                logger.debug(f"[PyTgCalls raw_update] {exc}")

        @self.calls.on_left()
        async def _on_left(client, update):
            """Handle unexpected disconnection from any VC."""
            try:
                chat_id = getattr(update, "chat_id", None)
                if chat_id:
                    self.vc.on_call_ended(chat_id)
            except Exception as exc:
                logger.debug(f"[PyTgCalls on_left] {exc}")

    # ------------------------------------------------------------------ #
    #  Startup                                                             #
    # ------------------------------------------------------------------ #

    async def _startup(self):
        logger.info("============================================")
        logger.info("   Voice Chat Bridge Bot  —  Starting up   ")
        logger.info("============================================")

        # 1. Connect to MongoDB
        if not self.db.connect():
            logger.critical("Database connection failed — cannot continue.")
            sys.exit(1)

        # 2. Restore persisted audio settings
        saved_audio = self.db.get_audio_settings()
        self.proc.load_from_db(saved_audio)
        logger.info(f"Audio settings restored: {saved_audio}")

        # 3. Sync sudo users from DB into runtime config
        db_sudo = self.db.get_sudo_users()
        if db_sudo:
            combined = list(set(self.cfg.SUDO_USERS + db_sudo))
            self.cfg.SUDO_USERS = combined
            logger.info(f"Loaded {len(db_sudo)} sudo user(s) from DB")

        # 4. Restore record group from DB (overrides env if set)
        rg = self.db.get_record_group(self.cfg.RECORD_GROUP)
        if rg:
            self.cfg.RECORD_GROUP = rg

        # 5. Bind PyTgCalls events
        self._bind_call_events()

        # 6. Start Pyrogram + PyTgCalls clients
        await self.bot.start()
        logger.success("Bot client started")

        await self.user.start()
        logger.success("User client started")

        await self.calls.start()
        logger.success("PyTgCalls (WebRTC) started")

        logger.info(f"Owner ID      : {self.cfg.OWNER_ID}")
        logger.info(f"Record group  : {self.cfg.RECORD_GROUP}")
        logger.info(f"Sudo users    : {self.cfg.SUDO_USERS}")

        # 7. Send startup notification to owner
        try:
            await self.bot.send_message(
                self.cfg.OWNER_ID,
                "🟢 **VCBridge Bot is online!**\n"
                f"📁 Record group: `{self.cfg.RECORD_GROUP}`\n"
                "Send /record to start relaying, or /panel for controls.",
            )
        except Exception as exc:
            logger.warning(f"Could not notify owner on startup: {exc}")

        logger.success("Bot is fully operational. Waiting for commands…")

    # ------------------------------------------------------------------ #
    #  Shutdown                                                            #
    # ------------------------------------------------------------------ #

    async def _shutdown(self):
        logger.info("Graceful shutdown initiated…")

        # Leave all VCs cleanly
        try:
            await self.vc.leave_source()
        except Exception as exc:
            logger.warning(f"leave_source on shutdown: {exc}")

        try:
            await self.vc.leave_all_targets()
        except Exception as exc:
            logger.warning(f"leave_all_targets on shutdown: {exc}")

        # Stop clients in reverse order
        for name, coro in [
            ("PyTgCalls", self.calls.stop),
            ("User client", self.user.stop),
            ("Bot client", self.bot.stop),
        ]:
            try:
                await coro()
                logger.info(f"{name} stopped")
            except Exception as exc:
                logger.warning(f"{name} stop error: {exc}")

        logger.success("Shutdown complete.")

    # ------------------------------------------------------------------ #
    #  Main run loop                                                       #
    # ------------------------------------------------------------------ #

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Graceful SIGINT / SIGTERM handler
        def _handle_signal(sig, _frame):
            logger.info(f"Signal {sig.name} received — shutting down gracefully")
            loop.create_task(self._shutdown())
            loop.stop()

        try:
            signal.signal(signal.SIGINT,  lambda s, f: _handle_signal(s, f))
            signal.signal(signal.SIGTERM, lambda s, f: _handle_signal(s, f))
        except Exception:
            pass  # Signal handling may not work on all platforms

        try:
            loop.run_until_complete(self._startup())
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
            loop.run_until_complete(self._shutdown())
        except Exception as exc:
            logger.critical(f"Fatal error in main loop: {exc}")
            loop.run_until_complete(self._shutdown())
        finally:
            try:
                loop.close()
            except Exception:
                pass


# -------------------------------------------------------------------- #
#  Entry point                                                          #
# -------------------------------------------------------------------- #

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/vcbridge_{time:YYYY-MM-DD}.log",
        rotation="50 MB",
        retention="14 days",
        compression="zip",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} — {message}",
    )
    VCBridgeBot().run()
