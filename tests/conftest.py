from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from relaygpu import _clock
from tests.helpers import Mock, VirtualClock, async_relay, sync_relay


@pytest.fixture(autouse=True)
def clock(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    """Every SDK sleep is recorded, never slept (retry backoff, poll floors), and advances the virtual clock, which is
    the SDK's ``monotonic`` in the unit suite. Staging e2e keeps the real clock (a real task takes real time)."""
    c = VirtualClock()

    async def fake_async(seconds: float) -> None:
        c.sleep(seconds)

    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setattr(_clock, "monotonic", c.monotonic)
    monkeypatch.setattr(_clock, "sync_sleep", c.sleep)
    monkeypatch.setattr(_clock, "async_sleep", fake_async)
    return c


@pytest.fixture
def sleeps(clock: VirtualClock) -> list[float]:
    """The seconds of every SDK sleep so far, in order. Tests assert on the list."""
    return clock.sleeps


@pytest.fixture(params=["sync", "async"])
def make(request: pytest.FixtureRequest) -> Callable[..., Any]:
    """``make(mock, **client_kwargs)`` → a ``Relay`` or an ``AsyncRelay`` over the mock; every test using it runs twice."""
    factory = sync_relay if request.param == "sync" else async_relay

    def build(mock: Mock, **kw: Any) -> Any:
        return factory(mock, **kw)

    build.kind = request.param  # type: ignore[attr-defined]
    return build
