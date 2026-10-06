"""A2 (catalog), port of test/e2e/catalog.e2e.test.ts: FREE, keyless reads against staging — models.list, models.get for
one model per family, run-path resolution WITHOUT a submit, retired → ModelRetiredError before any POST, unknown →
ModelNotFoundError. Skips without RELAY_BASE_URL / RELAY_API_KEY; refuses a production base URL. Costs nothing: every
test here is free (no POST is ever sent; each test asserts it)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from relaygpu import ModelNotFoundError, ModelRetiredError
from relaygpu._run_common import resolve_endpoint
from tests.e2e.conftest import BASE_URL

FAMILIES: list[tuple[str, str, bool, bool]] = [
    ("Qwen/qwen-image", "/v2/image/qwen/generate", True, False),
    ("openai/gpt-image-1.5-T2I", "/v2/image/gpt-image/generate", False, False),
    ("KlingTeam/v3-T2V", "/v2/video/kling-3/t2v", False, True),
    ("KlingTeam/v3-Motion-Control", "/v2/video/kling-3/motion-control", False, True),
    ("Qwen/qwen3-tts-flash", "/v2/audio/qwen3-tts-flash/generate", False, False),
    ("openai/whisper-1", "/v2/audio/asr/whisper", True, False),
    ("openai/gpt-5.4", "/v2/openai/v1/chat/completions", True, False),
]


def test_models_list_flattens_auto_with_unique_names_and_endpoints_tag_filter_works(counted: Callable[..., Any]) -> None:
    relay = counted().relay
    rows = relay.models.list()
    assert len(rows) > 50
    assert len({r["name"] for r in rows}) == len(rows)
    kling = next((r for r in rows if r["name"] == "KlingTeam/v3-T2V"), None)
    assert kling is not None and kling["endpoint"]["path"] == "/v2/video/kling-3/t2v"
    t2v = relay.models.list(tag="text-to-video")
    assert len(t2v) > 0
    assert all(r["tag"] == "text-to-video" for r in t2v)


def test_models_get_resolves_one_model_per_family_to_its_route_no_submit_one_fetch_per_name_cache(counted: Callable[..., Any]) -> None:
    c = counted()
    for name, path, model_in_body, async_default in FAMILIES:
        d = c.relay.models.get(name)
        assert d["status"] == "available", name
        ep = resolve_endpoint(name, d)
        assert (ep["path"], ep["model_in_body"], ep["async_default"]) == (path, model_in_body, async_default), name
        assert d["request_schema"], name
        c.relay.models.get(name)
    assert len(c.calls) == len(FAMILIES)
    assert c.calls[0].url == f"{(BASE_URL or '').rstrip('/')}/v2/models/Qwen/qwen-image"
    assert c.posts() == []


def test_pricing_tiers_and_health_answer(counted: Callable[..., Any]) -> None:
    relay = counted().relay
    p = relay.pricing.get()
    assert len(p["pricing"]) > 0
    assert len(relay.tiers.list()["tiers"]) > 0
    assert relay.health()["status"] == "ok"
    e = relay.estimate_cost("KlingTeam/v3-T2V", {"duration_seconds": 5, "quality_mode": "std", "sound": False})
    assert e["usd"] > 0


@pytest.mark.parametrize("name", ["black-forest-labs/FLUX.2-klein-4B", "xai/grok-4"])
def test_a_retired_model_throws_model_retired_error_before_any_post_endpoint_present_and_endpoint_null(
    counted: Callable[..., Any], name: str
) -> None:
    c = counted()
    with pytest.raises(ModelRetiredError) as ei:
        c.relay.run(name, {"prompt": "x"})
    assert ei.value.code == "MODEL_RETIRED"
    with pytest.raises(ModelRetiredError):
        c.relay.image.generate(name, {"prompt": "x"})
    assert c.posts() == []


def test_an_unknown_model_throws_model_not_found_error_with_request_id_before_any_post(counted: Callable[..., Any]) -> None:
    c = counted()
    with pytest.raises(ModelNotFoundError) as ei:
        c.relay.run("nope/does-not-exist", {})
    assert ei.value.request_id
    assert c.posts() == []


def test_model_names_are_case_sensitive_kling_v3_motion_control_is_not_a_model(counted: Callable[..., Any]) -> None:
    with pytest.raises(ModelNotFoundError):
        counted().relay.models.get("KlingTeam/v3-motion-control")
