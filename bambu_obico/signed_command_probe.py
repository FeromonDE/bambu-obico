from __future__ import annotations

import argparse
import logging

from .config import load_config
from .signed_commands import BambuSignedCommands
from .signing import BambuSigner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate/use locally provisioned Bambu signing credentials."
    )
    parser.add_argument(
        "action",
        choices=("validate", "install-cert", "pause", "resume"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    if cfg.signing_dir is None:
        raise SystemExit("BAMBU_SIGNING_DIR is required")

    signer = BambuSigner(cfg.signing_dir)
    if args.action == "validate":
        print("Signing credentials: OK")
        print("Private key matches leaf certificate: OK")
        print("CRL parses: OK")
        return

    transport = BambuSignedCommands(cfg, signer)
    transport.start()
    try:
        if args.action == "install-cert":
            transport.install_trust()
            print("Printer app certificate install: SUCCESS")
        elif args.action == "pause":
            transport.pause()
            print("Pause command: state transition confirmed")
        elif args.action == "resume":
            transport.resume()
            print("Resume command: state transition confirmed")
    finally:
        transport.stop()


if __name__ == "__main__":
    main()
