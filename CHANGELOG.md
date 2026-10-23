# Changelog

`relaygpu-client` versions track the TypeScript client [`@relaygpu/client`](https://www.npmjs.com/package/@relaygpu/client)
minor for minor: 0.1.x here is the port of 0.1.x there. The API may change before 1.0.

## 0.1.2 (unreleased)

- **Catalog reads are account-aware.** `models.list`, `models.get`, `pricing.get` and `tiers.list` now send the
  client's credential (`api_key` or `jwt`), so with one they return your account's view: the models your tier and
  grants can run, beyond the public list. A client without a credential sends none and reads the public catalog,
  unchanged. A key Relay refuses raises `AuthenticationError` on these reads, a key below the credit floor
  `InsufficientCreditsError`. The `models.get` cache stays per client. `health`, task polls and workflow run polls
  stay credential-free.
- **Generated types** follow the prod public spec of 2026-10-24: the Anthropic-compatible route is live again with no
  public model list; the `doubao-seedance-2-0-filter-off` pair left the public model map.

## 0.1.1 (2026-10-22)

- **Generated types** follow the prod public spec of 2026-10-22: Claude is served through RelayConductor
  (`relay/conductor` on the OpenAI-compatible route), so the Anthropic-compatible route lists no models;
  `relay/music-1`, `relay/tts-1`, `relay/tts-1-turbo`, `relay/stt-1`, `MiniMax/MiniMax-H3`, `relay/conductor`,
  `KlingTeam/v3-Omni` and `google/gemini-nano-banana-2.1` are in the model map; the Qwen image pair and
  `google/gemini-3.5-flash` are gone.
- **Error statuses** for the OAuth, routing and `SERVICE_UNAVAILABLE` codes, so they map to their typed errors
  instead of the status class.

## 0.1.0 (unreleased)

First release: a port of `@relaygpu/client` 0.1.0.

- **Clients**: `Relay` (sync, `httpx.Client`) and `AsyncRelay` (`asyncio`, `httpx.AsyncClient`) over one core;
  `Relay` is generated from `AsyncRelay` (`scripts/unasync.py`), so both expose the same namespaces, methods and
  types. `api_key` (`X-API-Key`) or `jwt` (Bearer); the key is passed explicitly, never read from the environment,
  and never appears in `repr()` or an error. Context managers, `timeout` in seconds (default 600 per attempt),
  `retry` policy (`max_retries` 2, `max_rate_limit_retries` 3, `max_retry_after` 60 s, or `False`),
  `default_headers`, bring-your-own `http_client`.
- **Any model**: `run()` resolves every model through `models.get` (`GET /v2/models/{name}`, cached 5 min): route,
  body shape, async default and schemas. Unknown → `ModelNotFoundError`, retired → `ModelRetiredError`, both before
  anything is billed. `is_accepted()` narrows the `202` envelope.
- **Families**: `image.generate` / `image.edit` (→ `ImageResult` of `RelayImage`: `url` / `b64`, `to_bytes()`,
  `save()`), `video.generate` (the `202`, or with `wait=True` the completed task), `audio.speech`,
  `audio.transcribe`.
- **Tasks**: `tasks.get`, `tasks.wait` (long-polls `?wait=30`; `on_progress` on status transitions;
  `TaskFailedError` carrying the task's `error_code`).
- **Idempotency and retries**: every async submit and workflow run carries an `Idempotency-Key` (yours or a UUID)
  and is retried safely; replays come back with `replayed: True`. Sync submits are never retried.
- **Files**: `files.upload` (bytes, path, binary file object, iterator of bytes; streamed), `copy`, `get`, `list`,
  `list_all`, `delete`; implicit upload of file values in any `*_url` / `*_urls` field (`upload={"retention": ...}`,
  default `relay1h`), `inline_images` for images ≤ 4 MB.
- **Webhooks**: `webhooks.verify` / `verify_webhook` (Standard Webhooks, ±5 min, multi-secret for the 24 h rotation
  window; a plain call on both clients), `secret`, `rotate_secret`, `deliveries.list` / `deliveries.get`; typed
  `task.*`, `workflow.*` and `instance.*` events.
- **Workflows**: `list`, `get`, `run` (optionally `wait=True`), `get_run`, `wait_run`, `cancel_run`.
- **Account and keys**: credits, credit history, usage, usage by key, usage timeseries, metrics, pricing, profile,
  model allowlist; key `list` / `list_all` / `get` / `create` / `update` / `rename` / `revoke` / `unrevoke` /
  `delete` / `topup` / `promote`.
- **Catalog**: `models.list` / `models.get`, `pricing.get`, `tiers.list`, `health()`; `estimate_cost()` from the
  public pricing rows (an estimate, never an invoice).
- **Errors**: a class per HTTP status (13) and per catalog error code, with `status`, `code`, `request_id`,
  `detail`, `retry_after`; `APIConnectionError`, `APITimeoutError`, `TaskFailedError`, `WebhookVerificationError`.
- **Types**: `TypedDict`s generated from Relay's public OpenAPI spec (`scripts/gen.py`) in `relaygpu.types`;
  `py.typed`, `mypy --strict` on the package. Python 3.10–3.13; one runtime dependency, `httpx`.
- `request()`: any route with the same errors and retry policy.
