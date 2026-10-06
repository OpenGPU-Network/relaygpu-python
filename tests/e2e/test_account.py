"""Port of test/e2e/account.e2e.test.ts. FREE e2e against staging: account reads, a JWT client, a throwaway key's
lifecycle (key_budget 0 → topup 0.01 moves the key's cap, not the wallet), the webhook secret (never printed).
Never calls rotate. A7: ``keys.list_all`` iterates; ``account.credits`` returns the balance."""

from __future__ import annotations

import contextlib
import time

import httpx
import pytest

from relaygpu import AsyncRelay, KeyNotFoundError, Relay
from tests.e2e.conftest import API_KEY, BASE_URL, env

EMAIL = env("RELAY_TEST_EMAIL")
PASSWORD = env("RELAY_TEST_PASSWORD")
CUSTOMER_ID = env("RELAY_CUSTOMER_ID")


@pytest.fixture
def relay() -> Relay:
    return Relay(api_key=API_KEY, base_url=BASE_URL)


def test_credits_profile_usage_pricing_allowlist_read_with_the_superkey(relay: Relay) -> None:
    credits = relay.account.credits()
    assert isinstance(credits["balance"], (int, float))
    profile = relay.account.profile()
    assert (profile.get("tier_details") or {}).get("is_custom") is True
    if CUSTOMER_ID:
        assert profile.get("customer_id") == CUSTOMER_ID
    usage = relay.account.usage(limit=5)
    assert isinstance(usage["keys"], list)
    pricing = relay.account.pricing()
    assert isinstance(pricing["pricing"], list)
    allow = relay.account.model_allowlist()
    assert "model_allowlist" in allow
    start = int(time.time()) - 86_400
    assert relay.account.usage_timeseries(start_time=start)["object"]
    assert relay.account.metrics(start_time=start)["object"]


async def test_async_client_reads_credits() -> None:
    async with AsyncRelay(api_key=API_KEY, base_url=BASE_URL) as relay:
        assert isinstance((await relay.account.credits())["balance"], (int, float))


def test_keys_list_shows_the_superkey_masked(relay: Relay) -> None:
    page = relay.keys.list(is_superkey=True, status="active", limit=1)
    assert page["total"] == 1
    assert page["keys"][0].get("is_superkey") is True
    assert page["keys"][0]["key"].endswith("...")


def test_keys_list_all_iterates(relay: Relay) -> None:
    total = relay.keys.list(limit=1)["total"]
    keys = list(relay.keys.list_all(limit=2))  # a small page forces the cursor to be followed
    ids = [k.get("key_id") for k in keys]
    assert len(ids) == total >= 1
    assert len(set(ids)) == len(ids)


def test_webhooks_secret_returns_a_whsec_never_printed_deliveries_list_reads(relay: Relay) -> None:
    s = relay.webhooks.secret()
    assert s["secret"].startswith("whsec_")
    page = relay.webhooks.deliveries.list(limit=5)
    assert isinstance(page["data"], list)


def test_throwaway_key_mint_get_rename_topup_revoke_delete(relay: Relay) -> None:
    name = f"sdk-py-e2e-{int(time.time() * 1000)}"
    created = relay.keys.create(name=name, key_budget=0)
    key_id = created["key_id"]
    deleted = False
    try:
        assert created["key"].startswith("relay_sk_")
        assert created["is_superkey"] is False
        assert created["key_budget"] == 0
        assert relay.keys.get(key_id)["name"] == name
        relay.keys.rename(key_id, f"{name}-renamed")
        assert relay.keys.list(name=f"{name}-renamed")["keys"][0].get("key_id") == key_id
        top = relay.keys.topup(key_id, 0.01)
        assert top["new_budget"] == pytest.approx(0.01, abs=1e-6)
        relay.keys.revoke(key_id)
        assert relay.keys.get(key_id)["status"] == "revoked"
        relay.keys.delete(key_id)
        deleted = True
        with pytest.raises(KeyNotFoundError) as ei:
            relay.keys.get(key_id)
        assert ei.value.code == "KEY_NOT_FOUND"
    finally:
        if not deleted:
            for step in (relay.keys.revoke, relay.keys.delete):
                with contextlib.suppress(Exception):
                    step(key_id)


@pytest.mark.skipif(not EMAIL or not PASSWORD, reason="RELAY_TEST_EMAIL / RELAY_TEST_PASSWORD absent")
def test_a_jwt_client_reads_credits() -> None:
    res = httpx.post(f"{BASE_URL}/v2/auth/email", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert res.status_code == 200
    jwt = res.json()["access_token"]
    via_jwt = Relay(jwt=jwt, base_url=BASE_URL)
    assert isinstance(via_jwt.account.credits()["balance"], (int, float))
    # credits history is JWT-only
    assert via_jwt.account.credits_history(limit=1)
