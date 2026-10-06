"""Public types: every generated schema (``relaygpu._generated.types``) plus the SDK's own shapes and the aliases the
TS client names (``TaskStatus``, ``FileObject``, …). TypedDicts only: values are plain ``dict``s at runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypedDict

from ._generated.models_map import KnownImageModel, KnownSpeechModel, KnownTranscribeModel, KnownVideoModel
from ._generated.types import *  # noqa: F403
from ._generated.types import (
    AsyncTaskAccepted,
    FileListResponse,
    FileResponse,
    ModelDetail,
    ModelEndpoint,
    ModelRow,
    ModelsResponse,
    PricingItem,
    PricingResponse,
    TaskStatusResponse,
    TiersResponse,
    WorkflowRunState,
)
from ._shapes import WorkflowList, WorkflowRunSubmitted, WorkflowTemplate
from .webhooks import (
    InstanceEventName,
    InstanceWebhookEvent,
    TaskWebhookEvent,
    WebhookDeliveryListParams,
    WebhookEvent,
    WebhookEventName,
    WebhookHeaders,
    WebhookSecret,
    WorkflowWebhookEvent,
)

if TYPE_CHECKING:
    from typing import TypeAlias

    from typing_extensions import Required
else:
    Required = TypeAlias = Any

Mode = Literal["auto", "direct", "opengpu"]
TaskStatus: TypeAlias = TaskStatusResponse
FileObject: TypeAlias = FileResponse
ModelCatalog: TypeAlias = ModelsResponse
PricingRow: TypeAlias = PricingItem

Retention: TypeAlias = str
"""How long an uploaded file's link lives: ``relay1h`` (free, daily quota) or a ``media_storage`` SKU from
``GET /v2/pricing`` (``relay1d``, ``relay7d``, ``relay30d``). SKUs are data: any string the server offers works."""

StoreOutput: TypeAlias = str
"""A ``store_output`` SKU: ``provider`` (default) or ``relay1d|relay7d|relay30d``; the live set is ``/v2/pricing.media_storage``."""


class HealthResponse(TypedDict):
    """``GET /v2/health``."""

    status: Literal["ok", "degraded"]
    version: str
    commit: str


class AsyncAccepted(AsyncTaskAccepted, total=False):
    """The ``202`` envelope of an async submit, with ``replayed`` (the server replayed an earlier submit with the same
    ``Idempotency-Key``) and the response's ``request_id``."""

    replayed: Required[bool]
    request_id: Required[str | None]


class TaskProgress(TypedDict):
    """A status transition while waiting (``queued`` → ``running`` → …). There is no finer-grained progress."""

    status: Literal["queued", "running", "completed", "failed"]
    elapsed_seconds: int


class UploadOptions(TypedDict, total=False):
    """Options of the implicit uploads a submit makes for file values in ``*_url`` fields."""

    retention: Retention
    """Default ``relay1h`` (free)."""


class UsageInput(TypedDict, total=False):
    """What a call used (or will use), in the server's usage vocabulary (an audit-style ``usage`` block can be passed
    straight in). Only the fields the model's billing type reads matter."""

    mode: Literal["direct", "opengpu"]
    """Pricing mode. Default ``direct``."""
    input_tokens: float
    output_tokens: float
    cached_input_tokens: float
    """Cached-read tokens: a subset of ``input_tokens``."""
    cache_write_5m_input_tokens: float
    cache_write_1h_input_tokens: float
    image_count: float
    resolution_tier: str
    """Resolution key of a resolution-priced row (``1K``, ``2K``, ``4K``, ``1024x1024``, …)."""
    duration_seconds: float
    quality_mode: str
    """Video: ``std`` | ``pro`` (Kling)."""
    sound: bool
    """Video: sound on/off (Kling ``sound``, Seedance ``generate_audio``, Motion-Control ``keep_original_sound``)."""
    has_ref: bool
    """Video: a reference input is present (Kling O1)."""
    character_count: float
    media_input_tokens: float
    media_output_tokens: float
    request_count: float
    store_output: str
    """Output hosting: ``provider`` is free; ``relay1d|relay7d|relay30d`` add a per-file fee."""
    retention: str
    """Upload retention (``POST /v2/files``): ``relay1h`` is free; ``relay1d|relay7d|relay30d`` add a per-file fee."""
    file_count: float
    """Files the storage fee applies to. Default: ``image_count``, else 1."""


class CostEstimate(TypedDict):
    """A client-side estimate. ``basis`` says how it was computed. Never an invoice."""

    usd: float
    basis: str


__all__ = [
    "AsyncAccepted",
    "CostEstimate",
    "FileListResponse",
    "FileObject",
    "HealthResponse",
    "InstanceEventName",
    "InstanceWebhookEvent",
    "KnownImageModel",
    "KnownSpeechModel",
    "KnownTranscribeModel",
    "KnownVideoModel",
    "Mode",
    "ModelCatalog",
    "ModelDetail",
    "ModelEndpoint",
    "ModelRow",
    "PricingResponse",
    "PricingRow",
    "Retention",
    "StoreOutput",
    "TaskProgress",
    "TaskStatus",
    "TaskWebhookEvent",
    "TiersResponse",
    "UploadOptions",
    "UsageInput",
    "WebhookDeliveryListParams",
    "WebhookEvent",
    "WebhookEventName",
    "WebhookHeaders",
    "WebhookSecret",
    "WorkflowList",
    "WorkflowRunState",
    "WorkflowRunSubmitted",
    "WorkflowTemplate",
    "WorkflowWebhookEvent",
]
