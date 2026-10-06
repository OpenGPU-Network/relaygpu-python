"""The clock, the two sleeps and the file write: the one seam for time and blocking I/O. SDK modules call these through
the module (``_clock.async_sleep(...)``, ``_clock.monotonic()``) so tests patch them in one place; scripts/unasync.py
rewrites each ``async_*`` name to its ``sync_*`` twin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path


def monotonic() -> float:
    """The clock of waits and caches."""
    return time.monotonic()


async def async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def sync_sleep(seconds: float) -> None:
    time.sleep(seconds)


async def async_write_bytes(path: str | Path, data: bytes) -> None:
    """Writes a file off the event loop."""
    await asyncio.to_thread(Path(path).write_bytes, data)


def sync_write_bytes(path: str | Path, data: bytes) -> None:
    Path(path).write_bytes(data)
