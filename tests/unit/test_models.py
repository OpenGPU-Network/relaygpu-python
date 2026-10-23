"""Port of test/unit/models.test.ts: models.get (path, credential, 5-min cache, uncached failure), models.list, and the
pricing / tiers / health reads (keyless without a credential)."""

from __future__ import annotations

from typing import Any

import pytest

from relaygpu import AuthenticationError, InsufficientCreditsError, ModelNotFoundError
from tests.helpers import DETAILS, KEY, MODELS, PRICING, Mock, VirtualClock, detail, json_reply, maybe, relay_error


class TestModelsGet:
    async def test_puts_the_slash_raw_in_the_path_sends_the_clients_key_caches_for_one_fetch_per_name(self, make: Any) -> None:
        m = Mock(detail("KlingTeam/v3-T2V"))
        relay = make(m)
        a = await maybe(relay.models.get("KlingTeam/v3-T2V"))
        b = await maybe(relay.models.get("KlingTeam/v3-T2V"))
        assert a is b
        assert len(m.calls) == 1
        assert m.calls[0].url == "http://relay.test/v2/models/KlingTeam/v3-T2V"
        assert m.calls[0].headers["x-api-key"] == KEY
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
    async def test_are_gets_of_the_public_catalog_without_a_credential(self, make: Any) -> None:
        m = Mock(
            json_reply(200, PRICING), json_reply(200, {"tiers": []}), json_reply(200, {"status": "ok", "version": "2", "commit": "abc"})
        )
        relay = make(m, api_key=None)
        assert (await maybe(relay.pricing.get()))["media_storage"]["relay1d"] == 0.0005
        await maybe(relay.tiers.list())
        assert (await maybe(relay.health()))["status"] == "ok"
        assert [c.url for c in m.calls] == ["http://relay.test/v2/pricing", "http://relay.test/v2/tiers", "http://relay.test/v2/health"]
        assert all("x-api-key" not in c.headers for c in m.calls)


def _four_reads() -> Mock:
    return Mock(
        json_reply(200, MODELS),
        detail("KlingTeam/v3-T2V"),
        json_reply(200, PRICING),
        json_reply(200, {"tiers": []}),
    )


async def _read_all(relay: Any) -> None:
    await maybe(relay.models.list())
    await maybe(relay.models.get("KlingTeam/v3-T2V"))
    await maybe(relay.pricing.get())
    await maybe(relay.tiers.list())


class TestCatalogReadsAsTheAccount:
    async def test_a_keyed_client_sends_x_api_key_on_all_four_reads(self, make: Any) -> None:
        m = _four_reads()
        await _read_all(make(m))
        assert [c.url.removeprefix("http://relay.test") for c in m.calls] == [
            "/v2/models",
            "/v2/models/KlingTeam/v3-T2V",
            "/v2/pricing",
            "/v2/tiers",
        ]
        assert all(c.headers["x-api-key"] == KEY and "authorization" not in c.headers for c in m.calls)

    async def test_a_jwt_client_sends_bearer_on_all_four_reads(self, make: Any) -> None:
        m = _four_reads()
        await _read_all(make(m, api_key=None, jwt="a.b.c"))
        assert all(c.headers["authorization"] == "Bearer a.b.c" and "x-api-key" not in c.headers for c in m.calls)

    async def test_a_client_with_no_credential_sends_none(self, make: Any) -> None:
        m = _four_reads()
        await _read_all(make(m, api_key=None))
        assert len(m.calls) == 4
        assert all("x-api-key" not in c.headers and "authorization" not in c.headers for c in m.calls)

    async def test_the_task_poll_and_health_stay_credential_free_with_a_keyed_client(self, make: Any) -> None:
        m = Mock(json_reply(200, {"status": "queued"}), json_reply(200, {"status": "ok", "version": "2", "commit": "abc"}))
        relay = make(m)
        await maybe(relay.tasks.get("direct:1"))
        await maybe(relay.health())
        assert len(m.calls) == 2
        assert all("x-api-key" not in c.headers and "authorization" not in c.headers for c in m.calls)

    @pytest.mark.parametrize(
        ("status", "code", "exc"),
        [(401, "INVALID_API_KEY", AuthenticationError), (402, "INSUFFICIENT_CREDITS", InsufficientCreditsError)],
    )
    async def test_a_refused_or_underfunded_key_raises_its_typed_error_on_models_list(
        self, make: Any, status: int, code: str, exc: type[Exception]
    ) -> None:
        with pytest.raises(exc) as ei:
            await maybe(make(Mock(relay_error(status, code))).models.list())
        assert ei.value.status == status  # type: ignore[attr-defined]

    async def test_the_models_get_cache_is_per_client_a_keyed_and_a_keyless_client_do_not_share(self, make: Any) -> None:
        name = "KlingTeam/v3-T2V"
        m = Mock(detail(name), detail("nope/nope"))
        keyed = make(m)
        keyless = make(m, api_key=None)
        assert (await maybe(keyed.models.get(name)))["name"] == name
        assert await maybe(keyed.models.get(name)) is not None
        assert len(m.calls) == 1
        with pytest.raises(ModelNotFoundError):
            await maybe(keyless.models.get(name))
        assert len(m.calls) == 2
        assert "x-api-key" in m.calls[0].headers and "x-api-key" not in m.calls[1].headers
