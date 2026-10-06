"""Port of test/unit/files.test.ts (every `it` re-pinned, on both clients via `make`), plus the Python file shapes:
paths streamed in chunks, file objects, generators, async generators on AsyncRelay, `str` path vs `str` value."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import relaygpu
import relaygpu._async.files as afiles
import relaygpu._sync.files as sfiles
from relaygpu import errors as E
from relaygpu.inputs import INLINE_IMAGE_MAX_BYTES, sniff_media_type
from tests.helpers import Mock, Recorded, agen, collect, json_reply, load, maybe, relay_error

UPLOAD = load("files_upload_201_20261006.json")
UPLOAD_STREAM = load("files_upload_stream_201_20261006.json")
GET = load("files_get_200_20261006.json")
LIST = load("files_list_200_20261006.json")
E404 = load("files_get_404_20261006.json")
E415 = load("files_upload_415_20261006.json")

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0x0D])
MP4 = bytes([0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6F, 0x6D, 0, 0, 2, 0])


def created(body: Any = None) -> httpx.Response:
    return json_reply(201, UPLOAD["body"] if body is None else body)


def q(c: Recorded) -> httpx.QueryParams:
    return httpx.URL(c.url).params


def gen(*chunks: bytes) -> Iterator[bytes]:
    yield from chunks


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


# ---- files.upload ----


class TestUpload:
    async def test_raw_body_media_type_retention_and_filename_on_the_query(self, make: Any, tmp_path: Path) -> None:
        m = Mock(created())
        f = await maybe(make(m).files.upload(write(tmp_path, "pixel.png", PNG), retention="relay1h"))
        assert f == UPLOAD["body"]
        c = m.calls[0]
        assert c.method == "POST"
        assert httpx.URL(c.url).path == "/v2/files"
        assert q(c)["retention"] == "relay1h"
        assert q(c)["filename"] == "pixel.png"
        assert c.headers["content-type"] == "image/png"
        assert c.raw == PNG
        # a path is streamed with its size as Content-Length (never chunked, never buffered whole)
        assert c.headers["content-length"] == str(len(PNG))
        assert "transfer-encoding" not in c.headers

    async def test_omits_absent_query_params_content_type_and_filename_win(self, make: Any, tmp_path: Path) -> None:
        m = Mock(created())
        await maybe(make(m).files.upload(write(tmp_path, "p.png", PNG), content_type="image/x-custom", filename="a.bin"))
        assert m.calls[0].headers["content-type"] == "image/x-custom"
        assert q(m.calls[0])["filename"] == "a.bin"
        assert "retention" not in q(m.calls[0])

    async def test_forwards_idempotency_key(self, make: Any) -> None:
        m = Mock(created(), created())
        r = make(m)
        a = await maybe(r.files.upload(PNG, idempotency_key="clip-001"))
        b = await maybe(r.files.upload(PNG, idempotency_key="clip-001"))
        assert [c.headers["idempotency-key"] for c in m.calls] == ["clip-001", "clip-001"]
        assert a["file_id"] == b["file_id"]

    SNIFF_CASES: list[tuple[bytes, str]] = [  # noqa: RUF012
        (PNG, "image/png"),
        (bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(16), "image/jpeg"),
        (b"RIFF\0\0\0\0WEBP" + bytes(16), "image/webp"),
        (b"GIF89a" + bytes(16), "image/gif"),
        (MP4, "video/mp4"),
        (bytes([0, 0, 0, 0x14]) + b"ftypqt  " + bytes(16), "video/quicktime"),
        (bytes([0x1A, 0x45, 0xDF, 0xA3]) + bytes(16), "video/webm"),
        (b"RIFF\0\0\0\0WAVE" + bytes(16), "audio/wav"),
        (b"ID3" + bytes(16), "audio/mpeg"),
        (bytes([0xFF, 0xFB, 0x90]) + bytes(16), "audio/mpeg"),
        (b"OggS" + bytes(16), "audio/ogg"),
        (b"fLaC" + bytes(16), "audio/flac"),
    ]

    async def test_sniffs_png_jpeg_webp_gif_mp4_mov_webm_wav_mp3_ogg_flac(self, make: Any) -> None:
        m = Mock(*[created() for _ in self.SNIFF_CASES])
        r = make(m)
        for data, _ in self.SNIFF_CASES:
            await maybe(r.files.upload(bytearray(data)))
        assert [c.headers["content-type"] for c in m.calls] == [t for _, t in self.SNIFF_CASES]

    def test_sniffer_table_pure(self) -> None:
        for data, t in self.SNIFF_CASES:
            assert sniff_media_type(data) == t
        assert sniff_media_type(bytes([0, 0, 0, 0x20]) + b"ftypM4A ") == "audio/mp4"
        assert sniff_media_type(b"") is None
        assert sniff_media_type(b"\xff") is None  # a lone 0xff is not an MP3 frame sync
        assert sniff_media_type(b"hello world, plain text") is None

    async def test_refuses_an_unrecognisable_type_before_sending_anything(self, make: Any) -> None:
        m = Mock()
        r = make(m)
        with pytest.raises(TypeError):
            await maybe(r.files.upload(b"hello world, plain text"))
        with pytest.raises(TypeError, match="content_type"):
            await maybe(r.files.upload(io.BytesIO(b"hello")))
        for bad in (123, [PNG], {"a": PNG}, None):
            with pytest.raises(TypeError, match=r"files\.upload takes"):
                await maybe(r.files.upload(bad))
        assert m.calls == []

    async def test_an_iterator_is_peeked_for_its_type_and_still_sends_every_byte_once(self, make: Any) -> None:
        sent: list[bytes] = []

        def capture(rec: Recorded) -> httpx.Response:
            sent.append(rec.raw)
            return created(UPLOAD_STREAM["body"])

        m = Mock(capture)
        tail = bytes([7]) * 5000
        f = await maybe(make(m).files.upload(gen(MP4[:6], MP4[6:], tail), retention="relay1h", filename="tiny.mp4"))
        assert f["content_type"] == "video/mp4"
        assert m.calls[0].headers["content-type"] == "video/mp4"
        assert len(sent[0]) == len(MP4) + len(tail)
        assert sent[0][:16] == MP4

    async def test_a_streamed_upload_is_never_retried_even_with_an_idempotency_key(self, make: Any) -> None:
        m = Mock(relay_error(502, "UPSTREAM_ERROR"), created())
        with pytest.raises(relaygpu.RelayAPIError) as ei:
            await maybe(make(m).files.upload(gen(MP4), idempotency_key="k"))
        assert ei.value.status == 502
        assert len(m.calls) == 1

    async def test_a_keyed_bytes_upload_is_retried_on_5xx_with_the_same_key(self, make: Any) -> None:
        m = Mock(relay_error(502, "UPSTREAM_ERROR"), created())
        f = await maybe(make(m).files.upload(PNG, content_type="image/png", idempotency_key="k"))
        assert f["file_id"] == UPLOAD["body"]["file_id"]
        assert [c.headers["idempotency-key"] for c in m.calls] == ["k", "k"]

    async def test_a_keyed_path_upload_is_retried_on_5xx_like_a_ts_blob(self, make: Any, tmp_path: Path) -> None:
        m = Mock(relay_error(502, "UPSTREAM_ERROR"), created())
        await maybe(make(m).files.upload(write(tmp_path, "p.png", PNG), idempotency_key="k"))
        assert [c.raw for c in m.calls] == [PNG, PNG]


# ---- Python file shapes ----


class TestPythonShapes:
    async def test_a_path_is_streamed_in_chunks(self, make: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        reads: list[int] = []
        for mod in (afiles, sfiles):
            monkeypatch.setattr(mod, "CHUNK_SIZE", 1000)
        orig_sync, orig_async = sfiles.sync_read, afiles.async_read

        def spy_sync(f: Any, n: int) -> bytes:
            reads.append(n)
            return orig_sync(f, n)

        async def spy_async(f: Any, n: int) -> bytes:
            reads.append(n)
            return await orig_async(f, n)

        monkeypatch.setattr(sfiles, "sync_read", spy_sync)
        monkeypatch.setattr(afiles, "async_read", spy_async)
        data = MP4 + bytes(4500)
        m = Mock(created())
        await maybe(make(m).files.upload(write(tmp_path, "clip.mp4", data)))
        assert m.calls[0].raw == data
        assert m.calls[0].headers["content-type"] == "video/mp4"
        assert q(m.calls[0])["filename"] == "clip.mp4"
        assert reads == [1000] * 6  # five full chunks, one partial, then EOF; never one whole-file read

    async def test_a_str_is_a_path_in_upload(self, make: Any, tmp_path: Path) -> None:
        m = Mock(created())
        await maybe(make(m).files.upload(str(write(tmp_path, "voice.wav", b"RIFF\0\0\0\0WAVE" + bytes(8)))))
        assert m.calls[0].headers["content-type"] == "audio/wav"
        assert q(m.calls[0])["filename"] == "voice.wav"
        with pytest.raises(OSError):
            await maybe(make(Mock()).files.upload(str(tmp_path / "missing.png")))

    async def test_a_binary_file_object_streams_with_its_name(self, make: Any, tmp_path: Path) -> None:
        p = write(tmp_path, "frame.png", PNG + bytes(100))
        m = Mock(created())
        with open(p, "rb") as fh:
            await maybe(make(m).files.upload(fh))
        assert m.calls[0].raw == PNG + bytes(100)
        assert m.calls[0].headers["content-type"] == "image/png"
        assert q(m.calls[0])["filename"] == "frame.png"
        m2 = Mock(created())
        await maybe(make(m2).files.upload(io.BytesIO(MP4)))  # no .name → no filename
        assert "filename" not in q(m2.calls[0])
        assert m2.calls[0].headers["content-type"] == "video/mp4"

    async def test_a_text_mode_file_is_refused(self, make: Any, tmp_path: Path) -> None:
        p = write(tmp_path, "t.txt", b"hello")
        m = Mock()
        with open(p) as fh, pytest.raises(TypeError, match="binary mode"):
            await maybe(make(m).files.upload(fh, content_type="image/png"))

    async def test_async_generator_on_async_relay(self, tmp_path: Path) -> None:
        from tests.helpers import async_relay

        m = Mock(created())
        await async_relay(m).files.upload(agen(MP4[:3], MP4[3:], b"x" * 10), filename="a.mp4")
        assert m.calls[0].raw == MP4 + b"x" * 10
        assert m.calls[0].headers["content-type"] == "video/mp4"

    def test_the_sync_client_refuses_an_async_generator_before_any_call(self) -> None:
        from tests.helpers import sync_relay

        m = Mock()
        r = sync_relay(m)
        with pytest.raises(TypeError, match="async iterator"):
            r.files.upload(agen(MP4))
        with pytest.raises(TypeError, match="async iterator"):
            r.files.prepare_inputs({"image_url": PNG, "video_url": agen(MP4)})
        assert m.calls == []

    async def test_a_str_is_never_file_data_in_prepare_inputs(self, make: Any, tmp_path: Path) -> None:
        p = str(write(tmp_path, "x.png", PNG))
        m = Mock()
        out = await maybe(make(m).files.prepare_inputs({"image_url": p, "image": b64(PNG)}))
        assert out == {"image_url": p, "image": b64(PNG)}
        assert m.calls == []

    async def test_identity_dedupe_equal_but_distinct_objects_upload_twice(self, make: Any, tmp_path: Path) -> None:
        m = Mock(created(), created(), created())
        a, b = bytearray(PNG), bytearray(PNG)
        p = write(tmp_path, "c.png", PNG)
        await maybe(make(m).files.prepare_inputs({"image_url": a, "mask_url": b, "x_url": a, "y_urls": [p, p]}))
        assert len(m.calls) == 3
        assert q(m.calls[2])["filename"] == "c.png"

    async def test_tuples_are_arrays(self, make: Any) -> None:
        m = Mock(created())
        out = await maybe(make(m).files.prepare_inputs({"image_urls": (PNG, "https://x.test/a.png")}))
        assert out["image_urls"] == [UPLOAD["body"]["url"], "https://x.test/a.png"]

    def test_inline_image_max_bytes(self) -> None:
        assert INLINE_IMAGE_MAX_BYTES == 4 * 1024 * 1024


# ---- files.copy ----


class TestCopy:
    async def test_posts_the_json_form_with_retention_and_filename_in_the_body(self, make: Any) -> None:
        m = Mock(created())
        await maybe(make(m).files.copy("https://example.com/voice.wav", retention="relay7d", filename="voice.wav", idempotency_key="c1"))
        c = m.calls[0]
        assert c.url == "http://relay.test/v2/files"
        assert c.headers["content-type"] == "application/json"
        assert c.headers["idempotency-key"] == "c1"
        assert c.body == {"url": "https://example.com/voice.wav", "retention": "relay7d", "filename": "voice.wav"}

    async def test_sends_only_the_url_when_nothing_else_is_given(self, make: Any) -> None:
        m = Mock(created())
        await maybe(make(m).files.copy("https://example.com/a.png"))
        assert m.calls[0].body == {"url": "https://example.com/a.png"}


# ---- get / list / list_all / delete ----


class TestReads:
    async def test_get_returns_the_file(self, make: Any) -> None:
        m = Mock(json_reply(200, GET["body"]))
        assert await maybe(make(m).files.get(GET["body"]["file_id"])) == GET["body"]
        assert m.calls[0].url == f"http://relay.test/v2/files/{GET['body']['file_id']}"

    async def test_list_sends_the_filters_and_returns_the_page(self, make: Any) -> None:
        m = Mock(json_reply(200, LIST["body"]))
        page = await maybe(make(m).files.list(source="upload", status="ready", limit=1, cursor="file_x"))
        assert page == LIST["body"]
        assert dict(q(m.calls[0])) == {"source": "upload", "status": "ready", "limit": "1", "cursor": "file_x"}

    async def test_list_all_follows_next_cursor_to_the_null_page(self, make: Any) -> None:
        def f(i: str) -> dict[str, Any]:
            return {**LIST["body"]["files"][0], "file_id": i}

        m = Mock(
            json_reply(200, {"files": [f("a"), f("b")], "next_cursor": "b"}),
            json_reply(200, {"files": [f("c")], "next_cursor": "c"}),
            json_reply(200, {"files": [], "next_cursor": None}),
        )
        ids = [x["file_id"] for x in await collect(make(m).files.list_all(source="upload", limit=2))]
        assert ids == ["a", "b", "c"]
        assert [q(c).get("cursor") for c in m.calls] == [None, "b", "c"]
        assert all(q(c)["limit"] == "2" and q(c)["source"] == "upload" for c in m.calls)

    async def test_list_all_stops_on_a_repeated_cursor(self, make: Any) -> None:
        row = LIST["body"]["files"][0]
        m = Mock(json_reply(200, {"files": [row], "next_cursor": "z"}), json_reply(200, {"files": [row], "next_cursor": "z"}))
        assert len(await collect(make(m).files.list_all())) == 2
        assert len(m.calls) == 2

    async def test_delete_answers_204_none(self, make: Any) -> None:
        m = Mock(httpx.Response(204))
        assert await maybe(make(m).files.delete("file_1")) is None
        assert m.calls[0].method == "DELETE"
        assert m.calls[0].url == "http://relay.test/v2/files/file_1"


# ---- errors ----


class TestErrors:
    async def test_404_file_not_found_captured(self, make: Any) -> None:
        m = Mock(json_reply(404, E404["body"]))
        with pytest.raises(E.FileNotFoundError) as ei:
            await maybe(make(m).files.get("file_000000000000000000000000"))
        assert ei.value.code == "FILE_NOT_FOUND"
        assert ei.value.request_id == E404["body"]["error"]["request_id"]

    async def test_415_captured_and_413_are_relay_api_errors_with_the_code(self, make: Any) -> None:
        m = Mock(json_reply(415, E415["body"]), relay_error(413, "FILE_TOO_LARGE"))
        r = make(m)
        with pytest.raises(E.FileTypeUnsupportedError) as a:
            await maybe(r.files.upload(PNG, content_type="image/png"))
        assert isinstance(a.value, E.RelayAPIError)
        assert a.value.status == 415
        with pytest.raises(E.FileTooLargeError) as b:
            await maybe(r.files.upload(PNG))
        assert b.value.code == "FILE_TOO_LARGE"

    async def test_429_file_quota_exceeded_carries_retry_after_402_is_insufficient_credits(self, make: Any) -> None:
        m = Mock(relay_error(429, "FILE_QUOTA_EXCEEDED", {}, {"retry-after": "3600"}), relay_error(402, "INSUFFICIENT_CREDITS"))
        r = make(m)
        with pytest.raises(E.FileQuotaExceededError) as a:
            await maybe(r.files.upload(PNG))
        assert isinstance(a.value, E.RateLimitError)
        assert a.value.retry_after == 3600
        with pytest.raises(E.InsufficientCreditsError):
            await maybe(r.files.upload(PNG, retention="relay1d"))


# ---- prepare_inputs (implicit upload) ----

KLING_I2V = {
    "type": "object",
    "properties": {
        "image": {"type": "string", "description": "Reference image URL or base64 encoded string"},
        "image_tail": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Optional end-frame image (URL or base64). Used for start→end-frame interpolation.",
        },
    },
}
GEMINI = {
    "type": "object",
    "properties": {
        "image": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Optional reference image for image-to-image editing. Accepts raw base64 or a data URI (data:image/png;base64,...).",
        },
        "images": {
            "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
            "description": "Multiple reference images (1-14). Each item is raw base64 or a data URI.",
        },
    },
}
GPT_I2I = {
    "type": "object",
    "properties": {
        "image": {"type": "string", "description": "Source image as base64 encoded string"},
        "mask": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Mask image as base64 encoded string (optional)"},
    },
}
SEEDANCE = {
    "type": "object",
    "properties": {
        "first_frame_url": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "URL or base64 data URI of the first frame image.",
        },
        "reference_video_url": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "URL of a reference video (role=reference_video).",
        },
    },
}


class TestPrepareInputs:
    n = 0

    def reply(self) -> httpx.Response:
        TestPrepareInputs.n += 1
        k = TestPrepareInputs.n
        return created({**UPLOAD["body"], "file_id": f"file_{k}", "url": f"https://cdn.relaygpu.com/content/u{k}"})

    async def test_uploads_a_file_in_video_url_once_and_swaps_in_its_link_input_not_mutated(self, make: Any, tmp_path: Path) -> None:
        m = Mock(self.reply())
        clip = write(tmp_path, "d.mp4", MP4)
        inp = {"prompt": "dance", "video_url": clip}
        out = await maybe(make(m).files.prepare_inputs(inp))
        assert out == {"prompt": "dance", "video_url": f"https://cdn.relaygpu.com/content/u{TestPrepareInputs.n}"}
        assert inp["video_url"] is clip
        assert out is not inp
        assert len(m.calls) == 1
        assert q(m.calls[0])["retention"] == "relay1h"
        assert m.calls[0].raw == MP4

    async def test_the_same_file_in_two_fields_uploads_once(self, make: Any) -> None:
        m = Mock(self.reply())
        out = await maybe(make(m).files.prepare_inputs({"image_url": PNG, "reference_image_url": PNG}))
        assert len(m.calls) == 1
        assert out["image_url"] == out["reference_image_url"]

    async def test_arrays_in_urls_fields_nested_objects_mixed_strings(self, make: Any, tmp_path: Path) -> None:
        m = Mock(self.reply(), self.reply(), self.reply())
        a = write(tmp_path, "a.png", PNG)
        b = bytearray(PNG)
        inp = {
            "reference_image_urls": [a, "https://x.test/keep.png", b],
            "settings": {"deep": {"audio_url": bytes(MP4)}, "label": "x"},
            "list": [{"first_frame_url": a}],
        }
        out = await maybe(make(m).files.prepare_inputs(inp))
        assert len(m.calls) == 3  # a, b, the audio — a reused in list[0]
        assert out["reference_image_urls"][1] == "https://x.test/keep.png"
        assert out["reference_image_urls"][0].startswith("https://cdn.relaygpu.com/")
        assert out["reference_image_urls"][2].startswith("https://cdn.relaygpu.com/")
        assert out["settings"]["deep"]["audio_url"].startswith("https://cdn.relaygpu.com/")
        assert out["settings"]["label"] == "x"
        assert out["list"][0]["first_frame_url"] == out["reference_image_urls"][0]
        assert inp["reference_image_urls"][0] is a

    async def test_a_pure_string_body_makes_zero_calls_and_returns_an_equal_copy(self, make: Any) -> None:
        m = Mock()
        inp = {"prompt": "x", "video_url": "https://x.test/a.mp4", "n": 2, "nested": {"image_url": "https://x.test/b.png"}}
        out = await maybe(make(m).files.prepare_inputs(inp, inline_images=True))
        assert out == inp
        assert out is not inp
        assert m.calls == []

    async def test_retention_comes_from_upload(self, make: Any) -> None:
        m = Mock(self.reply())
        await maybe(make(m).files.prepare_inputs({"video_url": MP4}, upload={"retention": "relay1d"}))
        assert q(m.calls[0])["retention"] == "relay1d"

    async def test_a_paths_name_rides_as_filename_an_iterator_is_uploaded(self, make: Any, tmp_path: Path) -> None:
        m = Mock(self.reply(), self.reply())
        await maybe(make(m).files.prepare_inputs({"video_url": write(tmp_path, "clip.mp4", MP4), "audio_url": gen(MP4)}))
        assert q(m.calls[0])["filename"] == "clip.mp4"
        assert m.calls[1].headers["content-type"] == "video/mp4"

    async def test_inline_images_an_image_le_4mb_in_a_base64_field_becomes_base64_zero_uploads(self, make: Any, tmp_path: Path) -> None:
        m = Mock()
        r = make(m)
        img = write(tmp_path, "i.png", PNG)
        k = await maybe(r.files.prepare_inputs({"image": img, "image_tail": PNG}, inline_images=True, request_schema=KLING_I2V))
        assert k == {"image": b64(PNG), "image_tail": b64(PNG)}  # "base64 encoded string" → raw
        g = await maybe(r.files.prepare_inputs({"images": [img, PNG]}, inline_images=True, request_schema=GEMINI))
        assert g["images"] == [f"data:image/png;base64,{b64(PNG)}"] * 2
        s = await maybe(r.files.prepare_inputs({"first_frame_url": img}, inline_images=True, request_schema=SEEDANCE))
        assert s["first_frame_url"] == f"data:image/png;base64,{b64(PNG)}"  # a *_url field takes a data URI
        assert m.calls == []

    async def test_inline_images_gt_4mb_a_stream_a_non_image_or_a_url_only_field_uploads(self, make: Any, tmp_path: Path) -> None:
        m = Mock(self.reply(), self.reply(), self.reply(), self.reply())
        r = make(m)
        big = PNG + bytes(INLINE_IMAGE_MAX_BYTES + 1 - len(PNG))
        out = await maybe(
            r.files.prepare_inputs(
                {"first_frame_url": big, "reference_video_url": write(tmp_path, "r.png", PNG)},
                inline_images=True,
                request_schema=SEEDANCE,
            )
        )
        assert out["first_frame_url"].startswith("https:")
        assert out["reference_video_url"].startswith("https:")
        await maybe(r.files.prepare_inputs({"image": gen(PNG)}, inline_images=True, request_schema=KLING_I2V))
        await maybe(r.files.prepare_inputs({"first_frame_url": MP4}, inline_images=True, request_schema=SEEDANCE))
        assert len(m.calls) == 4

    async def test_without_inline_images_a_url_or_base64_field_uploads(self, make: Any) -> None:
        m = Mock(self.reply())
        out = await maybe(make(m).files.prepare_inputs({"image": PNG}, request_schema=KLING_I2V))
        assert out["image"].startswith("https:")
        assert len(m.calls) == 1

    async def test_a_base64_only_field_is_always_inlined_streams_included(self, make: Any, tmp_path: Path) -> None:
        m = Mock()
        out = await maybe(
            make(m).files.prepare_inputs({"image": write(tmp_path, "s.png", PNG), "mask": gen(PNG[:4], PNG[4:])}, request_schema=GPT_I2I)
        )
        assert out == {"image": b64(PNG), "mask": b64(PNG)}
        assert m.calls == []

    async def test_a_file_in_a_field_that_takes_neither_a_url_nor_base64_is_a_type_error_before_any_call(self, make: Any) -> None:
        m = Mock()
        r = make(m)
        with pytest.raises(TypeError, match="neither a URL nor base64"):
            await maybe(r.files.prepare_inputs({"prompt": PNG}))
        with pytest.raises(TypeError, match="request schema"):
            await maybe(r.files.prepare_inputs({"image": PNG}))
        # a misplaced file after a valid one: still refused before the first upload
        with pytest.raises(TypeError, match='"prompt"'):
            await maybe(r.files.prepare_inputs({"image_url": PNG, "prompt": PNG}))
        assert m.calls == []
