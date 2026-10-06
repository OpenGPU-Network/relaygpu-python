"""Port of test/unit/http.test.ts: headers and auth, the retry policy (F7), transport errors."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from relaygpu import (
    APIConnectionError,
    APITimeoutError,
    AsyncRelay,
    CapacityError,
    ProviderError,
    RateLimitError,
    Relay,
)
from tests.helpers import KEY, Mock, json_reply, maybe, relay_error


async def req(r: Any, method: str, path: str, **kw: Any) -> Any:
    return await maybe(r._http.request(method, path, **kw))


class TestHeadersAndAuth:
    async def test_key_jwt_never_both(self, make: Any) -> None:
        m = Mock(json_reply(200, {}), json_reply(200, {}))
        await req(make(m), "GET", "/v2/x")
        assert m.calls[0].headers["x-api-key"] == KEY
        assert "authorization" not in m.calls[0].headers
        await req(make(m, api_key=None, jwt="a.b.c"), "GET", "/v2/x")
        assert m.calls[1].headers["authorization"] == "Bearer a.b.c"
        assert "x-api-key" not in m.calls[1].headers
        with pytest.raises(TypeError):
            Relay(api_key=KEY, jwt="a.b.c")
        with pytest.raises(TypeError):
            AsyncRelay(api_key=KEY, jwt="a.b.c")

    async def test_no_auth_and_default_base_url_is_prod(self, make: Any) -> None:
        m = Mock(json_reply(200, {}))
        await req(make(m, base_url=None), "GET", "/v2/tasks/direct:1", no_auth=True)
        assert m.calls[0].url == "https://relaygpu.com/v2/tasks/direct:1"
        assert "x-api-key" not in m.calls[0].headers

    async def test_user_agent_and_accept(self, make: Any) -> None:
        m = Mock(json_reply(200, {}))
        await req(make(m), "GET", "/v2/x")
        assert m.calls[0].headers["user-agent"] == "relaygpu-python/0.1.0"
        assert m.calls[0].headers["accept"] == "application/json"

    async def test_query_encoding_matches_js(self, make: Any) -> None:
        m = Mock(json_reply(200, {}))
        await req(make(m), "GET", "/v2/x", query={"a": True, "b": False, "c": None, "d": ["x", "y"], "e": 30})
        assert m.calls[0].url == "http://relay.test/v2/x?a=true&b=false&d=x&d=y&e=30"

    async def test_json_body_is_compact(self, make: Any) -> None:
        m = Mock(json_reply(200, {}))
        await req(make(m), "POST", "/v2/x", json={"prompt": "ü", "n": 1})
        assert m.calls[0].raw == '{"prompt":"ü","n":1}'.encode()
        assert m.calls[0].headers["content-type"] == "application/json"

    async def test_key_never_in_repr_or_error(self, make: Any) -> None:
        m = Mock(relay_error(401, "INVALID_API_KEY"))
        c = make(m)
        assert repr(c) in ("Relay(base_url='http://relay.test')", "AsyncRelay(base_url='http://relay.test')")
        assert KEY not in repr(c) and KEY not in str(c) and KEY not in repr(c._http)
        with pytest.raises(Exception) as ei:
            await req(c, "GET", "/v2/x")
        e = ei.value
        blob = repr(e) + str(e) + repr(e.args) + json.dumps({k: str(v) for k, v in vars(e).items()})
        assert KEY not in blob
        assert "x-api-key" not in {k.lower() for k in (e.headers or {})}

    async def test_request_id_and_replayed(self, make: Any) -> None:
        m = Mock(json_reply(202, {"task_id": "direct:1"}, {"x-request-id": "rid", "idempotency-replayed": "true"}))
        r = await req(make(m), "POST", "/v2/x", json={}, idempotency_key="k")
        assert (r.status, r.request_id, r.replayed) == (202, "rid", True)
        assert m.calls[0].headers["idempotency-key"] == "k"

    async def test_response_shapes(self, make: Any) -> None:
        m = Mock(
            httpx.Response(204),
            httpx.Response(200, text="hi", headers={"content-type": "text/plain"}),
            httpx.Response(200, content=b"\x89PNG"),
        )
        c = make(m)
        assert (await req(c, "DELETE", "/v2/x")).data is None
        assert (await req(c, "GET", "/v2/x")).data == "hi"
        assert (await req(c, "GET", "/v2/x")).data == b"\x89PNG"


def err429(after: str = "0") -> httpx.Response:
    return relay_error(429, "RATE_LIMITED", headers={"retry-after": after})


class TestRetryPolicy:
    async def test_get_retries_429_honouring_retry_after_max_3(self, make: Any, sleeps: list[float]) -> None:
        m = Mock(err429(), err429("2"), json_reply(200, {"ok": 1}))
        r = await req(make(m), "GET", "/v2/x")
        assert r.data == {"ok": 1}
        assert len(m.calls) == 3
        assert sleeps == [0.0, 2.0]
        m2 = Mock(*[relay_error(503, "CAPACITY_EXHAUSTED", headers={"retry-after": "0"}) for _ in range(4)])
        with pytest.raises(CapacityError):
            await req(make(m2), "GET", "/v2/x")
        assert len(m2.calls) == 4

    async def test_retry_after_over_the_cap_is_raised_not_slept(self, make: Any, sleeps: list[float]) -> None:
        m = Mock(err429("3600"))
        with pytest.raises(RateLimitError) as ei:
            await req(make(m), "GET", "/v2/x")
        assert ei.value.retry_after == 3600
        assert len(m.calls) == 1 and sleeps == []

    async def test_get_retries_5xx_max_2(self, make: Any, sleeps: list[float]) -> None:
        m = Mock(*[relay_error(502, "UPSTREAM_ERROR") for _ in range(3)])
        with pytest.raises(ProviderError):
            await req(make(m), "GET", "/v2/x")
        assert len(m.calls) == 3
        assert len(sleeps) == 2 and 0.375 <= sleeps[0] <= 0.625 and 0.75 <= sleeps[1] <= 1.25  # backoff with jitter

    async def test_unkeyed_post_never_retried(self, make: Any) -> None:
        for reply in (relay_error(502, "UPSTREAM_ERROR"), err429(), httpx.ConnectError("refused")):
            m = Mock(reply, json_reply(200, {}))
            with pytest.raises(Exception):  # noqa: B017
                await req(make(m), "POST", "/v2/image/qwen/generate", json={"prompt": "x"})
            assert len(m.calls) == 1
            assert "idempotency-key" not in m.calls[0].headers

    async def test_keyed_post_retries_5xx_with_the_same_key(self, make: Any) -> None:
        m = Mock(relay_error(500, "INTERNAL_ERROR"), json_reply(202, {"task_id": "direct:1"}))
        r = await req(make(m), "POST", "/v2/video/x", json={"async": True}, idempotency_key="same-key")
        assert r.status == 202
        assert [c.headers["idempotency-key"] for c in m.calls] == ["same-key", "same-key"]
        assert m.calls[0].raw == m.calls[1].raw

    async def test_keyed_post_retries_network_error_and_one_409_in_progress(self, make: Any) -> None:
        in_progress = relay_error(409, "IDEMPOTENCY_IN_PROGRESS", headers={"retry-after": "0"})
        m = Mock(httpx.ConnectError("reset"), in_progress, json_reply(202, {"task_id": "direct:1"}))
        r = await req(make(m), "POST", "/v2/video/x", json={}, idempotency_key="k")
        assert r.status == 202 and len(m.calls) == 3
        m2 = Mock(in_progress, relay_error(409, "IDEMPOTENCY_IN_PROGRESS", headers={"retry-after": "0"}))
        with pytest.raises(Exception) as ei:
            await req(make(m2), "POST", "/v2/video/x", json={}, idempotency_key="k")
        assert getattr(ei.value, "code", None) == "IDEMPOTENCY_IN_PROGRESS"
        assert len(m2.calls) == 2

    async def test_keyed_post_never_retries_a_4xx(self, make: Any) -> None:
        m = Mock(relay_error(429, "WORKFLOW_RUN_LIMIT_REACHED"), json_reply(202, {}))
        with pytest.raises(RateLimitError) as ei:
            await req(make(m), "POST", "/v2/workflows/w/run", json={}, idempotency_key="k")
        assert ei.value.code == "WORKFLOW_RUN_LIMIT_REACHED" and ei.value.retry_after is None
        assert len(m.calls) == 1

    async def test_5xx_with_retry_after_is_still_bounded(self, make: Any) -> None:
        """Deliberate difference from TS 0.1.0: there `waitMs ?? backoff(retries++)` skips the count when Retry-After
        is present, so a keyed POST answered 500 + Retry-After forever retries forever. Here max_retries holds."""
        m = Mock(*[relay_error(500, "INTERNAL_ERROR", headers={"retry-after": "0"}) for _ in range(3)])
        with pytest.raises(Exception):  # noqa: B017
            await req(make(m), "POST", "/v2/video/x", json={}, idempotency_key="k")
        assert len(m.calls) == 3

    async def test_iterator_body_is_sent_once_never_retried(self, make: Any) -> None:
        m = Mock(relay_error(500, "INTERNAL_ERROR"), json_reply(201, {}))
        body: Any = iter([b"ab", b"cd"]) if make.kind == "sync" else _agen([b"ab", b"cd"])
        with pytest.raises(Exception):  # noqa: B017
            await req(make(m), "POST", "/v2/files", content=body, content_type="video/mp4", idempotency_key="k")
        assert len(m.calls) == 1 and m.calls[0].raw == b"abcd"

    async def test_retry_false_disables_everything(self, make: Any) -> None:
        m = Mock(relay_error(503, "CAPACITY_EXHAUSTED", headers={"retry-after": "0"}))
        with pytest.raises(CapacityError):
            await req(make(m, retry=False), "GET", "/v2/x")
        assert len(m.calls) == 1

    async def test_retry_options(self, make: Any) -> None:
        m = Mock(err429(), json_reply(200, {}))
        with pytest.raises(RateLimitError):
            await req(make(m, retry={"max_rate_limit_retries": 0}), "GET", "/v2/x")
        assert len(m.calls) == 1

    async def test_network_failure_and_timeout(self, make: Any) -> None:
        m = Mock(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"))
        c = make(m, retry=False)
        with pytest.raises(APIConnectionError) as e1:
            await req(c, "GET", "/v2/x")
        assert e1.value.__cause__ is None and e1.value.__suppress_context__  # never chains the request (credential)
        with pytest.raises(APITimeoutError):
            await req(c, "GET", "/v2/x")

    async def test_per_request_timeout_reaches_httpx(self, make: Any) -> None:
        seen: list[Any] = []

        def reply(rec: Any) -> httpx.Response:
            return json_reply(200, {})

        m = Mock(reply)
        c = make(m, timeout=12.5)
        orig = c._http._client.request

        def spy(*a: Any, **kw: Any) -> Any:
            seen.append(kw["timeout"])
            return orig(*a, **kw)

        c._http._client.request = spy
        await req(c, "GET", "/v2/x")
        assert seen == [12.5]


async def _agen(chunks: list[bytes]) -> Any:
    for ch in chunks:
        yield ch


async def test_async_cancellation_propagates_and_is_never_retried() -> None:
    started = asyncio.Event()
    calls = 0

    async def hang(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    c = AsyncRelay(base_url="http://relay.test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(hang)))
    t = asyncio.create_task(c._http.request("GET", "/v2/x"))
    await started.wait()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert calls == 1


def test_context_managers_close_owned_clients_only() -> None:
    with Relay() as r:
        inner = r._http._client
    assert inner.is_closed
    mine = httpx.Client()
    with Relay(http_client=mine):
        pass
    assert not mine.is_closed

    async def main() -> None:
        async with AsyncRelay() as ar:
            ainner = ar._http._client
        assert ainner.is_closed

    asyncio.run(main())
