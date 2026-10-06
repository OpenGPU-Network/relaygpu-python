"""Image generation and editing (port of src/image.ts). The parsing lives in ``relaygpu/_images.py``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from .. import _clock
from .._images import decode_b64, image_values, parse_image_value
from ..types import KnownImageModel, Mode, TaskProgress, UploadOptions
from ._http import AsyncHttpClient
from .run import run

if TYPE_CHECKING:
    from .client import AsyncRelay

__all__ = ["AsyncImageResult", "AsyncImages", "AsyncRelayImage", "normalise_images", "to_relay_image"]


@dataclass(frozen=True, repr=False)
class AsyncRelayImage:
    """One output image, URL- or base64-backed. URLs expire (1 h by default; ``store_output`` buys 1/7/30 d)."""

    url: str | None = None
    b64: str | None = None
    """Raw base64 (no ``data:`` prefix)."""
    mime_type: str | None = None

    _transport: ClassVar[AsyncHttpClient | None] = None
    """The client's transport (set on the images ``images.generate`` / ``edit`` return; never a dataclass field)."""

    def __getstate__(self) -> dict[str, Any]:
        """Pickle / copy without the transport (such a copy downloads over a fresh client)."""
        return {k: v for k, v in self.__dict__.items() if k != "_transport"}

    def __repr__(self) -> str:
        if self.url is not None:
            return f"{type(self).__name__}(url={self.url!r})"
        return f"{type(self).__name__}(b64=<{len(self.b64 or '')} chars>, mime_type={self.mime_type!r})"

    async def to_bytes(self) -> bytes:
        """Downloads (URL; no Relay credential is sent) or decodes (base64) the bytes. An image ``images.generate`` /
        ``edit`` returned downloads over that client's HTTP transport; one built by ``to_relay_image`` over a fresh one."""
        if self.url is None:
            return decode_b64(self.b64 or "")
        if self._transport is not None:
            return await self._transport.download(self.url)
        transport = AsyncHttpClient()
        try:
            return await transport.download(self.url)
        finally:
            await transport.aclose()

    async def save(self, path: str | Path) -> None:
        """Writes the bytes to a file."""
        await _clock.async_write_bytes(path, await self.to_bytes())


@dataclass(frozen=True)
class AsyncImageResult:
    """``images``: every output image, normalised. ``raw``: the response body (or the async task's ``result``) exactly
    as the API sent it."""

    images: list[AsyncRelayImage] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def to_relay_image(value: str, mime_hint: str | None = None) -> AsyncRelayImage:
    """Builds a ``RelayImage`` from a URL, a data URI or a bare base64 string."""
    url, b64, mime = parse_image_value(value, mime_hint)
    return AsyncRelayImage(url=url, b64=b64, mime_type=mime)


def normalise_images(body: Mapping[str, Any], input: Mapping[str, Any] | None = None) -> list[AsyncRelayImage]:
    """Normalises every image response shape the live routes use: ``urls[]``, ``images[]`` (base64 or links),
    OpenAI-style ``data[]``, single ``url`` / ``image``."""
    values, hint = image_values(body, input)
    return [to_relay_image(v, hint) for v in values]


class AsyncImages:
    """Image generation and editing. Both methods take any image model; the route comes from ``relay.models.get``."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def generate(
        self,
        model: KnownImageModel | str,
        input: Mapping[str, Any],
        *,
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        async_: bool | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> AsyncImageResult:
        """Generates images. Returns ``ImageResult(images, raw)`` with every image normalised to a ``RelayImage`` (URL or
        base64). If the route answers async (``async_=True``), waits for the task like ``run()`` does; ``timeout`` is
        that wait's budget (default 20 min) and the per-attempt HTTP timeout of a sync call."""
        raw = await run(
            self._relay,
            model,
            input,
            wait=True,
            on_progress=on_progress,
            timeout=timeout,
            mode=mode,
            store_output=store_output,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            async_=async_,
            upload=upload,
            inline_images=inline_images,
        )
        body = cast("dict[str, Any]", raw)  # wait=True: never the 202 envelope
        images = normalise_images(body, input)
        for img in images:
            object.__setattr__(img, "_transport", self._relay._http)  # frozen; a ClassVar, so no field changes
        return AsyncImageResult(images=images, raw=body)

    async def edit(
        self,
        model: KnownImageModel | str,
        input: Mapping[str, Any],
        *,
        on_progress: Callable[[TaskProgress], None] | None = None,
        timeout: float | None = None,
        mode: Mode | None = None,
        store_output: str | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
        async_: bool | None = None,
        upload: UploadOptions | None = None,
        inline_images: bool = False,
    ) -> AsyncImageResult:
        """Image-to-image editing (``image`` / ``images`` / ``*_url`` inputs; file values are uploaded). Same contract
        as ``generate``."""
        return await self.generate(
            model,
            input,
            on_progress=on_progress,
            timeout=timeout,
            mode=mode,
            store_output=store_output,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            async_=async_,
            upload=upload,
            inline_images=inline_images,
        )
