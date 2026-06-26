"""
vc_manager.py — Voice Chat Connection Controller
Manages source VC (silent listener) and target VCs (broadcast mode).
Auto-reconnect with exponential backoff on unexpected disconnections.
"""
import asyncio
import io
import time
from typing import Dict, Optional, Set, Tuple

from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream
from pytgcalls.exceptions import AlreadyJoinedError, NoActiveGroupCall, NotInGroupCallError
from loguru import logger

from audio_bridge import AudioBridge
from database import Database
from config import Config


class VCManager:
    """Controls all voice-chat connections for the userbot."""

    def __init__(
        self,
        user_client: Client,
        calls: PyTgCalls,
        bridge: AudioBridge,
        db: Database,
        cfg: Config,
    ):
        self._user   = user_client
        self._calls  = calls
        self._bridge = bridge
        self._db     = db
        self._cfg    = cfg

        # Source VC state
        self._source_id: Optional[int]   = None
        self._source_joined: Optional[float] = None
        self._source_ok: bool = False

        # Target VC state
        self._targets: Set[int]                      = set()
        self._fwd_tasks: Dict[int, asyncio.Task]     = {}

        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Source VC                                                           #
    # ------------------------------------------------------------------ #

    async def join_source(self, chat_id: int) -> Tuple[bool, str]:
        """Join source VC in receive-only (silent listener) mode."""
        async with self._lock:
            try:
                await self._calls.join_group_call(
                    chat_id,
                    MediaStream(
                        audio_path=None,
                        video_path=None,
                        audio_parameters=AudioQuality.HIGH,
                    ),
                )
                self._source_id     = chat_id
                self._source_joined = time.monotonic()
                self._source_ok     = True
                self._db.set_record_group(chat_id)
                logger.success(f"[VCManager] Joined source VC: {chat_id}")
                return True, f"✅ Joined source VC `{chat_id}`"
            except AlreadyJoinedError:
                self._source_id = chat_id
                self._source_ok = True
                return True, f"Already in source VC `{chat_id}`"
            except NoActiveGroupCall:
                return False, f"❌ No active voice chat in `{chat_id}`"
            except Exception as exc:
                logger.error(f"[VCManager] join_source({chat_id}): {exc}")
                return False, f"❌ Error joining source: {exc}"

    async def leave_source(self) -> Tuple[bool, str]:
        """Leave source VC and stop the audio bridge."""
        async with self._lock:
            if not self._source_id:
                return False, "Not connected to any source VC."
            cid = self._source_id
            try:
                await self._calls.leave_group_call(cid)
            except Exception as exc:
                logger.warning(f"[VCManager] leave_source({cid}) warning: {exc}")
            self._bridge.stop()
            self._source_id     = None
            self._source_ok     = False
            self._source_joined = None
            logger.info(f"[VCManager] Left source VC: {cid}")
            return True, f"✅ Left source VC `{cid}`"

    # ------------------------------------------------------------------ #
    #  Target VCs                                                          #
    # ------------------------------------------------------------------ #

    async def join_target(self, chat_id: int) -> Tuple[bool, str]:
        """Join a target VC and start the audio forwarding task."""
        async with self._lock:
            if chat_id in self._targets:
                return False, f"Already in target VC `{chat_id}`"
            try:
                await self._calls.join_group_call(
                    chat_id,
                    MediaStream(
                        audio_path="/dev/urandom",   # silently replaced by change_stream
                        audio_parameters=AudioQuality.HIGH,
                    ),
                )
                self._targets.add(chat_id)
                self._bridge.add_target(chat_id)
                self._db.add_target_group(chat_id)
                task = asyncio.create_task(
                    self._forward(chat_id), name=f"fwd_{chat_id}"
                )
                self._fwd_tasks[chat_id] = task
                logger.success(f"[VCManager] Joined target VC: {chat_id}")
                return True, f"✅ Joined target VC `{chat_id}`"
            except AlreadyJoinedError:
                self._targets.add(chat_id)
                self._bridge.add_target(chat_id)
                return True, f"Already in `{chat_id}` — relay active"
            except NoActiveGroupCall:
                return False, f"❌ No active voice chat in `{chat_id}`"
            except Exception as exc:
                logger.error(f"[VCManager] join_target({chat_id}): {exc}")
                return False, f"❌ Error: {exc}"

    async def leave_target(self, chat_id: int) -> Tuple[bool, str]:
        """Leave a specific target VC."""
        async with self._lock:
            if chat_id not in self._targets:
                return False, f"Not in target VC `{chat_id}`"
            # Cancel forwarding task
            task = self._fwd_tasks.pop(chat_id, None)
            if task and not task.done():
                task.cancel()
            try:
                await self._calls.leave_group_call(chat_id)
            except Exception as exc:
                logger.warning(f"[VCManager] leave_target({chat_id}) warning: {exc}")
            self._targets.discard(chat_id)
            self._bridge.remove_target(chat_id)
            self._db.remove_target_group(chat_id)
            logger.info(f"[VCManager] Left target VC: {chat_id}")
            return True, f"✅ Left target VC `{chat_id}`"

    async def leave_all_targets(self) -> Tuple[int, int]:
        """Leave every target VC. Returns (success_count, total_count)."""
        targets = list(self._targets)
        ok = 0
        for cid in targets:
            result, _ = await self.leave_target(cid)
            if result:
                ok += 1
        return ok, len(targets)

    # ------------------------------------------------------------------ #
    #  Audio forwarding task                                               #
    # ------------------------------------------------------------------ #

    async def _forward(self, chat_id: int):
        """
        Continuously pulls processed audio frames from the bridge
        and streams them to the target VC via PyTgCalls.change_stream().
        """
        logger.info(f"[VCManager] Forward task started for {chat_id}")
        consecutive_errors = 0

        while chat_id in self._targets:
            try:
                frame = await self._bridge.pull(chat_id, timeout=0.04)
                if frame:
                    await self._calls.change_stream(
                        chat_id,
                        MediaStream(
                            audio_path=io.BytesIO(frame),
                            audio_parameters=AudioQuality.HIGH,
                        ),
                    )
                    consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except NotInGroupCallError:
                logger.warning(f"[VCManager] Not in group call {chat_id} — reconnecting")
                asyncio.create_task(self._reconnect(chat_id))
                break
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    f"[VCManager] forward({chat_id}) error #{consecutive_errors}: {exc}"
                )
                if consecutive_errors >= 15:
                    logger.error(
                        f"[VCManager] Too many errors for {chat_id} — scheduling reconnect"
                    )
                    asyncio.create_task(self._reconnect(chat_id))
                    break
                await asyncio.sleep(0.1)

        logger.info(f"[VCManager] Forward task ended for {chat_id}")

    # ------------------------------------------------------------------ #
    #  Auto-reconnect                                                      #
    # ------------------------------------------------------------------ #

    async def _reconnect(self, chat_id: int, max_attempts: int = 5):
        """Exponential-backoff reconnect for a dropped target VC."""
        # Clean up stale state first
        self._targets.discard(chat_id)
        self._bridge.remove_target(chat_id)
        self._fwd_tasks.pop(chat_id, None)

        for attempt in range(1, max_attempts + 1):
            delay = 2 ** attempt          # 2, 4, 8, 16, 32 seconds
            logger.info(
                f"[VCManager] Reconnect {chat_id}: attempt {attempt}/{max_attempts} in {delay}s"
            )
            await asyncio.sleep(delay)
            ok, msg = await self.join_target(chat_id)
            if ok:
                logger.success(f"[VCManager] Reconnected to target {chat_id}")
                return

        logger.error(f"[VCManager] Gave up reconnecting {chat_id} after {max_attempts} attempts")
        self._db.remove_target_group(chat_id)

    # ------------------------------------------------------------------ #
    #  PyTgCalls event hooks (called from bot.py)                         #
    # ------------------------------------------------------------------ #

    def on_raw_audio(self, chat_id: int, raw_pcm: bytes):
        """Invoked when a PCM frame arrives from the source VC."""
        if chat_id == self._source_id:
            self._bridge.push(raw_pcm)

    def on_call_ended(self, chat_id: int):
        """Invoked when a call ends unexpectedly."""
        if chat_id == self._source_id:
            logger.warning(f"[VCManager] Source VC {chat_id} ended unexpectedly")
            self._source_ok = False
        elif chat_id in self._targets:
            logger.warning(f"[VCManager] Target VC {chat_id} ended — scheduling reconnect")
            asyncio.create_task(self._reconnect(chat_id))

    # ------------------------------------------------------------------ #
    #  Properties                                                          #
    # ------------------------------------------------------------------ #

    @property
    def source_id(self) -> Optional[int]:
        return self._source_id

    @property
    def source_ok(self) -> bool:
        return self._source_ok

    @property
    def source_joined(self) -> Optional[float]:
        return self._source_joined

    @property
    def targets(self) -> Set[int]:
        return set(self._targets)
