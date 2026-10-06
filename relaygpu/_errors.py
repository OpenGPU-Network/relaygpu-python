"""The status → class table, the code refinement and the parser of every non-2xx answer (port of src/errors.ts)."""

from __future__ import annotations

import email.utils
import math
import time
from collections.abc import Mapping
from typing import Any

import httpx

from ._exceptions import (
    AuthenticationError,
    CapacityError,
    ConflictError,
    GoneError,
    InsufficientCreditsError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    RateLimitError,
    RelayAPIError,
    RelayInternalError,
    UpstreamTimeoutError,
    ValidationError,
)
from ._generated.error_codes import ERROR_CODE_CLASSES

STATUS_CLASSES: dict[int, type[RelayAPIError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    402: InsufficientCreditsError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    410: GoneError,
    422: ValidationError,
    429: RateLimitError,
    500: RelayInternalError,
    502: ProviderError,
    503: CapacityError,
    504: UpstreamTimeoutError,
}

# `error.type` of the OpenAI-shaped body a chat stream answers before its first chunk.
STREAM_TYPE_CLASSES: dict[str, type[RelayAPIError]] = {
    "invalid_request_error": InvalidRequestError,
    "authentication_error": AuthenticationError,
    "permission_error": PermissionDeniedError,
    "not_found_error": NotFoundError,
    "rate_limit_exceeded": RateLimitError,
    "rate_limit_error": RateLimitError,
    "capacity_exhausted": CapacityError,
    "worker_timeout": UpstreamTimeoutError,
    "timeout_error": UpstreamTimeoutError,
    "server_error": RelayInternalError,
}


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP date); ``None`` when absent or unreadable."""
    if value is None or not value.strip():
        return None
    try:
        secs = float(value)
    except ValueError:
        secs = math.nan
    if math.isfinite(secs):
        return max(0.0, secs)
    try:
        at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return float(max(0, math.ceil(at.timestamp() - time.time())))


def class_for(status: int, code: str | None, stream_type: str | None) -> type[RelayAPIError]:
    base = (STREAM_TYPE_CLASSES.get(stream_type) if stream_type else None) or STATUS_CLASSES.get(status) or RelayAPIError
    refined = ERROR_CODE_CLASSES.get(code) if code else None
    # A code only narrows: it applies when its class sits under the status class (VALIDATION_ERROR at 400 stays InvalidRequestError).
    return refined if refined is not None and issubclass(refined, base) else base


def error_from_response(status: int, body: Any, headers: httpx.Headers | Mapping[str, str]) -> RelayAPIError:
    """The typed error for a non-2xx answer. ``body`` is the parsed JSON (or text, or ``None``)."""
    hdrs = headers if isinstance(headers, httpx.Headers) else httpx.Headers(headers)
    err: dict[str, Any] | None = body["error"] if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
    # An int `code` is the OpenAI-shaped pre-first-chunk stream error (bool is not a code).
    stream_shaped = err is not None and isinstance(err.get("code"), int) and not isinstance(err.get("code"), bool)
    code = err["code"] if err is not None and isinstance(err.get("code"), str) else None
    etype = err["type"] if err is not None and isinstance(err.get("type"), str) else None
    rid = err.get("request_id") if err is not None else None
    request_id = rid if isinstance(rid, str) else hdrs.get("x-request-id")
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
    elif err is not None and "detail" in err:
        detail = err["detail"]
    else:
        detail = None
    emsg = err.get("message") if err is not None else None
    message = (
        (emsg if isinstance(emsg, str) and emsg else None)
        or (detail if isinstance(detail, str) and detail else None)
        or (body[:500] if isinstance(body, str) and body else None)
        or f"HTTP {status}"
    )
    cls = class_for(status, code, etype if stream_shaped else None)
    return cls(
        message,
        status=status,
        code=code,
        type=etype,
        request_id=request_id,
        detail=detail,
        retry_after=parse_retry_after(hdrs.get("retry-after")),
        headers=hdrs,
    )
