"""Port of test/unit/keys.test.ts."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from relaygpu import KeyBudgetExhaustedError, KeyNotFoundError, NotFoundError, PermissionDeniedError, RelayInternalError
from tests.helpers import Mock, collect, json_reply, maybe, relay_error


def req(m: Mock, i: int) -> dict[str, Any]:
    u = urlsplit(m.calls[i].url)
    return {"method": m.calls[i].method, "path": u.path, "query": dict(parse_qsl(u.query)), "body": m.calls[i].body}


def key(kid: str) -> dict[str, Any]:
    return {
        "key": "relay_sk_abcdefghijkl...",
        "key_id": kid,
        "name": kid,
        "status": "active",
        "created_at": "t",
        "last_used_at": None,
        "restrictions": {},
        "key_budget": None,
        "is_superkey": False,
        "system": False,
    }


def page(ids: list[str], nxt: str | None) -> dict[str, Any]:
    return {
        "total": 9,
        "max_allowed": 20,
        "limit": len(ids),
        "has_more": nxt is not None,
        "next_cursor": nxt,
        "keys": [key(i) for i in ids],
    }


class TestListListAllGet:
    async def test_list_encodes_filters(self, make: Any) -> None:
        m = Mock(json_reply(200, page([], None)))
        await maybe(make(m).keys.list(status="active", name_prefix="sdk-", is_superkey=False, limit=5))
        r = req(m, 0)
        assert (r["method"], r["path"]) == ("GET", "/v2/customer/keys")
        assert r["query"] == {"status": "active", "name_prefix": "sdk-", "is_superkey": "false", "limit": "5"}

    async def test_list_all_follows_next_cursor_as_starting_after(self, make: Any) -> None:
        m = Mock(json_reply(200, page(["k1", "k2"], "k2")), json_reply(200, page(["k3"], None)))
        ids = [k["key_id"] for k in await collect(make(m).keys.list_all(status="active"))]
        assert ids == ["k1", "k2", "k3"]
        assert len(m.calls) == 2
        assert req(m, 0)["query"] == {"status": "active"}
        assert req(m, 1)["query"] == {"status": "active", "starting_after": "k2"}

    async def test_list_all_stops_when_has_more_without_a_cursor(self, make: Any) -> None:
        m = Mock(json_reply(200, {**page(["k1"], None), "has_more": True}))
        assert len(await collect(make(m).keys.list_all())) == 1
        assert len(m.calls) == 1

    async def test_get_pages_the_list_limit_1000_and_stops_at_the_match(self, make: Any) -> None:
        m = Mock(json_reply(200, page(["k1", "k2"], "k2")), json_reply(200, page(["k3", "k4"], "k4")), json_reply(200, page(["k5"], None)))
        assert (await maybe(make(m).keys.get("k3")))["key_id"] == "k3"
        assert len(m.calls) == 2
        assert req(m, 0)["query"] == {"limit": "1000"}

    async def test_get_on_an_absent_key_id_raises_key_not_found_client_side(self, make: Any) -> None:
        m = Mock(json_reply(200, page(["k1"], "k1")), json_reply(200, page(["k2"], None)))
        with pytest.raises(KeyNotFoundError) as ei:
            await maybe(make(m).keys.get("nope"))
        assert isinstance(ei.value, NotFoundError)
        assert ei.value.code == "KEY_NOT_FOUND"
        assert ei.value.status is None


class TestMutations:
    async def test_paths_methods_and_bodies_key_id_is_path_escaped(self, make: Any) -> None:
        m = Mock(*[json_reply(200, {}) for _ in range(8)])
        keys = make(m).keys
        await maybe(keys.create(name="end_user_42", key_budget=5))
        await maybe(keys.rename("key_1", "renamed"))
        await maybe(keys.update("key_1", key_budget=None))
        await maybe(keys.revoke("key_1"))
        await maybe(keys.unrevoke("key_1"))
        await maybe(keys.delete("key_1"))
        await maybe(keys.topup("key_1", 2.5))
        await maybe(keys.promote("key/1"))
        assert [(req(m, i)["method"], req(m, i)["path"], req(m, i)["body"]) for i in range(8)] == [
            ("POST", "/v2/customer/keys", {"name": "end_user_42", "key_budget": 5}),
            ("PATCH", "/v2/customer/keys/key_1", {"name": "renamed"}),
            ("PATCH", "/v2/customer/keys/key_1", {"key_budget": None}),
            ("POST", "/v2/customer/keys/key_1/revoke", None),
            ("POST", "/v2/customer/keys/key_1/unrevoke", None),
            ("DELETE", "/v2/customer/keys/key_1", None),
            ("POST", "/v2/customer/keys/key_1/topup", {"amount": 2.5}),
            ("POST", "/v2/customer/keys/key%2F1/promote", None),
        ]
        assert all(c.raw == b"" for c in m.calls[3:6]) and m.calls[7].raw == b""

    async def test_create_is_never_retried(self, make: Any) -> None:
        m = Mock(relay_error(500, "INTERNAL_ERROR"), json_reply(201, {}))
        with pytest.raises(RelayInternalError):
            await maybe(make(m).keys.create(name="x"))
        assert len(m.calls) == 1
        assert "idempotency-key" not in m.calls[0].headers

    async def test_server_refusals_surface_typed(self, make: Any) -> None:
        keys = make(Mock(relay_error(403, None), relay_error(403, "KEY_NOT_OWNED"), relay_error(402, "KEY_BUDGET_EXHAUSTED"))).keys
        with pytest.raises(PermissionDeniedError):
            await maybe(keys.create(name="x", key_budget=1))
        with pytest.raises(PermissionDeniedError):
            await maybe(keys.promote("key_1"))
        with pytest.raises(KeyBudgetExhaustedError):
            await maybe(keys.topup("key_1", 1))
