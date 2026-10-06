"""Port of test/unit/webhooks.test.ts: Standard Webhooks verification and the webhooks namespace routes."""

from __future__ import annotations

import email
import json
import pickle
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from relaygpu import PermissionDeniedError, RelayError
from relaygpu.webhooks import WebhookEvent, WebhookVerificationError, verify_webhook
from tests.helpers import Mock, json_reply, maybe, new_secret, relay_error, sign

SECRET = new_secret()
PREVIOUS = new_secret()
NOW = 1_791_000_000


RUN_BODY = json.dumps(
    {
        "event": "workflow.failed",
        "task_id": "wf:7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "status": "completed",
        "elapsed_seconds": 14,
        "created_at": "2026-10-06T10:00:00Z",
        "mode": "workflows",
        "model": "script-voiceover",
        "task_address": None,
        "result": {
            "run_id": "wf:7c9e6679-7425-40de-944b-e07fc1f90ae7",
            "workflow_id": "script-voiceover",
            "version": 2,
            "status": "cancelled",
            "inputs": {},
            "steps": [],
        },
    },
    separators=(",", ":"),
)


def delivery(
    body: str = RUN_BODY, *, secrets: list[str] | None = None, ts: int = NOW, msg_id: str = "msg_2abc"
) -> tuple[str, dict[str, str]]:
    signature = " ".join(sign(s, msg_id, ts, body) for s in (secrets or [SECRET]))
    return body, {"webhook-id": msg_id, "webhook-timestamp": str(ts), "webhook-signature": signature}


def reject(code: str, *args: Any, **kw: Any) -> WebhookVerificationError:
    with pytest.raises(WebhookVerificationError) as ei:
        verify_webhook(*args, **kw)
    e = ei.value
    assert isinstance(e, RelayError)
    assert e.code == code
    assert SECRET not in str(e) + repr(e) + repr(e.__dict__)
    return e


class TestVerify:
    def test_accepts_a_correctly_signed_delivery_and_returns_the_event(self) -> None:
        body, h = delivery()
        e = verify_webhook(body, h, SECRET, now=NOW)
        assert e["event"] == "workflow.failed"
        assert e["task_id"].startswith("wf:")

    def test_accepts_a_bytes_body_byte_exact_non_ascii_included(self) -> None:
        body = json.dumps(
            {"event": "task.completed", "task_id": "direct:1", "status": "completed", "elapsed_seconds": 1, "result": {"text": "çağrı ✓"}},
            ensure_ascii=False,
        )
        _, h = delivery(body)
        assert verify_webhook(body.encode(), h, SECRET, now=NOW)["event"] == "task.completed"
        assert verify_webhook(bytearray(body.encode()), h, SECRET, now=NOW)["event"] == "task.completed"

    def test_rejects_a_tampered_body(self) -> None:
        body, h = delivery()
        reject("WEBHOOK_SIGNATURE_MISMATCH", body.replace("cancelled", "completed"), h, SECRET, now=NOW)

    def test_rejects_10_minutes_old_and_10_minutes_future_honours_tolerance(self) -> None:
        body, h = delivery()
        e = reject("WEBHOOK_TIMESTAMP_OUT_OF_RANGE", body, h, SECRET, now=NOW + 600)
        assert str(e) == "webhook-timestamp is outside the 300 s tolerance"
        reject("WEBHOOK_TIMESTAMP_OUT_OF_RANGE", body, h, SECRET, now=NOW - 600)
        assert verify_webhook(body, h, SECRET, now=NOW + 299)
        assert verify_webhook(body, h, SECRET, now=NOW + 600, tolerance=900)

    def test_uses_the_clock_by_default(self) -> None:
        body, h = delivery(ts=int(time.time()) - 600)
        reject("WEBHOOK_TIMESTAMP_OUT_OF_RANGE", body, h, SECRET)
        body, h = delivery(ts=int(time.time()))
        assert verify_webhook(body, h, SECRET)

    def test_rotation_window_previous_alone_and_current_previous(self) -> None:
        body, h = delivery(secrets=[PREVIOUS, SECRET])
        assert len(h["webhook-signature"].split(" ")) == 2
        assert verify_webhook(body, h, PREVIOUS, now=NOW)
        assert verify_webhook(body, h, [SECRET, PREVIOUS], now=NOW)
        assert verify_webhook(body, h, (SECRET, PREVIOUS), now=NOW)
        assert verify_webhook(body, h, SECRET, now=NOW)
        # after the grace: only the new signature, the previous secret alone no longer matches
        body, after = delivery(secrets=[SECRET])
        reject("WEBHOOK_SIGNATURE_MISMATCH", body, after, PREVIOUS, now=NOW)
        assert verify_webhook(body, after, [SECRET, PREVIOUS], now=NOW)

    def test_rejects_a_wrong_secret_a_malformed_secret_and_no_secret(self) -> None:
        body, h = delivery()
        reject("WEBHOOK_SIGNATURE_MISMATCH", body, h, new_secret(), now=NOW)
        reject("WEBHOOK_INVALID_SECRET", body, h, "whsec_!!!not-base64", now=NOW)
        reject("WEBHOOK_INVALID_SECRET", body, h, [], now=NOW)
        reject("WEBHOOK_INVALID_SECRET", body, h, "", now=NOW)
        reject("WEBHOOK_INVALID_SECRET", body, h, "whsec_", now=NOW)

    def test_a_secret_without_the_whsec_prefix_or_padding_still_verifies(self) -> None:
        # atob semantics: the prefix is optional and padding may be dropped.
        body, h = delivery()
        assert verify_webhook(body, h, SECRET[len("whsec_") :], now=NOW)
        assert verify_webhook(body, h, SECRET.rstrip("="), now=NOW)

    def test_ignores_non_v1_tokens_and_garbage_tokens(self) -> None:
        body, h = delivery()
        mixed = {**h, "webhook-signature": f"v2,abc v1,%%% {h['webhook-signature']}"}
        assert verify_webhook(body, mixed, SECRET, now=NOW)
        reject("WEBHOOK_SIGNATURE_MISMATCH", body, {**h, "webhook-signature": "v2,abc"}, SECRET, now=NOW)

    def test_rejects_missing_headers_and_a_non_integer_timestamp(self) -> None:
        body, h = delivery()
        for name in ("webhook-id", "webhook-timestamp", "webhook-signature"):
            headers = {k: v for k, v in h.items() if k != name}
            reject("WEBHOOK_MISSING_HEADERS", body, headers, SECRET, now=NOW)
        reject("WEBHOOK_INVALID_TIMESTAMP", body, {**h, "webhook-timestamp": "1791000000.5"}, SECRET, now=NOW)

    def test_rejects_a_signed_body_that_is_not_a_json_event(self) -> None:
        body, h = delivery("not json")
        reject("WEBHOOK_INVALID_BODY", body, h, SECRET, now=NOW)
        body, h = delivery("[1,2]")
        reject("WEBHOOK_INVALID_BODY", body, h, SECRET, now=NOW)
        body, h = delivery('{"event": NaN}')  # JSON.parse refuses NaN; so does this port
        reject("WEBHOOK_INVALID_BODY", body, h, SECRET, now=NOW)

    def test_header_lookup_lowercase_mixed_case_list_values_httpx_headers_and_http_server_message(self) -> None:
        body, h = delivery()
        assert verify_webhook(body, h, SECRET, now=NOW)
        mixed: dict[str, Any] = {
            "Webhook-Id": h["webhook-id"],
            "WEBHOOK-TIMESTAMP": h["webhook-timestamp"],
            "Webhook-Signature": [h["webhook-signature"]],
        }
        assert verify_webhook(body, mixed, SECRET, now=NOW)
        assert verify_webhook(body, httpx.Headers(h), SECRET, now=NOW)
        # http.server's BaseHTTPRequestHandler.headers is an email.message.Message
        msg = email.message_from_string("".join(f"{k.title()}: {v}\r\n" for k, v in h.items()) + "\r\n")
        assert verify_webhook(body, msg, SECRET, now=NOW)

    def test_narrows_on_event(self) -> None:
        body, h = delivery()
        e: WebhookEvent = verify_webhook(body, h, SECRET, now=NOW)
        if e["event"] == "workflow.completed" or e["event"] == "workflow.failed":
            assert e["mode"] == "workflows"
            assert e["status"] == "completed"
            result = e.get("result")
            assert result is not None
            assert result["status"] == "cancelled"
        else:
            raise AssertionError("unreachable")

    def test_error_is_picklable_and_carries_code(self) -> None:
        body, h = delivery()
        e = reject("WEBHOOK_SIGNATURE_MISMATCH", body, h, new_secret(), now=NOW)
        e2 = pickle.loads(pickle.dumps(e))
        assert type(e2) is WebhookVerificationError
        assert (e2.code, str(e2), e2.status) == ("WEBHOOK_SIGNATURE_MISMATCH", str(e), None)

    async def test_webhooks_verify_delegates_without_a_request_and_is_sync_on_both_clients(self, make: Any) -> None:
        m = Mock()
        relay = make(m)
        body, h = delivery()
        e = relay.webhooks.verify(body, h, SECRET, now=NOW)  # never awaited, on either client
        assert e["event"] == "workflow.failed"
        with pytest.raises(WebhookVerificationError):
            relay.webhooks.verify(body, h, SECRET, now=NOW + 600)
        assert m.calls == []


class TestRoutes:
    async def test_secret_and_rotate_secret(self, make: Any) -> None:
        s = {"secret": "whsec_AAAA", "created_at": "2026-10-06T00:00:00Z", "previous_valid_until": None}
        m = Mock(json_reply(200, s), json_reply(200, {**s, "previous_valid_until": "2026-10-07T00:00:00Z"}))
        wh = make(m, retry=False).webhooks
        assert await maybe(wh.secret()) == s
        assert (await maybe(wh.rotate_secret()))["previous_valid_until"] == "2026-10-07T00:00:00Z"
        assert [f"{c.method} {urlsplit(c.url).path}" for c in m.calls] == [
            "GET /v2/customer/webhook-secret",
            "POST /v2/customer/webhook-secret/rotate",
        ]

    async def test_deliveries_list_encodes_filters_get_keeps_the_run_id_raw(self, make: Any) -> None:
        m = Mock(
            json_reply(200, {"object": "page", "data": [], "has_more": False, "next_page": None}), json_reply(200, {"task_id": "wf:abc"})
        )
        wh = make(m, retry=False).webhooks
        await maybe(wh.deliveries.list(event="workflow.completed", outcome="gave_up", task_id="wf:abc", limit=5, page="cur"))
        u = urlsplit(m.calls[0].url)
        assert u.path == "/v2/customer/webhook-deliveries"
        assert dict(parse_qsl(u.query)) == {
            "event": "workflow.completed",
            "outcome": "gave_up",
            "task_id": "wf:abc",
            "limit": "5",
            "page": "cur",
        }
        await maybe(wh.deliveries.get("wf:7c9e6679-7425"))
        assert m.calls[1].url == "http://relay.test/v2/customer/webhook-deliveries/wf:7c9e6679-7425"

    async def test_an_inference_key_gets_the_servers_403_as_permission_denied(self, make: Any) -> None:
        wh = make(Mock(relay_error(403, None)), retry=False).webhooks
        with pytest.raises(PermissionDeniedError):
            await maybe(wh.secret())
