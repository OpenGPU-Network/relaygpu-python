"""Async task polling (port of src/tasks.ts). Keyless: the task id is the capability."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from .. import _clock
from .. import _run_common as common
from ..types import TaskProgress, TaskStatus

if TYPE_CHECKING:
    from .client import AsyncRelay


class AsyncTasks:
    """``GET /v2/tasks/{id}``, plain and long-polled."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def get(self, id: str) -> TaskStatus:
        """``GET /v2/tasks/{id}``: the current status. An unknown or expired id raises ``TaskNotFoundError``."""
        res = await self._relay._http.request("GET", common.task_path(id), no_auth=True)
        return cast(TaskStatus, res.data)

    async def wait(
        self,
        id: str,
        *,
        timeout: float = common.DEFAULT_WAIT_TIMEOUT,
        on_progress: Callable[[TaskProgress], None] | None = None,
    ) -> TaskStatus:
        """Long-polls ``GET /v2/tasks/{id}?wait=W`` until the task is terminal and returns the completed task.

        Raises ``TaskFailedError`` (``code`` = the task's ``error_code``) on ``failed`` and ``APITimeoutError`` when
        ``timeout`` (seconds, default 20 min) runs out; the task keeps running server-side. ``on_progress`` fires on
        status transitions only (``queued`` → ``running`` → …); there is no finer-grained progress.
        """
        deadline = _clock.monotonic() + timeout
        path = common.task_path(id)
        last: str | None = None
        last_call: float | None = None
        task: TaskStatus | None = None
        while True:
            if last_call is not None:
                gap = common.POLL_FLOOR - (_clock.monotonic() - last_call)
                if gap > 0:
                    await _clock.async_sleep(gap)
            remaining = deadline - _clock.monotonic()
            if remaining <= 0:
                raise common.wait_timeout_error(id, timeout, task)
            w = common.poll_wait(remaining)
            last_call = _clock.monotonic()
            res = await self._relay._http.request("GET", path, query={"wait": w}, no_auth=True, timeout=w + common.POLL_SLACK)
            current = cast(TaskStatus, res.data)
            task = current
            status = current["status"]
            if status != last:
                last = status
                if on_progress is not None:
                    on_progress({"status": status, "elapsed_seconds": current.get("elapsed_seconds", 0)})
            if status == "completed":
                return current
            if status == "failed":
                raise common.task_failed_error(id, current, res.request_id)
