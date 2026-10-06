"""Generate a video and wait for it (long-polls the task; video takes minutes).

python examples/02_video_wait.py "a paper boat drifting down a rain gutter"
"""

import os
import sys

from relaygpu import Relay, TaskFailedError
from relaygpu.types import TaskProgress


def show(p: TaskProgress) -> None:
    # Status transitions and elapsed time only: there is no queue position, log stream or cancel.
    print(f"{p['status']} after {p['elapsed_seconds']}s")


def main() -> int:
    with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
        try:
            task = relay.video.generate(
                "KlingTeam/v3-T2V",
                {
                    "prompt": sys.argv[1] if len(sys.argv) > 1 else "A paper boat drifting down a rain gutter",
                    "duration": 3,
                    "quality_mode": "std",
                },
                wait=True,
                on_progress=show,
            )
            result = task.get("result") or {}
            urls = result.get("urls") or []
            print("video:", urls[0] if urls else None)  # expires 1 h after completion unless you pass store_output
        except TaskFailedError as e:
            # The task ran and failed: e.code is the task's error_code (e.g. CONTENT_POLICY_DECLINED, UPSTREAM_TIMEOUT).
            print(f"task {e.task_id} failed: {e.code or 'unclassified'}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
