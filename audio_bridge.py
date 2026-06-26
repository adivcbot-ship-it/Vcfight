"""
audio_bridge.py — Zero-Disk In-RAM Audio Relay
Source PCM frames are pushed in, processed, then distributed
to per-target asyncio Queues for real-time forwarding.
"""
import asyncio
import threading
import time
from typing import Dict, Optional
from loguru import logger

from audio_processor import AudioProcessor


class AudioBridge:
    """
    Receives PCM frames from the source VC (via push()),
    runs them through AudioProcessor, and places them into
    per-target asyncio.Queue objects for the forward tasks.
    No files are written at any point.
    """

    MAX_QUEUE_FRAMES = 50       # ~1 second worth of 20ms frames

    def __init__(self, processor: AudioProcessor):
        self._proc = processor
        self._queues: Dict[int, asyncio.Queue] = {}
        self._running = False
        self._lock = threading.Lock()
        self._bytes_relayed: int = 0
        self._start_time: Optional[float] = None

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Activate the bridge. Must be called before push()."""
        self._running = True
        self._bytes_relayed = 0
        self._start_time = time.monotonic()
        logger.success("[AudioBridge] started")

    def stop(self):
        """Deactivate bridge and clear all target queues."""
        self._running = False
        with self._lock:
            self._queues.clear()
        logger.info("[AudioBridge] stopped")

    # ------------------------------------------------------------------ #
    #  Target management                                                   #
    # ------------------------------------------------------------------ #

    def add_target(self, chat_id: int):
        """Register a new target VC — creates a dedicated queue."""
        with self._lock:
            if chat_id not in self._queues:
                # NOTE: asyncio.Queue must be created inside the running loop.
                # We defer creation to first pull() call if loop not ready;
                # but typically add_target is called from an async context so
                # the loop is already running.
                try:
                    self._queues[chat_id] = asyncio.Queue(maxsize=self.MAX_QUEUE_FRAMES)
                except RuntimeError:
                    # No event loop yet; will be created on first pull()
                    self._queues[chat_id] = None
                logger.debug(f"[AudioBridge] added target {chat_id}")

    def remove_target(self, chat_id: int):
        """Unregister a target VC."""
        with self._lock:
            self._queues.pop(chat_id, None)
        logger.debug(f"[AudioBridge] removed target {chat_id}")

    def has_target(self, chat_id: int) -> bool:
        return chat_id in self._queues

    def target_count(self) -> int:
        return len(self._queues)

    # ------------------------------------------------------------------ #
    #  Audio push  (called from PyTgCalls thread/callback)                #
    # ------------------------------------------------------------------ #

    def push(self, raw_pcm: bytes):
        """
        Thread-safe. Called by PyTgCalls when a PCM frame arrives from source.
        Processes audio and distributes to every target queue.
        """
        if not self._running:
            return

        processed = self._proc.process(raw_pcm)
        self._bytes_relayed += len(processed)

        with self._lock:
            queues = list(self._queues.values())

        for q in queues:
            if q is None:
                continue
            if q.full():
                # Drop oldest frame to maintain real-time
                try:
                    q.get_nowait()
                except Exception:
                    pass
            try:
                q.put_nowait(processed)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Audio pull  (called by per-target asyncio forward tasks)           #
    # ------------------------------------------------------------------ #

    async def pull(self, chat_id: int, timeout: float = 0.05) -> Optional[bytes]:
        """
        Async. Wait up to `timeout` seconds for the next processed frame
        destined for `chat_id`. Returns None on timeout or unknown target.
        """
        with self._lock:
            q = self._queues.get(chat_id)

        if q is None:
            # Lazy-init if queue was registered before event loop started
            with self._lock:
                if chat_id in self._queues and self._queues[chat_id] is None:
                    self._queues[chat_id] = asyncio.Queue(maxsize=self.MAX_QUEUE_FRAMES)
                    q = self._queues[chat_id]
                else:
                    return None

        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ------------------------------------------------------------------ #
    #  Stats                                                               #
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self._running

    @property
    def bytes_relayed(self) -> int:
        return self._bytes_relayed

    @property
    def uptime(self) -> float:
        if self._start_time and self._running:
            return time.monotonic() - self._start_time
        return 0.0
