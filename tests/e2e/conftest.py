"""Staging e2e (``pytest -m e2e``): BILLED calls on the SDK test account. Reads RELAY_* from the environment, else from
the repo's gitignored ``.env`` (values never printed); skips without them; refuses a production base URL.

``RELAY_E2E_WHEEL=1`` runs the suite against the installed wheel instead of the source tree (A1): the test runner
must then be a venv where the built wheel is installed and ``relaygpu`` resolves to site-packages.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tests.helpers import env, load_dotenv

if TYPE_CHECKING:
    from relaygpu import Relay

PROD = re.compile(r"relaygpu\.com|relay\.opengpu\.network|:1301\b")

load_dotenv()

BASE_URL = env("RELAY_BASE_URL")
API_KEY = env("RELAY_API_KEY")
READY = bool(BASE_URL and API_KEY and not PROD.search(BASE_URL or ""))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)
            if not READY:
                item.add_marker(pytest.mark.skip(reason="staging e2e: RELAY_API_KEY / RELAY_BASE_URL absent (or a production URL)"))


@pytest.fixture(scope="session", autouse=True)
def _wheel_or_source() -> None:
    import relaygpu

    origin = Path(relaygpu.__file__).resolve()
    if env("RELAY_E2E_WHEEL"):
        assert "site-packages" in str(origin), f"RELAY_E2E_WHEEL=1 but relaygpu resolves to {origin}"
    print(f"\n[e2e] relaygpu from {'wheel' if 'site-packages' in str(origin) else 'source'}; base {'staging' if READY else 'none'}")


@dataclass
class Call:
    method: str
    url: str
    idem: str | None
    body: bytes


class Counted:
    """A client over staging whose every request is recorded (method, URL, Idempotency-Key)."""

    def __init__(self, **kw: Any) -> None:
        from relaygpu import Relay

        self.calls: list[Call] = []
        kw.setdefault("api_key", API_KEY)
        kw.setdefault("base_url", BASE_URL)

        def hook(req: httpx.Request) -> None:
            try:
                body = req.content
            except httpx.RequestNotRead:  # a streamed upload: not buffered here
                body = b""
            self.calls.append(Call(req.method, str(req.url), req.headers.get("idempotency-key"), body))

        self.relay = Relay(http_client=httpx.Client(follow_redirects=True, event_hooks={"request": [hook]}), **kw)

    def posts(self) -> list[Call]:
        return [c for c in self.calls if c.method == "POST"]


@pytest.fixture
def relay() -> Relay:
    """A plain sync client over staging with the test account's key."""
    from relaygpu import Relay

    return Relay(api_key=API_KEY, base_url=BASE_URL)


@pytest.fixture
def counted() -> Callable[..., Counted]:
    return Counted
