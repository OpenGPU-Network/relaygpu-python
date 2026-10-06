"""Port of test/unit/audio.test.ts. The TS type-level test (ASR-only bodies, Blob in ``audio_url``) has no runtime twin:
v0.1 takes ``Mapping[str, Any]`` inputs; file values in ``audio_url`` are package (b)'s tests."""

from __future__ import annotations

import re
from typing import Any

from tests.helpers import TTS_QWEN, Mock, detail, json_reply, maybe, task


class TestAudio:
    async def test_speech_the_response_body_the_captured_qwen3_tts_flash_answer_model_not_in_body(self, make: Any) -> None:
        m = Mock(detail("Qwen/qwen3-tts-flash"), json_reply(200, TTS_QWEN["body"]))
        r = await maybe(make(m).audio.speech("Qwen/qwen3-tts-flash", {"input": "Hello from Relay.", "voice": "Cherry"}))
        assert r == TTS_QWEN["body"]
        assert re.match(r"^https://cdn\.relaygpu\.com/", r["audio_url"])
        assert m.calls[1].url == "http://relay.test/v2/audio/qwen3-tts-flash/generate"
        assert m.calls[1].body == {"input": "Hello from Relay.", "voice": "Cherry"}
        assert "idempotency-key" not in m.calls[1].headers

    async def test_transcribe_post_v2_audio_asr_whisper_with_model_in_body(self, make: Any) -> None:
        m = Mock(
            detail("openai/whisper-1"),
            json_reply(200, {"text": "hello", "language": "en", "duration": 1.2, "task_id": "direct:a", "mode": "direct"}),
        )
        r = await maybe(make(m).audio.transcribe("openai/whisper-1", {"audio_url": "https://x.test/a.mp3", "language": "en"}))
        assert r["text"] == "hello"
        assert m.calls[1].url == "http://relay.test/v2/audio/asr/whisper"
        assert m.calls[1].body == {"audio_url": "https://x.test/a.mp3", "language": "en", "model": "openai/whisper-1"}

    async def test_an_async_answer_async_true_is_waited_for_and_the_result_returned(self, make: Any) -> None:
        m = Mock(
            detail("Qwen/qwen3-tts-flash"),
            json_reply(202, {"task_id": "direct:t1", "status": "queued", "poll_url": "/v2/tasks/direct:t1", "message": "m"}),
            task("completed", result=TTS_QWEN["body"]),
        )
        r = await maybe(make(m).audio.speech("Qwen/qwen3-tts-flash", {"input": "x", "voice": "Cherry"}, async_=True))
        assert r == TTS_QWEN["body"]
        assert m.calls[1].headers["idempotency-key"]
