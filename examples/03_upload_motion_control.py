"""Upload a local video, then transfer its motion onto a character image (Kling v3 Motion Control).

python examples/03_upload_motion_control.py ./dance.mp4 https://example.com/character.png
"""

import os
import sys
from pathlib import Path

from relaygpu import FileTooLargeError, Relay, ValidationError


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: python examples/03_upload_motion_control.py <local video.mp4> <character image URL>", file=sys.stderr)
        return 2
    video_path, image_url = Path(sys.argv[1]), sys.argv[2]

    with Relay(os.environ["RELAY_API_KEY"], base_url=os.environ.get("RELAY_BASE_URL")) as relay:
        try:
            # Form 1, explicit: host the file yourself and get a link any *_url field takes.
            # relay1h is free; relay1d / relay7d / relay30d are billed per file (relay.pricing.get()["media_storage"]).
            # A Path is streamed from disk; the media type is sniffed from its first bytes; the filename defaults to its name.
            file = relay.files.upload(video_path, retention="relay1d")
            print("uploaded:", file["file_id"], file["url"], "expires", file["expires_at"])

            # Form 2, implicit: put the file straight into a *_url field (bytes, a Path, a binary file object or an
            # iterator of bytes; a str is always a URL). The SDK uploads it first (relay1h unless upload={"retention": ...}
            # says otherwise) and sends the link. "video_url": file["url"] would work the same.
            accepted = relay.video.generate(
                "KlingTeam/v3-Motion-Control",
                {
                    "video_url": video_path.read_bytes(),
                    "image_url": image_url,
                    "character_orientation": "video",  # "image" | "video": which input decides the facing direction
                    "duration": 5,
                    "quality_mode": "std",
                },
            )
            # No wait: the 202 envelope. Poll later with relay.tasks.wait(accepted["task_id"]), or pass webhook_url.
            print("task:", accepted["task_id"], "(replayed)" if accepted.get("replayed") else "")

            # Take the explicit upload down now (idempotent; the fee is not refunded).
            relay.files.delete(file["file_id"])
            print("deleted:", file["file_id"])
        except FileTooLargeError:
            print("files are capped at 100 MB", file=sys.stderr)
            return 1
        except ValidationError as e:
            print(f"rejected ({e.code}):", e, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
