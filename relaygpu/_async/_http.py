"""The transport (port of src/http.ts `HttpClient`). Hand-written async; ``_sync/_http.py`` is generated from it by
scripts/unasync.py. Everything that is not I/O lives in ``relaygpu/_core.py``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .._core import (
    _UNSET,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    APIResponse,
    Query,
    RawBody,
    RetryOptions,
    RetryPolicy,
    RetryState,
    auth_headers,
    build_headers,
    encode_json,
    is_replayable,
    parse_ok,
    query_params,
    read_error_body,
)
from .._errors import error_from_response
from .._exceptions import APIConnectionError, APITimeoutError
from .._sleep import async_sleep


class AsyncHttpClient:
    """Headers, timeouts, the retry policy, ``X-Request-ID`` and ``Idempotency-Replayed``. Never exposes the credential."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        jwt: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        retry: bool | RetryOptions | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.__auth = auth_headers(api_key, jwt)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout) if timeout is not None else DEFAULT_TIMEOUT
        self.retry = RetryPolicy.from_option(retry)
        self._default_headers = dict(default_headers or {})
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.AsyncClient(follow_redirects=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def url(self, path: str) -> str:
        return self.base_url + path

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Query | None = None,
        json: Any = _UNSET,
        content: RawBody | None = None,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
        no_auth: bool = False,
    ) -> APIResponse[Any]:
        """One API call with the retry policy. ``json`` is serialised; ``content`` is sent raw (bytes, or an iterator
        of bytes, which is sent once and never retried). ``idempotency_key`` makes a POST retry-safe."""
        method = method.upper()
        is_get = method in ("GET", "HEAD")
        hdrs = build_headers(self.__auth, self._default_headers, headers, no_auth=no_auth, idempotency_key=idempotency_key)
        body: RawBody | None = None
        if content is not None:
            body = content
            if content_type:
                hdrs["Content-Type"] = content_type
        elif json is not _UNSET:
            body = encode_json(json)
            hdrs["Content-Type"] = "application/json"
        state = RetryState(self.retry, is_get, not is_get and idempotency_key is not None, is_replayable(body))
        url = self.url(path)
        params: list[tuple[str, str | int | float | bool | None]] = list(query_params(query))
        t = timeout if timeout is not None else self.timeout
        while True:
            try:
                res = await self._send(method, url, params, hdrs, body, t, path)
            except (APIConnectionError, APITimeoutError):
                wait = state.after_transport_error()
                if wait is None:
                    raise
                await async_sleep(wait)
                continue
            if res.is_success:
                return parse_ok(res)
            err = error_from_response(res.status_code, read_error_body(res), res.headers)
            wait = state.after_error(res.status_code, err.code, err.retry_after)
            if wait is None:
                raise err
            await async_sleep(wait)

    async def _send(
        self,
        method: str,
        url: str,
        params: list[tuple[str, str | int | float | bool | None]],
        headers: dict[str, str],
        body: RawBody | None,
        timeout: float,
        path: str,
    ) -> httpx.Response:
        try:
            res = await self._client.request(
                method,
                url,
                params=params or None,
                headers=headers,
                content=body,
                timeout=timeout,
            )
            await res.aread()
            return res
        except httpx.TimeoutException:
            # `from None`: the httpx exception holds the request, and the request holds the credential.
            raise APITimeoutError(f"Request timed out after {timeout:g} s: {method} {path}") from None
        except httpx.TransportError as e:
            raise APIConnectionError(f"Connection error: {method} {path}: {type(e).__name__}: {e}") from None
