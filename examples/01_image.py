"""Generate an image, print its link, save it to a temp file.

python examples/01_image.py "a red fox in an autumn forest"
"""

import os
import sys
import tempfile
from pathlib import Path

from relaygpu import ContentPolicyDeclinedError, InsufficientCreditsError, Relay


def main() -> int:
    with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
        try:
            result = relay.image.generate(
                "Qwen/qwen-image",
                {"prompt": sys.argv[1] if len(sys.argv) > 1 else "A red fox in an autumn forest", "size": "1024x1024"},
            )
            image = result.images[0]
            print("url:", image.url)  # a link that lives 1 h; pass store_output="relay7d" to keep it longer

            ext = (image.mime_type or "image/bin").split("/")[1]
            path = Path(tempfile.gettempdir()) / f"relay-image.{ext}"
            image.save(path)  # downloads the link (without your key) or decodes base64
            print("saved:", path)
        except InsufficientCreditsError as e:
            # Status picks the class, e.code refines it: KeyBudgetExhaustedError is an InsufficientCreditsError (402).
            print(f"out of credit ({e.code}), request {e.request_id}", file=sys.stderr)
            return 1
        except ContentPolicyDeclinedError as e:
            print("the provider declined this prompt:", e, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
