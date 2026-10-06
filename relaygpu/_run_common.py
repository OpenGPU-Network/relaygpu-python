"""The transport-free half of run / submit / tasks (port of src/submit.ts, src/run.ts, src/tasks.ts): refusals, body
building, the long-poll arithmetic and the errors a wait raises. ``_async/run.py`` and ``_async/tasks.py`` (and their
generated sync twins) only do I/O."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from ._exceptions import APITimeoutError, RelayError, TaskFailedError
from ._generated.error_codes import ModelRetiredError
from ._util import path_id
from .types import AsyncAccepted, Mode, ModelDetail, ModelEndpoint, TaskStatus

MODEL_CACHE_SECONDS = 5 * 60.0
"""``models.get`` caches one detail per name and client for this long."""

DEFAULT_WAIT_TIMEOUT = 20 * 60.0
"""Seconds ``tasks.wait`` waits by default (20 min)."""
MAX_WAIT_S = 30
"""The server holds a ``?wait=`` call at most this long."""
POLL_FLOOR = 0.5
"""Seconds between two polls at least, so a shed (early) answer never turns into a hot loop."""
POLL_SLACK = 15.0
"""Slack over ``wait`` for the HTTP timeout of one long-poll."""


def is_accepted(v: Mapping[str, Any]) -> TypeGuard[AsyncAccepted]:
    """True for the ``202`` envelope ``run(..., wait=False)`` returns when the route answered async."""
    return isinstance(v.get("task_id"), str) and isinstance(v.get("poll_url"), str) and v.get("status") == "queued"


def resolve_endpoint(name: str, detail: ModelDetail) -> ModelEndpoint:
    """Refuses a retired model (or one with no single route) before anything is sent."""
    if detail.get("status") == "retired":
        raise ModelRetiredError(f"Model '{name}' is retired and no longer served", status=403, code="MODEL_RETIRED")
    endpoint = detail.get("endpoint")
    if not endpoint:
        raise RelayError(f"Model '{name}' has no single route to submit to; call it with relay.request()")
    return endpoint


def build_body(
    model: str,
    input: Mapping[str, Any],
    endpoint: ModelEndpoint,
    *,
    mode: Mode | None,
    store_output: str | None,
    webhook_url: str | None,
    async_: bool | None,
) -> tuple[dict[str, Any], bool]:
    """The submit body and whether the submit is async: ``async_`` → the input's ``async`` (a bool) → the route's
    ``async_default``. ``async: false`` is sent only when the route defaults async."""
    body: dict[str, Any] = dict(input)
    if endpoint.get("model_in_body"):
        body["model"] = model
    else:
        body.pop("model", None)
    if mode:
        body["mode"] = mode
    if store_output:
        body["store_output"] = store_output
    if webhook_url:
        body["webhook_url"] = webhook_url
    from_input = input.get("async")
    if async_ is not None:
        is_async = async_
    elif isinstance(from_input, bool):
        is_async = from_input
    else:
        is_async = bool(endpoint.get("async_default"))
    if is_async:
        body["async"] = True
    elif endpoint.get("async_default"):
        body["async"] = False
    else:
        body.pop("async", None)
    return body, is_async


def accepted_envelope(data: Any, replayed: bool, request_id: str | None) -> AsyncAccepted:
    out: dict[str, Any] = {**(data or {}), "replayed": replayed, "request_id": request_id}
    return out  # type: ignore[return-value]


@dataclass(frozen=True)
class SubmitResult:
    """A sync route's body (``accepted is None``) or an async submit's ``202`` envelope."""

    data: dict[str, Any]
    accepted: AsyncAccepted | None
    request_id: str | None


def task_path(task_id: str) -> str:
    """The task id goes into the path raw: the colon of ``direct:{uuid}`` stays literal."""
    return "/v2/tasks/" + path_id(task_id)


def poll_wait(remaining: float) -> int:
    """``?wait=`` of the next long-poll: never more than the remaining budget (ceil), at least 1, at most 30."""
    return max(1, min(MAX_WAIT_S, math.ceil(remaining)))


def wait_timeout_error(task_id: str, budget: float, task: TaskStatus | None) -> APITimeoutError:
    status = task.get("status") if task else None
    return APITimeoutError(
        f"Task {task_id} is still {status or 'pending'} after {budget:g} s (it keeps running; poll it again or wait longer)",
        detail={"task_id": task_id, "task": task},
    )


def task_failed_error(task_id: str, task: TaskStatus, request_id: str | None) -> TaskFailedError:
    return TaskFailedError(
        task.get("error") or f"Task {task_id} failed",
        code=task.get("error_code"),
        detail=task.get("error_detail"),
        request_id=request_id,
        task_id=task_id,
        task=task,
    )
