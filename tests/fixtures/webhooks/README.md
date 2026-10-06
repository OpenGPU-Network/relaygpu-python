# Webhook fixtures

Real staging deliveries, verified by `test/unit/webhooks.fixtures.test.ts` with
`process.env.RELAY_WEBHOOK_SECRET` (the account's current `whsec_…`) and `now` set
to each fixture's own `webhook-timestamp`. The test skips when this folder holds
no `*.json` file or the env var is absent.

One delivery per file, `<event>.json` (e.g. `workflow.completed.json`):

```json
{
  "event": "workflow.completed",
  "captured_at": "2026-10-06T21:14:03Z",
  "headers": {
    "webhook-id": "msg_...",
    "webhook-timestamp": "1791320043",
    "webhook-signature": "v1,<base64> [v1,<base64> during a rotation grace]"
  },
  "body": "<the raw request body as a JSON string, byte-exact>"
}
```

- `body` must be the bytes the receiver got, unparsed and unreformatted (the
  signature covers them exactly); store it as a JSON string value.
- Header names lowercase, values verbatim.
- If the secret is rotated, the fixtures signed by the old secret stop verifying
  once `RELAY_WEBHOOK_SECRET` is updated: re-capture them.
- A `workflow.completed` fixture is additionally asserted to narrow to the run
  event (`result.status === "completed"`, `result.run_id === task_id`).
