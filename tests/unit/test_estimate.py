"""Port of test/unit/estimate.test.ts (F8) on the /v2/pricing capture of 2026-10-06 (staging). ``toBeCloseTo(x, d)``
is ``abs(diff) < 10**-d / 2``. Python additions: ``basis`` strings byte-identical to what the TS SDK prints (pinned
from a node run of src/estimate.ts on the same fixture), and JavaScript number formatting."""

from __future__ import annotations

from typing import Any

import pytest

from relaygpu import RelayError
from relaygpu._estimate import js_number, money
from tests.helpers import PRICING, Mock, json_reply, maybe


def close(d: int) -> float:
    return 10**-d / 2


async def est(make: Any, model: str, usage: dict[str, Any]) -> Any:
    m = Mock(json_reply(200, PRICING))
    r = await maybe(make(m).estimate_cost(model, usage))
    assert len(m.calls) == 1
    assert m.calls[0].url == "http://relay.test/v2/pricing"
    return r


class TestEstimateCost:
    async def test_per_token_input_output_cached_subset_anthropic_cache_writes(self, make: Any) -> None:
        # openai.openai/gpt-5.5: 5 / 30 / 0.5 cached
        assert (await est(make, "openai/gpt-5.5", {"input_tokens": 200_000, "output_tokens": 100_000}))["usd"] == pytest.approx(
            0.2 * 5 + 0.1 * 30, abs=close(8)
        )
        assert (await est(make, "openai/gpt-5.5", {"input_tokens": 200_000, "cached_input_tokens": 80_000}))["usd"] == pytest.approx(
            0.12 * 5 + 0.08 * 0.5, abs=close(8)
        )
        # anthropic/claude-fable-5-1: 10 / 50 / 0.25 cached / 12.5 5m-write / 20 1h-write
        r = await est(
            make,
            "anthropic/claude-fable-5-1",
            {"input_tokens": 1_000_000, "cache_write_5m_input_tokens": 200_000, "cache_write_1h_input_tokens": 100_000, "output_tokens": 0},
        )
        assert r["usd"] == pytest.approx(0.7 * 10 + 0.2 * 12.5 + 0.1 * 20, abs=close(8))
        assert "5m-write" in r["basis"]

    async def test_per_token_long_context_reprices_the_whole_request_above_the_threshold(self, make: Any) -> None:
        # gpt-5.5 threshold 278528 → long 10 / 45
        r = await est(make, "openai/gpt-5.5", {"input_tokens": 300_000, "output_tokens": 10_000})
        assert r["usd"] == pytest.approx(0.3 * 10 + 0.01 * 45, abs=close(8))
        assert "long context" in r["basis"]

    async def test_per_image_flat_and_resolution_tiers_unknown_tier_falls_back_to_the_cheapest_without_a_default(self, make: Any) -> None:
        assert (await est(make, "Qwen/qwen-image", {"image_count": 2}))["usd"] == pytest.approx(0.072, abs=close(8))
        assert (await est(make, "google/gemini-3-pro-image", {"image_count": 1, "resolution_tier": "4K"}))["usd"] == pytest.approx(
            0.24, abs=close(8)
        )
        g = await est(make, "openai/gpt-image-2", {"image_count": 1, "resolution_tier": "1024x1024"})
        assert g["usd"] == pytest.approx(0.211, abs=close(8))
        assert "1024x1024" in g["basis"]

    async def test_per_second_video_kling_quality_sound_grid_default_cell_resolution_map(self, make: Any) -> None:
        k = await est(make, "KlingTeam/v3-T2V", {"duration_seconds": 5, "quality_mode": "std", "sound": False})
        assert k["usd"] == pytest.approx(0.42, abs=close(8))
        assert k["basis"] == "per_second_video: 5 s × $0.084 (std|silent)"
        assert (await est(make, "KlingTeam/v3-T2V", {"duration_seconds": 5}))["usd"] == pytest.approx(
            0.84, abs=close(8)
        )  # default = most expensive
        assert (await est(make, "Wan-AI/Wan2.5-T2V", {"duration_seconds": 10, "resolution_tier": "1080p"}))["usd"] == pytest.approx(
            1, abs=close(8)
        )

    async def test_per_character_per_second_audio_per_media_token(self, make: Any) -> None:
        assert (await est(make, "Qwen/qwen3-tts-flash", {"character_count": 17}))["usd"] == pytest.approx(
            (17 * 0.0112) / 1000, abs=close(10)
        )
        assert (await est(make, "openai/whisper-1", {"duration_seconds": 60}))["usd"] == pytest.approx(0.006, abs=close(10))
        assert (await est(make, "ByteDance/doubao-seedance-2-0-260128", {"media_output_tokens": 1_000_000}))["usd"] == pytest.approx(
            7.14, abs=close(8)
        )

    async def test_adds_the_per_file_storage_fee_for_store_output_and_retention_from_media_storage(self, make: Any) -> None:
        r = await est(make, "Qwen/qwen-image", {"image_count": 2, "store_output": "relay7d"})
        assert r["usd"] == pytest.approx(0.072 + 2 * 0.001, abs=close(8))
        assert "store_output relay7d" in r["basis"]
        assert (await est(make, "Qwen/qwen-image", {"image_count": 1, "store_output": "provider", "retention": "relay1h"}))[
            "usd"
        ] == pytest.approx(0.036, abs=close(8))
        assert (await est(make, "Qwen/qwen-image", {"image_count": 1, "retention": "relay30d"}))["usd"] == pytest.approx(
            0.036 + 0.003, abs=close(8)
        )

    async def test_mode_selects_the_row_opengpu_rows_are_separate(self, make: Any) -> None:
        assert (await est(make, "Qwen/Qwen3.5-397B-A17B-FP8", {"mode": "opengpu", "input_tokens": 1_000_000}))["usd"] == pytest.approx(
            0.16, abs=close(8)
        )

    async def test_throws_a_relay_error_for_an_unknown_model_an_unoffered_sku_an_unknown_billing_type(self, make: Any) -> None:
        with pytest.raises(RelayError, match=r'no direct pricing row for "nope/nope"'):
            await est(make, "nope/nope", {})
        with pytest.raises(RelayError, match="not offered"):
            await est(make, "Qwen/qwen-image", {"store_output": "relay90d"})
        m = Mock(json_reply(200, {"pricing": [{"mode": "direct", "model": "x.a/b", "billing_type": "per_galaxy"}], "total_count": 1}))
        with pytest.raises(RelayError, match="per_galaxy"):
            await maybe(make(m).estimate_cost("a/b", {}))


# Every (model, usage) → (usd, basis) below was printed by the TS SDK (relaygpu-node src/estimate.ts via vite-node, 2026-10-06)
# on the same fixture; the Python port must answer the same float and the same bytes.
TS_PINNED: list[tuple[str, dict[str, Any], float, str]] = [
    ("openai/gpt-5.5", {"input_tokens": 200000, "output_tokens": 100000}, 4, "per_token: 200000 in × $5/1M + 100000 out × $30/1M"),
    (
        "openai/gpt-5.5",
        {"input_tokens": 200000, "cached_input_tokens": 80000},
        0.64,
        "per_token: 120000 in × $5/1M + 80000 cached × $0.5/1M + 0 out × $30/1M",
    ),
    (
        "openai/gpt-5.5",
        {"input_tokens": 300000, "output_tokens": 10000},
        3.45,
        "per_token (long context): 300000 in × $10/1M + 10000 out × $45/1M",
    ),
    (
        "openai/gpt-5.5",
        {"input_tokens": 300000, "cached_input_tokens": 1000, "output_tokens": 7},
        2.991315,
        "per_token (long context): 299000 in × $10/1M + 1000 cached × $1/1M + 7 out × $45/1M",
    ),
    (
        "anthropic/claude-fable-5-1",
        {"input_tokens": 1000000, "cache_write_5m_input_tokens": 200000, "cache_write_1h_input_tokens": 100000, "output_tokens": 0},
        11.5,
        "per_token: 700000 in × $10/1M + 0 cached × $0.25/1M + 200000 5m-write × $12.5/1M + 100000 1h-write × $20/1M + 0 out × $50/1M",
    ),
    (
        "anthropic/claude-fable-5-1",
        {"input_tokens": 123457, "cached_input_tokens": 3333, "output_tokens": 777},
        1.24092325,
        "per_token: 120124 in × $10/1M + 3333 cached × $0.25/1M + 777 out × $50/1M",
    ),
    ("Qwen/qwen-image", {"image_count": 2}, 0.072, "per_image: 2 × $0.036"),
    (
        "Qwen/qwen-image",
        {"image_count": 2, "store_output": "relay7d"},
        0.074,
        "per_image: 2 × $0.036 + store_output relay7d: 2 file × $0.001",
    ),
    (
        "Qwen/qwen-image",
        {"image_count": 1, "retention": "relay30d", "store_output": "relay1d"},
        0.0395,
        "per_image: 1 × $0.036 + store_output relay1d: 1 file × $0.0005 + retention relay30d: 1 file × $0.003",
    ),
    (
        "Qwen/qwen-image",
        {"image_count": 3, "file_count": 1, "store_output": "relay1d"},
        0.1085,
        "per_image: 3 × $0.036 + store_output relay1d: 1 file × $0.0005",
    ),
    ("google/gemini-3-pro-image", {"image_count": 1, "resolution_tier": "4K"}, 0.24, "per_image: 1 × $0.24 (4K)"),
    ("google/gemini-3-pro-image", {"image_count": 2}, 0.28, "per_image: 2 × $0.14 (1K)"),
    ("openai/gpt-image-2", {"image_count": 1, "resolution_tier": "1024x1024"}, 0.211, "per_image: 1 × $0.211 (1024x1024)"),
    (
        "openai/gpt-image-2",
        {"image_count": 1, "resolution_tier": "nope", "input_tokens": 1000, "output_tokens": 5000},
        0.15,
        "per_image: 1 × $0.15 (1024x768)",
    ),
    (
        "KlingTeam/v3-T2V",
        {"duration_seconds": 5, "quality_mode": "std", "sound": False},
        0.42,
        "per_second_video: 5 s × $0.084 (std|silent)",
    ),
    ("KlingTeam/v3-T2V", {"duration_seconds": 5}, 0.84, "per_second_video: 5 s × $0.168 (default)"),
    (
        "KlingTeam/v3-T2V",
        {"duration_seconds": 7, "quality_mode": "pro", "sound": True},
        1.176,
        "per_second_video: 7 s × $0.168 (pro|sound)",
    ),
    ("Wan-AI/Wan2.5-T2V", {"duration_seconds": 10, "resolution_tier": "1080p"}, 1, "per_second_video: 10 s × $0.1 (1080p)"),
    ("Qwen/qwen3-tts-flash", {"character_count": 17}, 0.0001904, "per_character: 17 chars × $0.0112/1K"),
    ("openai/whisper-1", {"duration_seconds": 60}, 0.006, "per_second_audio: 60 s × $0.0001"),
    ("openai/whisper-1", {"duration_seconds": 2.37}, 0.000237, "per_second_audio: 2.37 s × $0.0001"),
    (
        "ByteDance/doubao-seedance-2-0-260128",
        {"media_output_tokens": 1000000},
        7.14,
        "per_media_token: 0 in × $7.14/1M + 1000000 out × $7.14/1M",
    ),
    (
        "ByteDance/doubao-seedance-2-0-260128",
        {"media_input_tokens": 12345, "media_output_tokens": 67890, "sound": True},
        0.5728779,
        "per_media_token: 12345 in × $7.14/1M + 67890 out × $7.14/1M",
    ),
    (
        "Qwen/Qwen3.5-397B-A17B-FP8",
        {"mode": "opengpu", "input_tokens": 1000000},
        0.16,
        "per_token: 1000000 in × $0.16/1M + 0 out × $0.96/1M",
    ),
    (
        "KlingTeam/kling-video-o1",
        {"duration_seconds": 5, "quality_mode": "std", "has_ref": True},
        0.63,
        "per_second_video: 5 s × $0.126 (std|w-ref)",
    ),
    (
        "ByteDance/doubao-seedance-2-5-260628",
        {"media_output_tokens": 333333, "media_input_tokens": 1, "sound": False},
        3.33667334,
        "per_media_token: 1 in × $10.01/1M + 333333 out × $10.01/1M",
    ),
]


class TestByteIdenticalToTs:
    @pytest.mark.parametrize(("model", "usage", "usd", "basis"), TS_PINNED, ids=[f"{p[0]}-{i}" for i, p in enumerate(TS_PINNED)])
    async def test_usd_and_basis_match_the_ts_sdk(self, make: Any, model: str, usage: dict[str, Any], usd: float, basis: str) -> None:
        r = await est(make, model, usage)
        assert (r["usd"], r["basis"]) == (usd, basis)

    def test_numbers_print_as_javascript_prints_them(self) -> None:
        assert js_number(20.0) == "20"
        cases = {
            20: "20",
            1.5: "1.5",
            0.1 + 0.2: "0.30000000000000004",
            1e-7: "1e-7",
            5e-7: "5e-7",
            1e21: "1e+21",
            123456789: "123456789",
            0.000001: "0.000001",
        }
        assert {k: js_number(k) for k in cases} == cases
        assert money(0.084) == "$0.084"
        assert money(1 / 3) == "$0.333333"
        assert money(1234565) == "$1234570"  # toPrecision ties go away from zero
        assert money(0.0000012345678) == "$0.00000123457"
