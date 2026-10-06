"""Small pure helpers shared by both clients."""

from __future__ import annotations

import uuid
from urllib.parse import quote

# encodeURIComponent's unreserved set (beyond the alphanumerics and `-_.~` quote() always keeps).
_URI_COMPONENT_SAFE = "!*'()"


def random_uuid() -> str:
    """A v4 UUID from the OS CSPRNG (idempotency keys must be unguessable)."""
    return str(uuid.uuid4())


def path_id(value: str) -> str:
    """One path segment: percent-encoded like encodeURIComponent, with the task-id colon kept literal."""
    return quote(value, safe=_URI_COMPONENT_SAFE + ":")


def model_path(name: str) -> str:
    """A model name as a path: the `/` stays literal (the route is `{model:path}`), every other reserved character is encoded."""
    return "/v2/models/" + "/".join(quote(seg, safe=_URI_COMPONENT_SAFE) for seg in name.split("/"))
