import json
import ssl
import time

import paho.mqtt.client as mqtt

from .config import load_config


INTERESTING_KEYS = (
    "gcode_state",
    "mc_percent",
    "mc_remaining_time",
    "layer_num",
    "total_layer_num",
    "nozzle_temper",
    "nozzle_target_temper",
    "bed_temper",
    "bed_target_temper",
    "subtask_name",
    "print_type",
    "stg_cur",
    "stg_cur_name",
)


def _find_print_payload(payload):
    if not isinstance(payload, dict):
        return {}
    candidate = payload.get("print")
    return candidate if isinstance(candidate, dict) else payload


def main():
    cfg = load_config()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bambu-obico-probe")
    client.username_pw_set("bblp", cfg.access_code)

    if cfg.tls_insecure:
        # Bambu printers use a self-signed certificate on the LAN MQTT endpoint.
        # Encryption remains enabled, but certificate/hostname verification is disabled.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context()

    client.tls_set_context(context)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"MQTT connection failed: {reason_code}")
            return
        print(f"MQTT connected; subscribing to {cfg.report_topic}")
        client.subscribe(cfg.report_topic)

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        request_topic = f"device/{cfg.serial}/request"
        payload = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        info = client.publish(request_topic, json.dumps(payload))
        print(f"Requested full state with pushing.pushall (mid={info.mid})")

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception as exc:
            print(f"Invalid JSON payload: {exc}")
            return

        if cfg.dump_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return

        report = _find_print_payload(payload)
        summary = {key: report[key] for key in INTERESTING_KEYS if key in report}
        if isinstance(report, dict):
            summary["_command"] = report.get("command")
            summary["_msg"] = report.get("msg")
            summary["_field_count"] = len(report)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    print(f"Connecting to Bambu MQTT at {cfg.host}:{cfg.port} ...")
    client.connect(cfg.host, cfg.port, keepalive=30)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
