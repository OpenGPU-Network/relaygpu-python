"""The ``webhooks`` namespace (port of src/webhooks.ts ``Webhooks``). Verification itself is ``relaygpu/webhooks.py``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from .._util import path_id
from ..webhooks import (
    WebhookDeliveryDetail,
    WebhookDeliveryListParams,
    WebhookDeliveryPage,
    WebhookEvent,
    WebhookHeaders,
    WebhookSecret,
    verify_webhook,
)

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from .client import AsyncRelay


class AsyncWebhookDeliveries:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def list(self, **params: Unpack[WebhookDeliveryListParams]) -> WebhookDeliveryPage:
        """``GET /v2/customer/webhook-deliveries``, newest first; pass ``next_page`` back as ``page``. JWT or superkey."""
        res = await self._relay._http.request("GET", "/v2/customer/webhook-deliveries", query=cast("dict[str, Any]", params))
        return cast(WebhookDeliveryPage, res.data)

    async def get(self, task_id_or_run_id: str) -> WebhookDeliveryDetail:
        """``GET /v2/customer/webhook-deliveries/{id}``: the attempt timeline for a task id or a workflow run id (``wf:…``)."""
        res = await self._relay._http.request("GET", f"/v2/customer/webhook-deliveries/{path_id(task_id_or_run_id)}")
        return cast(WebhookDeliveryDetail, res.data)


class AsyncWebhooks:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay
        self.deliveries = AsyncWebhookDeliveries(relay)

    def verify(
        self,
        raw_body: bytes | bytearray | str,
        headers: WebhookHeaders,
        secret: str | Sequence[str],
        *,
        tolerance: float = 300,
        now: float | None = None,
    ) -> WebhookEvent:
        """See :func:`relaygpu.webhooks.verify_webhook`. Synchronous on both clients; needs no credential, makes no request."""
        return verify_webhook(raw_body, headers, secret, tolerance=tolerance, now=now)

    async def secret(self) -> WebhookSecret:
        """``GET /v2/customer/webhook-secret``: the account-level signing secret (``whsec_…``). JWT or superkey."""
        res = await self._relay._http.request("GET", "/v2/customer/webhook-secret")
        return cast(WebhookSecret, res.data)

    async def rotate_secret(self) -> WebhookSecret:
        """``POST /v2/customer/webhook-secret/rotate``: issues a new secret; the previous one keeps verifying for 24 h
        (``previous_valid_until``), deliveries carry both signatures meanwhile. Only two secrets are ever valid: rotating
        again inside the window retires the oldest at once. JWT or superkey."""
        res = await self._relay._http.request("POST", "/v2/customer/webhook-secret/rotate")
        return cast(WebhookSecret, res.data)
