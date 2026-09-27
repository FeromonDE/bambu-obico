from __future__ import annotations

import logging
import threading
import time
from typing import Any, Mapping

import requests

LOG = logging.getLogger(__name__)

POST_PIC_INTERVAL_SECONDS = 10.0
IDLE_WATCH_INTERVAL_MULTIPLIER = 12
MAX_JPEG_SIZE = 7_000_000

_PRINTING_STATES = {"RUNNING", "PRINTING", "PREPARE"}


class SnapshotPoster:
    """Upload primary-camera JPEG frames to Obico for previews, AI and timelapse.

    Live viewing remains H264/WebRTC. Obico's print-history poster and timelapse
    pipeline are fed separately through POST /api/v1/octo/pic/.
    """

    def __init__(
        self,
        server: str,
        auth_token: str,
        snapshot_url: str,
        camera_name: str = "Eufy",
    ) -> None:
        self.endpoint = server.rstrip("/") + "/api/v1/octo/pic/"
        self.auth_token = auth_token
        self.snapshot_url = snapshot_url
        self.camera_name = camera_name

        self._stop = threading.Event()
        self._viewing_boost = threading.Event()
        self._lock = threading.RLock()
        self._printing = False
        self._viewing = False
        self._should_watch = False
        self._last_post_ts = 0.0
        self._normal_posts_this_print = 0
        self._thread: threading.Thread | None = None
        self._session = requests.Session()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        LOG.info("Obico JPEG snapshot poster started")

    def stop(self) -> None:
        self._stop.set()
        self._viewing_boost.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._session.close()

    def update_printer_state(self, state: Mapping[str, Any]) -> None:
        raw = str(state.get("gcode_state") or "").upper()
        printing = raw in _PRINTING_STATES
        with self._lock:
            if printing and not self._printing:
                # Always create at least one raw frame at the beginning of a print.
                self._last_post_ts = 0.0
                self._normal_posts_this_print = 0
            self._printing = printing

    def update_remote_status(self, remote_status: Mapping[str, Any]) -> None:
        with self._lock:
            was_viewing = self._viewing
            if "viewing" in remote_status:
                self._viewing = bool(remote_status.get("viewing"))
            if "should_watch" in remote_status:
                self._should_watch = bool(remote_status.get("should_watch"))
            if self._viewing and not was_viewing:
                self._viewing_boost.set()

    def _capture(self) -> bytes:
        response = self._session.get(
            self.snapshot_url,
            timeout=5,
            verify=False,
        )
        response.raise_for_status()
        data = response.content
        if not data:
            raise ValueError("snapshot endpoint returned an empty response")
        if len(data) > MAX_JPEG_SIZE:
            raise ValueError("snapshot exceeds Obico's 7 MB capture limit")
        return data

    def _post(self, viewing_boost: bool) -> None:
        jpeg = self._capture()
        response = self._session.post(
            self.endpoint,
            headers={"Authorization": f"Token {self.auth_token}"},
            files={"pic": ("snapshot.jpg", jpeg, "image/jpeg")},
            data={
                "is_primary_camera": "true",
                "is_nozzle_camera": "false",
                "camera_name": self.camera_name,
                "viewing_boost": "true" if viewing_boost else "false",
            },
            timeout=20,
        )
        response.raise_for_status()

        if not viewing_boost:
            with self._lock:
                self._normal_posts_this_print += 1
                count = self._normal_posts_this_print
            if count == 1:
                LOG.info("First Obico print snapshot uploaded")
            else:
                LOG.debug("Obico print snapshot uploaded")
        else:
            LOG.debug("Obico viewing-boost snapshot uploaded")

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._viewing_boost.wait(timeout=1.0):
                self._viewing_boost.clear()
                if self._stop.is_set():
                    break
                try:
                    self._post(viewing_boost=True)
                except Exception as exc:
                    LOG.warning("Failed to upload Obico viewing snapshot: %s", exc)
                continue

            now = time.time()
            with self._lock:
                if not self._printing:
                    continue
                interval = POST_PIC_INTERVAL_SECONDS
                if not self._viewing and not self._should_watch:
                    interval *= IDLE_WATCH_INTERVAL_MULTIPLIER
                if self._last_post_ts > now - interval:
                    continue
                # Match moonraker-obico: throttle before doing network work.
                self._last_post_ts = now

            try:
                self._post(viewing_boost=False)
            except Exception as exc:
                LOG.warning("Failed to upload Obico print snapshot: %s", exc)
