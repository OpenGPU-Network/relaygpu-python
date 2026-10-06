"""Port of test/unit/image.test.ts: RelayImage normalisation and image.generate / edit. The TS type-level test
("known models get the generated body") has no runtime twin: v0.1 takes ``Mapping[str, Any]`` inputs (no per-model
overloads). Python additions: URL downloads never carry the credential, the download error message, bad base64."""

from __future__ import annotations

import base64
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

import relaygpu._async.image as async_image
import relaygpu._sync.image as sync_image
from relaygpu import RelayError
from tests.helpers import IMAGE_QWEN, KEY, Mock, detail, json_reply, maybe

# A 1×1 PNG (synthetic: no base64 route was captured, each costs a billed call).
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQ=="


@pytest.fixture(params=["sync", "async"])
def mod(request: pytest.FixtureRequest) -> ModuleType:
    return sync_image if request.param == "sync" else async_image


class TestRelayImageNormalisation:
    def test_urls_qwen_seedream_wan_the_captured_qwen_image_body_url_backed_images(self, mod: ModuleType) -> None:
        imgs = mod.normalise_images(IMAGE_QWEN["body"])
        assert len(imgs) == 1
        assert imgs[0].url == IMAGE_QWEN["body"]["urls"][0]
        assert imgs[0].b64 is None

    async def test_images_of_bare_base64_flux_2_pro_gpt_image_gemini_b64_with_the_mime_sniffed(self, mod: ModuleType) -> None:
        imgs = mod.normalise_images({"images": [PNG_B64, JPEG_B64], "model": "black-forest-labs/FLUX-2-pro"})
        assert [i.mime_type for i in imgs] == ["image/png", "image/jpeg"]
        assert imgs[0].url is None
        data = await maybe(imgs[0].to_bytes())
        assert data[1:4] == b"PNG"
        assert data == base64.b64decode(PNG_B64)

    def test_data_uris_keep_their_mime_and_drop_the_prefix_output_format_is_the_fallback_hint(self, mod: ModuleType) -> None:
        d = mod.to_relay_image(f"data:image/webp;base64,{PNG_B64}")
        assert (d.b64, d.mime_type) == (PNG_B64, "image/webp")
        hinted = mod.normalise_images({"images": ["AAAA"]}, {"output_format": "webp"})
        assert hinted[0].mime_type == "image/webp"
        links = mod.normalise_images({"images": ["https://cdn.relaygpu.com/content/x"]})
        assert links[0].url == "https://cdn.relaygpu.com/content/x"
        oa = mod.normalise_images({"data": [{"b64_json": PNG_B64}, {"url": "https://x.test/y.png"}]})
        assert [i.url or i.mime_type for i in oa] == ["image/png", "https://x.test/y.png"]

    async def test_save_writes_the_decoded_bytes(self, mod: ModuleType, tmp_path: Path) -> None:
        path = tmp_path / "out.png"
        await maybe(mod.to_relay_image(PNG_B64).save(path))
        assert path.read_bytes()[:8] == base64.b64decode(PNG_B64)[:8]

    def test_single_value_shapes_and_empty_bodies(self, mod: ModuleType) -> None:
        """Python addition: ``url`` / ``image`` / ``image_url`` / ``b64_json`` only when no list matched."""
        assert [i.url for i in mod.normalise_images({"url": "https://x.test/a.png"})] == ["https://x.test/a.png"]
        assert [i.b64 for i in mod.normalise_images({"b64_json": PNG_B64})] == [PNG_B64]
        assert mod.normalise_images({"urls": [], "images": None}) == []
        both = mod.normalise_images({"urls": ["https://x.test/a.png"], "url": "https://x.test/ignored.png"})
        assert [i.url for i in both] == ["https://x.test/a.png"]

    async def test_bad_base64_raises_the_ts_message(self, mod: ModuleType) -> None:
        with pytest.raises(RelayError, match=r"^Image payload is not valid base64$"):
            await maybe(mod.to_relay_image("not base64 at all!").to_bytes())
        # atob semantics: whitespace ignored, padding optional.
        assert await maybe(mod.to_relay_image(" iVBO Rw0K\nGgo ").to_bytes()) == base64.b64decode("iVBORw0KGgo=")


class TestDownload:
    async def test_a_url_download_never_carries_the_relay_credential(self, mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, content=b"\x89PNG")

        cls = httpx.Client if mod is sync_image else httpx.AsyncClient
        monkeypatch.setattr(mod, "_download_client", lambda: cls(transport=httpx.MockTransport(handler)))
        relay = mod.to_relay_image("https://cdn.relaygpu.com/content/x")
        assert await maybe(relay.to_bytes()) == b"\x89PNG"
        assert len(seen) == 1
        assert "x-api-key" not in seen[0].headers and "authorization" not in seen[0].headers
        assert KEY not in str(seen[0].headers)

    async def test_a_non_2xx_download_raises_with_the_ts_message(self, mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        cls = httpx.Client if mod is sync_image else httpx.AsyncClient
        monkeypatch.setattr(mod, "_download_client", lambda: cls(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
        with pytest.raises(RelayError) as ei:
            await maybe(mod.to_relay_image("https://cdn.relaygpu.com/content/x").to_bytes())
        assert str(ei.value) == "Image download failed: HTTP 403 (result links expire; see store_output)"
        assert ei.value.status == 403


class TestImageGenerate:
    async def test_returns_images_raw_for_a_sync_route_model_in_body_no_key(self, make: Any) -> None:
        m = Mock(detail("Qwen/qwen-image"), json_reply(200, IMAGE_QWEN["body"]))
        r = await maybe(make(m).image.generate("Qwen/qwen-image", {"prompt": "apple", "size": "512x512"}))
        assert r.raw == IMAGE_QWEN["body"]
        assert r.images[0].url == IMAGE_QWEN["body"]["urls"][0]
        assert m.calls[1].body == {"prompt": "apple", "size": "512x512", "model": "Qwen/qwen-image"}
        assert "idempotency-key" not in m.calls[1].headers

    async def test_a_base64_route_gpt_image_1_5_t2i_model_not_in_body(self, make: Any) -> None:
        m = Mock(
            detail("openai/gpt-image-1.5-T2I"),
            json_reply(200, {"images": [PNG_B64], "model": "openai/gpt-image-1.5-T2I", "size": "1024x1024"}),
        )
        r = await maybe(make(m).image.generate("openai/gpt-image-1.5-T2I", {"prompt": "x", "output_format": "png"}))
        assert (r.images[0].b64, r.images[0].mime_type) == (PNG_B64, "image/png")
        assert m.calls[1].url == "http://relay.test/v2/image/gpt-image/generate"
        assert "model" not in m.calls[1].body

    async def test_edit_resolves_the_edit_route(self, make: Any) -> None:
        m = Mock(detail("Qwen/qwen-image-edit"), json_reply(200, IMAGE_QWEN["body"]))
        await maybe(make(m).image.edit("Qwen/qwen-image-edit", {"prompt": "x", "image": "https://x.test/in.png"}))
        assert m.calls[1].url == "http://relay.test/v2/image/qwen/edit"

    async def test_the_result_types_follow_the_client(self, make: Any) -> None:
        """Python addition: ``Relay`` answers ``ImageResult`` / ``RelayImage``; ``AsyncRelay`` the ``Async*`` twins."""
        m = Mock(detail("Qwen/qwen-image"), json_reply(200, IMAGE_QWEN["body"]))
        r = await maybe(make(m).image.generate("Qwen/qwen-image", {"prompt": "a"}))
        mod = sync_image if make.kind == "sync" else async_image
        result_cls = mod.ImageResult if make.kind == "sync" else mod.AsyncImageResult
        image_cls = mod.RelayImage if make.kind == "sync" else mod.AsyncRelayImage
        assert isinstance(r, result_cls)
        assert isinstance(r.images[0], image_cls)
        assert "b64=<" in repr(mod.to_relay_image(PNG_B64))
