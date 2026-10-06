"""Shared shapes of the workflows / account / keys namespaces (defined once for both clients): keyword-argument
TypedDicts mirroring the spec's query parameters, and the workflow-run envelope."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypedDict

from ._generated.types import WorkflowListResponse, WorkflowRunAccepted, WorkflowTemplateItem

if TYPE_CHECKING:
    from typing_extensions import Required
else:
    Required = Any

WorkflowList: TypeAlias = WorkflowListResponse
WorkflowTemplate: TypeAlias = WorkflowTemplateItem

DEFAULT_RUN_TIMEOUT = 30 * 60.0
"""Default ``wait_run`` budget, seconds."""

RUN_TERMINAL = frozenset({"completed", "failed", "cancelled"})


class WorkflowRunSubmitted(WorkflowRunAccepted, total=False):
    """The ``202`` of ``workflows.run``, with ``replayed`` (an earlier submit with the same ``Idempotency-Key``)."""

    replayed: Required[bool]


# ---- account (the keyword arguments; ``from_`` is sent as ``from``) ----


class CreditHistoryParams(TypedDict, total=False):
    action: str | None
    limit: int
    offset: int
    via: str | None


class UsageParams(TypedDict, total=False):
    from_: str | None
    limit: int | None
    period: str | None
    starting_after: str | None
    to: str | None


class KeyUsageParams(TypedDict, total=False):
    from_: str | None
    period: str | None
    to: str | None


GroupBy = Literal["model", "source", "mode", "key_id"]


class UsageTimeseriesParams(TypedDict, total=False):
    start_time: Required[int]
    end_time: int | None
    bucket_width: Literal["1m", "1h", "1d"]
    group_by: list[GroupBy] | None
    key_ids: list[str] | None
    limit: int | None
    models: list[str] | None
    modes: list[str] | None
    page: str
    sources: list[str] | None


MetricsParams = UsageTimeseriesParams


# ---- keys ----


class KeyListAllParams(TypedDict, total=False):
    is_superkey: bool | None
    limit: int
    name: str | None
    name_prefix: str | None
    status: Literal["active", "revoked"] | None


class KeyListParams(KeyListAllParams, total=False):
    starting_after: str | None


def query_of(params: Any) -> dict[str, Any]:
    """Keyword arguments → query, in the caller's order; ``from_`` (a Python keyword) is sent as ``from``."""
    return {("from" if k == "from_" else k): v for k, v in params.items()}
