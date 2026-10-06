"""Port of test/unit/webhooks.fixtures.test.ts (A4): verify the real staging deliveries captured by the EM.

The secret is ``RELAY_WEBHOOK_SECRET`` from the environment, else from the repo's gitignored ``.env`` (``helpers.dotenv``,
the e2e suite's parsing, without touching ``os.environ``). Absent → the tests SKIP. Values are never printed.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from relaygpu.webhooks import WebhookVerificationError, verify_webhook
from tests.helpers import FIXTURES, dotenv, env, load, new_secret, sign

DIR = FIXTURES / "webhooks"
SECRET = env("RELAY_WEBHOOK_SECRET") or dotenv().get("RELAY_WEBHOOK_SECRET") or None
FILES = sorted(DIR.glob("*.json")) if DIR.exists() else []
CAPTURES = [(f.name, load(f"webhooks/{f.name}")) for f in FILES]

pytestmark = pytest.mark.skipif(not FILES or not SECRET, reason="no webhook fixtures or RELAY_WEBHOOK_SECRET absent")


def test_both_captured_fixtures_are_present() -> None:
    assert {name for name, _ in CAPTURES} >= {"task.completed.json", "workflow.completed.json"}


@pytest.mark.parametrize(("name", "fx"), CAPTURES, ids=[n for n, _ in CAPTURES])
class TestFixture:
    def test_verifies_and_carries_its_event(self, name: str, fx: dict[str, Any]) -> None:
        now = int(fx["headers"]["webhook-timestamp"])
        assert verify_webhook(fx["body"], fx["headers"], SECRET or "", now=now)["event"] == fx["event"]
        assert verify_webhook(fx["body"].encode(), fx["headers"], SECRET or "", now=now)["event"] == fx["event"]

    def test_tampered_body_and_stale_timestamp_are_rejected(self, name: str, fx: dict[str, Any]) -> None:
        now = int(fx["headers"]["webhook-timestamp"])
        tampered = re.sub(r'"elapsed_seconds":\s*(\d+)', lambda m: f'"elapsed_seconds": {int(m.group(1)) + 1}', fx["body"], count=1)
        assert tampered != fx["body"]
        with pytest.raises(WebhookVerificationError) as e1:
            verify_webhook(tampered, fx["headers"], SECRET or "", now=now)
        assert e1.value.code == "WEBHOOK_SIGNATURE_MISMATCH"
        with pytest.raises(WebhookVerificationError) as e2:
            verify_webhook(fx["body"], fx["headers"], SECRET or "", now=now + 600)  # 10 minutes old
        assert e2.value.code == "WEBHOOK_TIMESTAMP_OUT_OF_RANGE"

    def test_current_previous_pair_accepted(self, name: str, fx: dict[str, Any]) -> None:
        # A9 rotation: the real secret as `previous` next to a throwaway `current`, and a delivery signed by both.
        now = int(fx["headers"]["webhook-timestamp"])
        throwaway = new_secret()
        h = fx["headers"]
        extra = sign(throwaway, h["webhook-id"], h["webhook-timestamp"], fx["body"])
        assert verify_webhook(fx["body"], h, [throwaway, SECRET or ""], now=now)["event"] == fx["event"]
        both = {**h, "webhook-signature": f"{extra} {h['webhook-signature']}"}
        assert verify_webhook(fx["body"], both, [throwaway, SECRET or ""], now=now)["event"] == fx["event"]
        assert verify_webhook(fx["body"], both, throwaway, now=now)["event"] == fx["event"]
        with pytest.raises(WebhookVerificationError):
            verify_webhook(fx["body"], h, throwaway, now=now)

    def test_workflow_completed_narrows_to_the_run_event(self, name: str, fx: dict[str, Any]) -> None:
        if fx["event"] != "workflow.completed":
            pytest.skip("workflow.completed fixtures only")
        e = verify_webhook(fx["body"], fx["headers"], SECRET or "", now=int(fx["headers"]["webhook-timestamp"]))
        if e["event"] != "workflow.completed":
            raise AssertionError(f"expected workflow.completed, got {e['event']}")
        assert e["status"] == "completed"
        assert e["task_id"].startswith("wf:")
        result = e.get("result")
        assert result is not None
        assert result["status"] == "completed"
        assert result["run_id"] == e["task_id"]
