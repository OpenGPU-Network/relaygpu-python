"""The transport-free half of the HTTP layer (port of src/http.ts): options, headers, query and body encoding, the
retry policy (F7) and response parsing. ``_async/_http.py`` (and its generated sync twin) only send bytes."""

from __future__ import annotations

import json as _json
import keyword
import random
from collections.abc import AsyncIterable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypedDict, TypeVar, Union

import httpx

from ._exceptions import APIConnectionError, APITimeoutError
from ._version import VERSION

DEFAULT_BASE_URL = "https://relaygpu.com"
DEFAULT_TIMEOUT = 600.0
"""Seconds, per HTTP attempt (10 min)."""
USER_AGENT = f"relaygpu-python/{VERSION}"

T = TypeVar("T")

_UNSET: Any = object()
"""The "no JSON body" default of `request(json=...)`; one object shared by both clients."""

QueryValue = Union[str, int, float, bool, Sequence[str], None]
Query = Mapping[str, QueryValue]
RawBody = Union[bytes, Iterable[bytes], AsyncIterable[bytes]]


class RetryOptions(TypedDict, total=False):
    max_rate_limit_retries: int
    """GET retries on 429/503 (honouring ``Retry-After``). Default 3."""
    max_retries: int
    """GET retries on 5xx / network error, and keyed-POST retries on 5xx / network error / timeout. Default 2."""
    max_retry_after: float
    """Longest ``Retry-After`` (seconds) the client sleeps through before raising instead. Default 60."""


@dataclass(frozen=True)
class RetryPolicy:
    max_rate_limit_retries: int = 3
    max_retries: int = 2
    max_retry_after: float = 60.0

    @classmethod
    def from_option(cls, retry: bool | RetryOptions | None) -> RetryPolicy | None:
        if retry is False:
            return None
        return cls(**retry) if isinstance(retry, dict) else cls()


@dataclass
class APIResponse(Generic[T]):
    data: T
    status: int
    headers: httpx.Headers
    request_id: str | None
    replayed: bool
    """``True`` when a 202 replays an earlier submit with the same ``Idempotency-Key``."""


def backoff(attempt: int) -> float:
    return float(min(8.0, 0.5 * 2**attempt) * (0.75 + random.random() * 0.5))


def auth_headers(api_key: str | None, jwt: str | None) -> dict[str, str]:
    if api_key and jwt:
        raise TypeError("Relay: pass api_key or jwt, not both")
    if api_key:
        return {"X-API-Key": api_key}
    if jwt:
        return {"Authorization": f"Bearer {jwt}"}
    return {}


def build_headers(
    auth: Mapping[str, str],
    default_headers: Mapping[str, str],
    extra: Mapping[str, str] | None,
    *,
    no_auth: bool,
    idempotency_key: str | None,
) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json", "User-Agent": USER_AGENT}
    headers.update(default_headers)
    if not no_auth:
        headers.update(auth)
    headers.update(extra or {})
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def query_params(query: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """``None`` drops the key, a list repeats it, a bool is ``true``/``false`` (as JS ``String()`` writes it). A key
    spelled as a Python keyword plus one ``_`` (``from_``, the keyword-argument spelling) is sent as the keyword."""
    out: list[tuple[str, str]] = []
    for key, v in (query or {}).items():
        k = key[:-1] if key.endswith("_") and keyword.iskeyword(key[:-1]) else key
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out.extend((k, str(x)) for x in v)
        elif isinstance(v, bool):
            out.append((k, "true" if v else "false"))
        else:
            out.append((k, str(v)))
    return out


def encode_json(body: Any) -> bytes:
    """Compact JSON, as ``JSON.stringify`` writes it (an idempotent retry re-sends these exact bytes)."""
    return _json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ReplayableBody:
    """Marks an iterable body that re-opens its source on each iteration (a ``Path`` upload), like a TS Blob: the
    transport may resend it."""


def is_replayable(content: RawBody | None) -> bool:
    """An iterator body can be sent once; it is never retried. Bytes and a ``ReplayableBody`` can be resent."""
    return content is None or isinstance(content, (bytes, bytearray, memoryview, ReplayableBody))


@contextmanager
def transport_errors(timed_out: str, failed: str) -> Iterator[None]:
    """Maps httpx's transport failures to the SDK's: a timeout → ``APITimeoutError(timed_out)``, any other transport
    error → ``APIConnectionError("{failed}: {type}: {error}")``. ``from None``: the httpx exception holds the request,
    and the request may hold the credential."""
    try:
        yield
    except httpx.TimeoutException:
        raise APITimeoutError(timed_out) from None
    except httpx.TransportError as e:
        raise APIConnectionError(f"{failed}: {type(e).__name__}: {e}") from None


def read_error_body(res: httpx.Response) -> Any:
    text = res.text if res.content else ""
    if not text:
        return None
    try:
        return _json.loads(text)
    except ValueError:
        return text


def parse_ok(res: httpx.Response) -> APIResponse[Any]:
    ct = res.headers.get("content-type", "")
    data: Any
    if res.status_code == 204:
        data = None
    elif "json" in ct:
        data = res.json() if res.content else None
    elif ct.startswith("text/"):
        data = res.text
    else:
        data = res.content
    return APIResponse(
        data=data,
        status=res.status_code,
        headers=res.headers,
        request_id=res.headers.get("x-request-id"),
        replayed=res.headers.get("idempotency-replayed") == "true",
    )


@dataclass
class RetryState:
    """One request's retry budget. Each method answers the seconds to sleep before the next attempt, or ``None`` to raise."""

    policy: RetryPolicy | None
    is_get: bool
    keyed: bool
    replayable: bool
    rate_limit_retries: int = 0
    retries: int = 0
    in_progress_retried: bool = False

    def after_transport_error(self) -> float | None:
        p = self.policy
        if p and (self.is_get or self.keyed) and self.replayable and self.retries < p.max_retries:
            self.retries += 1
            return backoff(self.retries - 1)
        return None

    def after_error(self, status: int, code: str | None, retry_after: float | None) -> float | None:
        p = self.policy
        if not p or not self.replayable:
            return None
        if retry_after is not None and retry_after > p.max_retry_after:
            return None
        if self.is_get and status in (429, 503) and self.rate_limit_retries < p.max_rate_limit_retries:
            wait = retry_after if retry_after is not None else backoff(self.rate_limit_retries)
            self.rate_limit_retries += 1
            return wait
        if self.keyed and status == 409 and code == "IDEMPOTENCY_IN_PROGRESS" and not self.in_progress_retried:
            self.in_progress_retried = True
            return retry_after if retry_after is not None else 1.0
        if (status != 503 if self.is_get else self.keyed) and status >= 500 and self.retries < p.max_retries:
            self.retries += 1
            return retry_after if retry_after is not None else backoff(self.retries - 1)
        return None
