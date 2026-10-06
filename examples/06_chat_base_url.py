"""Chat needs no Relay SDK: point the OpenAI SDK (or the Anthropic one, at /v2/anthropic) at Relay.

pip install openai        # an example-only dependency; relaygpu-client does not need it
python examples/06_chat_base_url.py
"""

import os

from openai import OpenAI

base = os.environ.get("RELAY_BASE_URL") or "https://relaygpu.com"
client = OpenAI(api_key=os.environ["RELAY_API_KEY"], base_url=f"{base}/v2/openai/v1")

completion = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello in five words."}],
    max_tokens=20,
)
print(completion.choices[0].message.content)
