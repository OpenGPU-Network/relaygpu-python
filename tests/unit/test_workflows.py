"""Port of test/unit/workflows.test.ts. Time is virtual: the clock of tests/conftest.py advances by each recorded SDK sleep."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from relaygpu import (
    APITimeoutError,
    TaskFailedError,
    WorkflowRunLimitReachedError,
    WorkflowRunNotCancellableError,
    _clock,
)
from tests.helpers import KEY, Mock, async_relay, json_reply, maybe, relay_error

RUN = "wf:7c9e6679-7425-40de-944b-e07fc1f90ae7"
ACCEPTED = {"run_id": RUN, "status": "queued", "poll_url": f"/v2/workflows/runs/{RUN}"}
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def state(status: str, **extra: Any) -> dict[str, Any]:
    return {"run_id": RUN, "workflow_id": "script-voiceover", "version": 2, "status": status, "inputs": {}, "steps": [], **extra}


class TestListGet:
    async def test_hits_the_public_routes(self, make: Any) -> None:
        m = Mock(
            json_reply(200, {"total": 0, "workflows": []}),
            json_reply(200, {"workflow_id": "gen-edit", "name": "x", "steps": [], "version": 1}),
        )
        wf = make(m).workflows
        await maybe(wf.list())
        await maybe(wf.get("gen-edit"))
        assert [f"{c.method} {c.url}" for c in m.calls] == [
            "GET http://relay.test/v2/workflows",
            "GET http://relay.test/v2/workflows/gen-edit",
        ]


class TestRun:
    async def test_sends_inputs_webhook_url_and_store_output_with_a_generated_idempotency_key(self, make: Any) -> None:
        m = Mock(json_reply(202, ACCEPTED))
        r = await maybe(
            make(m).workflows.run("script-voiceover", {"voice": "Serena"}, webhook_url="https://example.com/hook", store_output="relay1d")
        )
        assert r == {**ACCEPTED, "replayed": False}
        c = m.calls[0]
        assert f"{c.method} {c.url}" == "POST http://relay.test/v2/workflows/script-voiceover/run"
        assert c.body == {"inputs": {"voice": "Serena"}, "webhook_url": "https://example.com/hook", "store_output": "relay1d"}
        assert UUID4.match(c.headers["idempotency-key"])
        assert c.headers["x-api-key"] == KEY

    async def test_omits_optional_fields_fresh_key_per_call_callers_key_verbatim(self, make: Any) -> None:
        m = Mock(json_reply(202, ACCEPTED), json_reply(202, ACCEPTED), json_reply(202, ACCEPTED, {"idempotency-replayed": "true"}))
        wf = make(m).workflows
        await maybe(wf.run("gen-edit", {"prompt": "a", "edit": "b"}))
        await maybe(wf.run("gen-edit", {"prompt": "a", "edit": "b"}))
        r = await maybe(wf.run("gen-edit", {"prompt": "a", "edit": "b"}, idempotency_key="my-key-1"))
        assert m.calls[0].body == {"inputs": {"prompt": "a", "edit": "b"}}
        assert m.calls[0].headers["idempotency-key"] != m.calls[1].headers["idempotency-key"]
        assert m.calls[2].headers["idempotency-key"] == "my-key-1"
        assert r["replayed"] is True

    async def test_inputs_default_to_an_empty_object(self, make: Any) -> None:
        m = Mock(json_reply(202, ACCEPTED))
        await maybe(make(m).workflows.run("gen-edit"))
        assert m.calls[0].body == {"inputs": {}}

    async def test_a_5xx_is_retried_with_the_same_key(self, make: Any) -> None:
        m = Mock(relay_error(502, "UPSTREAM_ERROR", {}, {"retry-after": "0"}), json_reply(202, ACCEPTED))
        await maybe(make(m).workflows.run("gen-edit", {}))
        assert len(m.calls) == 2
        assert m.calls[1].headers["idempotency-key"] == m.calls[0].headers["idempotency-key"]

    async def test_429_workflow_run_limit_reached_surfaces_never_retried(self, make: Any) -> None:
        m = Mock(relay_error(429, "WORKFLOW_RUN_LIMIT_REACHED", {}, {"retry-after": "0"}), json_reply(202, ACCEPTED))
        with pytest.raises(WorkflowRunLimitReachedError):
            await maybe(make(m).workflows.run("gen-edit", {}))
        assert len(m.calls) == 1

    async def test_wait_true_polls_the_run_keyless_to_its_terminal_state(self, make: Any, sleeps: list[float]) -> None:
        m = Mock(json_reply(202, ACCEPTED), json_reply(200, state("running")), json_reply(200, state("completed", output={"audio": "u"})))
        seen: list[str] = []
        run = await maybe(make(m).workflows.run("script-voiceover", {}, wait=True, on_progress=lambda r: seen.append(r["status"])))
        assert run["status"] == "completed"
        assert m.calls[1].url == f"http://relay.test/v2/workflows/runs/{RUN}"
        assert "x-api-key" not in m.calls[1].headers
        assert sleeps == [1.0]
        assert seen == ["running", "completed"]

    async def test_wait_true_honours_timeout(self, make: Any) -> None:
        m = Mock(json_reply(202, ACCEPTED), *[json_reply(200, state("running")) for _ in range(3)])
        with pytest.raises(APITimeoutError):
            await maybe(make(m).workflows.run("script-voiceover", {}, wait=True, timeout=1.5))
        assert len(m.calls) == 4  # submit + polls at t=0, 1, 1.5


class TestWaitRun:
    async def test_reports_status_transitions_once_each_and_backs_off_1s_to_5s(self, make: Any, sleeps: list[float]) -> None:
        seen: list[str] = []
        m = Mock(*[json_reply(200, state(s)) for s in ["queued", "running", "running", "running", "running", "completed"]])
        run = await maybe(make(m).workflows.wait_run(RUN, on_progress=lambda r: seen.append(r["status"])))
        assert run["status"] == "completed"
        assert seen == ["queued", "running", "completed"]
        assert sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]

    async def test_failed_and_cancelled_raise_task_failed_error_carrying_the_run(self, make: Any) -> None:
        failed = state("failed", error="Step 2 failed: boom", failed_step_index=1)
        wf = make(Mock(json_reply(200, failed), json_reply(200, state("cancelled")))).workflows
        with pytest.raises(TaskFailedError) as ei:
            await maybe(wf.wait_run(RUN))
        assert (ei.value.task_id, str(ei.value), ei.value.task) == (RUN, "Step 2 failed: boom", failed)
        with pytest.raises(TaskFailedError) as ec:
            await maybe(wf.wait_run(RUN))
        assert str(ec.value) == "Workflow run cancelled"

    async def test_the_budget_elapsing_raises_api_timeout_error(self, make: Any, sleeps: list[float]) -> None:
        m = Mock(*[json_reply(200, state("running")) for _ in range(10)])
        with pytest.raises(APITimeoutError) as ei:
            await maybe(make(m).workflows.wait_run(RUN, timeout=2.5))
        assert len(m.calls) == 3  # t=0, 1, 2.5 (the sleep clipped to the budget)
        assert sleeps == [1.0, 1.5]
        assert str(ei.value) == f"Workflow run {RUN} still running after 2.5 s"

    async def test_cancelling_the_awaiting_task_stops_the_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # TS: an AbortSignal stops the wait. Python: cancel the task; CancelledError propagates, never swallowed.
        started = asyncio.Event()

        async def blocking_sleep(seconds: float) -> None:
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(_clock, "async_sleep", blocking_sleep)
        m = Mock(json_reply(200, state("running")))
        t = asyncio.ensure_future(async_relay(m).workflows.wait_run(RUN))
        await started.wait()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert len(m.calls) == 1


class TestCancelRun:
    async def test_posts_with_the_key_409_surfaces_typed(self, make: Any) -> None:
        m = Mock(json_reply(200, state("cancelled")), relay_error(409, "WORKFLOW_RUN_NOT_CANCELLABLE"))
        wf = make(m).workflows
        assert (await maybe(wf.cancel_run(RUN)))["status"] == "cancelled"
        assert f"{m.calls[0].method} {m.calls[0].url}" == f"POST http://relay.test/v2/workflows/runs/{RUN}/cancel"
        assert m.calls[0].headers["x-api-key"] == KEY
        with pytest.raises(WorkflowRunNotCancellableError):
            await maybe(wf.cancel_run(RUN))
