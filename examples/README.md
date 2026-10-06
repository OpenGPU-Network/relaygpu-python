# Examples

Six runnable quickstarts for `relaygpu-client` (plus an `asyncio` twin of the second). Run them from the repo
root, against the package installed from this checkout or from the built wheel.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .                 # or: python -m build && pip install dist/relaygpu_client-0.1.0-py3-none-any.whl
export RELAY_API_KEY=relay_sk_...
# export RELAY_BASE_URL=...      # optional; default https://relaygpu.com
python examples/01_image.py
```

| Variable | Used by | |
|---|---|---|
| `RELAY_API_KEY` | every example except `04 --self-test` | required; passed to `Relay(...)` explicitly (the SDK never reads the environment) |
| `RELAY_BASE_URL` | all | optional; default `https://relaygpu.com` |
| `RELAY_WEBHOOK_SECRET` | `04_webhook_receiver.py` | the account's `whsec_…` secret (`relay.webhooks.secret()`, JWT or superkey) |

| File | What it does | Run | Billed |
|---|---|---|---|
| `01_image.py` | `Qwen/qwen-image`, prints the link, saves the image to the temp dir | `python examples/01_image.py ["prompt"]` | yes |
| `02_video_wait.py` | Kling v3 T2V, 3 s, `std`; waits with progress | `python examples/02_video_wait.py ["prompt"]` | yes |
| `02_video_wait_async.py` | the same with `AsyncRelay` + `asyncio.run`: submit, then `tasks.wait` | `python examples/02_video_wait_async.py ["prompt"]` | yes |
| `03_upload_motion_control.py` | explicit upload (`relay1d`) + implicit bytes upload into Kling v3 Motion Control; returns the `202`, deletes the explicit upload | `python examples/03_upload_motion_control.py <video.mp4> <image URL>` | yes |
| `04_webhook_receiver.py` | verifies deliveries on `:8787` (or `$PORT`) with `RELAY_WEBHOOK_SECRET`, stdlib `http.server` | `python examples/04_webhook_receiver.py` | no |
| | self-test: signs deliveries with a throwaway secret, posts them to itself, exits 0; offline, no key | `python examples/04_webhook_receiver.py --self-test` | no |
| `05_workflow_run.py` | `script-voiceover` workflow, waits, prints the output | `python examples/05_workflow_run.py ["brief"]` | yes |
| `06_chat_base_url.py` | the OpenAI Python SDK pointed at Relay | `pip install openai`, then `python examples/06_chat_base_url.py` | yes |

`openai` is needed by `06_chat_base_url.py` only; it is not a dependency of `relaygpu-client`.

Motion control needs a video of a person moving (MP4/MOV, at least 3 s, at most 100 MB) and a public URL of an
image showing one clear human character.

Check them without a billed call: `python -m py_compile examples/*.py` and
`python examples/04_webhook_receiver.py --self-test` (CI runs both).
