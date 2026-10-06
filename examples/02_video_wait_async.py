"""The async twin of 02_video_wait.py: AsyncRelay mirrors Relay, with ``await``.

python examples/02_video_wait_async.py "a paper boat drifting down a rain gutter"
"""

import asyncio
import os
import sys

from relaygpu import AsyncRelay, TaskFailedError
from relaygpu.types import TaskProgress


def show(p: TaskProgress) -> None:
    print(f"{p['status']} after {p['elapsed_seconds']}s")


async def main() -> int:
    async with AsyncRelay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
        try:
            # Submit, then wait separately: keep task_id if you want to resume after a restart (relay.tasks.wait).
            accepted = await relay.video.generate(
                "KlingTeam/v3-T2V",
                {
                    "prompt": sys.argv[1] if len(sys.argv) > 1 else "A paper boat drifting down a rain gutter",
                    "duration": 3,
                    "quality_mode": "std",
                },
            )
            print("task:", accepted["task_id"])
            # Cancellation is the event loop's: asyncio.timeout() / task.cancel() stop the wait (not the task).
            task = await relay.tasks.wait(accepted["task_id"], timeout=20 * 60, on_progress=show)
            urls = (task.get("result") or {}).get("urls") or []
            print("video:", urls[0] if urls else None)
        except TaskFailedError as e:
            print(f"task {e.task_id} failed: {e.code or 'unclassified'}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
