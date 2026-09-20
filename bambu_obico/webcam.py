from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import websocket

LOG = logging.getLogger(__name__)

JANUS_WS_PORT = 17730
VIDEO_PORT = 17732
VIDEO_RTCP_PORT = 17733
DATA_PORT = 17734
STREAM_ID = 1


class WebcamBridge:
    def __init__(
        self,
        auth_token: str,
        h264_http_url: str,
        snapshot_url: str,
        relay_to_obico: Callable[[dict[str, Any]], None],
    ) -> None:
        self.auth_token = auth_token
        self.h264_http_url = h264_http_url
        self.snapshot_url = snapshot_url
        self.relay_to_obico = relay_to_obico
        self.runtime = Path(tempfile.gettempdir()) / "bambu-obico-janus"
        self.janus_proc: subprocess.Popen | None = None
        self.ffmpeg_proc: subprocess.Popen | None = None
        self.janus_ws: websocket.WebSocketApp | None = None
        self._stop = threading.Event()

    def settings(self) -> dict[str, Any]:
        return {
            "webcams": [{
                "name": "Eufy",
                "is_primary_camera": True,
                "is_nozzle_camera": False,
                "stream_mode": "h264_copy",
                "stream_id": STREAM_ID,
                "data_channel_available": True,
                "flipV": False,
                "flipH": False,
                "rotation": 0,
                "streamRatio": "16:9",
            }],
            "data_channel_id": STREAM_ID,
        }

    def _write_configs(self) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        # Janus resolves plugin/transport .jcfg files directly from
        # --configs-folder, not from plugin/transport subdirectories.

        (self.runtime / "janus.jcfg").write_text(f"""
general: {{
    plugins_folder = "/usr/lib/x86_64-linux-gnu/janus/plugins"
    transports_folder = "/usr/lib/x86_64-linux-gnu/janus/transports"
    events_folder = "/usr/lib/x86_64-linux-gnu/janus/events"
    loggers_folder = "/usr/lib/x86_64-linux-gnu/janus/loggers"
    admin_secret = "janusoverlord"
}}
nat: {{
    turn_server = "turn.obico.io"
    turn_port = 80
    turn_type = "tcp"
    turn_user = "{self.auth_token}"
    turn_pwd = "{self.auth_token}"
    ice_ignore_list = "vmnet"
    ignore_unreachable_ice_server = true
}}
plugins: {{
    disable = "libjanus_audiobridge.so,libjanus_echotest.so,libjanus_nosip.so,libjanus_sip.so,libjanus_textroom.so,libjanus_videoroom.so,libjanus_duktape.so,libjanus_lua.so,libjanus_recordplay.so,libjanus_videocall.so,libjanus_voicemail.so"
}}
transports: {{
    disable = "libjanus_mqtt.so,libjanus_nanomsg.so,libjanus_pfunix.so,libjanus_rabbitmq.so,libjanus_http.so"
}}
""")
        (self.runtime / "janus.plugin.streaming.jcfg").write_text(f"""
h264-{STREAM_ID}: {{
    type = "rtp"
    id = {STREAM_ID}
    description = "h264-video"
    enabled = true
    audio = false
    video = true
    videoport = {VIDEO_PORT}
    videortcpport = {VIDEO_RTCP_PORT}
    videoiface = "127.0.0.1"
    videopt = 96
    videortpmap = "H264/90000"
    videofmtp = "profile-level-id=42e01f;packetization-mode=1"
    videobufferkf = true
    data = true
    dataport = {DATA_PORT}
    datatype = "binary"
    dataiface = "127.0.0.1"
    databuffermsg = false
}}
""")
        (self.runtime / "janus.transport.websockets.jcfg").write_text(f"""
general: {{
    json = "compact"
    ws = true
    ws_port = {JANUS_WS_PORT}
    ws_ip = "127.0.0.1"
    wss = false
}}
admin: {{
    admin_ws = false
    admin_wss = false
}}
""")

    def start(self) -> None:
        janus = shutil.which("janus")
        ffmpeg = shutil.which("ffmpeg")
        if not janus or not ffmpeg:
            raise RuntimeError("janus and ffmpeg must be installed")

        self._write_configs()
        self._stop.clear()
        self.janus_proc = subprocess.Popen(
            [janus, "--stun-server=stun.l.google.com:19302", "--configs-folder", str(self.runtime)],
            stdout=None,
            stderr=None,
        )
        for _ in range(50):
            if self.janus_proc.poll() is not None:
                raise RuntimeError(f"Janus exited with code {self.janus_proc.returncode}")
            try:
                import socket
                with socket.create_connection(("127.0.0.1", JANUS_WS_PORT), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Janus WebSocket did not open")

        self.janus_ws = websocket.WebSocketApp(
            f"ws://127.0.0.1:{JANUS_WS_PORT}/",
            subprotocols=["janus-protocol"],
            on_message=self._on_janus_message,
            on_error=lambda ws, err: LOG.warning("Janus WebSocket error: %s", err),
            on_close=lambda ws, code, msg: LOG.warning("Janus WebSocket closed: %s", code),
        )
        threading.Thread(target=self.janus_ws.run_forever, daemon=True).start()

        self.ffmpeg_proc = subprocess.Popen([
            ffmpeg, "-loglevel", "warning", "-re", "-i", self.h264_http_url,
            "-c:v", "copy", "-an", "-f", "rtp",
            f"rtp://127.0.0.1:{VIDEO_PORT}?pkt_size=1300",
        ])
        LOG.info("Eufy H264 webcam bridge started")

    def _on_janus_message(self, ws, raw: str) -> None:
        self.relay_to_obico({"janus": raw})

    def handle_obico_message(self, msg: dict[str, Any]) -> None:
        raw = msg.get("janus")
        if raw is None or self.janus_ws is None:
            return
        if not isinstance(raw, str):
            raw = json.dumps(raw)
        try:
            self.janus_ws.send(raw)
        except Exception as exc:
            LOG.warning("Failed to relay Obico signaling to Janus: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self.janus_ws is not None:
            self.janus_ws.close()
        for proc in (self.ffmpeg_proc, self.janus_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
