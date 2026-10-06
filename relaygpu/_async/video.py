"""Video generation (port of src/video.ts). Always submitted async, with an ``Idempotency-Key`` (retried safely)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, overload

from .._exceptions import RelayError
from ..types import AsyncAccepted, KnownVideoModel, Mode, TaskProgress, TaskStatus, UploadOptions
from .run import submit

if TYPE_CHECKING:
    from .client import AsyncRelay


class AsyncVideos:
    """Video generation. Always submitted async (with an ``Idempotency-Key``, retried safely)."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    @overload
    async def generate(
        self,
        model: KnownVideoModel | str,
        input: Mapping[str, Any],
        *,
        wait: Literal[False] = False,
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> AsyncAccepted: ...

    @overload
    async def generate(
        self,
        model: KnownVideoModel | str,
        input: Mapping[str, Any],
        *,
        wait: Literal[True],
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> TaskStatus: ...

    @overload
    async def generate(
        self,
        model: KnownVideoModel | str,
        input: Mapping[str, Any],
        *,
        wait: bool,
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> AsyncAccepted | TaskStatus: ...

    async def generate(
        self,
        model: KnownVideoModel | str,
        input: Mapping[str, Any],
        *,
        wait: bool = False,
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> AsyncAccepted | TaskStatus:
        """Submits a video task. Returns the ``202`` envelope (``task_id``, ``poll_url``, ``replayed``) by default; with
        ``wait=True`` waits (``timeout`` = the budget, default 20 min) and returns the completed ``TaskStatus``
        (``result["urls"]``), or raises ``TaskFailedError``. Any ``*_url`` field may hold bytes / a path / a file-like
        object: it is uploaded first (``upload={"retention": ...}``, default ``relay1h``)."""
        res = await submit(
            self._relay,
            model,
            input,
            mode=mode,
            store_output=store_output,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            async_=True,
            upload=upload,
            inline_images=inline_images,
        )
        if res.accepted is None:
            # Every video route honours `async: true`; an inline answer means the contract moved.
            raise RelayError(
                f"Model '{model}' answered inline to an async submit; use relay.run() for this model", request_id=res.request_id
            )
        if not wait:
            return res.accepted
        task_id = res.accepted["task_id"]
        if timeout is None:  # tasks.wait owns the default budget
            return await self._relay.tasks.wait(task_id, on_progress=on_progress)
        return await self._relay.tasks.wait(task_id, timeout=timeout, on_progress=on_progress)
