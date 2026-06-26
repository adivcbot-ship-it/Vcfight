"""
handlers.py — All Telegram Command & Callback Handlers
Registers every bot command and inline panel callback.
"""
import asyncio
import os
import sys
import time

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from loguru import logger

from config import Config
from database import Database
from audio_processor import AudioProcessor
from audio_bridge import AudioBridge
from vc_manager import VCManager
from utils import fmt_uptime, fmt_bytes, bar, elapsed


class Handlers:
    """Registers all bot commands and callback query handlers."""

    def __init__(
        self,
        bot: Client,
        vc: VCManager,
        bridge: AudioBridge,
        proc: AudioProcessor,
        db: Database,
        cfg: Config,
        bot_start: float,
    ):
        self.bot        = bot
        self.vc         = vc
        self.bridge     = bridge
        self.proc       = proc
        self.db         = db
        self.cfg        = cfg
        self._bot_start = bot_start
        self._register()

    # ------------------------------------------------------------------ #
    #  Auth helpers                                                        #
    # ------------------------------------------------------------------ #

    def _auth(self, msg: Message) -> bool:
        return self.cfg.is_authorized(msg.from_user.id)

    def _owner(self, msg: Message) -> bool:
        return self.cfg.is_owner(msg.from_user.id)

    # ------------------------------------------------------------------ #
    #  Status text builder                                                 #
    # ------------------------------------------------------------------ #

    def _status_text(self) -> str:
        audio = self.db.get_audio_settings()

        # Source block
        if self.vc.source_ok and self.vc.source_id:
            src_status = f"✅ Connected (`{self.vc.source_id}`)"
            joined_ago = elapsed(self.vc.source_joined) if self.vc.source_joined else "?"
            src_detail = (
                f"├─ Chat: `{self.vc.source_id}`\n"
                f"├─ Joined: {joined_ago} ago\n"
                f"└─ Status: 🟢 Listening"
            )
        else:
            src_status = "❌ Not connected"
            src_detail = "└─ Status: 🔴 Idle"

        # Targets block
        targets = sorted(self.vc.targets)
        if targets:
            tgt_lines = "\n".join(
                f"{'\u2514\u2500' if i == len(targets)-1 else '\u251c\u2500'} `{t}`"
                for i, t in enumerate(targets)
            )
            tgt_header = f"🎯 Target VCs: **{len(targets)} connected**"
        else:
            tgt_lines  = "└─ None"
            tgt_header = "🎯 Target VCs: **0 connected**"

        vol_raw  = audio.get("volume", 100)
        vol_user = round(vol_raw / 200 * 60)
        bass     = audio.get("bass", 0)
        treble   = audio.get("treble", 0)
        clarity  = audio.get("clarity", 0)
        gain     = audio.get("gain", 0)
        muted    = audio.get("muted", False)
        sign     = "+" if gain >= 0 else ""
        fwd_icon = "🔇 **Muted**" if muted else "🔊 **Forwarding Active**"

        uptime = fmt_uptime(time.monotonic() - self._bot_start)
        data   = fmt_bytes(self.bridge.bytes_relayed)

        return (
            f"📊 **Bot Status**\n\n"
            f"🔵 **Source VC:** {src_status}\n"
            f"{src_detail}\n\n"
            f"{tgt_header}\n{tgt_lines}\n\n"
            f"🔊 **Audio Settings:**\n"
            f"├─ Volume:  {vol_user}/60 {bar(vol_user,60)} 🔊\n"
            f"├─ Bass:    {bass}/60 {bar(bass,60)} 🎵\n"
            f"├─ Treble:  {treble}/60 {bar(treble,60)} 🎶\n"
            f"├─ Clarity: {clarity}/60 {bar(clarity,60)} ✨\n"
            f"└─ Gain:    {sign}{gain}dB 📈\n\n"
            f"{fwd_icon}\n"
            f"⏱️ Uptime: `{uptime}`\n"
            f"📊 Data Relayed: `{data}`"
        )

    # ------------------------------------------------------------------ #
    #  Panel builder                                                       #
    # ------------------------------------------------------------------ #

    def _panel(self):
        audio   = self.db.get_audio_settings()
        vol     = round(audio.get("volume", 100) / 200 * 60)
        bass    = audio.get("bass", 0)
        treble  = audio.get("treble", 0)
        clarity = audio.get("clarity", 0)
        gain    = audio.get("gain", 0)
        muted   = audio.get("muted", False)
        sign    = "+" if gain >= 0 else ""
        mute_icon = "🔇 MUTED" if muted else "🔊 Live"

        text = (
            f"🎛️ **CONTROL PANEL**  {mute_icon}\n\n"
            f"`🔊 Volume:  {bar(vol,60,10)} {vol:>2}/60`\n"
            f"`🎵 Bass:    {bar(bass,60,10)} {bass:>2}/60`\n"
            f"`🎶 Treble:  {bar(treble,60,10)} {treble:>2}/60`\n"
            f"`✨ Clarity: {bar(clarity,60,10)} {clarity:>2}/60`\n"
            f"`📈 Gain:    {sign}{gain:>3}dB`"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔇 Mute",    callback_data="mute"),
                InlineKeyboardButton("🔊 Unmute",  callback_data="unmute"),
                InlineKeyboardButton("📊 Status",  callback_data="status"),
                InlineKeyboardButton("🔄 Restart", callback_data="restart"),
            ],
            [
                InlineKeyboardButton("🔊 Vol +5",     callback_data="vol_up"),
                InlineKeyboardButton("🔊 Vol -5",     callback_data="vol_dn"),
                InlineKeyboardButton("🎵 Bass +5",    callback_data="bass_up"),
                InlineKeyboardButton("🎵 Bass -5",    callback_data="bass_dn"),
            ],
            [
                InlineKeyboardButton("🎶 Treble +5",  callback_data="treble_up"),
                InlineKeyboardButton("🎶 Treble -5",  callback_data="treble_dn"),
                InlineKeyboardButton("✨ Clarity +5", callback_data="clarity_up"),
                InlineKeyboardButton("✨ Clarity -5", callback_data="clarity_dn"),
            ],
            [
                InlineKeyboardButton("📈 Gain +1",  callback_data="gain_up"),
                InlineKeyboardButton("📈 Gain -1",  callback_data="gain_dn"),
                InlineKeyboardButton("🔄 Refresh",  callback_data="refresh"),
            ],
        ])
        return text, keyboard

    # ------------------------------------------------------------------ #
    #  Handler registration                                                #
    # ------------------------------------------------------------------ #

    def _register(self):
        b = self.bot

        # ---------- /start -----------------------------------------------
        @b.on_message(filters.command("start") & filters.private)
        async def cmd_start(_, msg: Message):
            await msg.reply(
                "🎙️ **Voice Chat Bridge Bot**\n"
                "_Real-time audio relay across multiple Telegram VCs._\n\n"
                "`/record`  — join source VC + relay to all targets\n"
                "`/join <id>` — add a target VC\n"
                "`/panel`   — interactive control panel\n"
                "`/status`  — live status dashboard\n"
                "`/help`    — full command list"
            )

        # ---------- /help ------------------------------------------------
        @b.on_message(filters.command("help"))
        async def cmd_help(_, msg: Message):
            await msg.reply(
                "📖 **Command Reference**\n\n"
                "**Owner / Sudo:**\n"
                "`/status`               — full session status\n"
                "`/setrecordgroup <id>`  — set source VC (owner only)\n"
                "`/restart`              — restart the bot\n\n"
                "**VC Controls:**\n"
                "`/record`               — start relay from source VC\n"
                "`/leaverecord`          — stop relay & leave all VCs\n"
                "`/join <id> [id2...]`   — join target VC(s)\n"
                "`/leave <id>`           — leave a target VC\n"
                "`/leaveall`             — leave all target VCs\n\n"
                "**Audio:**\n"
                "`/level <0-60>`         — set volume\n"
                "`/bass <0-60>`          — set bass boost\n"
                "`/treble <0-60>`        — set treble boost\n"
                "`/clarity <0-60>`       — set clarity\n"
                "`/gain <-15 to +15>`    — set gain (dB)\n"
                "`/mute`                 — mute forwarding\n"
                "`/unmute`               — unmute forwarding\n"
                "`/panel`                — interactive panel"
            )

        # ---------- /status ----------------------------------------------
        @b.on_message(filters.command("status"))
        async def cmd_status(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            await msg.reply(self._status_text(), parse_mode="markdown")

        # ---------- /setrecordgroup -------------------------------------
        @b.on_message(filters.command("setrecordgroup"))
        async def cmd_setrg(_, msg: Message):
            if not self._owner(msg):
                return await msg.reply("❌ Owner-only command.")
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply(
                    "Usage: `/setrecordgroup <chat_id>`\n"
                    "Example: `/setrecordgroup -1002930799867`"
                )
            try:
                cid = int(args[1].strip())
            except ValueError:
                return await msg.reply("❌ Invalid chat ID — must be an integer.")
            self.cfg.RECORD_GROUP = cid
            self.db.set_record_group(cid)
            await msg.reply(f"✅ Record group updated to `{cid}`")

        # ---------- /restart ---------------------------------------------
        @b.on_message(filters.command("restart"))
        async def cmd_restart(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            await msg.reply("🔄 Restarting bot…")
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        # ---------- /join ------------------------------------------------
        @b.on_message(filters.command("join"))
        async def cmd_join(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            args = msg.text.split()[1:]
            if not args:
                return await msg.reply(
                    "Usage: `/join <chat_id> [chat_id2 ...]`\n"
                    "Example: `/join -100123456789`"
                )
            lines = []
            for a in args:
                try:
                    cid = int(a.strip())
                except ValueError:
                    lines.append(f"❌ Invalid ID: `{a}`")
                    continue
                ok, resp = await self.vc.join_target(cid)
                lines.append(resp)
            await msg.reply("\n".join(lines))

        # ---------- /leave -----------------------------------------------
        @b.on_message(filters.command("leave"))
        async def cmd_leave(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            parts = msg.text.split()
            if len(parts) < 2:
                return await msg.reply("Usage: `/leave <chat_id>`")
            try:
                cid = int(parts[1].strip())
            except ValueError:
                return await msg.reply("❌ Invalid chat ID.")
            ok, resp = await self.vc.leave_target(cid)
            await msg.reply(resp)

        # ---------- /leaveall --------------------------------------------
        @b.on_message(filters.command("leaveall"))
        async def cmd_leaveall(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            ok, total = await self.vc.leave_all_targets()
            await msg.reply(f"✅ Left **{ok}/{total}** target VCs.")

        # ---------- /record ----------------------------------------------
        @b.on_message(filters.command("record"))
        async def cmd_record(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            rg = self.db.get_record_group(self.cfg.RECORD_GROUP)
            if not rg:
                return await msg.reply(
                    "❌ No record group configured.\n"
                    "Use `/setrecordgroup <chat_id>` first."
                )
            ok, src_resp = await self.vc.join_source(rg)
            if ok:
                self.bridge.start()
            lines = [src_resp]
            saved_targets = self.db.get_target_groups()
            for cid in saved_targets:
                _, tresp = await self.vc.join_target(cid)
                lines.append(tresp)
            if not saved_targets:
                lines.append("ℹ️ No target VCs saved. Use `/join <id>` to add them.")
            await msg.reply("\n".join(lines))

        # ---------- /leaverecord ----------------------------------------
        @b.on_message(filters.command("leaverecord"))
        async def cmd_leaverecord(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            ok, src_resp = await self.vc.leave_source()
            n_ok, n_total = await self.vc.leave_all_targets()
            await msg.reply(f"{src_resp}\n✅ Left **{n_ok}/{n_total}** target VCs.")

        # ---------- /level -----------------------------------------------
        @b.on_message(filters.command("level"))
        async def cmd_level(_, msg: Message):
            if not self._auth(msg): return
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply("Usage: `/level <0-60>`")
            try:
                v = max(0, min(60, int(args[1])))
            except ValueError:
                return await msg.reply("❌ Value must be 0–60.")
            vi = round(v / 60 * 200)
            self.proc.update(volume=vi)
            self.db.update_audio_settings(volume=vi)
            await msg.reply(f"🔊 Volume → `{v}/60`\n{bar(v,60,20)}")

        # ---------- /bass ------------------------------------------------
        @b.on_message(filters.command("bass"))
        async def cmd_bass(_, msg: Message):
            if not self._auth(msg): return
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply("Usage: `/bass <0-60>`")
            try:
                v = max(0, min(60, int(args[1])))
            except ValueError:
                return await msg.reply("❌ Value must be 0–60.")
            self.proc.update(bass=v)
            self.db.update_audio_settings(bass=v)
            await msg.reply(f"🎵 Bass → `{v}/60`\n{bar(v,60,20)}")

        # ---------- /treble ----------------------------------------------
        @b.on_message(filters.command("treble"))
        async def cmd_treble(_, msg: Message):
            if not self._auth(msg): return
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply("Usage: `/treble <0-60>`")
            try:
                v = max(0, min(60, int(args[1])))
            except ValueError:
                return await msg.reply("❌ Value must be 0–60.")
            self.proc.update(treble=v)
            self.db.update_audio_settings(treble=v)
            await msg.reply(f"🎶 Treble → `{v}/60`\n{bar(v,60,20)}")

        # ---------- /clarity ---------------------------------------------
        @b.on_message(filters.command("clarity"))
        async def cmd_clarity(_, msg: Message):
            if not self._auth(msg): return
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply("Usage: `/clarity <0-60>`")
            try:
                v = max(0, min(60, int(args[1])))
            except ValueError:
                return await msg.reply("❌ Value must be 0–60.")
            self.proc.update(clarity=v)
            self.db.update_audio_settings(clarity=v)
            await msg.reply(f"✨ Clarity → `{v}/60`\n{bar(v,60,20)}")

        # ---------- /gain ------------------------------------------------
        @b.on_message(filters.command("gain"))
        async def cmd_gain(_, msg: Message):
            if not self._auth(msg): return
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply("Usage: `/gain <-15 to +15>`")
            try:
                v = max(-15, min(15, int(args[1])))
            except ValueError:
                return await msg.reply("❌ Value must be -15 to +15.")
            self.proc.update(gain=v)
            self.db.update_audio_settings(gain=v)
            sign = "+" if v >= 0 else ""
            await msg.reply(f"📈 Gain → `{sign}{v}dB`")

        # ---------- /mute / /unmute --------------------------------------
        @b.on_message(filters.command("mute"))
        async def cmd_mute(_, msg: Message):
            if not self._auth(msg): return
            self.proc.set_muted(True)
            self.db.update_audio_settings(muted=True)
            await msg.reply("🔇 Audio forwarding **muted** (still connected to all VCs).")

        @b.on_message(filters.command("unmute"))
        async def cmd_unmute(_, msg: Message):
            if not self._auth(msg): return
            self.proc.set_muted(False)
            self.db.update_audio_settings(muted=False)
            await msg.reply("🔊 Audio forwarding **unmuted**.")

        # ---------- /panel -----------------------------------------------
        @b.on_message(filters.command("panel"))
        async def cmd_panel(_, msg: Message):
            if not self._auth(msg):
                return await msg.reply("❌ Unauthorized.")
            text, kb = self._panel()
            await msg.reply(text, reply_markup=kb, parse_mode="markdown")

        # ---------- Callback queries -------------------------------------
        @b.on_callback_query()
        async def on_callback(_, cq: CallbackQuery):
            if not self.cfg.is_authorized(cq.from_user.id):
                return await cq.answer("❌ Unauthorized", show_alert=True)

            data  = cq.data
            audio = self.db.get_audio_settings()

            # -- simple toggles / actions --
            if data == "mute":
                self.proc.set_muted(True)
                self.db.update_audio_settings(muted=True)
                await cq.answer("🔇 Muted")

            elif data == "unmute":
                self.proc.set_muted(False)
                self.db.update_audio_settings(muted=False)
                await cq.answer("🔊 Unmuted")

            elif data == "status":
                await cq.answer()
                await cq.message.reply(self._status_text(), parse_mode="markdown")
                return

            elif data == "restart":
                await cq.answer("🔄 Restarting…")
                await asyncio.sleep(1)
                os.execv(sys.executable, [sys.executable] + sys.argv)

            elif data == "refresh":
                await cq.answer("🔄 Refreshed")

            # -- volume --
            elif data in ("vol_up", "vol_dn"):
                cur = round(audio.get("volume", 100) / 200 * 60)
                nv  = min(60, max(0, cur + (5 if data == "vol_up" else -5)))
                vi  = round(nv / 60 * 200)
                self.proc.update(volume=vi)
                self.db.update_audio_settings(volume=vi)
                await cq.answer(f"🔊 Volume: {nv}/60")

            # -- bass --
            elif data in ("bass_up", "bass_dn"):
                v = min(60, max(0, audio.get("bass", 0) + (5 if data == "bass_up" else -5)))
                self.proc.update(bass=v)
                self.db.update_audio_settings(bass=v)
                await cq.answer(f"🎵 Bass: {v}/60")

            # -- treble --
            elif data in ("treble_up", "treble_dn"):
                v = min(60, max(0, audio.get("treble", 0) + (5 if data == "treble_up" else -5)))
                self.proc.update(treble=v)
                self.db.update_audio_settings(treble=v)
                await cq.answer(f"🎶 Treble: {v}/60")

            # -- clarity --
            elif data in ("clarity_up", "clarity_dn"):
                v = min(60, max(0, audio.get("clarity", 0) + (5 if data == "clarity_up" else -5)))
                self.proc.update(clarity=v)
                self.db.update_audio_settings(clarity=v)
                await cq.answer(f"✨ Clarity: {v}/60")

            # -- gain --
            elif data in ("gain_up", "gain_dn"):
                v = min(15, max(-15, audio.get("gain", 0) + (1 if data == "gain_up" else -1)))
                self.proc.update(gain=v)
                self.db.update_audio_settings(gain=v)
                sign = "+" if v >= 0 else ""
                await cq.answer(f"📈 Gain: {sign}{v}dB")

            # Refresh the panel after any change
            try:
                new_text, new_kb = self._panel()
                await cq.message.edit_text(
                    new_text, reply_markup=new_kb, parse_mode="markdown"
                )
            except Exception:
                pass  # Message unchanged — Telegram will throw if content is same
