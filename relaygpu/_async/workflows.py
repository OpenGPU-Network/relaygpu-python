"""STUB (lead scaffold): the owning package replaces this file."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import AsyncRelay


class AsyncWorkflows:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay
