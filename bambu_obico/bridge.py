from __future__ import annotations

import logging
import threading
import time

from .config import load_config
from .connection import BambuConn
from .obico_conn import ObicoConn
from .lifecycle import PrintLifecycle
from .webcam import WebcamBridge

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
    state_lock = threading.RLock()
    webcam = None

    def presence_message():
        message = {
            "settings": {
                "webcams": webcam.settings()["webcams"] if webcam else [],
                "data_channel_id": webcam.settings()["data_channel_id"] if webcam else None,
                "temperature": {"profiles": []},
                "agent": {"name": "bambu-obico", "version": "0.1.0"},
                "installed_plugins": [],
            }
        }
        with state_lock:
            if last_state is not None:
                update = lifecycle.update(last_state)
                if update.message:
                    message.update(update.message)
        return message

    def on_obico_message(message):
        if webcam is not None:
            webcam.handle_obico_message(message)

    obico = ObicoConn(
        cfg.obico_server,
        cfg.obico_auth_token,
        on_message=on_obico_message,
        on_open=lambda: obico.send(presence_message()),
    )

    if cfg.webcam_h264_http_url and cfg.webcam_snapshot_url:
        webcam = WebcamBridge(
            cfg.obico_auth_token,
            cfg.webcam_h264_http_url,
            cfg.webcam_snapshot_url,
            relay_to_obico=obico.send,
        )
        webcam.start()

    threading.Thread(target=obico.run_forever, daemon=True).start()

    def on_bambu_state(state):
        nonlocal last_state
        with state_lock:
            last_state = state
            update = lifecycle.update(state)
        if update.transition:
            LOG.info("Print lifecycle event: %s", update.transition)
        if update.message:
            obico.send(update.message)

    def heartbeat_loop():
        while True:
            time.sleep(60)
            with state_lock:
                if last_state is None:
                    continue
                update = lifecycle.update(last_state)
            if update.message:
                obico.send(update.message)
                LOG.debug("Sent Obico status heartbeat")

    threading.Thread(target=heartbeat_loop, daemon=True).start()

    bambu = BambuConn(cfg, on_state=on_bambu_state)
    try:
        bambu.run_forever()
    finally:
        if webcam is not None:
            webcam.stop()
        obico.stop()


if __name__ == "__main__":
    main()
