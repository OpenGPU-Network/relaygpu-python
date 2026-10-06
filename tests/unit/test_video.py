"""Port of test/unit/video.test.ts. The TS type-level test (envelope vs TaskStatus return, Blob in ``*_url``) has no
runtime twin beyond the annotations (checked by mypy); file values in ``*_url`` fields are package (b)'s tests."""

from __future__ import annotations

import re
from typing import Any

import pytest

from relaygpu import RelayError, TaskFailedError
from tests.helpers import VIDEO_KLING, Mock, detail, detail_with, json_reply, maybe, task


def accepted() -> Any:
    return json_reply(202, VIDEO_KLING["accepted"]["body"], {"x-request-id": "rid"})


class TestVideoGenerate:
    async def test_returns_the_202_envelope_by_default_route_path_from_models_get_no_model_in_body(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"), accepted())
        r = await maybe(
            make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "ball", "duration": 5, "quality_mode": "std", "sound": False})
        )
        assert r == {**VIDEO_KLING["accepted"]["body"], "replayed": False, "request_id": "rid"}
        assert (m.calls[1].method, m.calls[1].url) == ("POST", "http://relay.test/v2/video/kling-3/t2v")
        assert m.calls[1].body == {"prompt": "ball", "duration": 5, "quality_mode": "std", "sound": False, "async": True}
        assert len(m.calls) == 2

    async def test_wait_true_long_polls_and_returns_the_completed_task_status_on_progress_on_transitions(self, make: Any) -> None:
        m = Mock(
            detail("KlingTeam/v3-T2V"), accepted(), task("running", elapsed_seconds=30), json_reply(200, VIDEO_KLING["completed"]["body"])
        )
        seen: list[str] = []
        t = await maybe(
            make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "ball"}, wait=True, on_progress=lambda p: seen.append(p["status"]))
        )
        assert t == VIDEO_KLING["completed"]["body"]
        assert re.match(r"^https://cdn\.relaygpu\.com/", t["result"]["urls"][0])
        assert seen == ["running", "completed"]
        assert all(c.url.endswith("?wait=30") for c in m.calls[2:])

    async def test_wait_true_on_a_failed_task_throws_task_failed_error(self, make: Any) -> None:
        m = Mock(
            detail("KlingTeam/v3-T2V"),
            accepted(),
            task("failed", error="upstream timed out", error_code="UPSTREAM_TIMEOUT", error_detail=None),
        )
        with pytest.raises(TaskFailedError) as ei:
            await maybe(make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "x"}, wait=True))
        assert ei.value.code == "UPSTREAM_TIMEOUT"

    async def test_motion_control_resolves_its_own_route_video_url_is_the_reference_field(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-Motion-Control"), accepted())
        await maybe(
            make(m).video.generate(
                "KlingTeam/v3-Motion-Control",
                {"image_url": "https://x.test/i.png", "video_url": "https://x.test/v.mp4", "character_orientation": "image"},
            )
        )
        assert m.calls[1].url == "http://relay.test/v2/video/kling-3/motion-control"
        assert m.calls[1].body["video_url"] == "https://x.test/v.mp4"
        assert m.calls[1].body["async"] is True

    async def test_an_inline_answer_to_the_async_submit_is_a_relay_error(self, make: Any) -> None:
        """Python addition (TS branch untested there): a 200 to ``async: true`` means the contract moved."""
        m = Mock(detail_with("KlingTeam/v3-T2V", {}), json_reply(200, {"urls": ["https://x.test/v.mp4"]}, {"x-request-id": "rid-200"}))
        with pytest.raises(RelayError) as ei:
            await maybe(make(m).video.generate("KlingTeam/v3-T2V", {"prompt": "x"}))
        assert str(ei.value) == "Model 'KlingTeam/v3-T2V' answered inline to an async submit; use relay.run() for this model"
        assert ei.value.request_id == "rid-200"
