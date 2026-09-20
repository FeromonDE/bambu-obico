from __future__ import annotations

import json
import logging
import queue
import ssl
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

import websocket

LOG = logging.getLogger(__name__)


def _ws_url(server: str) -> str:
    parsed = urlparse(server.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/") + "/ws/dev/", "", "", ""))


class ObicoConn:
    """Small Obico device WebSocket client compatible with moonraker-obico auth."""

    def __init__(
        self,
        server: str,
        auth_token: str,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        on_open: Callable[[], None] | None = None,
    ) -> None:
        self.url = _ws_url(server)
        self.auth_token = auth_token
        self.on_message = on_message
        self.on_open = on_open
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=50)
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._sender: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def send(self, data: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            LOG.warning("Obico message queue full; dropping status update")

    def _on_open(self, ws) -> None:
        self._connected.set()
        LOG.info("Obico WebSocket connected")
        if self.on_open is not None:
            self.on_open()

    def _on_close(self, ws, status_code, message) -> None:
        self._connected.clear()
        LOG.warning("Obico WebSocket closed: %s", status_code)
        if status_code == 4321:
            LOG.error("Obico rejected a shared auth token; stopping")
            self._stop.set()

    def _on_error(self, ws, error) -> None:
        self._connected.clear()
        LOG.warning("Obico WebSocket error: %s", error)

    def _on_ws_message(self, ws, message) -> None:
        if self.on_message is None:
            return
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            LOG.debug("Ignoring non-JSON Obico message")
            return
        self.on_message(data)

    def _sender_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            while not self._stop.is_set() and not self.connected:
                time.sleep(0.2)
            if self._stop.is_set():
                break
            try:
                assert self._ws is not None
                self._ws.send(json.dumps(data, default=str))
            except Exception as exc:
                LOG.warning("Failed to send Obico status: %s", exc)
                self._connected.clear()
                # Preserve the newest failed update when possible.
                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    pass

    def run_forever(self) -> None:
        self._stop.clear()
        if self._sender is None or not self._sender.is_alive():
            self._sender = threading.Thread(target=self._sender_loop, daemon=True)
            self._sender.start()

        delay = 1
        while not self._stop.is_set():
            self._connected.clear()
            self._ws = websocket.WebSocketApp(
                self.url,
                header=["authorization: bearer " + self.auth_token],
                on_open=self._on_open,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_ws_message,
            )
            LOG.info("Connecting to Obico WebSocket")
            self._ws.run_forever()
            if self._stop.is_set():
                break
            time.sleep(delay)
            delay = min(delay * 2, 30)

    def stop(self) -> None:
        self._stop.set()
        self._connected.clear()
        if self._ws is not None:
            self._ws.close()
