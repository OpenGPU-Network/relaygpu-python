"""Run a workflow (an LLM writes a line, a TTS model speaks it) and wait for the result.

python examples/05_workflow_run.py "a lighthouse at dusk"
"""

import json
import os
import sys

from relaygpu import Relay, TaskFailedError


def main() -> int:
    with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
        try:
            # Inputs follow the template's input_schema: relay.workflows.get("script-voiceover").
            # Each step is billed as an ordinary request. Pass webhook_url for one signed workflow.* delivery instead of waiting.
            run = relay.workflows.run(
                "script-voiceover",
                {
                    "messages": [{"role": "user", "content": sys.argv[1] if len(sys.argv) > 1 else "a lighthouse at dusk"}],
                    "voice": "Cherry",
                },
                wait=True,
                on_progress=lambda r: print(r["status"]),
            )
            print("run:", run["run_id"], run["status"])
            print("output:", json.dumps(run.get("output"), indent=2))  # the last step's output (the speech)
        except TaskFailedError as e:
            # A failed or cancelled run raises; e.task is the run as polled.
            print(f"run {e.task_id} did not complete: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
