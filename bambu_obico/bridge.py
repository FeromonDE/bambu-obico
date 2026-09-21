from __future__ import annotations

import logging
import threading
import time

from .config import load_config
from .connection import BambuConn
from .obico_conn import ObicoConn
from .lifecycle import PrintLifecycle
from .webcam import WebcamBridge
from .signed_commands import BambuSignedCommands, SignedCommandError
from .signing import BambuSigner, SigningError

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
    signed_control = None
    control_lock = threading.RLock()

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

    def run_printer_command(command: str) -> None:
        nonlocal signed_control
        if command not in {"pause", "resume"}:
            LOG.warning("Ignoring unsupported Obico printer command: %s", command)
            return
        if cfg.signing_dir is None:
            LOG.error("Cannot execute Obico %s: BAMBU_SIGNING_DIR is not configured", command)
            return

        try:
            with control_lock:
                if signed_control is None:
                    signer = BambuSigner(cfg.signing_dir)
                    signed_control = BambuSignedCommands(cfg, signer)
                    signed_control.start()
                    LOG.info("Signed Bambu control channel started")

                if command == "pause":
                    signed_control.pause()
                else:
                    signed_control.resume()
                LOG.info("Executed Obico printer command: %s", command)
        except (SignedCommandError, SigningError, OSError, ValueError) as exc:
            LOG.error("Obico printer command %s failed: %s", command, exc)

    def on_obico_message(message):
        if webcam is not None:
            webcam.handle_obico_message(message)

        commands = message.get("commands")
        if not isinstance(commands, list):
            return

        for item in commands:
            if not isinstance(item, dict):
                continue
            command = item.get("cmd")
            if command == "cancel":
                # Keep cancel deliberately disabled until pause/resume have
                # been validated through Obico on the real printer.
                LOG.warning("Ignoring Obico cancel command: cancel is not enabled yet")
                continue
            if command in {"pause", "resume"}:
                threading.Thread(
                    target=run_printer_command,
                    args=(command,),
                    daemon=True,
                ).start()

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
        if signed_control is not None:
            signed_control.stop()
        if webcam is not None:
            webcam.stop()
        obico.stop()


if __name__ == "__main__":
    main()
