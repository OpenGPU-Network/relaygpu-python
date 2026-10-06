"""A8 + A1 (media), port of test/e2e/media.e2e.test.ts: BILLED against staging — the lead runs this once. Skips without
RELAY_API_KEY / RELAY_BASE_URL; refuses a production base URL. Approximate cost at 2026-10-06 list prices:

- A8 Kling v3-T2V 3 s std silent: 1 task (0.252; the replay and the 422 create none) — not waited on
- A1 video + wait: Kling v3-T2V 3 s std silent: 0.252 (≤ 5 long-poll requests)
- A1 image Qwen/qwen-image 512x512: 0.036 · TTS qwen3-tts-flash ~25 chars: ~0.0003
- A1 ASR whisper-1 on that TTS clip's BYTES (~2 s, implicit relay1h upload = free): ~0.0002

Total ≈ $0.54. The A3 motion-control part lives in tests/e2e/test_files.py and reuses VIDEO_URL / IMAGE_URL below:
run `pytest -m e2e tests/e2e/test_media.py tests/e2e/test_files.py` in that order.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from relaygpu import IdempotencyKeyReusedError
from relaygpu._util import random_uuid

KLING = "KlingTeam/v3-T2V"
# Results of the A1 runs, reused as real inputs by tests/e2e/test_files.py (A3 motion control) when it runs after this file.
VIDEO_URL: str | None = None
IMAGE_URL: str | None = None


def test_a8_same_idempotency_key_same_task_id_second_replayed_changed_body_idempotency_key_reused_error(
    counted: Callable[..., Any],
) -> None:
    c = counted()
    key = f"sdk-e2e-py-{random_uuid()}"
    body = {"prompt": "A red ball rolling slowly across a wooden table", "duration": 3, "quality_mode": "std", "sound": False}
    first = c.relay.video.generate(KLING, body, idempotency_key=key)
    second = c.relay.video.generate(KLING, body, idempotency_key=key)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["task_id"] == first["task_id"]
    with pytest.raises(IdempotencyKeyReusedError) as ei:
        c.relay.video.generate(KLING, {**body, "prompt": "A blue ball"}, idempotency_key=key)
    assert (ei.value.status, ei.value.code) == (422, "IDEMPOTENCY_KEY_REUSED")
    submits = c.posts()
    assert len(submits) == 3
    assert all(s.idem == key for s in submits)


def test_a1_a8_a_generated_key_rides_the_async_video_submit_wait_long_polls_in_at_most_5_requests(counted: Callable[..., Any]) -> None:
    c = counted()
    seen: list[str] = []
    t = c.relay.video.generate(
        KLING,
        {"prompt": "A paper boat on a calm pond", "duration": 3, "quality_mode": "std", "sound": False},
        wait=True,
        on_progress=lambda p: seen.append(p["status"]),
    )
    assert t["status"] == "completed"
    assert re.match(r"^https://", (t.get("result") or {}).get("urls", [""])[0])
    submit = next(x for x in c.calls if x.method == "POST")
    assert submit.idem and re.match(r"^[0-9a-f-]{36}$", submit.idem)
    polls = [x for x in c.calls if "/v2/tasks/" in x.url]
    assert all(re.search(r"[?&]wait=\d+", x.url) for x in polls)
    assert len(polls) <= 5  # A1: each long-poll is held ≤ 30 s
    assert seen[-1] == "completed"
    global VIDEO_URL
    VIDEO_URL = t["result"]["urls"][0]
    print(f"A1 video: task {t['task_id']} elapsed {t['elapsed_seconds']}s, {len(polls)} poll requests, transitions {'>'.join(seen)}")


def test_a1_image_sync_and_tts_carry_no_idempotency_key_asr_takes_bytes_through_the_implicit_upload(counted: Callable[..., Any]) -> None:
    c = counted()
    img = c.relay.image.generate("Qwen/qwen-image", {"prompt": "A small red apple on a white table", "size": "512x512"})
    assert len(img.images) > 0
    assert img.images[0].url and img.images[0].url.startswith("https://")
    assert len(img.images[0].to_bytes()) > 0
    global IMAGE_URL
    IMAGE_URL = img.images[0].url
    print(f"A1 image: {len(img.images)} RelayImage(s)")

    tts = c.relay.audio.speech("Qwen/qwen3-tts-flash", {"input": "Hello from the Relay Python SDK.", "voice": "Cherry"})
    assert tts["audio_url"].startswith("https://")
    clip = httpx.get(tts["audio_url"], follow_redirects=True, timeout=60).content  # a plain GET: no Relay credential
    assert len(clip) > 0

    asr = c.relay.audio.transcribe("openai/whisper-1", {"audio_url": clip})
    assert isinstance(asr["text"], str)

    inference = [x for x in c.posts() if "/v2/files" not in x.url]
    assert len(inference) == 3
    assert all(x.idem is None for x in inference)
    assert len([x for x in c.posts() if "/v2/files" in x.url]) == 1
    # The implicit relay1h upload of the clip: delete it (test hygiene; it would expire in 1 h anyway).
    sent = json.loads(next(x for x in c.posts() if "/v2/audio/asr/" in x.url).body)["audio_url"]
    up = next((f for f in c.relay.files.list(source="upload", limit=5)["files"] if f["url"] == sent), None)
    if up is not None:
        c.relay.files.delete(up["file_id"])
