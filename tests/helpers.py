"""Offline transport mock (port of test/unit/helpers.ts): queue replies, record every request. Works for both clients."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import inspect
import json
import os
import re
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import httpx

from relaygpu import AsyncRelay, Relay

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
KEY = "relay_sk_unit_secret_never_logged"
BASE = "http://relay.test"


@dataclass
class Recorded:
    method: str
    url: str
    headers: dict[str, str]
    body: Any
    raw: bytes


Reply = Union[httpx.Response, Exception, Callable[[Recorded], httpx.Response]]


def json_reply(status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
    content = b"" if body is None else json.dumps(body).encode()
    return httpx.Response(status, content=content, headers={"content-type": "application/json", **(headers or {})})


def relay_error(
    status: int, code: str | None, extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    extra = dict(extra or {})
    detail = extra.pop("detail", f"boom {code}")
    err = {"code": code, "type": None, "source": None, "message": f"boom {code}", "request_id": "req_test", **extra}
    return json_reply(status, {"detail": detail, "error": err}, headers)


class Mock:
    """``Mock(reply, reply, ...)``; ``.calls`` records each request. An empty queue fails the test loudly."""

    def __init__(self, *replies: Reply) -> None:
        self.queue: list[Reply] = list(replies)
        self.calls: list[Recorded] = []

    def push(self, *replies: Reply) -> None:
        self.queue.extend(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body: Any = raw
        with contextlib.suppress(ValueError):
            body = json.loads(raw) if raw else None
        rec = Recorded(request.method, str(request.url), {k.lower(): v for k, v in request.headers.items()}, body, raw)
        self.calls.append(rec)
        if not self.queue:
            raise AssertionError(f"Mock: no reply queued for {rec.method} {rec.url}")
        nxt = self.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if callable(nxt) and not isinstance(nxt, httpx.Response):
            return nxt(rec)
        return nxt


class VirtualClock:
    """``relaygpu._clock.monotonic`` while a test runs: starts at 1000.0 and moves only when the SDK (virtually) sleeps or
    a test advances ``now`` (e.g. a poll the server held)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def sync_relay(mock: Mock, **kw: Any) -> Relay:
    kw.setdefault("api_key", KEY)
    kw.setdefault("base_url", BASE)
    return Relay(http_client=httpx.Client(transport=httpx.MockTransport(mock.handler)), **kw)


def async_relay(mock: Mock, **kw: Any) -> AsyncRelay:
    kw.setdefault("api_key", KEY)
    kw.setdefault("base_url", BASE)
    return AsyncRelay(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock.handler)), **kw)


async def maybe(value: Any) -> Any:
    """Awaits a coroutine (AsyncRelay), passes a value through (Relay): one test body drives both clients."""
    return await value if inspect.isawaitable(value) else value


async def agen(*chunks: bytes) -> AsyncIterator[bytes]:
    """An async iterator of bytes (a body / file value only ``AsyncRelay`` reads)."""
    for c in chunks:
        yield c


async def collect(it: Iterable[Any] | AsyncIterator[Any] | Iterator[Any]) -> list[Any]:
    if hasattr(it, "__aiter__"):
        return [x async for x in it]  # type: ignore[union-attr]
    return list(it)  # type: ignore[arg-type]


# ---- staging captures of 2026-10-06 (copied verbatim from relaygpu-node/test/fixtures) ----


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


DETAILS: dict[str, dict[str, Any]] = load("model-details_20261006.json")
PRICING = load("pricing_20261006.json")
MODELS = load("models_20261006.json")
IMAGE_QWEN = load("image_qwen-image_20261006.json")
TTS_QWEN = load("audio_qwen3-tts-flash_20261006.json")
VIDEO_KLING = load("video_kling-v3-t2v_20261006.json")


def detail(name: str) -> httpx.Response:
    """The captured ``GET /v2/models/{name}`` reply."""
    d = DETAILS.get(name)
    if d is None:
        raise KeyError(f"no captured detail for {name}")
    return json_reply(d["status"], d["body"])


def detail_with(name: str, endpoint: dict[str, Any]) -> httpx.Response:
    """A detail with its endpoint overridden (e.g. an image route forced to answer async)."""
    body = DETAILS[name]["body"]
    return json_reply(200, {**body, "endpoint": {**body["endpoint"], **endpoint}})


def task(status: str, **extra: Any) -> httpx.Response:
    return json_reply(200, {"task_id": "direct:t1", "status": status, "elapsed_seconds": 1, **extra})


def accepted202(headers: dict[str, str] | None = None) -> httpx.Response:
    """The captured Kling ``202`` envelope, with ``X-Request-ID: rid-202``."""
    return json_reply(202, VIDEO_KLING["accepted"]["body"], {"x-request-id": "rid-202", **(headers or {})})


# ---- webhook signing (Standard Webhooks) ----


def new_secret() -> str:
    """A fresh ``whsec_<base64>`` signing secret."""
    return "whsec_" + base64.b64encode(os.urandom(24)).decode()


def sign(secret: str, msg_id: str, ts: int | str, body: str) -> str:
    """One ``v1,<base64 HMAC-SHA256>`` signature of ``{msg_id}.{ts}.{body}``."""
    key = base64.b64decode(secret[len("whsec_") :])
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.{body}".encode(), hashlib.sha256).digest()).decode()


# ---- the repo's gitignored .env (values never printed) ----


def dotenv() -> dict[str, str]:
    """``KEY=value`` lines of the repo's ``.env`` (first occurrence wins); ``{}`` when there is none."""
    out: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text("utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m:
                out.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
    return out


def load_dotenv() -> None:
    """Copies ``.env`` into ``os.environ`` without overriding what the environment already sets (the e2e suite)."""
    for k, v in dotenv().items():
        os.environ.setdefault(k, v)


def env(name: str) -> str | None:
    return os.environ.get(name) or None
