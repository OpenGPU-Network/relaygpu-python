"""Client-side cost estimate from the public ``/v2/pricing`` rows (port of src/estimate.ts, F8). An ESTIMATE, never an
invoice: the server bills from provider-reported usage, applies custom-tier overrides the public list never shows, and
resolves tier keys with rules this file only mirrors. Pure: ``_async/estimate.py`` only fetches the pricing.

Numbers in ``basis`` are formatted exactly as JavaScript prints them, so the strings are byte-identical to the TS SDK."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from ._exceptions import RelayError
from .types import CostEstimate, PricingResponse, UsageInput

M = 1_000_000

Part = tuple[float, str]
"""``(usd, basis)`` of one line of the estimate."""


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


def _to_precision6(n: float) -> float:
    """``+n.toPrecision(6)``: six significant digits, ties away from zero (the spec picks the larger n)."""
    if n == 0 or not math.isfinite(n):
        return float(n)
    d = Decimal(float(n))
    return float(d.quantize(Decimal(1).scaleb(d.adjusted() - 5), rounding=ROUND_HALF_UP))


def money(n: float) -> str:
    return "$" + js_number(_to_precision6(n))


def _js_round(x: float) -> float:
    """``Math.round``: to the nearest integer, ties toward +∞."""
    f = math.floor(x)
    return float(f + 1 if x - f >= 0.5 else f)


def _or(v: Any, default: Any) -> Any:
    """``v ?? default``."""
    return default if v is None else v


def _num(v: Any) -> bool:
    """``typeof v === "number"``."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_name(row: Mapping[str, Any]) -> str:
    """The row's model name without its ``source.`` prefix (``video.KlingTeam/v3-T2V`` → ``KlingTeam/v3-T2V``)."""
    model: str = row["model"]
    return model[model.find(".") + 1 :]


def _find_row(rows: Sequence[Mapping[str, Any]], model: str, mode: str) -> Mapping[str, Any] | None:
    in_mode = [r for r in rows if r.get("mode") == mode]
    hit = next((r for r in in_mode if _row_name(r) == model), None)
    return hit if hit is not None else next((r for r in in_mode if r.get("model") == model), None)


def _tier_rate(m: Mapping[str, Any] | None, candidates: Sequence[str | None]) -> tuple[float, str] | None:
    """Mirrors the server's tier fallback: first matching candidate, then ``default``, then the cheapest cell."""
    if not m:
        return None
    for c in candidates:
        if c and _num(m.get(c)):
            return m[c], c
    if _num(m.get("default")):
        return m["default"], "default"
    cells = sorted(((k, v) for k, v in m.items() if _num(v)), key=lambda e: e[1])
    if not cells:
        return None
    return cells[0][1], cells[0][0]


def _tokens(row: Mapping[str, Any], u: UsageInput, allow_context_tier: bool = True) -> Part:
    inp = _or(u.get("input_tokens"), 0)
    out = _or(u.get("output_tokens"), 0)
    cached = _or(u.get("cached_input_tokens"), 0)
    w5 = _or(u.get("cache_write_5m_input_tokens"), 0)
    w1 = _or(u.get("cache_write_1h_input_tokens"), 0)
    threshold = row.get("context_tier_threshold")
    long = allow_context_tier and threshold is not None and inp > threshold

    def r(field: str) -> float | None:
        v = row.get(f"{field}_long") if long else None
        if v is None:
            v = row.get(field)
        return v if _num(v) else None

    in_rate = r("per_1m_input_tokens")
    in_rate = 0 if in_rate is None else in_rate
    out_rate = r("per_1m_output_tokens")
    out_rate = 0 if out_rate is None else out_rate
    cache_rate = r("per_1m_cached_input_tokens")
    parts: list[str] = []
    if w5 > 0 or w1 > 0 or (cache_rate is not None and cached > 0):
        read_rate = in_rate if cache_rate is None else cache_rate
        fresh = max(inp - cached - w5 - w1, 0)
        usd = (fresh * in_rate + cached * read_rate + out * out_rate) / M
        parts += [f"{js_number(fresh)} in × {money(in_rate)}/1M", f"{js_number(cached)} cached × {money(read_rate)}/1M"]
        for count, field, label in (
            (w5, "per_1m_cache_write_5m_input_tokens", "5m-write"),
            (w1, "per_1m_cache_write_1h_input_tokens", "1h-write"),
        ):
            if count > 0:
                rate = r(field)
                rate = in_rate if rate is None else rate
                usd += (count * rate) / M
                parts.append(f"{js_number(count)} {label} × {money(rate)}/1M")
    else:
        usd = (inp * in_rate + out * out_rate) / M
        parts.append(f"{js_number(inp)} in × {money(in_rate)}/1M")
    parts.append(f"{js_number(out)} out × {money(out_rate)}/1M")
    return usd, f"per_token{' (long context)' if long else ''}: {' + '.join(parts)}"


def _images(row: Mapping[str, Any], u: UsageInput) -> Part:
    count = _or(u.get("image_count"), 1)
    res = row.get("per_image_resolution")
    if isinstance(res, Mapping) and res:
        t = _tier_rate(res, [u.get("resolution_tier")])
        if t is None:
            raise RelayError(f"estimate_cost: no usable per_image_resolution rate for {row['model']}")
        return count * t[0], f"per_image: {js_number(count)} × {money(t[0])} ({t[1]})"
    rate = row.get("per_image")
    rate = 0 if rate is None else rate
    return count * rate, f"per_image: {js_number(count)} × {money(rate)}"


def _sound(u: UsageInput) -> str | None:
    s = u.get("sound")
    return None if s is None else "sound" if s else "silent"


def _video_candidates(u: UsageInput) -> list[str | None]:
    """``resolution|quality|sound``, ``quality|sound``, ``quality|ref``, ``resolution``: the server's candidate chain."""
    sound = _sound(u)
    has_ref = u.get("has_ref")
    ref = None if has_ref is None else "w-ref" if has_ref else "no-ref"
    quality = u.get("quality_mode")
    resolution = u.get("resolution_tier")
    grid = f"{quality}|{sound}" if quality and sound else None
    ref_grid = f"{quality}|{ref}" if quality and ref else None
    return [f"{resolution}|{grid}" if resolution and grid else None, grid, ref_grid, resolution]


def _media_rate(v: Any, u: UsageInput) -> float:
    if _num(v):
        rate: float = v
        return rate
    if isinstance(v, Mapping):
        t = _tier_rate(v, [_sound(u)])
        return 0 if t is None else t[0]
    return 0


def _base(row: Mapping[str, Any], u: UsageInput) -> Part:
    bt = row.get("billing_type")
    if bt == "per_token":
        return _tokens(row, u)
    if bt == "per_image":
        return _images(row, u)
    if bt == "per_image_plus_tokens":
        a, b = _images(row, u), _tokens(row, u, False)
        return a[0] + b[0], f"{a[1]} + {b[1]}"
    if bt == "per_character":
        n = _or(u.get("character_count"), 0)
        rate = row.get("per_1k_characters")
        rate = 0 if rate is None else rate
        return (n * rate) / 1000, f"per_character: {js_number(n)} chars × {money(rate)}/1K"
    if bt == "per_second_audio":
        s = _or(u.get("duration_seconds"), 0)
        rate = row.get("per_second_audio")
        rate = 0 if rate is None else rate
        return s * rate, f"per_second_audio: {js_number(s)} s × {money(rate)}"
    if bt == "per_second_video":
        s = _or(u.get("duration_seconds"), 0)
        psv = row.get("per_second_video")
        t = _tier_rate(psv if isinstance(psv, Mapping) else None, _video_candidates(u))
        if t is None:
            raise RelayError(f"estimate_cost: no usable per_second_video rate for {row['model']}")
        return s * t[0], f"per_second_video: {js_number(s)} s × {money(t[0])} ({t[1]})"
    if bt == "per_media_token":
        i = _or(u.get("media_input_tokens"), 0)
        o = _or(u.get("media_output_tokens"), 0)
        ri = _media_rate(row.get("per_1m_media_input_tokens"), u)
        ro = _media_rate(row.get("per_1m_media_output_tokens"), u)
        return (i * ri + o * ro) / M, f"per_media_token: {js_number(i)} in × {money(ri)}/1M + {js_number(o)} out × {money(ro)}/1M"
    if bt == "per_request":
        n = _or(u.get("request_count"), 1)
        per_request = row.get("per_request")
        if not _num(per_request):
            raise RelayError(f"estimate_cost: per_request row for {row['model']} carries no rate")
        return n * per_request, f"per_request: {js_number(n)} × {money(cast(float, per_request))}"
    raise RelayError(f'estimate_cost: billing type "{bt}" of {row["model"]} is not known to this SDK version')


def _storage_fee(storage: Mapping[str, Any] | None, sku: str | None, free: str, files: float, label: str) -> Part | None:
    if not sku or sku == free:
        return None
    fee = storage.get(sku) if storage else None
    if not _num(fee):
        offered = [k for k in storage if k != "unit"] if storage else []
        raise RelayError(f'estimate_cost: {label} "{sku}" is not offered (media_storage: {", ".join(offered) if offered else "none"})')
    rate = cast(float, fee)
    return files * rate, f"{label} {sku}: {js_number(files)} file × {money(rate)}"


def estimate(pricing: PricingResponse, model: str, usage: UsageInput) -> CostEstimate:
    """The estimate from a ``/v2/pricing`` body. Raises ``RelayError`` for a model with no row in the mode, an unknown
    billing type or an unoffered storage SKU."""
    mode: str = _or(usage.get("mode"), "direct")
    rows: Sequence[Mapping[str, Any]] = pricing["pricing"]
    row = _find_row(rows, model, mode)
    if row is None:
        raise RelayError(f'estimate_cost: no {mode} pricing row for "{model}" in /v2/pricing')
    parts = [_base(row, usage)]
    files: float = _or(usage.get("file_count"), _or(usage.get("image_count"), 1))
    storage: Mapping[str, Any] | None = pricing.get("media_storage")
    for fee in (
        _storage_fee(storage, usage.get("store_output"), "provider", files, "store_output"),
        _storage_fee(storage, usage.get("retention"), "relay1h", files, "retention"),
    ):
        if fee is not None:
            parts.append(fee)
    usd: float = 0
    for p in parts:
        usd = usd + p[0]
    return {"usd": _js_round(usd * 1e8) / 1e8, "basis": " + ".join(p[1] for p in parts)}
