"""Port of test/unit/run.test.ts: run() resolution (F2), refusals before any POST, and the Idempotency-Key rules of
submits (A1/A8, F7: fixture-pinned). Plus the Python additions: the async-resolution order and the implicit-upload seam."""

from __future__ import annotations

import re
from typing import Any

import pytest

from relaygpu import ModelNotFoundError, ModelRetiredError, ProviderError, RelayError
from relaygpu._run_common import is_accepted
from tests.helpers import IMAGE_QWEN, TTS_QWEN, VIDEO_KLING, Mock, Recorded, detail, detail_with, json_reply, maybe, relay_error, task

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def posts(calls: list[Recorded]) -> list[Recorded]:
    return [c for c in calls if c.method == "POST"]


def accepted202(headers: dict[str, str] | None = None) -> Any:
    return json_reply(202, VIDEO_KLING["accepted"]["body"], {"x-request-id": "rid-202", **(headers or {})})


class TestRunResolution:
    async def test_model_in_body_true_model_sent_sync_the_response_body_no_idempotency_key_no_async_field(self, make: Any) -> None:
        m = Mock(detail("Qwen/qwen-image"), json_reply(200, IMAGE_QWEN["body"]))
        out = await maybe(make(m).run("Qwen/qwen-image", {"prompt": "apple", "size": "512x512"}))
        assert out == IMAGE_QWEN["body"]
        post = m.calls[1]
        assert (post.method, post.url) == ("POST", "http://relay.test/v2/image/qwen/generate")
        assert post.body == {"prompt": "apple", "size": "512x512", "model": "Qwen/qwen-image"}
        assert "idempotency-key" not in post.headers
        assert "async" not in post.body
        assert "mode" not in post.body

    async def test_model_in_body_false_model_stripped_tts(self, make: Any) -> None:
        m = Mock(detail("Qwen/qwen3-tts-flash"), json_reply(200, TTS_QWEN["body"]))
        await maybe(make(m).run("Qwen/qwen3-tts-flash", {"input": "hi", "voice": "Cherry", "model": "ignored"}))
        assert m.calls[1].url == "http://relay.test/v2/audio/qwen3-tts-flash/generate"
        assert m.calls[1].body == {"input": "hi", "voice": "Cherry"}

    async def test_async_route_wait_default_long_polls_and_returns_task_result(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202(), json_reply(200, VIDEO_KLING["completed"]["body"]))
        out = await maybe(make(m).run("KlingTeam/v3-T2V", {"prompt": "ball", "duration": 5}))
        assert out == VIDEO_KLING["completed"]["body"]["result"]
        assert m.calls[1].body == {"prompt": "ball", "duration": 5, "async": True}
        assert "model" not in m.calls[1].body
        assert m.calls[2].url == f"http://relay.test/v2/tasks/{VIDEO_KLING['accepted']['body']['task_id']}?wait=30"

    async def test_wait_false_the_202_envelope_with_replayed_request_id_is_accepted_narrows_it(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202())
        out = await maybe(make(m).run("KlingTeam/v3-T2V", {"prompt": "ball"}, wait=False))
        assert is_accepted(out)
        assert out == {**VIDEO_KLING["accepted"]["body"], "replayed": False, "request_id": "rid-202"}
        assert not is_accepted(IMAGE_QWEN["body"])

    async def test_mode_store_output_webhook_url_map_to_body_fields(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202())
        await maybe(
            make(m).run(
                "KlingTeam/v3-T2V", {"prompt": "p"}, wait=False, mode="direct", store_output="relay7d", webhook_url="https://hook.test/x"
            )
        )
        assert m.calls[1].body == {
            "prompt": "p",
            "mode": "direct",
            "store_output": "relay7d",
            "webhook_url": "https://hook.test/x",
            "async": True,
        }

    async def test_async_false_on_an_async_default_route_sends_async_false_and_no_key(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), json_reply(200, VIDEO_KLING["completed"]["body"]["result"]))
        out = await maybe(make(m).run("KlingTeam/v3-T2V", {"prompt": "p"}, async_=False))
        assert out == VIDEO_KLING["completed"]["body"]["result"]
        assert m.calls[1].body["async"] is False
        assert "idempotency-key" not in m.calls[1].headers

    async def test_a_retired_model_throws_model_retired_error_with_zero_posts_endpoint_present_or_null(self, make: Any) -> None:
        for name in ("black-forest-labs/FLUX.2-klein-4B", "xai/grok-4"):
            m = Mock(detail(name))
            with pytest.raises(ModelRetiredError) as ei:
                await maybe(make(m).run(name, {"prompt": "x"}))
            assert (ei.value.status, ei.value.code) == (403, "MODEL_RETIRED")
            assert posts(m.calls) == []

    async def test_an_available_model_with_no_single_route_refuses_before_submit(self, make: Any) -> None:
        m = Mock(json_reply(200, {"name": "a/b", "status": "available", "endpoint": None}))
        with pytest.raises(RelayError) as ei:
            await maybe(make(m).run("a/b", {}))
        assert not isinstance(ei.value, ModelRetiredError)
        assert posts(m.calls) == []

    async def test_an_unknown_model_throws_model_not_found_error_with_zero_posts(self, make: Any) -> None:
        m = Mock(detail("nope/nope"))
        with pytest.raises(ModelNotFoundError):
            await maybe(make(m).run("nope/nope", {}))
        assert posts(m.calls) == []

    async def test_async_resolution_order_arg_then_input_bool_then_route_default(self, make: Any) -> None:
        """Python addition: ``async_`` beats the input's ``async`` (a bool only), which beats ``async_default``."""
        m = Mock(
            detail("Qwen/qwen-image"),
            json_reply(202, {"task_id": "direct:i1", "status": "queued", "poll_url": "/v2/tasks/direct:i1", "message": "m"}),
            json_reply(200, IMAGE_QWEN["body"]),
            json_reply(200, IMAGE_QWEN["body"]),
        )
        relay = make(m)
        out = await maybe(relay.run("Qwen/qwen-image", {"prompt": "a", "async": True}, wait=False))
        assert is_accepted(out)
        assert posts(m.calls)[0].body["async"] is True
        assert UUID.match(posts(m.calls)[0].headers["idempotency-key"])
        await maybe(relay.run("Qwen/qwen-image", {"prompt": "a", "async": True}, async_=False))
        assert "async" not in posts(m.calls)[1].body  # sync-default route: async:false is never sent
        await maybe(relay.run("Qwen/qwen-image", {"prompt": "a", "async": "yes"}))
        assert "async" not in posts(m.calls)[2].body  # a non-bool `async` is not a request for async

    async def test_the_implicit_upload_seam_gets_the_body_options_and_request_schema(self, make: Any) -> None:
        """Python addition: submit hands the built body to ``files.prepare_inputs`` and POSTs what it returns."""
        m = Mock(detail("KlingTeam/v3-Motion-Control"), accepted202())
        relay = make(m)
        seen: dict[str, Any] = {}

        def prepare(body: dict[str, Any], **kw: Any) -> Any:
            seen.update(body=body, **kw)
            out = {**body, "video_url": "https://cdn.test/up.mp4"}
            if make.kind == "async":

                async def done() -> Any:
                    return out

                return done()
            return out

        relay.files.prepare_inputs = prepare
        await maybe(
            relay.video.generate(
                "KlingTeam/v3-Motion-Control",
                {"image_url": "https://x.test/i.png", "video_url": b"\x00\x00\x00\x18ftypmp42", "character_orientation": "image"},
                upload={"retention": "relay1d"},
                inline_images=True,
            )
        )
        assert seen["upload"] == {"retention": "relay1d"}
        assert seen["inline_images"] is True
        from tests.helpers import DETAILS

        assert seen["request_schema"] == DETAILS["KlingTeam/v3-Motion-Control"]["body"]["request_schema"]
        assert seen["body"]["async"] is True
        assert m.calls[1].body["video_url"] == "https://cdn.test/up.mp4"


class TestIdempotencyKeyOnSubmits:
    async def test_a_generated_uuid_rides_every_async_submit_video_default_and_image_with_async_true(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202(), accepted202())
        relay = make(m)
        await maybe(relay.video.generate("KlingTeam/v3-T2V", {"prompt": "a"}))
        await maybe(relay.video.generate("KlingTeam/v3-T2V", {"prompt": "a"}))
        k1, k2 = (c.headers["idempotency-key"] for c in posts(m.calls))
        assert UUID.match(k1)
        assert UUID.match(k2)
        assert k1 != k2

        m2 = Mock(
            detail("Qwen/qwen-image"),
            json_reply(202, {"task_id": "direct:i1", "status": "queued", "poll_url": "/v2/tasks/direct:i1", "message": "m"}),
            task("completed", task_id="direct:i1", result=IMAGE_QWEN["body"]),
        )
        img = await maybe(make(m2).image.generate("Qwen/qwen-image", {"prompt": "a"}, async_=True))
        assert UUID.match(posts(m2.calls)[0].headers["idempotency-key"])
        assert posts(m2.calls)[0].body["async"] is True
        assert posts(m2.calls)[0].body["model"] == "Qwen/qwen-image"
        assert img.images[0].url == IMAGE_QWEN["body"]["urls"][0]

    async def test_none_rides_a_sync_call_image_tts_asr_and_a_sync_5xx_is_not_retried(self, make: Any) -> None:
        """A1: sync calls carry no Idempotency-Key (fixture-pinned)."""
        m = Mock(
            detail("Qwen/qwen-image"),
            json_reply(200, IMAGE_QWEN["body"]),
            detail("Qwen/qwen3-tts-flash"),
            json_reply(200, TTS_QWEN["body"]),
            detail("openai/whisper-1"),
            relay_error(502, "UPSTREAM_ERROR", {}, {"retry-after": "0"}),
        )
        relay = make(m)
        await maybe(relay.image.generate("Qwen/qwen-image", {"prompt": "a"}))
        await maybe(relay.audio.speech("Qwen/qwen3-tts-flash", {"input": "hi", "voice": "Cherry"}))
        with pytest.raises(ProviderError):
            await maybe(relay.audio.transcribe("openai/whisper-1", {"audio_url": "https://x.test/a.mp3"}))
        p = posts(m.calls)
        assert len(p) == 3
        assert all("idempotency-key" not in c.headers for c in p)
        assert p[2].body == {"audio_url": "https://x.test/a.mp3", "model": "openai/whisper-1"}

    async def test_a_caller_idempotency_key_is_used_verbatim(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202())
        await maybe(make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "a"}, idempotency_key="my-key-1"))
        assert m.calls[1].headers["idempotency-key"] == "my-key-1"

    async def test_a_5xx_on_an_async_submit_is_retried_once_with_the_same_key(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), relay_error(502, "UPSTREAM_ERROR", {}, {"retry-after": "0"}), accepted202())
        r = await maybe(make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "a"}))
        p = posts(m.calls)
        assert len(p) == 2
        assert UUID.match(p[0].headers["idempotency-key"])
        assert p[1].headers["idempotency-key"] == p[0].headers["idempotency-key"]
        assert r["task_id"] == VIDEO_KLING["accepted"]["body"]["task_id"]

    async def test_replayed_true_surfaces_from_the_idempotency_replayed_header(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted202({"idempotency-replayed": "true"}))
        r = await maybe(make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "a"}, idempotency_key="k"))
        assert (r["task_id"], r["replayed"]) == (VIDEO_KLING["accepted"]["body"]["task_id"], True)

    async def test_a_sync_call_passes_timeout_as_the_per_attempt_http_timeout(self, make: Any) -> None:
        """Python addition: ``timeout`` on a sync route is the HTTP timeout of the one POST (TS ``timeoutMs``)."""
        m = Mock(detail_with("Qwen/qwen-image", {}), json_reply(200, IMAGE_QWEN["body"]))
        relay = make(m)
        seen: list[Any] = []
        orig = relay._http.request

        def spy(method: str, path: str, **kw: Any) -> Any:
            seen.append((method, kw.get("timeout"), kw.get("idempotency_key")))
            return orig(method, path, **kw)

        relay._http.request = spy
        await maybe(relay.run("Qwen/qwen-image", {"prompt": "a"}, timeout=42.0))
        assert seen[-1] == ("POST", 42.0, None)
