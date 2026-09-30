from __future__ import annotations

import sys

from .config import load_config
from .snapshot_poster import SnapshotPoster


def main() -> None:
    cfg = load_config()
    if not cfg.obico_server or not cfg.obico_auth_token:
        raise SystemExit("OBICO_SERVER and OBICO_AUTH_TOKEN are required")
    if not cfg.webcam_snapshot_url:
        raise SystemExit("WEBCAM_SNAPSHOT_URL is not configured")

    poster = SnapshotPoster(
        cfg.obico_server,
        cfg.obico_auth_token,
        cfg.webcam_snapshot_url,
        camera_name="Eufy",
    )

    try:
        jpeg = poster.capture_once()
        is_jpeg = len(jpeg) >= 4 and jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
        print(f"Snapshot capture: OK ({len(jpeg)} bytes, jpeg={'yes' if is_jpeg else 'unknown'})")
        if not is_jpeg:
            raise SystemExit("Snapshot endpoint did not return a complete JPEG")

        poster.post_once(viewing_boost=True)
        print("Obico snapshot upload: OK")
        print("The printer preview should refresh without starting a print.")
    finally:
        poster.stop()


if __name__ == "__main__":
    main()
