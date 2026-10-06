"""The transport-free half of the image helpers (port of src/image.ts): parsing a URL / data URI / bare base64 value,
the MAGIC mime sniffer, the ``output_format`` hint and the walk over every image response shape the live routes use."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ._exceptions import RelayError
from ._util import forgiving_b64decode

MAGIC: list[tuple[str, str]] = [
    ("iVBORw0KGgo", "image/png"),
    ("/9j/", "image/jpeg"),
    ("UklGR", "image/webp"),
    ("R0lGOD", "image/gif"),
]

FORMAT_MIME: dict[str, str] = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}

_DATA_URI = re.compile(r"^data:([^;,]+)?(;base64)?,", re.I)
_HTTP_URL = re.compile(r"^https?://", re.I)


def download_error(status: int) -> RelayError:
    return RelayError(f"Image download failed: HTTP {status} (result links expire; see store_output)", status=status)


def parse_image_value(value: str, mime_hint: str | None = None) -> tuple[str | None, str | None, str | None]:
    """``(url, b64, mime_type)`` of a URL, a data URI or a bare base64 string (``toRelayImage``'s rule)."""
    data_uri = _DATA_URI.match(value)
    if not data_uri and _HTTP_URL.match(value):
        return value, None, None
    b64 = value[data_uri.end() :] if data_uri else value
    mime = (data_uri.group(1) if data_uri else None) or next((m for prefix, m in MAGIC if b64.startswith(prefix)), None) or mime_hint
    return None, b64, mime


def decode_b64(b64: str) -> bytes:
    """``atob(b64.trim())``: the forgiving-base64 decode of the trimmed value."""
    out = forgiving_b64decode(b64.strip())
    if out is None:
        raise RelayError("Image payload is not valid base64")
    return out


def image_values(body: Mapping[str, Any], input: Mapping[str, Any] | None = None) -> tuple[list[str], str | None]:
    """Every image value of a response body, in order, and the mime hint from the input's ``output_format``:
    ``urls[]`` / ``images[]`` / ``data[]`` (strings or ``{url|b64_json|b64|base64}`` objects), else a single
    ``url`` / ``image`` / ``image_url`` / ``b64_json``."""
    fmt = (input or {}).get("output_format")
    hint = FORMAT_MIME.get(fmt.lower()) if isinstance(fmt, str) else None
    out: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v:
            out.append(v)
        elif isinstance(v, Mapping) and v:
            add(next((v[k] for k in ("url", "b64_json", "b64", "base64") if v.get(k) is not None), None))

    for key in ("urls", "images", "data"):
        items = body.get(key)
        if isinstance(items, list):
            for item in items:
                add(item)
    if not out:
        for key in ("url", "image", "image_url", "b64_json"):
            add(body.get(key))
    return out, hint
