"""F1 / A2: every public method of @relaygpu/client 0.1.0 exists on Relay and AsyncRelay (snake_case), the two clients
mirror each other method for method, and relaygpu/_sync/ is exactly what scripts/unasync.py generates."""

from __future__ import annotations

import importlib.util
import inspect
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import relaygpu
from relaygpu import AsyncRelay, Relay
from tests.helpers import load

ROOT = Path(__file__).resolve().parents[2]
SURFACE = load("ts_surface_0.1.0.json")


def _load_unasync() -> Any:
    """scripts/unasync.py, imported from its path: its ``rename`` is the one async → sync name table."""
    spec = importlib.util.spec_from_file_location("unasync", ROOT / "scripts" / "unasync.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


unasync = _load_unasync()


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def resolve(client: Any, ns: str) -> Any:
    obj = client
    for part in filter(None, ns.split(".")):
        obj = getattr(obj, part)
    return obj


def public_methods(obj: Any) -> set[str]:
    return {n for n in dir(obj) if not n.startswith("_") and callable(getattr(obj, n))}


CLIENTS = [Relay(api_key="relay_sk_parity"), AsyncRelay(api_key="relay_sk_parity")]


@pytest.mark.parametrize("client", CLIENTS, ids=["Relay", "AsyncRelay"])
@pytest.mark.parametrize("ns", sorted(SURFACE["namespaces"]), ids=lambda n: n or "<client>")
def test_every_ts_method_exists_snake_cased(client: Any, ns: str) -> None:
    target = resolve(client, ns)
    missing = [m for m in SURFACE["namespaces"][ns] if not callable(getattr(target, snake(m), None))]
    assert missing == [], f"{type(client).__name__}.{ns}: missing {missing}"


def test_sync_and_async_clients_mirror_each_other() -> None:
    sync, aio = CLIENTS
    namespaces = [ns for ns in SURFACE["namespaces"]]
    for ns in namespaces:
        s, a = resolve(sync, ns), resolve(aio, ns)
        async_names = {unasync.rename(n): n for n in public_methods(a)}  # sync name → async name
        assert public_methods(s) == set(async_names), ns
        for name in public_methods(s):
            sm, am = getattr(s, name), getattr(a, async_names[name])
            if inspect.isclass(sm) or not inspect.ismethod(sm):
                continue
            # The sync method is never a coroutine; the async twin is one (or an async generator), except pure helpers.
            assert not inspect.iscoroutinefunction(sm) and not inspect.isasyncgenfunction(sm), f"{ns}.{name}"
            ssig, asig = inspect.signature(sm), inspect.signature(am)
            assert [(p.name, p.kind, p.default) for p in ssig.parameters.values()] == [
                (p.name, p.kind, p.default) for p in asig.parameters.values()
            ], f"{ns}.{name}: signatures differ"


def test_ts_values_have_python_twins() -> None:
    twins = {
        "Relay": "Relay",
        "DEFAULT_BASE_URL": "DEFAULT_BASE_URL",
        "VERSION": "VERSION",
        "isAccepted": "is_accepted",
        "toRelayImage": "to_relay_image",
        "INLINE_IMAGE_MAX_BYTES": "INLINE_IMAGE_MAX_BYTES",
        "verifyWebhook": "verify_webhook",
        "WebhookVerificationError": "WebhookVerificationError",
    }
    assert sorted(twins) == sorted(SURFACE["values"])
    missing = [py for py in twins.values() if not hasattr(relaygpu, py)]
    assert missing == []
    assert relaygpu.AsyncRelay is AsyncRelay
    assert relaygpu.DEFAULT_BASE_URL == "https://relaygpu.com"
    assert relaygpu.VERSION == "0.1.0"


def test_repr_is_pinned_and_never_carries_the_credential() -> None:
    assert repr(Relay(api_key="relay_sk_secret_value")) == "Relay(base_url='https://relaygpu.com')"
    assert repr(AsyncRelay(jwt="a.b.c", base_url="http://x/")) == "AsyncRelay(base_url='http://x')"
    assert "relay_sk_secret_value" not in str(Relay(api_key="relay_sk_secret_value"))


def test_sync_sources_are_generated_and_current() -> None:
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "unasync.py"), "--check"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
