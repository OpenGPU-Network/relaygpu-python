"""Port of test/unit/webhooks.fixtures.test.ts (A4): verify the real staging deliveries captured by the EM.

The secret is ``RELAY_WEBHOOK_SECRET`` from the environment, else from the repo's gitignored ``.env`` (read here, the same
parsing as tests/e2e/conftest.py, without touching ``os.environ``). Absent → the tests SKIP. Values are never printed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from relaygpu.webhooks import WebhookVerificationError, verify_webhook

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "tests" / "fixtures" / "webhooks"


def _secret() -> str | None:
    if os.environ.get("RELAY_WEBHOOK_SECRET"):
        return os.environ["RELAY_WEBHOOK_SECRET"]
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text("utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) == "RELAY_WEBHOOK_SECRET":
                return m.group(2).strip().strip('"').strip("'") or None
    return None


SECRET = _secret()
FILES = sorted(DIR.glob("*.json")) if DIR.exists() else []
FIXTURES = [(f.name, json.loads(f.read_text("utf-8"))) for f in FILES]

pytestmark = pytest.mark.skipif(not FILES or not SECRET, reason="no webhook fixtures or RELAY_WEBHOOK_SECRET absent")


def test_both_captured_fixtures_are_present() -> None:
    assert {name for name, _ in FIXTURES} >= {"task.completed.json", "workflow.completed.json"}


@pytest.mark.parametrize(("name", "fx"), FIXTURES, ids=[n for n, _ in FIXTURES])
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
        throwaway = "whsec_" + base64.b64encode(os.urandom(24)).decode()
        h = fx["headers"]
        signed = f"{h['webhook-id']}.{h['webhook-timestamp']}.{fx['body']}".encode()
        extra = "v1," + base64.b64encode(hmac.new(base64.b64decode(throwaway[6:]), signed, hashlib.sha256).digest()).decode()
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
