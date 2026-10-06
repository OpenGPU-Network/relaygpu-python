"""STUB (lead scaffold): the owning package replaces this file."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import AsyncRelay


async def estimate_cost(relay: AsyncRelay, model: str, usage: Any) -> Any:
    raise NotImplementedError
