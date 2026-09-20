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

    lifecycle = PrintLifecycle()
    last_state = None

    def presence_message():
        message = {
            "settings": {
                "webcams": [],
                "data_channel_id": None,
                "temperature": {"profiles": []},
                "agent": {"name": "bambu-obico", "version": "0.1.0"},
                "installed_plugins": [],
            }
        }
        if last_state is not None and lifecycle.current_print_ts is not None:
            update = lifecycle.update(last_state)
            if update.message:
                message.update(update.message)
        return message

    obico = ObicoConn(
        cfg.obico_server,
        cfg.obico_auth_token,
        on_open=lambda: obico.send(presence_message()),
    )
    threading.Thread(target=obico.run_forever, daemon=True).start()

    def on_bambu_state(state):
        nonlocal last_state
        last_state = state
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
