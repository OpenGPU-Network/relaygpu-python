"""A3 (files): BILLED against staging — the lead runs this once (port of test/e2e/files.e2e.test.ts and the A3 half of
media.e2e.test.ts). Skips without RELAY_API_KEY / RELAY_BASE_URL; refuses a production base URL (tests/e2e/conftest.py).

Cost at 2026-10-06 list prices: one relay1d fee (0.0005) for the 20 MB upload (the keyed replay is not charged);
the motion-control submit is one Kling v3 Motion Control task (5 s std, not waited on) plus a free relay1h upload.

The server judges the type by the declared Content-Type only (no magic-byte check; a 4 KB ftyp-plus-padding stream
was accepted on staging 2026-10-06), so the 20 MB body is synthetic.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

SIZE = 20 * 1024 * 1024
CHUNK = 1024 * 1024
FTYP = bytes([0, 0, 0, 0x18]) + b"ftypisom" + bytes([0, 0, 2, 0]) + b"isommp41"


def mp4_stream() -> Iterator[bytes]:
    """An ftyp box + zero padding, yielded in 1 MB chunks (never held whole in memory)."""
    sent = 0
    while sent < SIZE:
        n = min(CHUNK, SIZE - sent)
        chunk = bytearray(n)
        if sent == 0:
            chunk[: len(FTYP)] = FTYP
        sent += n
        yield bytes(chunk)


def test_a3_a_20mb_mp4_stream_uploads_with_relay1d_and_a_keyed_replay_answers_the_same_file(counted: Callable[..., Any]) -> None:
    c = counted()
    relay = c.relay
    created: set[str] = set()
    try:
        key = f"sdk-py-e2e-a3-{int(time.time() * 1000)}"
        first = relay.files.upload(mp4_stream(), retention="relay1d", filename="a3.mp4", idempotency_key=key)
        created.add(first["file_id"])
        assert first["source"] == "upload"
        assert first["content_type"] == "video/mp4"
        assert first["size_bytes"] == SIZE
        assert first["retention"] == "relay1d"
        assert first["status"] == "ready"
        assert first["cost_usd"] == 0.0005
        assert first["url"].startswith("https://cdn.relaygpu.com/")

        replay = relay.files.upload(mp4_stream(), retention="relay1d", filename="a3.mp4", idempotency_key=key)
        created.add(replay["file_id"])
        assert replay["file_id"] == first["file_id"]
        assert replay["url"] == first["url"]

        uploads = [p for p in c.posts() if "/v2/files" in p.url]
        assert len(uploads) == 2
        assert all(p.idem == key for p in uploads)
        assert all(p.body == b"" for p in uploads)  # streamed: the request body was never buffered

        got = relay.files.get(first["file_id"])
        assert got["status"] == "ready"
        page = relay.files.list(source="upload", limit=20)
        assert any(f["file_id"] == first["file_id"] for f in page["files"])
        print(f"A3 upload: {first['file_id']} {first['size_bytes']} B relay1d cost_usd={first['cost_usd']}; replay same file_id")
    finally:
        for fid in created:
            relay.files.delete(fid)


# ---- A3 motion control: bytes in video_url → one implicit upload, then the submit with the link ----


def run_motion_control_a3(counted: Callable[..., Any], video_url: str, image_url: str) -> None:
    """The A3 motion-control half, reusable from tests/e2e/test_media.py after its A1 runs (as the TS suite does):
    ``video_url`` = an A1 Kling result link, ``image_url`` = an A1 image result link."""
    c = counted()
    relay = c.relay
    clip = httpx.get(video_url, follow_redirects=True, timeout=120).content  # bytes: no declared type, sniffed
    accepted = relay.video.generate(
        "KlingTeam/v3-Motion-Control",
        {"video_url": clip, "image_url": image_url, "character_orientation": "video", "duration": 5, "quality_mode": "std"},
    )
    assert accepted["task_id"].startswith(("direct:", "opengpu:"))
    uploads = [p for p in c.posts() if "/v2/files" in p.url]
    assert len(uploads) == 1
    submit = next(p for p in c.posts() if "/v2/video/kling-3/motion-control" in p.url)
    body = json.loads(submit.body)
    assert body["video_url"].startswith("https://")
    assert body["image_url"] == image_url
    # The implicit upload is relay1h; find its ledger row and delete it.
    rows = relay.files.list(source="upload", limit=5)["files"]
    row = next((f for f in rows if f["url"] == body["video_url"]), None)
    assert row is not None
    print(f"A3 motion control: task {accepted['task_id']}, implicit upload {row['file_id']} ({row['retention']}), 1 /v2/files POST")
    relay.files.delete(row["file_id"])


def _a1_outputs() -> tuple[str | None, str | None]:
    """The A1 results to reuse: RELAY_E2E_A3_VIDEO_URL / RELAY_E2E_A3_IMAGE_URL, else what test_media.py's A1 runs
    left in its module (VIDEO_URL / IMAGE_URL), when it ran first in this session."""
    video, image = os.environ.get("RELAY_E2E_A3_VIDEO_URL"), os.environ.get("RELAY_E2E_A3_IMAGE_URL")
    if video and image:
        return video, image
    try:
        from tests.e2e import test_media
    except ImportError:
        return None, None
    return getattr(test_media, "VIDEO_URL", None), getattr(test_media, "IMAGE_URL", None)


def test_a3_motion_control_with_bytes_in_video_url_uploads_once_and_submits_the_url(counted: Callable[..., Any]) -> None:
    video, image = _a1_outputs()
    if not (video and image):
        pytest.skip("A3 motion control reuses A1 outputs: run after tests/e2e/test_media.py A1, or set RELAY_E2E_A3_VIDEO_URL / _IMAGE_URL")
    run_motion_control_a3(counted, video, image)
