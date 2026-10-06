"""Binary inputs (port of src/inputs.ts): what counts as a file, its media type, and the request-body rule that decides
whether each file in a request becomes a link (an upload) or an inline base64 string.

Pure: no network. The I/O half (reading files and iterators, uploading) lives in ``_async/files.py``; the two small
read seams at the bottom are the only blocking/threaded calls, kept here so both clients import their own one.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, Union, runtime_checkable

__all__ = [
    "INLINE_IMAGE_MAX_BYTES",
    "FileData",
    "SupportsRead",
    "file_name_of",
    "is_file_data",
    "sniff_media_type",
]

INLINE_IMAGE_MAX_BYTES = 4 * 1024 * 1024
"""Largest file ``inline_images=True`` encodes into the body; a larger one is uploaded."""

CHUNK_SIZE = 1024 * 1024
"""Bytes per read when a path or a file object is streamed."""

HEAD = 16
"""Bytes the sniffer looks at."""


@runtime_checkable
class SupportsRead(Protocol):
    """A binary file object (``open(p, "rb")``, ``io.BytesIO``, …)."""

    def read(self, size: int = ..., /) -> bytes: ...


BytesLike = Union[bytes, bytearray, memoryview]

FileData = Union[BytesLike, "os.PathLike[str]", SupportsRead, Iterator[bytes], AsyncIterator[bytes]]
"""Anything ``files.upload`` and the implicit upload accept. ``files.upload`` also takes a ``str`` path; inside a
request body a ``str`` is never file data (it is the URL or base64 value the caller wrote). An ``AsyncIterator`` is
read by ``AsyncRelay`` only."""


def is_bytes_like(v: object) -> bool:
    return isinstance(v, (bytes, bytearray, memoryview))


def is_file_data(v: object) -> bool:
    """bytes / bytearray / memoryview, a path object, a binary file object, or an iterator of bytes. Never a ``str``,
    a list or a dict."""
    if isinstance(v, (str, list, tuple, dict)):
        return False
    return is_bytes_like(v) or isinstance(v, os.PathLike) or callable(getattr(v, "read", None)) or isinstance(v, (Iterator, AsyncIterator))


def is_async_iterator(v: object) -> bool:
    return isinstance(v, AsyncIterator)


def is_stream(v: object) -> bool:
    """A file object or an iterator: its size is unknown and it can be read once (the TS ``ReadableStream`` case)."""
    return is_file_data(v) and not is_bytes_like(v) and not isinstance(v, os.PathLike)


def file_name_of(data: object) -> str | None:
    """The basename of a path, or of a file object's ``.name`` (the TS ``File.name``)."""
    name: object = None
    if isinstance(data, os.PathLike):
        name = os.fspath(data)
    elif callable(getattr(data, "read", None)):
        name = getattr(data, "name", None)
    if isinstance(name, bytes):
        name = os.fsdecode(name)
    if isinstance(name, str) and name:
        base = os.path.basename(name)
        return base or None
    return None


def size_of(data: object) -> int | None:
    """Known size in bytes, ``None`` for a file object or an iterator (never inlined by ``inline_images``)."""
    if isinstance(data, memoryview):
        return data.nbytes
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if isinstance(data, os.PathLike):
        return os.stat(data).st_size
    return None


# ---- the sniffer ----


def _at(b: bytes, i: int) -> int:
    return b[i] if i < len(b) else -1


def _ascii(b: bytes, at: int, s: str) -> bool:
    return b[at : at + len(s)] == s.encode("ascii")


def sniff_media_type(b: bytes) -> str | None:
    """The media type from the first bytes of a file; ``None`` when unrecognised."""
    if _at(b, 0) == 0x89 and _ascii(b, 1, "PNG"):
        return "image/png"
    if _at(b, 0) == 0xFF and _at(b, 1) == 0xD8 and _at(b, 2) == 0xFF:
        return "image/jpeg"
    if _ascii(b, 0, "GIF8"):
        return "image/gif"
    if _ascii(b, 0, "RIFF") and _ascii(b, 8, "WEBP"):
        return "image/webp"
    if _ascii(b, 0, "RIFF") and _ascii(b, 8, "WAVE"):
        return "audio/wav"
    if _ascii(b, 4, "ftyp"):
        if _ascii(b, 8, "qt  "):
            return "video/quicktime"
        if _ascii(b, 8, "M4A "):
            return "audio/mp4"
        return "video/mp4"
    if _at(b, 0) == 0x1A and _at(b, 1) == 0x45 and _at(b, 2) == 0xDF and _at(b, 3) == 0xA3:
        return "video/webm"
    if _ascii(b, 0, "OggS"):
        return "audio/ogg"
    if _ascii(b, 0, "fLaC"):
        return "audio/flac"
    if _ascii(b, 0, "ID3") or (_at(b, 0) == 0xFF and len(b) > 1 and (b[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    return None


def unknown_type_error(where: str) -> TypeError:
    return TypeError(f"Relay: cannot tell the media type of {where}; pass content_type (image/*, video/* or audio/*)")


UPLOAD_TYPE_ERROR = "Relay: files.upload takes bytes, a path, a binary file object or an iterator of bytes"


def as_chunk(c: object) -> bytes:
    """One chunk of a stream, refused unless it is bytes (a text-mode file or an iterator of str)."""
    if isinstance(c, bytes):
        return c
    if isinstance(c, (bytearray, memoryview)):
        return bytes(c)
    raise TypeError(f"Relay: a file stream must yield bytes, got {type(c).__name__} (open files in binary mode, 'rb')")


# ---- the request-body rule ----

Schema = Mapping[str, Any]

URL_KEY = re.compile(r"_urls?$")
B64_WORDS = re.compile(r"base64|data uri|data:[a-z]", re.I)
DATA_URI_WORDS = re.compile(r"data uri|data:[a-z]", re.I)
URL_WORDS = re.compile(r"\burls?\b", re.I)


def _deref(node: Any, root: Schema | None) -> Any:
    ref = node.get("$ref") if isinstance(node, Mapping) else None
    if not isinstance(ref, str) or root is None:
        return node
    name = ref.split("/")[-1]
    for table in ("$defs", "definitions"):
        defs = root.get(table)
        if isinstance(defs, Mapping) and defs.get(name) is not None:
            return defs[name]
    return node


def _branches(node: Any, root: Schema | None) -> list[Mapping[str, Any]]:
    """The node and its ``anyOf`` / ``oneOf`` / ``allOf`` branches."""
    n = _deref(node, root)
    if not isinstance(n, Mapping):
        return []
    out: list[Mapping[str, Any]] = [n]
    for kw in ("anyOf", "oneOf", "allOf"):
        for b in n.get(kw) or ():
            d = _deref(b, root)
            if isinstance(d, Mapping):
                out.append(d)
    return out


def property_schema(node: Any, key: str, root: Schema | None) -> Any:
    for b in _branches(node, root):
        props = b.get("properties")
        if isinstance(props, Mapping) and props.get(key) is not None:
            return props[key]
    return None


def item_schema(node: Any, root: Schema | None) -> Any:
    for b in _branches(node, root):
        items = b.get("items")
        if items is not None and items is not False:
            return items
    return None


def describe(node: Any, root: Schema | None) -> str:
    parts: list[str] = []
    for b in _branches(node, root):
        items = b.get("items")
        for d in (b.get("description"), items.get("description") if isinstance(items, Mapping) else None):
            if isinstance(d, str):
                parts.append(d)
    return " ".join(parts)


@dataclass(frozen=True)
class Field:
    key: str
    schema: Any
    inherited: str | None = None
    """An array item inherits the array field's description ("Each item is raw base64 or a data URI")."""


@dataclass(frozen=True)
class Rule:
    """How a field takes a file: a link, base64 (raw or data URI), or both."""

    url: bool
    base64: bool
    data_uri: bool


def field_rule(f: Field, root: Schema | None) -> Rule:
    desc = f"{describe(f.schema, root)} {f.inherited or ''}"
    url_key = bool(URL_KEY.search(f.key))
    b64 = bool(B64_WORDS.search(desc))
    return Rule(
        url=url_key or bool(URL_WORDS.search(desc)),
        base64=b64,
        # A *_url field takes a URL-shaped string, so a data URI; a plain "base64 string" field gets raw base64.
        data_uri=b64 and (url_key or bool(DATA_URI_WORDS.search(desc))),
    )


def check_field(f: Field, root: Schema | None) -> Rule:
    rule = field_rule(f, root)
    if not rule.url and not rule.base64:
        raise TypeError(
            f'Relay: field "{f.key}" holds file data but takes neither a URL nor base64'
            + ("" if root is not None else "; only *_url / *_urls fields are uploaded without the route's request schema")
        )
    return rule


def contains_file(v: object) -> bool:
    if is_file_data(v):
        return True
    if isinstance(v, (list, tuple)):
        return any(contains_file(x) for x in v)
    if isinstance(v, Mapping):
        return any(contains_file(x) for x in v.values())
    return False


def collect_files(value: Any, root: Schema | None) -> list[tuple[Any, Field, Rule]]:
    """Every file in the body, in walk order, with its field and rule. Raises on a misplaced file, so a bad body is
    refused before the first upload."""
    out: list[tuple[Any, Field, Rule]] = []

    def walk(v: Any, f: Field) -> None:
        if is_file_data(v):
            out.append((v, f, check_field(f, root)))
        elif isinstance(v, (list, tuple)):
            item = Field(f.key, item_schema(f.schema, root), f"{describe(f.schema, root)} {f.inherited or ''}")
            for x in v:
                walk(x, item)
        elif isinstance(v, Mapping):
            for k, x in v.items():
                if contains_file(x):
                    walk(x, Field(k, property_schema(f.schema, k, root)))

    walk(value, Field("", root))
    return out


def substitute(value: Any, replacements: Iterator[str]) -> Any:
    """A copy of ``value`` with each file swapped, in ``collect_files`` order, for the next replacement string."""
    if is_file_data(value):
        return next(replacements)
    if isinstance(value, (list, tuple)):
        return [substitute(x, replacements) for x in value]
    if isinstance(value, Mapping):
        return {k: substitute(x, replacements) if contains_file(x) else x for k, x in value.items()}
    return value


# ---- the read seam (scripts/unasync.py maps async_read → sync_read) ----


async def async_read(f: SupportsRead, size: int) -> bytes:
    """One chunk of a file object, off the event loop."""
    return as_chunk(await asyncio.to_thread(f.read, size))


def sync_read(f: SupportsRead, size: int) -> bytes:
    return as_chunk(f.read(size))
