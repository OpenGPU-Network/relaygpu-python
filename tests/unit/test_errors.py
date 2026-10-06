"""Port of test/unit/errors.test.ts: status picks the class, code refines it; stream-shaped errors; Retry-After."""

from __future__ import annotations

import email.utils
import json
import pickle
import time
from pathlib import Path
from typing import Any

import httpx

import relaygpu
from relaygpu import (
    CapacityError,
    IdempotencyKeyReusedError,
    InvalidRequestError,
    KeyBudgetExhaustedError,
    ModelNotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RelayAPIError,
    ScopeDeniedError,
    TaskFailedError,
    UpstreamTimeoutError,
    ValidationError,
    error_from_response,
    parse_retry_after,
)
from relaygpu._generated.error_codes import ERROR_CODE_CLASSES
from tests.helpers import load


def h(**kw: str) -> httpx.Headers:
    return httpx.Headers({k.replace("_", "-"): v for k, v in kw.items()})


def envelope(code: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "detail": "d",
        "error": {"code": code, "type": "authorization", "source": "client", "message": "m", "request_id": "req_1", **extra},
    }


def test_status_picks_the_class_code_refines_it() -> None:
    e = error_from_response(402, envelope("KEY_BUDGET_EXHAUSTED"), h())
    assert isinstance(e, KeyBudgetExhaustedError)
    assert (e.status, e.code, e.request_id, e.detail, str(e)) == (402, "KEY_BUDGET_EXHAUSTED", "req_1", "d", "m")
    assert isinstance(error_from_response(403, envelope("SCOPE_DENIED"), h()), ScopeDeniedError)
    assert isinstance(error_from_response(404, envelope("MODEL_NOT_FOUND"), h()), ModelNotFoundError)
    assert isinstance(error_from_response(422, envelope("IDEMPOTENCY_KEY_REUSED"), h()), IdempotencyKeyReusedError)


def test_unknown_code_degrades_to_the_status_class_and_keeps_the_code() -> None:
    e = error_from_response(403, envelope("SOMETHING_INVENTED_2099"), h())
    assert type(e) is PermissionDeniedError
    assert e.code == "SOMETHING_INVENTED_2099"


def test_null_code_status_class() -> None:
    e = error_from_response(503, envelope(None), h(retry_after="7"))
    assert type(e) is CapacityError
    assert e.code is None and e.retry_after == 7


def test_a_code_never_escapes_its_status() -> None:
    e = error_from_response(400, envelope("VALIDATION_ERROR"), h())
    assert type(e) is InvalidRequestError and e.code == "VALIDATION_ERROR"
    assert type(error_from_response(422, envelope("VALIDATION_ERROR"), h())) is ValidationError


def test_unmapped_status_relay_api_error_413_keeps_file_too_large() -> None:
    e = error_from_response(413, envelope("FILE_TOO_LARGE"), h())
    assert isinstance(e, RelayAPIError) and isinstance(e, relaygpu.FileTooLargeError) and e.code == "FILE_TOO_LARGE"
    assert type(error_from_response(418, None, h())) is RelayAPIError


def test_request_id_falls_back_to_header_non_json_body_keeps_a_message() -> None:
    e = error_from_response(502, "<html>bad gateway</html>", h(x_request_id="hdr_1"))
    assert e.request_id == "hdr_1"
    assert "bad gateway" in str(e)
    assert str(error_from_response(500, None, h())) == "HTTP 500"


def test_openai_shaped_stream_error_maps_from_error_type() -> None:
    e = error_from_response(504, {"error": {"message": "slow", "type": "worker_timeout", "code": 504}}, h(x_request_id="r"))
    assert isinstance(e, UpstreamTimeoutError)
    assert e.code is None and e.type == "worker_timeout" and e.request_id == "r"
    assert isinstance(
        error_from_response(429, {"error": {"message": "x", "type": "rate_limit_exceeded", "code": 429}}, h()), RateLimitError
    )


def test_validation_field_list_is_carried_in_detail() -> None:
    detail = [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]
    body = {
        "detail": detail,
        "error": {"code": "VALIDATION_ERROR", "type": "validation", "source": "client", "message": "1 error", "request_id": "r"},
    }
    assert error_from_response(422, body, h()).detail == detail


def test_parse_retry_after_reads_seconds_and_http_dates() -> None:
    assert parse_retry_after("3") == 3
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("soon") is None
    when = email.utils.formatdate(time.time() + 5, usegmt=True)
    assert (parse_retry_after(when) or 0) >= 3


def test_every_catalog_code_has_an_http_status() -> None:
    statuses = json.loads((Path(__file__).parents[2] / "scripts" / "error_statuses.json").read_text())
    for code in ERROR_CODE_CLASSES:
        assert isinstance(statuses.get(code), int), code


def test_error_classes_are_exported_and_match_the_ts_names() -> None:
    names = load("ts_surface_0.1.0.json")["errors"]
    missing = [n for n in names if not hasattr(relaygpu, n)]
    assert missing == []


def test_errors_pickle_and_repr_without_secrets() -> None:
    e = error_from_response(402, envelope("KEY_BUDGET_EXHAUSTED"), h())
    e2 = pickle.loads(pickle.dumps(e))
    assert type(e2) is KeyBudgetExhaustedError and e2.code == "KEY_BUDGET_EXHAUSTED" and e2.request_id == "req_1"
    t = TaskFailedError("boom", task_id="direct:1", task={"status": "failed"}, code="UPSTREAM_ERROR")
    t2 = pickle.loads(pickle.dumps(t))
    assert (t2.task_id, t2.code, str(t2)) == ("direct:1", "UPSTREAM_ERROR", "boom")
    assert repr(e) == "KeyBudgetExhaustedError('m', status=402, code='KEY_BUDGET_EXHAUSTED', request_id='req_1')"
