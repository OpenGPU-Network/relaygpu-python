"""The ``account`` namespace (port of src/account.ts)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from .._generated.types import (
    CreditHistoryResponse,
    CreditsResponse,
    CustomerPricingResponse,
    CustomerUsageResponse,
    KeyWithAnalytics,
    MetricsPage,
    ModelAllowlistResponse,
    ModelAllowlistView,
    ProfileResponse,
    UsageTimeseriesPage,
)
from .._shapes import CreditHistoryParams, KeyUsageParams, MetricsParams, UsageParams, UsageTimeseriesParams, query_of
from .._util import path_id

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from .client import AsyncRelay


class AsyncAccount:
    """Account reads (Customer + Metrics ops). Auth: a dashboard JWT, or the superkey of a partner (custom) tier. A plain
    inference key gets the server's 403 as ``PermissionDeniedError``; the SDK never guesses the key class client-side.

    Query parameters are keyword arguments with the spec's names; ``from`` is spelled ``from_``."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def _get(self, path: str, params: Any = None) -> Any:
        res = await self._relay._http.request("GET", path, query=query_of(params) if params else None)
        return res.data

    async def credits(self) -> CreditsResponse:
        """``GET /v2/customer/credits``: balance, promos, consumption."""
        return cast(CreditsResponse, await self._get("/v2/customer/credits"))

    async def credits_history(self, **params: Unpack[CreditHistoryParams]) -> CreditHistoryResponse:
        """``GET /v2/customer/credits/history``. **JWT only** (a superkey is refused by the server)."""
        return cast(CreditHistoryResponse, await self._get("/v2/customer/credits/history", params))

    async def usage(self, **params: Unpack[UsageParams]) -> CustomerUsageResponse:
        """``GET /v2/customer/usage``: per-key analytics; page with ``starting_after`` = ``next_cursor``."""
        return cast(CustomerUsageResponse, await self._get("/v2/customer/usage", params))

    async def usage_by_key(self, key_id: str, **params: Unpack[KeyUsageParams]) -> KeyWithAnalytics:
        """``GET /v2/customer/usage/{key_id}``. Address the key by ``key_id``, never its secret."""
        return cast(KeyWithAnalytics, await self._get(f"/v2/customer/usage/{path_id(key_id)}", params))

    async def usage_timeseries(self, **params: Unpack[UsageTimeseriesParams]) -> UsageTimeseriesPage:
        """``GET /v2/customer/usage/timeseries``: bucketed spend/tokens (``start_time`` = Unix seconds, required)."""
        return cast(UsageTimeseriesPage, await self._get("/v2/customer/usage/timeseries", params))

    async def metrics(self, **params: Unpack[MetricsParams]) -> MetricsPage:
        """``GET /v2/customer/metrics``: latency percentiles and error splits (``start_time`` = Unix seconds, required)."""
        return cast(MetricsPage, await self._get("/v2/customer/metrics", params))

    async def pricing(self) -> CustomerPricingResponse:
        """``GET /v2/customer/pricing``: the account's effective rows (custom-tier overrides included)."""
        return cast(CustomerPricingResponse, await self._get("/v2/customer/pricing"))

    async def profile(self) -> ProfileResponse:
        """``GET /v2/customer/profile``: profile, tier (``tier_details.is_custom``), balance, allowlist."""
        return cast(ProfileResponse, await self._get("/v2/customer/profile"))

    async def model_allowlist(self) -> ModelAllowlistView:
        """``GET /v2/customer/model-allowlist``: the customer-wide scope list for non-superkey keys (``None`` = unset)."""
        return cast(ModelAllowlistView, await self._get("/v2/customer/model-allowlist"))

    async def set_model_allowlist(self, model_allowlist: Sequence[str] | None) -> ModelAllowlistResponse:
        """``PATCH /v2/customer/model-allowlist``: replaces the list (``None`` clears it). Scopes are
        ``{mode}.{source}.{model}``. Partner tiers only; the superkey stays exempt."""
        body = {"model_allowlist": None if model_allowlist is None else [*model_allowlist]}
        res = await self._relay._http.request("PATCH", "/v2/customer/model-allowlist", json=body)
        return cast(ModelAllowlistResponse, res.data)
