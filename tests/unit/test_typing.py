"""The public import surface as type checkers see it: every error class and helper importable from `relaygpu`, and
the `wait` overloads narrowing the return type (a computed `__all__` once hid every error class from mypy)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SNIPPET = """
from relaygpu import (AsyncRelay, ContentPolicyDeclinedError, FileTooLargeError, KeyBudgetExhaustedError, Relay,
    RelayError, TaskFailedError, WebhookVerificationError, error_from_response, is_accepted, verify_webhook)
from relaygpu.types import AsyncAccepted, TaskStatus

def f(r: Relay) -> None:
    done: TaskStatus = r.video.generate("KlingTeam/v3-T2V", {}, wait=True)
    sub: AsyncAccepted = r.video.generate("KlingTeam/v3-T2V", {})
    out: dict[str, object] = r.run("Qwen/qwen-image", {})
"""


def test_mypy_sees_the_public_surface(tmp_path: Path) -> None:
    p = tmp_path / "snippet.py"
    p.write_text(SNIPPET)
    res = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", "--config-file", "/dev/null", str(p)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert res.returncode == 0, res.stdout + res.stderr
