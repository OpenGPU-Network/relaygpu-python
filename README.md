# relaygpu-client

> **Status: pre-release (0.x).** The API may change before 1.0.

The Python client for [Relay](https://relaygpu.com): image, video and audio generation, async tasks, file
uploads, webhook verification, workflows, account and keys, with typed errors. Request and response types are
generated from Relay's public OpenAPI spec. Chat is not wrapped: point the OpenAI or Anthropic SDK at Relay
([quickstart 6](#6-chat-use-the-sdk-you-already-have)). A port of the TypeScript client
[`@relaygpu/client`](https://www.npmjs.com/package/@relaygpu/client), version for version (0.1.x ↔ 0.1.x).

```bash
pip install relaygpu-client
# or
uv add relaygpu-client
```

```python
import relaygpu
```

The distribution is `relaygpu-client`; the import is `relaygpu`. Python 3.10–3.13, one runtime dependency
(`httpx`).

## The client

```python
import os

from relaygpu import Relay

relay = Relay(
    os.environ["RELAY_API_KEY"],  # relay_sk_…, sent as X-API-Key. Or jwt=... for a dashboard login token.
    base_url="https://relaygpu.com",  # the default
    timeout=600,  # seconds per HTTP attempt (default 10 min)
    retry={"max_retries": 2, "max_rate_limit_retries": 3, "max_retry_after": 60},  # the defaults; retry=False disables retries
)
```

The key is not read from the environment: pass it. It never appears in an error, a log line or `repr(relay)`.
Catalog reads (`models`, `pricing`, `tiers`, `health`) and task polls need no credential.

The client holds a connection pool. Use it as a context manager, or call `relay.close()` when you are done:

```python
with Relay(os.environ["RELAY_API_KEY"]) as relay:
    print(relay.health())
```

`AsyncRelay` is the same client for `asyncio`: every method is awaited, everything else is identical
([Sync and async](#sync-and-async)).

```python
from relaygpu import AsyncRelay

async with AsyncRelay(os.environ["RELAY_API_KEY"]) as relay:
    result = await relay.image.generate("Qwen/qwen-image", {"prompt": "a lighthouse at dusk"})
```

## Quickstarts

Each one is a runnable file in [`examples/`](examples/) (how to run them:
[examples/README.md](examples/README.md)). They read `RELAY_API_KEY`, and `RELAY_BASE_URL` when set.

### 1. Image

Generate an image with `Qwen/qwen-image`, print its link and save it.
([`examples/01_image.py`](examples/01_image.py))

```python
import os
import sys
import tempfile
from pathlib import Path

from relaygpu import ContentPolicyDeclinedError, InsufficientCreditsError, Relay

with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
    try:
        result = relay.image.generate(
            "Qwen/qwen-image",
            {"prompt": "A red fox in an autumn forest", "size": "1024x1024"},
        )
        image = result.images[0]
        print("url:", image.url)  # a link that lives 1 h; pass store_output="relay7d" to keep it longer

        ext = (image.mime_type or "image/bin").split("/")[1]
        path = Path(tempfile.gettempdir()) / f"relay-image.{ext}"
        image.save(path)  # downloads the link (without your key) or decodes base64
        print("saved:", path)
    except InsufficientCreditsError as e:
        # Status picks the class, e.code refines it: KeyBudgetExhaustedError is an InsufficientCreditsError (402).
        sys.exit(f"out of credit ({e.code}), request {e.request_id}")
    except ContentPolicyDeclinedError as e:
        sys.exit(f"the provider declined this prompt: {e}")
```

`result.images` is a list of `RelayImage` (`url` or `b64`, `mime_type`, `to_bytes()`, `save(path)`);
`result.raw` is the response body exactly as the API sent it.

### 2. Video, waiting for the result

Kling v3 text-to-video, 3 seconds, standard quality. `wait=True` long-polls the task until it ends.
([`examples/02_video_wait.py`](examples/02_video_wait.py); the `asyncio` version is
[`examples/02_video_wait_async.py`](examples/02_video_wait_async.py))

```python
import os
import sys

from relaygpu import Relay, TaskFailedError

with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
    try:
        task = relay.video.generate(
            "KlingTeam/v3-T2V",
            {"prompt": "A paper boat drifting down a rain gutter", "duration": 3, "quality_mode": "std"},
            wait=True,
            # Status transitions and elapsed time only: there is no queue position, log stream or cancel.
            on_progress=lambda p: print(f"{p['status']} after {p['elapsed_seconds']}s"),
        )
        urls = (task.get("result") or {}).get("urls") or []
        print("video:", urls[0] if urls else None)  # expires 1 h after completion unless you pass store_output
    except TaskFailedError as e:
        # The task ran and failed: e.code is the task's error_code (e.g. CONTENT_POLICY_DECLINED, UPSTREAM_TIMEOUT).
        sys.exit(f"task {e.task_id} failed: {e.code or 'unclassified'}: {e}")
```

### 3. Upload a file, then motion control

Both ways to send a local file: upload it explicitly (`files.upload`, here with 1-day retention), or put the
file straight into a `*_url` field and let the SDK upload it. The submit returns the `202` without waiting.
Arguments: a local video, then a character image URL.
([`examples/03_upload_motion_control.py`](examples/03_upload_motion_control.py))

```python
import os
import sys
from pathlib import Path

from relaygpu import FileTooLargeError, Relay, ValidationError

video_path, image_url = Path(sys.argv[1]), sys.argv[2]

with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
    try:
        # Form 1, explicit: host the file yourself and get a link any *_url field takes.
        # relay1h is free; relay1d / relay7d / relay30d are billed per file (relay.pricing.get()["media_storage"]).
        file = relay.files.upload(video_path, retention="relay1d")  # streamed from disk; media type sniffed
        print("uploaded:", file["file_id"], file["url"], "expires", file["expires_at"])

        # Form 2, implicit: put the file straight into a *_url field; the SDK uploads it first (relay1h unless
        # upload={"retention": ...} says otherwise) and sends the link. "video_url": file["url"] works the same.
        accepted = relay.video.generate(
            "KlingTeam/v3-Motion-Control",
            {
                "video_url": video_path.read_bytes(),
                "image_url": image_url,
                "character_orientation": "video",  # "image" | "video": which input decides the facing direction
                "duration": 5,
                "quality_mode": "std",
            },
        )
        # No wait: the 202 envelope. Poll later with relay.tasks.wait(accepted["task_id"]), or pass webhook_url.
        print("task:", accepted["task_id"], "(replayed)" if accepted.get("replayed") else "")

        # Take the explicit upload down now (idempotent; the fee is not refunded).
        relay.files.delete(file["file_id"])
    except FileTooLargeError:
        sys.exit("files are capped at 100 MB")
    except ValidationError as e:
        sys.exit(f"rejected ({e.code}): {e}")
```

### 4. Webhook receiver

Verify each delivery on its raw body and branch on `event`. The example file adds `--self-test`, which signs
deliveries with a throwaway secret and posts them to itself, so it runs offline and without a tunnel.
([`examples/04_webhook_receiver.py`](examples/04_webhook_receiver.py))

```python
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relaygpu import Relay, WebhookVerificationError

relay = Relay(os.environ.get("RELAY_API_KEY"))  # verify() itself needs no credential
# The account's signing secret: relay.webhooks.secret() (JWT or superkey). During the 24 h after a
# rotation, pass [current, previous] instead.
SECRET = os.environ["RELAY_WEBHOOK_SECRET"]


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        # Verify the bytes as sent, never a re-serialised json.loads result.
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            event = relay.webhooks.verify(raw, self.headers, SECRET)
        except WebhookVerificationError as e:
            print("rejected delivery:", e.code)
            self.send_response(400)
            self.end_headers()
            return
        # Delivery is at-least-once: dedupe on self.headers["webhook-id"] in production.
        name = event["event"]
        if name == "task.completed":
            print("task done:", event["task_id"], event.get("result"))
        elif name == "task.failed":
            print("task failed:", event["task_id"], event.get("error_code"), event.get("error"))
        elif name in ("workflow.completed", "workflow.failed"):
            # Run events: the top-level status is always "completed" (the envelope). The run's own
            # outcome is result["status"]: completed | failed | cancelled.
            print(f"{name}:", event["task_id"], (event.get("result") or {}).get("status"))
        else:
            print("other event:", name)  # instance.*
        self.send_response(204)
        self.end_headers()


ThreadingHTTPServer(("0.0.0.0", 8787), Receiver).serve_forever()
```

`verify` takes `self.headers` as is (any object with `.get` or `.items`, case-insensitive), a `dict`, or
`httpx.Headers`. In a framework, pass the raw body: `await request.body()` (Starlette/FastAPI),
`request.get_data()` (Flask), `request.body` (Django).

### 5. Workflow run

`script-voiceover`: an LLM writes a short line from your brief, a TTS model speaks it. Each step is billed as
an ordinary request. ([`examples/05_workflow_run.py`](examples/05_workflow_run.py))

```python
import json
import os
import sys

from relaygpu import Relay, TaskFailedError

with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
    try:
        # Inputs follow the template's input_schema: relay.workflows.get("script-voiceover").
        # Pass webhook_url for one signed workflow.* delivery instead of waiting.
        run = relay.workflows.run(
            "script-voiceover",
            {"messages": [{"role": "user", "content": "a lighthouse at dusk"}], "voice": "Cherry"},
            wait=True,
            on_progress=lambda r: print(r["status"]),
        )
        print("run:", run["run_id"], run["status"])
        print("output:", json.dumps(run.get("output"), indent=2))  # the last step's output (the speech)
    except TaskFailedError as e:
        # A failed or cancelled run raises; e.task is the run as polled.
        sys.exit(f"run {e.task_id} did not complete: {e}")
```

### 6. Chat: use the SDK you already have

Chat and completions run on the official OpenAI and Anthropic SDKs: point them at Relay. This SDK does not wrap
chat. `openai` is an example dependency only (`pip install openai`); `relaygpu-client` does not need it.
([`examples/06_chat_base_url.py`](examples/06_chat_base_url.py))

```python
import os

from openai import OpenAI

base = os.environ.get("RELAY_BASE_URL") or "https://relaygpu.com"
client = OpenAI(api_key=os.environ["RELAY_API_KEY"], base_url=f"{base}/v2/openai/v1")

completion = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello in five words."}],
    max_tokens=20,
)
print(completion.choices[0].message.content)
```

## Any model: `run()` and model resolution

```python
from relaygpu import is_accepted

out = relay.run("Qwen/qwen-image", {"prompt": "a lighthouse at dusk"})  # the output body
sub = relay.run("KlingTeam/v3-T2V", {"prompt": "waves", "duration": 3}, wait=False)
if is_accepted(sub):
    print(sub["task_id"])  # the 202 envelope
```

The SDK ships no model table. Every call resolves the model through `relay.models.get(name)`
(`GET /v2/models/{name}`, cached 5 min per client): its route, whether `model` goes in the body, whether the
route is async by default, and its request/response schemas. A model added to Relay after this release works
through `run()` and the family helpers (`image`, `video`, `audio`) without an upgrade; the models this release
knows also get `Literal` hints for autocomplete.

- An unknown name raises `ModelNotFoundError` (404); a retired one raises `ModelRetiredError` (403). Both
  before anything is submitted or billed.
- `relay.models.list(tag="text-to-video")` lists the catalog; `relay.models.get(name)` returns one model with
  its `request_schema`, `request_example` and `pricing`.
- `run()` returns a sync route's body, or waits for an async route and returns the task's `result`.
  `wait=False` returns the `202` envelope instead (narrow it with `is_accepted`).
- Request options are keyword arguments on every call: `timeout`, `on_progress`, `mode`, `store_output`,
  `webhook_url`, `idempotency_key`, `async_` (trailing underscore: `async` is a keyword), `upload`,
  `inline_images`.

## Async tasks and `tasks.wait`

Video (and any call with `async_=True`) answers `202` with a `task_id`. `relay.video.generate` returns that
envelope unless you pass `wait=True`; `run()` and the image/audio helpers wait by default.

```python
task = relay.tasks.wait(
    task_id,
    timeout=20 * 60,  # seconds; the default. The task keeps running past it (APITimeoutError)
    on_progress=lambda p: print(p["status"], p["elapsed_seconds"]),
)
```

`tasks.wait` long-polls `GET /v2/tasks/{id}?wait=30`: a 15 s task costs one or two requests, not a poll every
second. `on_progress` fires on status transitions (`queued` → `running` → `completed`) with `elapsed_seconds`,
and that is all there is: no queue position, no logs and no cancel, by design. A failed task raises
`TaskFailedError` with `code` = the task's `error_code`. Task polls need no key: the task id is the access
token. A task's result expires 1 hour after it finishes. `relay.tasks.get(task_id)` reads the status once.

Workflow runs have no long-poll: `workflows.wait_run` polls the run every 1 s, backing off to 5 s (default
budget 30 min).

## Idempotency and retries

Every async submit (`202` routes, `video.generate`, `run()` on an async route) and every workflow run is sent
with an `Idempotency-Key`: yours (`idempotency_key=...`) or a generated UUID. That makes the submit safe to
retry, so the SDK retries it on a network error, a timeout or a 5xx (up to 2 times, same key). When the server
recognises a key it has already accepted, it answers with the original `202` and the SDK returns it with
`"replayed": True`: no second task, no second charge. The same key with a different body raises
`IdempotencyKeyReusedError` (422). Keys live 24 hours.

```python
a = relay.video.generate("KlingTeam/v3-T2V", {"prompt": "waves", "duration": 3}, idempotency_key="order-1234")
b = relay.video.generate("KlingTeam/v3-T2V", {"prompt": "waves", "duration": 3}, idempotency_key="order-1234")
# b["task_id"] == a["task_id"], b["replayed"] is True
```

Sync calls (an image route answering `200`, chat, TTS) carry no key and are **never retried**: a retry would
run, and bill, the request again. GETs retry on 429/503 (honouring `Retry-After`, up to 3 times) and on other
5xx (up to 2). A `Retry-After` longer than `max_retry_after` (60 s) is not waited out: the error is raised.
`retry=False` turns every retry off.

## Files and implicit uploads

```python
file = relay.files.upload(Path("dance.mp4"), retention="relay1d")  # {file_id, url, expires_at, …}
relay.files.copy("https://example.com/clip.mp4", retention="relay7d")  # Relay fetches it
relay.files.get(file["file_id"])
relay.files.list(source="upload")  # one page; files.list_all(...) iterates every page
relay.files.delete(file["file_id"])  # the link goes down now; no refund
```

- `files.upload` takes `bytes`, a path (`pathlib.Path` or `str`), a binary file object (`open(p, "rb")`) or an
  iterator of bytes (`AsyncRelay` also takes an async iterator). Paths and file objects are streamed in
  chunks, never read whole into memory.
- Any `*_url` / `*_urls` input also takes `bytes`, a `Path`, a binary file object or an iterator of bytes: the
  SDK uploads it first and sends the link (quickstart 3). There a `str` is always a URL, never a path.
  Implicit uploads use `relay1h` unless you pass `upload={"retention": "relay1d"}`.
- `inline_images=True` sends images of 4 MB or less as base64 instead, on routes whose schema takes it; larger
  ones are still uploaded.
- One file is at most 100 MB (the server answers `413`, `FileTooLargeError`). The media type comes from
  `content_type=`, else from the file's first bytes.
- `relay1h` is free within a daily quota; `relay1d`, `relay7d` and `relay30d` are billed per file at upload.

## How long result links live

Result links (`urls`, `audio_url`, …) and uploaded files expire. By default a result link lives **1 hour**
after the task finishes. To keep outputs longer, buy storage per request with `store_output`:

```python
relay.image.generate("Qwen/qwen-image", {"prompt": "a lighthouse"}, store_output="relay7d")
```

| `store_output` / `retention` | Lifetime | Fee |
|---|---|---|
| `provider` (default) / `relay1h` | 1 hour | free |
| `relay1d` | 1 day | per file |
| `relay7d` | 7 days | per file |
| `relay30d` | 30 days | per file |

The fees are in `relay.pricing.get()["media_storage"]`; read them there rather than hard-coding them.

## Webhooks

Pass `webhook_url` on an async submit (one `task.completed` or `task.failed`) or on a workflow run (one
`workflow.completed` or `workflow.failed`). Deliveries are signed with
[Standard Webhooks](https://www.standardwebhooks.com/); `relay.webhooks.verify(raw_body, headers, secret)`
checks the signature and the timestamp (±5 min, `tolerance=300`) and returns the typed event, discriminated on
`event` (quickstart 4). It makes no request, needs no credential, and is a plain (not awaited) call on both
`Relay` and `AsyncRelay`; `relaygpu.verify_webhook(...)` is the same function without a client.

- Verify the **raw** body bytes, not a re-serialised `json.loads` result.
- Rotation: `relay.webhooks.rotate_secret()` issues a new secret and the previous one keeps working for 24
  hours. During that window pass both: `verify(raw, headers, [current, previous])`.
- On `workflow.*` events the top-level `status` is always `"completed"` (it describes the delivery). Branch on
  `event`, or on `result["status"]` (`completed`, `failed`, `cancelled`; a cancelled run arrives as
  `workflow.failed`).
- Delivery is at-least-once: dedupe on the `webhook-id` header.
- `relay.webhooks.secret()`, `.rotate_secret()` and `.deliveries.list()` / `.deliveries.get(task_id_or_run_id)`
  need a JWT or a partner superkey.

## Errors

Every error extends `RelayError`. HTTP errors extend `RelayAPIError`; the HTTP status picks the class:

| Status | Class |
|---|---|
| 400 | `InvalidRequestError` |
| 401 | `AuthenticationError` |
| 402 | `InsufficientCreditsError` |
| 403 | `PermissionDeniedError` |
| 404 | `NotFoundError` |
| 409 | `ConflictError` |
| 410 | `GoneError` |
| 422 | `ValidationError` |
| 429 | `RateLimitError` |
| 500 | `RelayInternalError` |
| 502 | `ProviderError` |
| 503 | `CapacityError` |
| 504 | `UpstreamTimeoutError` |

The `error.code` then picks a subclass: `KEY_BUDGET_EXHAUSTED` raises `KeyBudgetExhaustedError`, which is an
`InsufficientCreditsError`; `CONTENT_POLICY_DECLINED` raises `ContentPolicyDeclinedError` (a
`ValidationError`). Every code in Relay's error catalog has a class (all in `relaygpu.errors`, re-exported
from `relaygpu`). A code this release does not know falls back to the status class and keeps `e.code`;
`e.code` may also be `None`.

```python
from relaygpu import KeyBudgetExhaustedError, RateLimitError, RelayAPIError

try:
    relay.image.generate("Qwen/qwen-image", {"prompt": "a lighthouse"})
except KeyBudgetExhaustedError:
    print("this key's budget is spent")
except RateLimitError as e:
    print(f"retry in {e.retry_after}s")
except RelayAPIError as e:
    print(e.status, e.code, e.request_id, e.detail)
```

- `e.request_id` is the id to quote to support; `e.retry_after` (seconds) is set from `Retry-After`;
  `e.detail` is the server's detail.
- `TaskFailedError`: an async task (or workflow run) ended `failed`; `e.code` is the task's `error_code`,
  `e.task_id` and `e.task` carry the rest.
- `APIConnectionError` (no HTTP answer), `APITimeoutError` (a `timeout` ran out),
  `WebhookVerificationError` (a delivery failed verification).
- Branch on the class or `e.code`, never on the message, and never on `error.source` in the response body.
- `relaygpu.FileNotFoundError` (`FILE_NOT_FOUND`, a `NotFoundError`) is Relay's error, not the builtin.
  `from relaygpu import *` shadows the builtin `FileNotFoundError` in your module; import the names you use,
  or write `relaygpu.FileNotFoundError` / `relaygpu.errors.FileNotFoundError`.

## Account and keys

```python
import time

relay = Relay(jwt=dashboard_jwt)  # or Relay(superkey) for a partner (custom) tier

relay.account.credits()
relay.account.usage()
relay.account.usage_timeseries(start_time=int(time.time()) - 86_400)

created = relay.keys.create(name="end-user-42", key_budget=5)
print(created["key"])  # the secret, shown ONCE
for k in relay.keys.list_all():
    print(k["key_id"], k["name"])
relay.keys.topup(key_id, 2)
relay.keys.revoke(key_id)
```

These routes take a dashboard JWT or a custom-tier superkey. A plain inference key gets the server's 403
(`PermissionDeniedError`); the SDK does not guess the key class. `keys.create` returns the full secret in
`key` once; every later response masks it, so store it then. Address keys by `key_id`. `key_budget` is for
custom tiers only. Query parameters are keyword arguments with the API's names (`from` is spelled `from_`).

## Cost estimates

```python
est = relay.estimate_cost("KlingTeam/v3-T2V", {"duration_seconds": 3, "quality_mode": "std"})
print(est["usd"], est["basis"])
```

`estimate_cost` computes from the public `/v2/pricing` rows. It is an **estimate, not an invoice**: you are
billed from provider-reported usage, and custom-tier prices are not in the public list.

## Sync and async

One core, two transports. `AsyncRelay` (`httpx.AsyncClient`) is the source; `Relay` (`httpx.Client`) is
generated from it, so the two have the same namespaces, methods, arguments and return types. The only
differences:

| | `Relay` | `AsyncRelay` |
|---|---|---|
| Calls | `relay.image.generate(...)` | `await relay.image.generate(...)` |
| Lifetime | `with Relay(...) as relay` / `relay.close()` | `async with AsyncRelay(...) as relay` / `await relay.aclose()` |
| Paging | `for k in relay.keys.list_all()` | `async for k in relay.keys.list_all()` |
| Image bytes | `image.to_bytes()`, `image.save(p)` | `await image.to_bytes()`, `await image.save(p)` |
| Upload streams | iterator of bytes | iterator or async iterator of bytes |
| `webhooks.verify` | plain call | plain call (no `await`: it does no I/O) |

There is no `AbortSignal`. Cancellation belongs to the event loop: `asyncio.timeout(...)`, `asyncio.wait_for`
or `task.cancel()` stop an `AsyncRelay` call where it is (on a wait, the task itself keeps running
server-side). In sync code, `timeout=` bounds each call. Bring your own `httpx` client with
`http_client=httpx.Client(...)` / `httpx.AsyncClient(...)` (proxies, transports, mounts); the SDK does not
close a client you passed in. `default_headers={...}` adds headers to every request.

## Types

Request and response bodies are `TypedDict`s generated from Relay's public OpenAPI spec, in `relaygpu.types`
(for example `TaskStatus`, `FileObject`, `WorkflowRunState`, `WebhookEvent`, `UsageInput`), so mypy and pyright
check the keys you read. The package ships `py.typed` and is `mypy --strict` clean. Responses are plain
`dict`s at runtime: no wrapper objects, `json.dumps` works on them. The image helpers are the exception:
`ImageResult` / `RelayImage` are small frozen dataclasses.

## Runtimes

CPython 3.10, 3.11, 3.12 and 3.13, tested in CI on each. One runtime dependency, `httpx`. Sync and async
(`AsyncRelay` runs on `asyncio`). `relay.request(method, path, json=...)` reaches any route
with the same errors and retry policy.

## Chat

Chat is a base-URL swap on the official SDKs, in any language:

| SDK | Base URL |
|---|---|
| OpenAI | `https://relaygpu.com/v2/openai/v1` |
| Anthropic | `https://relaygpu.com/v2/anthropic` |

## License

MIT
