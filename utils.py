"""
utils.py — Shared utility helpers.
"""
import time


def fmt_uptime(seconds: float) -> str:
    """Human-readable uptime string from seconds."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def fmt_bytes(b: int) -> str:
    """Human-readable byte size string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"


def progress_bar(val: int, maximum: int, length: int = 10) -> str:
    """Text progress bar using block characters."""
    filled = round(length * max(0, val) / max(maximum, 1))
    return "█" * filled + "░" * (length - filled)


def elapsed(since: float) -> str:
    """Human-readable elapsed time from a monotonic timestamp."""
    return fmt_uptime(time.monotonic() - since)


# Short alias used in handlers
bar = progress_bar
