from __future__ import annotations

import logging
import threading
import time

from .config import load_config
from .connection import BambuConn
from .obico_conn import ObicoConn
from .obico_state import to_obico_message

LOG = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    if not cfg.obico_server or not cfg.obico_auth_token:
        raise SystemExit("OBICO_SERVER and OBICO_AUTH_TOKEN are required")

    obico = ObicoConn(cfg.obico_server, cfg.obico_auth_token)
    threading.Thread(target=obico.run_forever, daemon=True).start()

    # Temporary timestamp for protocol validation. Print lifecycle tracking
    # will replace this before the bridge is considered production-ready.
    current_print_ts = time.time()

    def on_bambu_state(state):
        message = to_obico_message(state, current_print_ts)
        if message:
            obico.send(message)

    bambu = BambuConn(cfg, on_state=on_bambu_state)
    try:
        bambu.run_forever()
    finally:
        obico.stop()


if __name__ == "__main__":
    main()
