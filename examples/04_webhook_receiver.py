"""A webhook receiver: verify the signature on the raw body, branch on the event, answer 2xx fast.

    RELAY_WEBHOOK_SECRET=whsec_... python examples/04_webhook_receiver.py   (listens on :8787, or $PORT)
    python examples/04_webhook_receiver.py --self-test                     (signs deliveries to itself, exits 0; offline)

Standard library only (http.server); any framework works the same: hand verify() the raw body bytes and the headers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from relaygpu import Relay, WebhookVerificationError

SELF_TEST = "--self-test" in sys.argv

# verify() makes no request and needs no credential; the key is only for the client's other calls.
relay = Relay(os.environ.get("RELAY_API_KEY"), base_url=os.environ.get("RELAY_BASE_URL"))
# The account's signing secret: relay.webhooks.secret() (JWT or superkey). During the 24 h after a
# rotation, pass [current, previous] instead.
SECRET = "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode() if SELF_TEST else os.environ.get("RELAY_WEBHOOK_SECRET", "")


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        # Verify the bytes as sent, never a re-serialised json.loads result.
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            event: Any = relay.webhooks.verify(raw, self.headers, SECRET)
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

    def log_message(self, format: str, *args: Any) -> None:  # keep the output to the lines above
        pass


def post(port: int, payload: dict[str, Any], *, tamper: bool = False) -> int:
    """Signs a delivery the way Relay does (Standard Webhooks) and POSTs it to ourselves."""
    body = json.dumps(payload).encode()
    msg_id = f"msg_{uuid.uuid4()}"
    ts = str(int(time.time()))
    key = base64.b64decode(SECRET.removeprefix("whsec_"))
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    if tamper:
        body = body.replace(b"completed", b"c0mpleted")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=body,
        method="POST",
        headers={"content-type": "application/json", "webhook-id": msg_id, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return int(res.status)
    except urllib.error.HTTPError as e:
        return e.code


def self_test(port: int) -> bool:
    base = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "elapsed_seconds": 42, "mode": "direct"}
    results = [
        post(
            port,
            {
                **base,
                "event": "task.completed",
                "status": "completed",
                "task_id": "direct:self-test",
                "model": "Qwen/qwen-image",
                "result": {"urls": ["https://cdn.relaygpu.com/content/x"]},
            },
        )
        == 204,
        post(
            port,
            {
                **base,
                "event": "workflow.failed",
                "status": "completed",
                "task_id": "wf:self-test",
                "mode": "workflows",
                "model": "script-voiceover",
                "result": {"status": "cancelled"},
            },
        )
        == 204,
        post(port, {**base, "event": "task.completed", "status": "completed", "task_id": "direct:tampered"}, tamper=True) == 400,
    ]
    print("self-test ok" if all(results) else f"self-test FAILED: {results}")
    return all(results)


def main() -> int:
    if not SECRET:
        print("set RELAY_WEBHOOK_SECRET (or run with --self-test)", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(
        ("127.0.0.1" if SELF_TEST else "0.0.0.0", 0 if SELF_TEST else int(os.environ.get("PORT", "8787"))), Receiver
    )
    port = server.server_address[1]
    print(f"listening on http://127.0.0.1:{port}")
    if not SELF_TEST:
        server.serve_forever()
        return 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return 0 if self_test(port) else 1
    finally:
        server.shutdown()
        relay.close()


if __name__ == "__main__":
    sys.exit(main())
