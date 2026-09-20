from __future__ import annotations

import logging
import threading

from .config import load_config
from .connection import BambuConn
from .obico_conn import ObicoConn
from .lifecycle import PrintLifecycle

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

    lifecycle = PrintLifecycle()

    def on_bambu_state(state):
        update = lifecycle.update(state)
        if update.transition:
            LOG.info("Print lifecycle event: %s", update.transition)
        if update.message:
            obico.send(update.message)

    bambu = BambuConn(cfg, on_state=on_bambu_state)
    try:
        bambu.run_forever()
    finally:
        obico.stop()


if __name__ == "__main__":
    main()
