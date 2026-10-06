"""Port of test/unit/models.test.ts: models.get (path, keyless, 5-min cache, uncached failure), models.list, and the
keyless pricing / tiers / health reads."""

from __future__ import annotations

from typing import Any

import pytest

from relaygpu import ModelNotFoundError
from tests.helpers import DETAILS, MODELS, PRICING, Mock, VirtualClock, detail, json_reply, maybe


class TestModelsGet:
    async def test_puts_the_slash_raw_in_the_path_sends_no_key_caches_for_one_fetch_per_name(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"))
        relay = make(m)
        a = await maybe(relay.models.get("KlingTeam/v3-T2V"))
        b = await maybe(relay.models.get("KlingTeam/v3-T2V"))
        assert a is b
        assert len(m.calls) == 1
        assert m.calls[0].url == "http://relay.test/v2/models/KlingTeam/v3-T2V"
        assert "x-api-key" not in m.calls[0].headers
        assert {k: a["endpoint"][k] for k in ("path", "model_in_body", "async_default")} == {
            "path": "/v2/video/kling-3/t2v",
            "model_in_body": False,
            "async_default": True,
        }

    async def test_keeps_dots_and_the_slash_literal_flux2_klein_4b(self, make: Any) -> None:
        m = Mock(detail("black-forest-labs/FLUX.2-klein-4B"))
        await maybe(make(m).models.get("black-forest-labs/FLUX.2-klein-4B"))
        assert m.calls[0].url == "http://relay.test/v2/models/black-forest-labs/FLUX.2-klein-4B"

    async def test_an_unknown_name_is_the_typed_model_not_found_error_and_the_failure_is_not_cached(self, make: Any) -> None:
        m = Mock(detail("nope/nope"), detail("nope/nope"))
        relay = make(m)
        with pytest.raises(ModelNotFoundError) as ei:
            await maybe(relay.models.get("nope/nope"))
        e = ei.value
        assert (e.status, e.code, e.request_id) == (404, "MODEL_NOT_FOUND", DETAILS["nope/nope"]["body"]["error"]["request_id"])
        with pytest.raises(ModelNotFoundError):
            await maybe(relay.models.get("nope/nope"))
        assert len(m.calls) == 2

    async def test_the_cache_expires_after_five_minutes(self, make: Any, clock: VirtualClock) -> None:
        """Python addition: the 5-minute TTL is per client and per name."""
        m = Mock(detail("KlingTeam/v3-T2V"), detail("KlingTeam/v3-T2V"), detail("Qwen/qwen-image"))
        relay = make(m)
        await maybe(relay.models.get("KlingTeam/v3-T2V"))
        clock.now += 299.0
        await maybe(relay.models.get("KlingTeam/v3-T2V"))
        assert len(m.calls) == 1
        clock.now += 1.0
        await maybe(relay.models.get("KlingTeam/v3-T2V"))
        assert len(m.calls) == 2
        await maybe(relay.models.get("Qwen/qwen-image"))
        assert len(m.calls) == 3


class TestModelsList:
    async def test_flattens_auto_into_unique_rows_and_filters_by_tag(self, make: Any) -> None:
        m = Mock(json_reply(200, MODELS), json_reply(200, MODELS))
        relay = make(m)
        rows = await maybe(relay.models.list())
        names = [r["name"] for r in rows]
        assert len(set(names)) == len(names)
        assert "KlingTeam/v3-T2V" in names
        assert "Qwen/qwen-image" in names
        videos = await maybe(relay.models.list(tag="text-to-video"))
        assert len(videos) > 0
        assert all(r["tag"] == "text-to-video" for r in videos)
        assert "KlingTeam/v3-T2V" in [r["name"] for r in videos]
        assert m.calls[0].url == "http://relay.test/v2/models"


class TestPricingTiersHealth:
    async def test_are_keyless_gets(self, make: Any) -> None:
        m = Mock(
            json_reply(200, PRICING), json_reply(200, {"tiers": []}), json_reply(200, {"status": "ok", "version": "2", "commit": "abc"})
        )
        relay = make(m)
        assert (await maybe(relay.pricing.get()))["media_storage"]["relay1d"] == 0.0005
        await maybe(relay.tiers.list())
        assert (await maybe(relay.health()))["status"] == "ok"
        assert [c.url for c in m.calls] == ["http://relay.test/v2/pricing", "http://relay.test/v2/tiers", "http://relay.test/v2/health"]
        assert all("x-api-key" not in c.headers for c in m.calls)
