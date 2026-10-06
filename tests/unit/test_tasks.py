"""Port of test/unit/tasks.test.ts: tasks.get (keyless, colon literal), tasks.wait (F3: long-poll arithmetic, 0.5 s
floor, transitions-only progress, TaskFailedError, APITimeoutError). TS's AbortSignal test becomes the asyncio
cancellation test. The clock is the virtual one of tests/conftest.py, advanced by the recorded sleeps and by held polls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from itertools import pairwise
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from relaygpu import APITimeoutError, TaskFailedError, TaskNotFoundError, _clock
from tests.helpers import VIDEO_KLING, Mock, Recorded, VirtualClock, async_relay, json_reply, maybe, relay_error, task

ID: str = VIDEO_KLING["accepted"]["body"]["task_id"]


def wait_param(url: str) -> int:
    return int(parse_qs(urlsplit(url).query)["wait"][0])


def held(clock: VirtualClock, reply: httpx.Response, *, seconds: float | None = None) -> Callable[[Recorded], httpx.Response]:
    """A reply the server held for ``seconds`` (default: the full ``?wait=``)."""

    def answer(rec: Recorded) -> httpx.Response:
        clock.now += wait_param(rec.url) if seconds is None else seconds
        return reply

    return answer


class TestTasksGet:
    async def test_is_keyless_with_the_colon_literal(self, make: Any) -> None:
        m = Mock(json_reply(200, VIDEO_KLING["completed"]["body"]))
        t = await maybe(make(m).tasks.get(ID))
        assert t["status"] == "completed"
        assert m.calls[0].url == f"http://relay.test/v2/tasks/{ID}"
        assert "x-api-key" not in m.calls[0].headers

    async def test_an_unknown_id_is_the_typed_task_not_found_error(self, make: Any) -> None:
        m = Mock(relay_error(404, "TASK_NOT_FOUND"))
        with pytest.raises(TaskNotFoundError):
            await maybe(make(m).tasks.get("direct:gone"))


class TestTasksWait:
    async def test_long_polls_wait_30_keylessly_and_resolves_the_completed_task_the_captured_kling_run_5_polls(
        self, make: Any, clock: VirtualClock
    ) -> None:
        polls = VIDEO_KLING["polls"]
        replies = [
            held(clock, task(p["task_status"], task_id=ID, elapsed_seconds=p["elapsed_seconds"]), seconds=p["ms"] / 1000)
            for p in polls[:-1]
        ]
        m = Mock(*replies, json_reply(200, VIDEO_KLING["completed"]["body"]))
        seen: list[Any] = []
        t = await maybe(make(m).tasks.wait(ID, on_progress=seen.append))
        assert t == VIDEO_KLING["completed"]["body"]
        assert len(m.calls) == len(polls)
        assert all(c.url == f"http://relay.test/v2/tasks/{ID}?wait=30" and "x-api-key" not in c.headers for c in m.calls)
        # on_progress only on transitions: running (once), then completed.
        assert seen == [{"status": "running", "elapsed_seconds": 30}, {"status": "completed", "elapsed_seconds": 121}]

    async def test_a_15_s_task_costs_at_most_2_requests_when_the_server_holds_the_poll(self, make: Any, clock: VirtualClock) -> None:
        m = Mock(held(clock, task("completed", elapsed_seconds=15, result={"urls": ["u"]}), seconds=15))
        await maybe(make(m).tasks.wait("direct:t1"))
        assert len(m.calls) <= 2

    async def test_wait_never_exceeds_the_remaining_budget_ceil_ge_1_and_a_shed_early_answer_just_loops_ge_0_5_s_apart(
        self, make: Any, clock: VirtualClock, sleeps: list[float]
    ) -> None:
        stamps: list[float] = []

        def running(_: Recorded) -> httpx.Response:
            stamps.append(clock.now)
            return task("running")

        m = Mock(running, running, running, task("completed", result={}))
        await maybe(make(m).tasks.wait("direct:t1", timeout=10.5))
        w = [wait_param(c.url) for c in m.calls]
        assert w[0] == 11
        assert all(1 <= x <= 11 for x in w)
        assert w[1] <= 10
        assert all(b - a >= 0.49 for a, b in pairwise(stamps))
        # Python addition: the floor is exactly the recorded sleep, one per shed answer.
        assert sleeps == [0.5, 0.5, 0.5]
        assert w == [11, 10, 10, 9]

    async def test_w_is_the_ceil_of_the_remaining_budget_after_held_polls(
        self, make: Any, clock: VirtualClock, sleeps: list[float]
    ) -> None:
        """Python addition: held polls need no floor sleep, and W shrinks with the budget (never above it, never 0)."""
        m = Mock(
            held(clock, task("running")),
            held(clock, task("running")),
            held(clock, task("running"), seconds=4.2),
            task("completed", result={}),
        )
        await maybe(make(m).tasks.wait("direct:t1", timeout=65.0))
        assert [wait_param(c.url) for c in m.calls] == [30, 30, 5, 1]
        assert sleeps == []  # every poll was held ≥ 0.5 s

    async def test_each_poll_gets_an_http_timeout_of_w_plus_15_s(self, make: Any, clock: VirtualClock) -> None:
        """Python addition: the per-poll HTTP timeout is ``W + 15`` seconds (TS ``w * 1000 + POLL_SLACK_MS``)."""
        m = Mock(task("running"), task("completed", result={}))
        relay = make(m)
        seen: list[Any] = []
        orig = relay._http.request

        def spy(method: str, path: str, **kw: Any) -> Any:
            seen.append((kw["query"]["wait"], kw["timeout"], kw["no_auth"]))
            return orig(method, path, **kw)

        relay._http.request = spy
        await maybe(relay.tasks.wait("direct:t1", timeout=20.0))
        assert seen == [(20, 35.0, True), (20, 35.0, True)]

    async def test_failed_raises_task_failed_error_carrying_error_code_error_error_detail_task_id(
        self, make: Any, clock: VirtualClock
    ) -> None:
        failed = {
            "task_id": ID,
            "status": "failed",
            "elapsed_seconds": 40,
            "error": "The provider declined the prompt",
            "error_code": "CONTENT_POLICY_DECLINED",
            "error_detail": {"upstream_code": "x", "filtered": "input"},
        }
        m = Mock(task("queued", task_id=ID), json_reply(200, failed, {"x-request-id": "rid-f"}))
        with pytest.raises(TaskFailedError) as ei:
            await maybe(make(m).tasks.wait(ID))
        e = ei.value
        assert (e.code, e.message, e.detail, e.task_id, e.task, e.request_id) == (
            "CONTENT_POLICY_DECLINED",
            failed["error"],
            failed["error_detail"],
            ID,
            failed,
            "rid-f",
        )

    async def test_a_failed_task_without_error_text_says_task_id_failed(self, make: Any, clock: VirtualClock) -> None:
        m = Mock(task("failed", task_id=ID, error=None, error_code=None))
        with pytest.raises(TaskFailedError) as ei:
            await maybe(make(m).tasks.wait(ID))
        assert (str(ei.value), ei.value.code, ei.value.detail) == (f"Task {ID} failed", None, None)

    async def test_budget_exhausted_raises_api_timeout_error(self, make: Any, clock: VirtualClock) -> None:
        m = Mock(*(task("running") for _ in range(10)))
        with pytest.raises(APITimeoutError) as ei:
            await maybe(make(m).tasks.wait("direct:t1", timeout=1.2))
        assert "still running" in str(ei.value)
        assert str(ei.value) == "Task direct:t1 is still running after 1.2 s (it keeps running; poll it again or wait longer)"
        assert ei.value.detail["task_id"] == "direct:t1"
        assert ei.value.detail["task"]["status"] == "running"
        assert 2 <= len(m.calls) <= 4

    async def test_a_zero_budget_raises_before_any_poll_still_pending(self, make: Any, clock: VirtualClock) -> None:
        m = Mock()
        with pytest.raises(APITimeoutError, match="is still pending after 0 s"):
            await maybe(make(m).tasks.wait("direct:t1", timeout=0))
        assert m.calls == []


class TestCancellation:
    async def test_cancelling_the_wait_propagates_cancelled_error_between_polls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replaces TS 'abort rejects with the signal's reason': cancellation is never swallowed."""
        parked = asyncio.Event()

        async def park(seconds: float) -> None:
            parked.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(_clock, "async_sleep", park)
        m = Mock(task("running"), task("running"))
        relay = async_relay(m)
        t = asyncio.ensure_future(relay.tasks.wait("direct:t1"))
        await asyncio.wait_for(parked.wait(), 5)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert len(m.calls) == 1

    async def test_cancelling_the_wait_in_flight_propagates_cancelled_error(self) -> None:
        started = asyncio.Event()

        class Hanging(httpx.AsyncBaseTransport):
            calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                Hanging.calls += 1
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        from relaygpu import AsyncRelay

        relay = AsyncRelay(base_url="http://relay.test", http_client=httpx.AsyncClient(transport=Hanging()))
        t = asyncio.ensure_future(relay.tasks.wait("direct:t1"))
        await asyncio.wait_for(started.wait(), 5)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert Hanging.calls == 1
