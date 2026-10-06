"""The two sleeps. Async sources call `async_sleep`; scripts/unasync.py rewrites it to `sync_sleep`."""

from __future__ import annotations

import asyncio
import time


async def async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def sync_sleep(seconds: float) -> None:
    time.sleep(seconds)
