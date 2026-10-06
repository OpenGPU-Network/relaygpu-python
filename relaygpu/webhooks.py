"""Webhook verification (Standard Webhooks) and the event types (port of src/webhooks.ts). Pure: no I/O, stdlib only.

``verify_webhook`` is a plain function; ``relay.webhooks.verify`` delegates to it on both clients (no ``await``: the
TS version is async only because WebCrypto is).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, TypedDict, Union

from ._exceptions import RelayError
from ._generated.types import (
    WebhookDeliveryDetail,
    WebhookDeliveryPage,
    WebhookPayload,
    WebhookSecretResponse,
    WorkflowRunWebhookPayload,
)
from ._util import forgiving_b64decode, js_number

if TYPE_CHECKING:
    from typing_extensions import Required
else:
    Required = Any

__all__ = [
    "InstanceEventName",
    "InstanceWebhookEvent",
    "TaskWebhookEvent",
    "WebhookDeliveryDetail",
    "WebhookDeliveryListParams",
    "WebhookDeliveryPage",
    "WebhookEvent",
    "WebhookEventName",
    "WebhookHeaders",
    "WebhookSecret",
    "WebhookVerificationError",
    "WorkflowWebhookEvent",
    "verify_webhook",
]

# ---------------------------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------------------------

TaskWebhookEvent: TypeAlias = WebhookPayload
"""``task.completed`` / ``task.failed``: the terminal task body plus ``event``."""

WorkflowWebhookEvent: TypeAlias = WorkflowRunWebhookPayload
"""``workflow.completed`` / ``workflow.failed``: one per workflow run submitted with ``webhook_url``.

- ``result`` is the run exactly as ``GET /v2/workflows/runs/{run_id}`` returns it (absent, with ``result_omitted: True``,
  only past the 1 MiB inline cap).
- The top-level ``status`` is **always** ``"completed"`` (the delivery's envelope is complete, not the run). Branch on
  ``event`` or on ``result["status"]``, never on ``status``.
- A **cancelled** run arrives as ``workflow.failed`` with ``result["status"] == "cancelled"``.
- ``task_id`` is the run id (``wf:…``).
"""

InstanceEventName = Literal["instance.ready", "instance.failed", "instance.terminated", "instance.warning", "instance.grace"]


class InstanceWebhookEvent(TypedDict, total=False):
    """``instance.*``: ``result`` is the instance as ``GET /v2/instances/{instance_id}`` returns it (plus ``wallet`` on
    ``instance.warning`` / ``instance.grace``). The top-level ``status`` is always ``"completed"``; the instance's own
    state is ``result["status"]``. ``task_id`` is ``{instance_id}:{event}``."""

    event: Required[InstanceEventName]
    task_id: Required[str]
    status: Required[str]
    mode: Required[str]
    model: Required[str]
    created_at: Required[str]
    elapsed_seconds: Required[float]
    result: dict[str, Any] | None
    result_omitted: bool | None
    task_address: str | None


WebhookEvent = Union[TaskWebhookEvent, WorkflowWebhookEvent, InstanceWebhookEvent]
"""A verified delivery, discriminated on ``event``."""

_DeliveryEventName = Literal["task.completed", "task.failed", "workflow.completed", "workflow.failed"]
"""The task and workflow events (the ones ``webhooks.deliveries`` lists)."""

WebhookEventName = Literal[_DeliveryEventName, InstanceEventName]


class _HeaderGetter(Protocol):
    def get(self, name: str, /) -> Any: ...


WebhookHeaders = Union[Mapping[str, Any], _HeaderGetter]
"""A ``dict`` (any key case; a value may be a list), ``httpx.Headers``, or any object with ``.get`` (e.g. the
``http.server`` request's ``self.headers``). Lookup is case-insensitive."""

WebhookSecret: TypeAlias = WebhookSecretResponse


class WebhookDeliveryListParams(TypedDict, total=False):
    """Query of ``GET /v2/customer/webhook-deliveries``."""

    event: _DeliveryEventName | None
    limit: int
    outcome: Literal["delivered", "failed", "gave_up", "blocked"] | None
    page: str | None
    task_id: str | None


class WebhookVerificationError(RelayError):
    """Client-side verification failure. ``code`` is one of ``WEBHOOK_MISSING_HEADERS``, ``WEBHOOK_INVALID_TIMESTAMP``,
    ``WEBHOOK_TIMESTAMP_OUT_OF_RANGE``, ``WEBHOOK_INVALID_SECRET``, ``WEBHOOK_SIGNATURE_MISMATCH``,
    ``WEBHOOK_INVALID_BODY``. Never carries the secret or the body."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code=code)


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------

_INT = re.compile(r"[0-9]+")


def _header(headers: WebhookHeaders, name: str) -> str | None:
    items = getattr(headers, "items", None)
    if callable(items):
        for k, v in items():
            if not isinstance(k, str) or k.lower() != name or v is None:
                continue
            return " ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)
        return None
    v = headers.get(name)
    return None if v is None else str(v)


def _no_constant(name: str) -> Any:
    raise ValueError(name)  # JSON.parse rejects NaN / Infinity


def verify_webhook(
    raw_body: bytes | bytearray | str,
    headers: WebhookHeaders,
    secret: str | Sequence[str],
    *,
    tolerance: float = 300,
    now: float | None = None,
) -> WebhookEvent:
    """Verifies a Relay webhook delivery (Standard Webhooks) and returns the typed event.

    Pass the **raw** request body, byte-exact (not a re-serialised ``json.loads`` result; a ``str`` is UTF-8 encoded),
    the request headers (case-insensitive) and the account's signing secret (``whsec_…``, from ``webhooks.secret()``).
    During the 24 h grace after a rotation pass ``[current, previous]``: a delivery is accepted when any ``v1,``
    signature matches any secret. ``tolerance`` is the accepted clock skew in seconds, both directions; ``now`` is a
    Unix epoch in seconds (for replaying captured deliveries), default the clock.

    Raises :class:`WebhookVerificationError` on missing headers, a timestamp outside ±``tolerance``, no matching
    signature, or a body that is not a JSON event. Dedupe on the ``webhook-id`` header: delivery is at-least-once.
    """
    msg_id = _header(headers, "webhook-id")
    ts = _header(headers, "webhook-timestamp")
    sig = _header(headers, "webhook-signature")
    if not msg_id or not ts or not sig:
        raise WebhookVerificationError("WEBHOOK_MISSING_HEADERS", "Missing webhook-id, webhook-timestamp or webhook-signature header")

    if not _INT.fullmatch(ts.strip()):
        raise WebhookVerificationError("WEBHOOK_INVALID_TIMESTAMP", "webhook-timestamp is not an integer epoch in seconds")
    current = now if now is not None else int(time.time())
    if abs(current - int(ts.strip())) > tolerance:
        raise WebhookVerificationError(
            "WEBHOOK_TIMESTAMP_OUT_OF_RANGE", f"webhook-timestamp is outside the {js_number(tolerance)} s tolerance"
        )

    secrets = [secret] if isinstance(secret, str) else list(secret)
    secrets = [s for s in secrets if isinstance(s, str) and s != ""]
    if not secrets:
        raise WebhookVerificationError("WEBHOOK_INVALID_SECRET", "No signing secret given")
    keys: list[bytes] = []
    for s in secrets:
        k = forgiving_b64decode(s[6:] if s.startswith("whsec_") else s)
        if not k:
            raise WebhookVerificationError("WEBHOOK_INVALID_SECRET", "Signing secret is not a whsec_<base64> value")
        keys.append(k)

    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    signed = f"{msg_id}.{ts}.".encode() + body

    given = [g for g in (forgiving_b64decode(t[3:]) for t in sig.split() if t.startswith("v1,")) if g is not None]

    ok = False
    for k in keys:
        expected = hmac.new(k, signed, hashlib.sha256).digest()
        for g in given:
            if hmac.compare_digest(expected, g):
                ok = True
    if not ok:
        raise WebhookVerificationError("WEBHOOK_SIGNATURE_MISMATCH", "No webhook-signature matches the signing secret")

    try:
        event = json.loads(body.decode("utf-8", errors="replace"), parse_constant=_no_constant)
    except ValueError:
        raise WebhookVerificationError("WEBHOOK_INVALID_BODY", "Webhook body is not JSON") from None
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        raise WebhookVerificationError("WEBHOOK_INVALID_BODY", "Webhook body has no event")
    return event  # type: ignore[return-value]
