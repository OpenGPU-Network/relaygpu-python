"""STUB (lead scaffold): the owning package replaces this file."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import AsyncRelay


class AsyncModels:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay


class AsyncPricing:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay


class AsyncTiers:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay


async def health(relay: AsyncRelay) -> Any:
    raise NotImplementedError
