"""A5 against staging, port of test/e2e/errors.e2e.test.ts. Both calls are refused at auth/scope time (no task, no
bill). Skips without RELAY_BUDGET_ZERO_KEY / RELAY_ALLOWLISTED_KEY / RELAY_ALLOWLISTED_MODEL."""

from __future__ import annotations

import re
from typing import Any

import pytest

from relaygpu import KeyBudgetExhaustedError, PermissionDeniedError, Relay
from tests.e2e.conftest import BASE_URL
from tests.helpers import env

BUDGET_ZERO = env("RELAY_BUDGET_ZERO_KEY")
ALLOWLISTED = env("RELAY_ALLOWLISTED_KEY")
# The allowlist holds a scope, `{mode}.{source}.{model}`; the model name is what follows the second dot.
ALLOWED_MODEL = ".".join((env("RELAY_ALLOWLISTED_MODEL") or "").split(".")[2:]) or None

pytestmark = pytest.mark.skipif(not (BUDGET_ZERO and ALLOWLISTED and ALLOWED_MODEL), reason="A5 keys absent")


def example_for(model: str) -> dict[str, Any]:
    """The model's own documented example: a valid body, so only the credential decides the answer."""
    detail = Relay(base_url=BASE_URL).models.get(model)
    return dict(detail.get("request_example") or {})


def test_a5_budget_zero_key_on_its_allowed_model_key_budget_exhausted_error_with_code_and_request_id() -> None:
    assert ALLOWED_MODEL
    relay = Relay(api_key=BUDGET_ZERO, base_url=BASE_URL)
    with pytest.raises(KeyBudgetExhaustedError) as ei:
        relay.run(ALLOWED_MODEL, example_for(ALLOWED_MODEL), wait=False)
    e = ei.value
    assert (e.status, e.code) == (402, "KEY_BUDGET_EXHAUSTED")
    assert e.request_id and re.match(r"\S+", e.request_id)
    print(f"A5 402: {type(e).__name__} code={e.code} request_id={e.request_id}")


def test_a5_allowlisted_key_on_any_other_model_the_403_class_with_its_code() -> None:
    other = "Qwen/qwen-image" if ALLOWED_MODEL == "Qwen/qwen3-tts-flash" else "Qwen/qwen3-tts-flash"
    relay = Relay(api_key=ALLOWLISTED, base_url=BASE_URL)
    with pytest.raises(PermissionDeniedError) as ei:
        relay.run(other, example_for(other), wait=False)
    e = ei.value
    assert e.status == 403
    assert e.code and re.match(r"^[A-Z_]+$", e.code)
    assert e.request_id
    print(f"A5 403: {type(e).__name__} code={e.code} request_id={e.request_id}")


def test_a5_an_invented_code_degrades_to_the_status_class_and_keeps_the_code() -> None:
    """Offline half of A5 (no request): an unknown code keeps `code` on the status class."""
    from relaygpu import error_from_response

    err = error_from_response(403, {"error": {"code": "INVENTED_2099", "message": "m", "request_id": "r"}}, {})
    assert type(err) is PermissionDeniedError and err.code == "INVENTED_2099"
