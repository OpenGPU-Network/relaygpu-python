"""The ``workflows`` namespace (port of src/workflows.ts)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from .. import _clock
from .._exceptions import APITimeoutError, TaskFailedError
from .._shapes import DEFAULT_RUN_TIMEOUT, RUN_TERMINAL, WorkflowList, WorkflowRunSubmitted, WorkflowTemplate
from .._util import path_id, random_uuid
from ..types import StoreOutput, WorkflowRunState

if TYPE_CHECKING:
    from .client import AsyncRelay

OnRunProgress = Callable[[WorkflowRunState], None]


class AsyncWorkflows:
    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def list(self) -> WorkflowList:
        """``GET /v2/workflows`` (public). Each entry carries ``input_schema``, ``steps`` and ``version``."""
        res = await self._relay._http.request("GET", "/v2/workflows")
        return cast(WorkflowList, res.data)

    async def get(self, workflow_id: str) -> WorkflowTemplate:
        """``GET /v2/workflows/{id}`` (public)."""
        res = await self._relay._http.request("GET", f"/v2/workflows/{path_id(workflow_id)}")
        return cast(WorkflowTemplate, res.data)

    @overload
    async def run(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        wait: Literal[False] = False,
        on_progress: OnRunProgress | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        store_output: StoreOutput | None = None,
    ) -> WorkflowRunSubmitted: ...

    @overload
    async def run(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        wait: Literal[True],
        on_progress: OnRunProgress | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        store_output: StoreOutput | None = None,
    ) -> WorkflowRunState: ...

    @overload
    async def run(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        wait: bool,
        on_progress: OnRunProgress | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        store_output: StoreOutput | None = None,
    ) -> WorkflowRunState | WorkflowRunSubmitted: ...

    async def run(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        wait: bool = False,
        on_progress: OnRunProgress | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        store_output: StoreOutput | None = None,
    ) -> WorkflowRunState | WorkflowRunSubmitted:
        """``POST /v2/workflows/{id}/run``. Always carries an ``Idempotency-Key`` (yours, or a generated UUID), so a network
        error / timeout / 5xx is retried with the same key and can never start a second run; a replayed 202 comes back with
        ``replayed: True``. Every step is billed as an ordinary request. ``429 WORKFLOW_RUN_LIMIT_REACHED`` (runs in
        flight) surfaces as ``WorkflowRunLimitReachedError``.

        ``webhook_url``: HTTPS URL for ONE signed ``workflow.completed`` / ``workflow.failed`` delivery when the run ends.
        ``store_output``: run-level storage for every media step (``relay1d|7d|30d`` are billed per file).
        With ``wait=True`` it returns the terminal run (see :meth:`wait_run`; ``timeout`` and ``on_progress`` apply there).
        """
        body: dict[str, Any] = {"inputs": dict(inputs) if inputs is not None else {}}
        if store_output is not None:
            body["store_output"] = store_output
        if webhook_url is not None:
            body["webhook_url"] = webhook_url
        key = idempotency_key if idempotency_key is not None else random_uuid()
        res = await self._relay._http.request("POST", f"/v2/workflows/{path_id(workflow_id)}/run", json=body, idempotency_key=key)
        accepted = cast(WorkflowRunSubmitted, {**res.data, "replayed": res.replayed})
        if not wait:
            return accepted
        return await self.wait_run(accepted["run_id"], timeout=timeout, on_progress=on_progress)

    async def get_run(self, run_id: str) -> WorkflowRunState:
        """``GET /v2/workflows/runs/{run_id}``. Keyless: the run id is the capability token."""
        res = await self._relay._http.request("GET", f"/v2/workflows/runs/{path_id(run_id)}", no_auth=True)
        return cast(WorkflowRunState, res.data)

    async def wait_run(
        self, run_id: str, *, timeout: float = DEFAULT_RUN_TIMEOUT, on_progress: OnRunProgress | None = None
    ) -> WorkflowRunState:
        """Polls the run (1 s, backing off to 5 s; runs have no long-poll) until it ends.

        ``completed`` returns the run; ``failed`` / ``cancelled`` raise ``TaskFailedError`` (``task_id`` = the run id,
        ``task`` = the run, message = ``run["error"]``). The ``timeout`` budget (seconds, default 30 min) elapsing raises
        ``APITimeoutError`` (the run keeps going). ``on_progress`` is called with the run whenever its ``status`` changes
        (including the first poll).
        """
        deadline = _clock.monotonic() + timeout
        delay = 1.0
        last: str | None = None
        while True:
            run = await self.get_run(run_id)
            status = run["status"]
            if status != last:
                last = status
                if on_progress is not None:
                    on_progress(run)
            if status in RUN_TERMINAL:
                if status == "completed":
                    return run
                rid = run.get("run_id")
                raise TaskFailedError(run.get("error") or f"Workflow run {status}", task_id=rid if rid is not None else run_id, task=run)
            remaining = deadline - _clock.monotonic()
            if remaining <= 0:
                raise APITimeoutError(f"Workflow run {run_id} still {status} after {timeout:g} s")
            await _clock.async_sleep(min(delay, remaining))
            delay = min(5.0, delay * 2)

    async def cancel_run(self, run_id: str) -> WorkflowRunState:
        """``POST /v2/workflows/runs/{run_id}/cancel`` (key required). Cancels between steps only: a step already running
        completes and bills. A run already ended answers 409 ``WORKFLOW_RUN_NOT_CANCELLABLE``."""
        res = await self._relay._http.request("POST", f"/v2/workflows/runs/{path_id(run_id)}/cancel")
        return cast(WorkflowRunState, res.data)
