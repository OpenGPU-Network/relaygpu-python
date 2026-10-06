"""Port of test/unit/account.test.ts."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from relaygpu import AuthenticationError, PermissionDeniedError
from tests.helpers import Mock, json_reply, maybe, relay_error


def req(m: Mock, i: int) -> dict[str, Any]:
    u = urlsplit(m.calls[i].url)
    return {"method": m.calls[i].method, "path": u.path, "query": parse_qsl(u.query), "body": m.calls[i].body}


async def test_reads_hit_the_documented_paths(make: Any) -> None:
    m = Mock(*[json_reply(200, {}) for _ in range(6)])
    account = make(m, retry=False).account
    for call in (account.credits, account.pricing, account.profile, account.model_allowlist, account.usage, account.credits_history):
        await maybe(call())
    assert [f"{req(m, i)['method']} {req(m, i)['path']}" for i in range(6)] == [
        "GET /v2/customer/credits",
        "GET /v2/customer/pricing",
        "GET /v2/customer/profile",
        "GET /v2/customer/model-allowlist",
        "GET /v2/customer/usage",
        "GET /v2/customer/credits/history",
    ]
    assert req(m, 4)["query"] == []


async def test_query_encoding_scalars_nulls_dropped_arrays_repeated(make: Any) -> None:
    m = Mock(*[json_reply(200, {}) for _ in range(6)])
    account = make(m, retry=False).account
    await maybe(account.usage(period="7d", limit=50, starting_after=None))
    assert req(m, 0)["query"] == [("period", "7d"), ("limit", "50")]
    await maybe(account.usage_by_key("key_ab/c", from_="2026-10-01", to="2026-10-06"))
    assert req(m, 1)["path"] == "/v2/customer/usage/key_ab%2Fc"
    assert req(m, 1)["query"] == [("from", "2026-10-01"), ("to", "2026-10-06")]
    await maybe(account.usage_timeseries(start_time=1791000000, bucket_width="1h", group_by=["model", "key_id"], models=["a", "b"]))
    assert req(m, 2)["path"] == "/v2/customer/usage/timeseries"
    assert req(m, 2)["query"] == [
        ("start_time", "1791000000"),
        ("bucket_width", "1h"),
        ("group_by", "model"),
        ("group_by", "key_id"),
        ("models", "a"),
        ("models", "b"),
    ]
    await maybe(account.metrics(start_time=1, end_time=2, modes=["direct"]))
    assert req(m, 3)["path"] == "/v2/customer/metrics"
    assert req(m, 3)["query"] == [("start_time", "1"), ("end_time", "2"), ("modes", "direct")]
    await maybe(account.credits_history(limit=10, offset=20, action="topup"))
    assert req(m, 4)["query"] == [("limit", "10"), ("offset", "20"), ("action", "topup")]
    await maybe(account.usage(from_="2026-10-01", period=None))
    assert req(m, 5)["query"] == [("from", "2026-10-01")]


async def test_set_model_allowlist_patches_the_list_none_clears(make: Any) -> None:
    m = Mock(
        json_reply(200, {"status": "ok", "model_allowlist": ["direct.openai.openai/gpt-5.2"], "updated_at": "t"}),
        json_reply(200, {"status": "ok", "model_allowlist": None, "updated_at": "t"}),
    )
    account = make(m, retry=False).account
    await maybe(account.set_model_allowlist(["direct.openai.openai/gpt-5.2"]))
    r = req(m, 0)
    assert (r["method"], r["path"], r["body"]) == (
        "PATCH",
        "/v2/customer/model-allowlist",
        {"model_allowlist": ["direct.openai.openai/gpt-5.2"]},
    )
    await maybe(account.set_model_allowlist(("direct.a.b",)))
    assert req(m, 1)["body"] == {"model_allowlist": ["direct.a.b"]}
    m.push(json_reply(200, {}))
    await maybe(account.set_model_allowlist(None))
    assert req(m, 2)["body"] == {"model_allowlist": None}


async def test_f9_an_inference_keys_403_surfaces_as_permission_denied(make: Any) -> None:
    m = Mock(relay_error(403, None, {"detail": "Superkey or JWT required"}))
    with pytest.raises(PermissionDeniedError) as ei:
        await maybe(make(m, retry=False).account.credits())
    assert ei.value.status == 403
    assert len(m.calls) == 1


async def test_credits_history_with_a_superkey_401_surfaces_as_authentication_error(make: Any) -> None:
    with pytest.raises(AuthenticationError):
        await maybe(make(Mock(relay_error(401, None)), retry=False).account.credits_history())
