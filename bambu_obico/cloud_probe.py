from __future__ import annotations

import argparse
import json
import ssl
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

from .cloud_auth import DEFAULT_PATH


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only Bambu Cloud MQTT connectivity probe")
    ap.add_argument("--credentials", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--serial", help="Printer serial; defaults to BAMBU_SERIAL environment via config")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    creds = json.loads(args.credentials.read_text(encoding="utf-8"))
    token = creds["accessToken"]
    uid = str(creds["userId"])

    serial = args.serial
    if not serial:
        from .config import load_config
        serial = load_config().serial

    done = threading.Event()
    result = {"connected": False, "report": False}

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(f"u_{uid}", token)
    client.tls_set_context(ssl.create_default_context())

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"Cloud MQTT connection rejected: {reason_code}")
            done.set()
            return
        result["connected"] = True
        print("Cloud MQTT connected.")
        topic = f"device/{serial}/report"
        client.subscribe(topic)
        print("Subscribed to printer report topic (read-only; no command published).")

    def on_message(client, userdata, msg):
        result["report"] = True
        try:
            data = json.loads(msg.payload)
            section = data.get("print") or data.get("pushing") or {}
            command = section.get("command")
            fields = len(section) if isinstance(section, dict) else 0
            print(f"Received printer report: command={command!r}, fields={fields}")
        except Exception:
            print("Received printer report.")
        done.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("us.mqtt.bambulab.com", 8883, 60)
    client.loop_start()
    try:
        done.wait(args.timeout)
    finally:
        client.disconnect()
        client.loop_stop()

    if not result["connected"]:
        raise SystemExit("Cloud MQTT connection failed.")
    if not result["report"]:
        print("Cloud MQTT authentication/subscription succeeded; no report arrived during the wait window.")
    else:
        print("Cloud MQTT read-only probe successful.")


if __name__ == "__main__":
    main()
