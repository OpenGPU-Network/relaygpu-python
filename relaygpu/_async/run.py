"""STUB (lead scaffold): the owning package replaces this file."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import AsyncRelay


async def run(relay: AsyncRelay, model: str, input: Any, **opts: Any) -> Any:
    raise NotImplementedError
