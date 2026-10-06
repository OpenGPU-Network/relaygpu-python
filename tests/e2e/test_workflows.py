"""Port of test/e2e/workflows.e2e.test.ts. BILLED (A7/A9): ONE workflow run (script-voiceover: claude-haiku-4-5 +
qwen3-tts-flash, ~$0.002 on staging), submitted with a webhook_url, waited to its end via ``wait_run``, then its delivery
read by run id with the superkey. ``list``/``get`` are free. The lead runs the billed test."""

from __future__ import annotations

import time
from typing import Any

import pytest

from relaygpu import Relay, RelayAPIError
from tests.e2e.conftest import API_KEY, BASE_URL

WORKFLOW = "script-voiceover"
WEBHOOK_URL = "https://example.com/relay-sdk-e2e"


@pytest.fixture
def relay() -> Relay:
    return Relay(api_key=API_KEY, base_url=BASE_URL)


def test_list_get_are_public_and_the_cheapest_workflow_is_the_expected_chain(relay: Relay) -> None:
    listed = relay.workflows.list()
    assert WORKFLOW in [w["workflow_id"] for w in listed["workflows"]]
    wf = relay.workflows.get(WORKFLOW)
    assert len(wf["steps"]) == 2


def test_billed_run_with_webhook_url_wait_run_to_terminal_deliveries_get_resolves(relay: Relay) -> None:
    accepted = relay.workflows.run(
        WORKFLOW,
        {"messages": [{"role": "user", "content": "One short upbeat line about the sea."}], "voice": "Serena"},
        webhook_url=WEBHOOK_URL,
    )
    assert accepted["status"] == "queued"
    assert accepted["run_id"].startswith("wf:")
    assert accepted["replayed"] is False

    seen: list[str] = []
    run = relay.workflows.wait_run(accepted["run_id"], timeout=5 * 60, on_progress=lambda r: seen.append(r["status"]))
    assert run["status"] == "completed"
    assert len(run["steps"]) == 2
    assert seen[-1] == "completed"

    # The delivery is claimed at the terminal write; example.com refuses POSTs, so the first attempt lands within
    # seconds and retries continue in the background.
    detail: Any = None
    for _ in range(20):
        try:
            detail = relay.webhooks.deliveries.get(accepted["run_id"])
            break
        except RelayAPIError as e:
            if e.status != 404:
                raise
        time.sleep(3)
    assert detail
    assert detail["task_id"] == accepted["run_id"]
    assert detail["event"] == "workflow.completed"
    assert detail["url"] == WEBHOOK_URL
