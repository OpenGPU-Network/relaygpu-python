"""``estimate_cost`` (port of src/estimate.ts): the one network call; the math lives in ``relaygpu/_estimate.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._estimate import estimate
from ..types import CostEstimate, UsageInput

if TYPE_CHECKING:
    from .client import AsyncRelay


async def estimate_cost(relay: AsyncRelay, model: str, usage: UsageInput) -> CostEstimate:
    """Estimates the USD cost of a call from ``GET /v2/pricing`` (the only network call). An estimate, never an
    invoice: the bill comes from provider-reported usage and any custom-tier pricing your account carries. Raises a
    ``RelayError`` for a model with no row in the mode, an unknown billing type or an unoffered storage SKU."""
    return estimate(await relay.pricing.get(), model, usage)
