"""Error base classes. Status picks the class, ``error.code`` refines it (``_generated/error_codes.py``); an unknown
code degrades to the status class and keeps ``code``. Never carries the credential."""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "CapacityError",
    "ConflictError",
    "GoneError",
    "InsufficientCreditsError",
    "InvalidRequestError",
    "NotFoundError",
    "PermissionDeniedError",
    "ProviderError",
    "RateLimitError",
    "RelayAPIError",
    "RelayError",
    "RelayInternalError",
    "TaskFailedError",
    "UpstreamTimeoutError",
    "ValidationError",
]


class RelayError(Exception):
    """Root of every error this SDK raises."""

    status: int | None
    code: str | None
    type: str | None
    request_id: str | None
    detail: Any
    retry_after: float | None
    """Seconds, from ``Retry-After`` (429/503 and ``409 IDEMPOTENCY_IN_PROGRESS``)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        type: str | None = None,
        request_id: str | None = None,
        detail: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.type = type
        self.request_id = request_id
        self.detail = detail
        self.retry_after = retry_after

    def __str__(self) -> str:
        return self.message

    def __reduce__(self) -> tuple[Any, ...]:
        # Picklable (multiprocessing, task queues) although __init__ takes keyword-only fields.
        return (_rebuild, (type(self), self.message, dict(self.__dict__)))

    def __repr__(self) -> str:
        parts = [repr(self.message)]
        for k in ("status", "code", "request_id"):
            v = getattr(self, k)
            if v is not None:
                parts.append(f"{k}={v!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


def _rebuild(cls: type[RelayError], message: str, state: dict[str, Any]) -> RelayError:
    err = Exception.__new__(cls)
    Exception.__init__(err, message)
    err.__dict__.update(state)
    return err


class RelayAPIError(RelayError):
    """An HTTP error answered by the API."""

    headers: httpx.Headers | None
    """Response headers (never the request's, so never the credential)."""

    def __init__(self, message: str, *, headers: httpx.Headers | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.headers = headers


class InvalidRequestError(RelayAPIError):
    """HTTP 400."""


class AuthenticationError(RelayAPIError):
    """HTTP 401."""


class InsufficientCreditsError(RelayAPIError):
    """HTTP 402."""


class PermissionDeniedError(RelayAPIError):
    """HTTP 403."""


class NotFoundError(RelayAPIError):
    """HTTP 404."""


class ConflictError(RelayAPIError):
    """HTTP 409."""


class GoneError(RelayAPIError):
    """HTTP 410."""


class ValidationError(RelayAPIError):
    """HTTP 422."""


class RateLimitError(RelayAPIError):
    """HTTP 429."""


class RelayInternalError(RelayAPIError):
    """HTTP 500."""


class ProviderError(RelayAPIError):
    """HTTP 502."""


class CapacityError(RelayAPIError):
    """HTTP 503."""


class UpstreamTimeoutError(RelayAPIError):
    """HTTP 504."""


class APIConnectionError(RelayError):
    """The request never got an HTTP answer (DNS, reset, TLS)."""


class APITimeoutError(RelayError):
    """The client-side ``timeout`` elapsed (per attempt, or the ``tasks.wait`` budget)."""


class TaskFailedError(RelayError):
    """A task (or workflow run) reached ``failed``; ``code`` is the task's top-level ``error_code``."""

    task_id: str
    task: Any

    def __init__(self, message: str, *, task_id: str, task: Any, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.task_id = task_id
        self.task = task
