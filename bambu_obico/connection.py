from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from .config import Config
from .state import StateCache

LOG = logging.getLogger(__name__)
StateCallback = Callable[[dict[str, Any]], None]


class BambuConn:
    """Read-only Bambu LAN MQTT connection with reconnect and state caching."""

    def __init__(self, config: Config, on_state: StateCallback | None = None) -> None:
        self.config = config
        self.on_state = on_state
        self.state = StateCache()
        self._stop = threading.Event()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="bambu-obico",
        )
        self._client.username_pw_set("bblp", config.access_code)
        self._client.tls_set_context(self._tls_context())
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

    @property
    def request_topic(self) -> str:
        return f"device/{self.config.serial}/request"

    def _tls_context(self) -> ssl.SSLContext:
        if self.config.tls_insecure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        return ssl.create_default_context()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOG.error("MQTT connection failed: %s", reason_code)
            return
        LOG.info("MQTT connected")
        # Never reuse cached state across sessions. A fresh pushall follows.
        self.state.clear()
        client.subscribe(self.config.report_topic)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        payload = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        client.publish(self.request_topic, json.dumps(payload))
        LOG.info("Requested full printer state")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        if not self._stop.is_set():
            LOG.warning("MQTT disconnected: %s; reconnect handled by paho", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOG.warning("Ignoring invalid MQTT JSON")
            return

        report = payload.get("print")
        if not isinstance(report, dict) or report.get("command") != "push_status":
            return

        was_initialized = self.state.initialized
        snapshot = self.state.apply(report)
        if not self.state.initialized:
            return

        if not was_initialized:
            LOG.info("Full state initialized with %d fields", len(snapshot))

        if self.on_state is not None:
            self.on_state(snapshot)

    def run_forever(self) -> None:
        self._stop.clear()
        LOG.info("Connecting to %s:%d", self.config.host, self.config.port)
        self._client.connect(self.config.host, self.config.port, keepalive=30)
        try:
            self._client.loop_forever(retry_first_connection=True)
        finally:
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        self._client.disconnect()
