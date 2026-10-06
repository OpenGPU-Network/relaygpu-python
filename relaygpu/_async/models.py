"""The catalog, keyless (port of src/models.ts). ``models.get`` is the SDK's one resolver of route, ``model_in_body``,
``async_default`` and schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .. import _clock
from .. import _run_common as common
from .._util import model_path
from ..types import HealthResponse, ModelCatalog, ModelDetail, ModelRow, PricingResponse, TiersResponse

if TYPE_CHECKING:
    from .client import AsyncRelay


class AsyncModels:
    """``GET /v2/models`` and ``GET /v2/models/{name}`` (keyless)."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay
        self._cache: dict[str, tuple[float, ModelDetail]] = {}

    async def list(self, *, tag: str | None = None) -> list[ModelRow]:
        """``GET /v2/models`` flattened: every model listed under ``auto``, once, optionally filtered by ``tag``
        (e.g. ``text-to-video``)."""
        res = await self._relay._http.request("GET", "/v2/models", no_auth=True)
        data = cast(ModelCatalog, res.data)
        seen: dict[str, ModelRow] = {}
        for rows in (data.get("auto") or {}).values():
            for row in rows or []:
                seen.setdefault(row["name"], row)
        rows_all = list(seen.values())
        return [r for r in rows_all if r.get("tag") == tag] if tag else rows_all

    async def get(self, name: str) -> ModelDetail:
        """``GET /v2/models/{name}``: route, request/response schema, example, pricing, status. Cached per client for
        5 minutes. An unknown name raises ``ModelNotFoundError`` (never cached); a retired model resolves with
        ``status: "retired"``."""
        now = _clock.monotonic()
        hit = self._cache.get(name)
        if hit is not None and hit[0] > now:
            return hit[1]
        res = await self._relay._http.request("GET", model_path(name), no_auth=True)
        detail = cast(ModelDetail, res.data)
        self._cache[name] = (now + common.MODEL_CACHE_SECONDS, detail)
        return detail


class AsyncPricing:
    """``GET /v2/pricing``: list prices per mode and model, plus the ``media_storage`` per-file fees."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def get(self) -> PricingResponse:
        res = await self._relay._http.request("GET", "/v2/pricing", no_auth=True)
        return cast(PricingResponse, res.data)


class AsyncTiers:
    """``GET /v2/tiers``."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def list(self) -> TiersResponse:
        res = await self._relay._http.request("GET", "/v2/tiers", no_auth=True)
        return cast(TiersResponse, res.data)


async def health(relay: AsyncRelay) -> HealthResponse:
    """``GET /v2/health``: ``{status, version, commit}``."""
    res = await relay._http.request("GET", "/v2/health", no_auth=True)
    return cast(HealthResponse, res.data)
