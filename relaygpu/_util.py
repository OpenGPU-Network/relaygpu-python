"""Small pure helpers shared by both clients."""

from __future__ import annotations

import base64
import binascii
import math
import re
import uuid
from decimal import Decimal
from urllib.parse import quote

# encodeURIComponent's unreserved set (beyond the alphanumerics and `-_.~` quote() always keeps).
_URI_COMPONENT_SAFE = "!*'()"
_ASCII_WS = re.compile(r"[\t\n\f\r ]")
_B64 = re.compile(r"[A-Za-z0-9+/]*")


def random_uuid() -> str:
    """A v4 UUID from the OS CSPRNG (idempotency keys must be unguessable)."""
    return str(uuid.uuid4())


def path_id(value: str) -> str:
    """One path segment: percent-encoded like encodeURIComponent, with the task-id colon kept literal."""
    return quote(value, safe=_URI_COMPONENT_SAFE + ":")


def model_path(name: str) -> str:
    """A model name as a path: the `/` stays literal (the route is `{model:path}`), every other reserved character is encoded."""
    return "/v2/models/" + "/".join(quote(seg, safe=_URI_COMPONENT_SAFE) for seg in name.split("/"))


def forgiving_b64decode(s: str) -> bytes | None:
    """``atob`` semantics (the WHATWG forgiving-base64 decode): ASCII whitespace dropped, padding optional; ``None``
    when ``s`` is not base64."""
    s = _ASCII_WS.sub("", s)
    if len(s) % 4 == 0 and s.endswith("="):
        s = s[:-2] if s.endswith("==") else s[:-1]
    if len(s) % 4 == 1 or not _B64.fullmatch(s):
        return None
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None


def js_number(x: float) -> str:
    """ECMAScript ``Number.prototype.toString()`` (shortest round-trip digits, JS exponent thresholds)."""
    if isinstance(x, bool):
        x = int(x)
    if x != x:
        return "NaN"
    if x in (math.inf, -math.inf):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    t = Decimal(repr(abs(float(x)))).normalize().as_tuple()
    digits = "".join(str(d) for d in t.digits)
    k = len(digits)
    n = int(t.exponent) + k
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits if k == 1 else digits[0] + "." + digits[1:]
        s = f"{mant}e{'+' if e > 0 else '-'}{abs(e)}"
    return sign + s
