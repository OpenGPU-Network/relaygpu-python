from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.helpers import Mock, async_relay, sync_relay


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Every SDK sleep is recorded, never slept (retry backoff, poll floors). Tests assert on the list."""
    record: list[float] = []

    def fake_sync(seconds: float) -> None:
        record.append(seconds)

    async def fake_async(seconds: float) -> None:
        record.append(seconds)

    for name, mod in list(sys.modules.items()):
        if name.startswith("relaygpu.") and mod is not None:
            if hasattr(mod, "sync_sleep"):
                monkeypatch.setattr(mod, "sync_sleep", fake_sync)
            if hasattr(mod, "async_sleep"):
                monkeypatch.setattr(mod, "async_sleep", fake_async)
    yield record


@pytest.fixture(params=["sync", "async"])
def make(request: pytest.FixtureRequest) -> Callable[..., Any]:
    """``make(mock, **client_kwargs)`` → a ``Relay`` or an ``AsyncRelay`` over the mock; every test using it runs twice."""
    factory = sync_relay if request.param == "sync" else async_relay

    def build(mock: Mock, **kw: Any) -> Any:
        return factory(mock, **kw)

    build.kind = request.param  # type: ignore[attr-defined]
    return build
