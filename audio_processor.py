"""
audio_processor.py — FFmpeg Audio Filter Engine
All processing done entirely in RAM (zero disk I/O).
Supports: volume + compressor + limiter, bass, treble, clarity, gain, mute.
"""
import subprocess
from typing import List
from loguru import logger


class AudioProcessor:
    """
    Builds and applies an FFmpeg lavfi filter chain to raw PCM chunks.

    Audio format (input & output):
        codec  : s16le  (signed 16-bit little-endian PCM)
        rate   : 48000 Hz
        channels: 2 (stereo)
    """

    SAMPLE_RATE    = 48000
    CHANNELS       = 2
    CHUNK_SAMPLES  = 960                            # 20 ms frame @ 48 kHz
    BYTES_PER_SAMPLE = 2                            # 16-bit
    FRAME_BYTES    = CHUNK_SAMPLES * CHANNELS * BYTES_PER_SAMPLE  # 3840 B

    def __init__(self):
        # Internal state mirrors what is stored in DB
        self._s: dict = {
            "volume":  100,    # 0-200 internal  (0-60 user-facing)
            "bass":    0,       # 0-60
            "treble":  0,       # 0-60
            "clarity": 0,       # 0-60
            "gain":    0,       # -15 to +15 dB
            "muted":   False,
        }
        self._filter_chain: str = "anull"
        self._rebuild()

    # ------------------------------------------------------------------ #
    #  Public settings API                                                 #
    # ------------------------------------------------------------------ #

    def update(self, **kwargs):
        """Update one or more settings and rebuild filter chain."""
        self._s.update(kwargs)
        self._rebuild()

    def set_muted(self, muted: bool):
        self._s["muted"] = muted
        # No need to rebuild chain — mute is handled in process()

    def get_settings(self) -> dict:
        return dict(self._s)

    def load_from_db(self, settings: dict):
        """Restore persisted settings on startup."""
        self._s.update(settings)
        self._rebuild()

    # ------------------------------------------------------------------ #
    #  Filter chain builder                                                #
    # ------------------------------------------------------------------ #

    def _rebuild(self):
        """Compose the full FFmpeg -af filter graph string."""
        parts: List[str] = []

        # ---- Volume + Compressor + Limiter ----------------------------
        vol = self._s.get("volume", 100)
        if vol != 100:
            v = vol / 100.0
            parts.append(f"volume={v:.4f}")
            if v > 1.0:
                # Dynamic range compressor prevents harsh distortion
                parts.append(
                    f"acompressor=threshold=0.125:ratio=4:attack=5:"
                    f"release=50:makeup={min(v, 2.0):.2f}"
                )
            # Hard limiter — always applied when volume is non-default
            parts.append("alimiter=limit=0.95:attack=5:release=50")

        # ---- Bass: low-shelf + sub-bass punch -------------------------
        bass = self._s.get("bass", 0)
        if bass > 0:
            db = bass * 0.5          # range: 0 to +30 dB
            parts.append(f"equalizer=f=80:t=h:width=200:g={db:.1f}")
            parts.append(f"equalizer=f=40:t=h:width=100:g={db * 0.6:.1f}")

        # ---- Treble: high-shelf ---------------------------------------
        treble = self._s.get("treble", 0)
        if treble > 0:
            db = treble * 0.4        # range: 0 to +24 dB
            parts.append(f"equalizer=f=8000:t=h:width=3000:g={db:.1f}")
            parts.append(f"equalizer=f=12000:t=h:width=4000:g={db * 0.7:.1f}")

        # ---- Clarity: presence + upper-mid boost ---------------------
        clarity = self._s.get("clarity", 0)
        if clarity > 0:
            db = clarity * 0.3       # range: 0 to +18 dB
            parts.append(f"equalizer=f=3000:t=h:width=2000:g={db:.1f}")
            parts.append(f"equalizer=f=5000:t=h:width=2000:g={db * 0.8:.1f}")

        # ---- Gain: soft dB adjust ------------------------------------
        gain = self._s.get("gain", 0)
        if gain != 0:
            linear = 10 ** (gain / 20.0)
            parts.append(f"volume={linear:.5f}")

        self._filter_chain = ",".join(parts) if parts else "anull"
        logger.debug(f"[AudioProcessor] filter chain: {self._filter_chain}")

    # ------------------------------------------------------------------ #
    #  Audio processing                                                    #
    # ------------------------------------------------------------------ #

    def process(self, raw_pcm: bytes) -> bytes:
        """
        Apply filter chain to a raw PCM chunk.
        - Returns silence (zeros) when muted.
        - Returns raw_pcm unchanged when filter chain is 'anull' (passthrough).
        - Otherwise runs FFmpeg synchronously via stdin/stdout pipe.
        """
        if self._s.get("muted", False):
            return bytes(len(raw_pcm))          # silence

        if self._filter_chain == "anull":
            return raw_pcm                       # passthrough — zero overhead

        try:
            result = subprocess.run(
                self._build_cmd(),
                input=raw_pcm,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            )
            return result.stdout if result.stdout else raw_pcm
        except subprocess.TimeoutExpired:
            logger.warning("[AudioProcessor] FFmpeg timed out — passing raw audio")
            return raw_pcm
        except FileNotFoundError:
            logger.error("[AudioProcessor] ffmpeg binary not found — passing raw audio")
            return raw_pcm
        except Exception as exc:
            logger.error(f"[AudioProcessor] FFmpeg error: {exc}")
            return raw_pcm

    def _build_cmd(self) -> List[str]:
        sr = str(self.SAMPLE_RATE)
        ch = str(self.CHANNELS)
        return [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", sr, "-ac", ch, "-i", "pipe:0",
            "-af", self._filter_chain,
            "-f", "s16le", "-ar", sr, "-ac", ch,
            "pipe:1",
        ]
