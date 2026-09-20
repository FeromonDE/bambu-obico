from __future__ import annotations

import argparse
import json

from .cloud_commands import BambuCloudCommands
from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(description="Explicit Bambu Cloud pause/resume test")
    ap.add_argument("command", choices=["pause", "resume"])
    args = ap.parse_args()
    cfg = load_config()

    ctl = BambuCloudCommands(cfg.serial)
    ctl.start()
    try:
        response = ctl.pause() if args.command == "pause" else ctl.resume()
        safe = {
            "command": response.get("command"),
            "sequence_id": response.get("sequence_id"),
            "result": response.get("result"),
            "reason": response.get("reason"),
        }
        print(json.dumps(safe, ensure_ascii=False))
    finally:
        ctl.stop()


if __name__ == "__main__":
    main()
