from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .cloud_auth import DEFAULT_PATH

LOG = logging.getLogger(__name__)


class CloudCommandError(RuntimeError):
    pass


class BambuCloudCommands:
    """Cloud MQTT command transport.

    Only pause/resume are exposed for now. Stop/cancel is intentionally absent
    until explicitly validated on a sacrificial print.
    """

    def __init__(self, serial: str, credentials: Path = DEFAULT_PATH, host: str = "us.mqtt.bambulab.com") -> None:
        data = json.loads(credentials.read_text(encoding="utf-8"))
        self.serial = serial
        self.host = host
        self._uid = str(data["userId"])
        self._token = data["accessToken"]
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: dict[str, tuple[threading.Event, dict[str, Any], str]] = {}

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bambu-obico-cloud-{self._seq}")
        self._client.username_pw_set(f"u_{self._uid}", self._token)
        self._client.tls_set_context(ssl.create_default_context())
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    @property
    def report_topic(self) -> str:
        return f"device/{self.serial}/report"

    @property
    def request_topic(self) -> str:
        return f"device/{self.serial}/request"

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOG.error("Cloud MQTT connection rejected: %s", reason_code)
            return
        client.subscribe(self.report_topic)
        self._connected.set()
        LOG.info("Cloud MQTT command transport connected")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        section = payload.get("print")
        if not isinstance(section, dict):
            return
        seq = str(section.get("sequence_id", ""))
        with self._lock:
            pending = self._pending.get(seq)
            if pending is None:
                return
            event, box, expected_command = pending
            if section.get("command") != expected_command:
                return
            box["response"] = section
            event.set()

    def start(self, timeout: float = 10.0) -> None:
        self._connected.clear()
        self._client.connect(self.host, 8883, 60)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self.stop()
            raise CloudCommandError("Timed out connecting to Bambu Cloud MQTT")

    def stop(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _next_sequence(self) -> str:
        with self._lock:
            self._seq += 1
            return str(self._seq)

    def _send_print_command(self, command: str, timeout: float = 10.0) -> dict[str, Any]:
        if command not in {"pause", "resume"}:
            raise ValueError(f"Command not enabled: {command}")
        if not self._connected.is_set():
            raise CloudCommandError("Cloud MQTT is not connected")

        seq = "0"
        event = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._pending[seq] = (event, box, command)
        try:
            payload = {"print": {"sequence_id": seq, "command": command}}
            info = self._client.publish(self.request_topic, json.dumps(payload))
            info.wait_for_publish(timeout=timeout)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise CloudCommandError(f"MQTT publish failed: rc={info.rc}")
            if not event.wait(timeout):
                raise CloudCommandError(f"No printer response for {command} (sequence_id={seq})")
            response = box["response"]
            # Preserve diagnostics from the printer response.
            result = str(response.get("result") or "").lower()
            if result and result != "success":
                raise CloudCommandError(
                    f"Printer rejected {command}: result={response.get('result')!r}, "
                    f"reason={response.get('reason')!r}"
                )
            return response
        finally:
            with self._lock:
                self._pending.pop(seq, None)

    def pause(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._send_print_command("pause", timeout)

    def resume(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._send_print_command("resume", timeout)
