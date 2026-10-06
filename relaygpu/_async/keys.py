"""The ``keys`` namespace (port of src/keys.ts)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from .._core import _UNSET
from .._generated.error_codes import KeyNotFoundError
from .._generated.types import (
    CreateKeyRequest,
    CreateKeyResponse,
    KeyBudgetTopupResponse,
    KeyListResponse,
    KeyResponse,
    PromoteKeyResponse,
    UpdateKeyRequest,
)
from .._shapes import KeyListAllParams, KeyListParams
from .._util import path_id

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from .client import AsyncRelay

KeyMutationResult = dict[str, Any]
"""The spec leaves these bodies untyped."""


def _key_path(key_id: str) -> str:
    return f"/v2/customer/keys/{path_id(key_id)}"


class AsyncKeys:
    """API key management. Auth: a dashboard JWT, or a partner superkey (custom tiers); anything else gets the server's
    403. Address keys by ``key_id``, never by secret (a secret in a path answers 400)."""

    def __init__(self, relay: AsyncRelay) -> None:
        self._relay = relay

    async def _call(self, method: str, path: str, body: Any = _UNSET) -> Any:
        return (await self._relay._http.request(method, path, json=body)).data

    async def list(self, **params: Unpack[KeyListParams]) -> KeyListResponse:
        """``GET /v2/customer/keys``: newest first, secrets masked. Page with ``starting_after`` = ``next_cursor``."""
        res = await self._relay._http.request("GET", "/v2/customer/keys", query=params)
        return cast(KeyListResponse, res.data)

    async def list_all(self, **params: Unpack[KeyListAllParams]) -> AsyncIterator[KeyResponse]:
        """Every key matching the filters, following ``next_cursor`` across pages."""
        cursor: str | None = None
        while True:
            query: KeyListParams = {**params, "starting_after": cursor}
            page = await self.list(**query)
            for k in page["keys"]:
                yield k
            nxt = page.get("next_cursor")
            if not page["has_more"] or not nxt:
                return
            cursor = nxt

    async def create(self, **fields: Unpack[CreateKeyRequest]) -> CreateKeyResponse:
        """``POST /v2/customer/keys``. The response's ``key`` is the full secret and is returned **only here**, once; store it
        now (every later response masks it). The first key on a custom tier becomes the superkey (``is_superkey: True``).
        ``key_budget`` is custom-tier only (403 otherwise). Never retried (a retry could mint twice)."""
        return cast(CreateKeyResponse, await self._call("POST", "/v2/customer/keys", dict(fields)))

    async def get(self, key_id: str) -> KeyResponse:
        """One key by ``key_id``. The API has no get-one route, so this pages ``GET /v2/customer/keys`` and matches
        ``key_id``; absent → ``KeyNotFoundError`` (code ``KEY_NOT_FOUND``, raised client-side, ``status`` None)."""
        async for k in self.list_all(limit=1000):
            if k.get("key_id") == key_id:
                return k
        raise KeyNotFoundError(f"Key not found: {key_id}", code="KEY_NOT_FOUND")

    async def update(self, key_id: str, **patch: Unpack[UpdateKeyRequest]) -> KeyMutationResult:
        """``PATCH /v2/customer/keys/{key_id}``: send only what changes; ``restrictions`` is replaced, not merged."""
        return cast(KeyMutationResult, await self._call("PATCH", _key_path(key_id), dict(patch)))

    async def rename(self, key_id: str, name: str) -> KeyMutationResult:
        """Renames a key (names are unique per customer; 400 on duplicate)."""
        return await self.update(key_id, name=name)

    async def revoke(self, key_id: str) -> KeyMutationResult:
        """``POST …/revoke``. Reversible with :meth:`unrevoke`."""
        return cast(KeyMutationResult, await self._call("POST", f"{_key_path(key_id)}/revoke"))

    async def unrevoke(self, key_id: str) -> KeyMutationResult:
        """``POST …/unrevoke``."""
        return cast(KeyMutationResult, await self._call("POST", f"{_key_path(key_id)}/unrevoke"))

    async def delete(self, key_id: str) -> KeyMutationResult:
        """``DELETE /v2/customer/keys/{key_id}``: permanent; the key must be revoked first."""
        return cast(KeyMutationResult, await self._call("DELETE", _key_path(key_id)))

    async def topup(self, key_id: str, amount: float) -> KeyBudgetTopupResponse:
        """``POST …/topup``: atomically adds ``amount`` USD to the key's ``key_budget`` (custom tiers; uncapped → amount)."""
        return cast(KeyBudgetTopupResponse, await self._call("POST", f"{_key_path(key_id)}/topup", {"amount": amount}))

    async def promote(self, key_id: str) -> PromoteKeyResponse:
        """``POST …/promote``: makes the key the superkey. JWT only server-side (a superkey gets 403)."""
        return cast(PromoteKeyResponse, await self._call("POST", f"{_key_path(key_id)}/promote"))
