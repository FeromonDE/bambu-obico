from __future__ import annotations

import json
import logging
import secrets
import ssl
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from .config import Config
from .signing import BambuSigner

LOG = logging.getLogger(__name__)


class SignedCommandError(RuntimeError):
    pass


class BambuSignedCommands:
    """Signed LAN MQTT control channel for a secured Bambu printer.

    Only pause/resume are intentionally exposed. Stop/cancel remains disabled
    until pause/resume are validated on the target printer.
    """

    def __init__(self, config: Config, signer: BambuSigner) -> None:
        self.config = config
        self.signer = signer

        self._connected = threading.Event()
        self._subscribed = threading.Event()
        self._trusted = threading.Event()
        self._state_cond = threading.Condition()
        self._last_gcode_state: str | None = None
        self._last_bed_target: int | None = None
        self._last_nozzle_target: int | None = None
        self._fun: int | None = None
        self._device_public_key = None
        self._status_generation = 0

        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str, str], tuple[threading.Event, dict[str, Any]]] = {}

        # Random process-local starting points avoid replay/collision issues after
        # restarts while preserving the Studio 20000-29999 command-id window.
        self._print_seq = 20000 + secrets.randbelow(5000)
        self._security_seq = 25000 + secrets.randbelow(5000)

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bambu-obico-signed-{secrets.token_hex(4)}",
        )
        self._client.username_pw_set("bblp", config.access_code)
        self._client.tls_set_context(self._tls_context())
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

    @property
    def report_topic(self) -> str:
        return f"device/{self.config.serial}/report"

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
            LOG.error("Signed MQTT connection rejected: %s", reason_code)
            return
        self._connected.set()
        client.subscribe(self.report_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self._connected.clear()
        self._subscribed.clear()
        self._trusted.clear()
        if reason_code != 0:
            LOG.warning("Signed MQTT disconnected: %s", reason_code)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        self._subscribed.set()

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        print_section = payload.get("print")
        if isinstance(print_section, dict):
            if print_section.get("command") == "push_status":
                state = print_section.get("gcode_state")
                bed_target = print_section.get("bed_target_temper")
                nozzle_target = print_section.get("nozzle_target_temper")
                fun = print_section.get("fun")
                with self._state_cond:
                    changed = False
                    if isinstance(state, str):
                        self._last_gcode_state = state.upper()
                        changed = True
                    if isinstance(bed_target, (int, float)):
                        self._last_bed_target = int(round(bed_target))
                        changed = True
                    if isinstance(nozzle_target, (int, float)):
                        self._last_nozzle_target = int(round(nozzle_target))
                        changed = True
                    if isinstance(fun, str):
                        try:
                            self._fun = int(fun, 16)
                        except ValueError:
                            pass
                    self._status_generation += 1
                    if changed:
                        self._state_cond.notify_all()
                    else:
                        self._state_cond.notify_all()
            self._resolve_pending("print", print_section)

        security = payload.get("security")
        if isinstance(security, dict):
            self._resolve_pending("security", security)

    def _resolve_pending(self, family: str, section: dict[str, Any]) -> None:
        seq = str(section.get("sequence_id", ""))
        command = str(section.get("command", ""))
        key = (family, seq, command)
        with self._lock:
            pending = self._pending.get(key)
            if pending is None:
                return
            event, box = pending
            box["response"] = section
            event.set()

    def start(self, timeout: float = 10.0) -> None:
        self._connected.clear()
        self._subscribed.clear()
        self._client.connect(self.config.host, self.config.port, keepalive=30)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self.stop()
            raise SignedCommandError("Timed out connecting to printer MQTT")
        if not self._subscribed.wait(timeout):
            self.stop()
            raise SignedCommandError("Timed out subscribing to printer report topic")
        self.request_full_state(timeout=min(timeout, 5.0))

    def stop(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _next_print_seq(self) -> str:
        with self._lock:
            seq = self._print_seq
            self._print_seq += 1
            if self._print_seq >= 25000:
                self._print_seq = 20000
            return str(seq)

    def _next_security_seq(self) -> str:
        with self._lock:
            seq = self._security_seq
            self._security_seq += 1
            if self._security_seq >= 30000:
                self._security_seq = 25000
            return str(seq)

    def _publish_and_wait(
        self,
        family: str,
        command: str,
        seq: str,
        wire_json: str,
        timeout: float,
    ) -> dict[str, Any]:
        event = threading.Event()
        box: dict[str, Any] = {}
        key = (family, seq, command)
        with self._lock:
            self._pending[key] = (event, box)
        try:
            info = self._client.publish(self.request_topic, wire_json, qos=0)
            info.wait_for_publish(timeout=timeout)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise SignedCommandError(f"MQTT publish failed: rc={info.rc}")
            if not event.wait(timeout):
                raise SignedCommandError(
                    f"No {family}.{command} response for sequence_id={seq}"
                )
            return box["response"]
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def request_full_state(self, timeout: float = 5.0) -> None:
        """Request a fresh push_status snapshot and wait until one arrives."""
        with self._state_cond:
            generation = self._status_generation

        payload = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        info = self._client.publish(
            self.request_topic,
            json.dumps(payload, separators=(",", ":")),
            qos=0,
        )
        info.wait_for_publish(timeout=timeout)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise SignedCommandError(f"pushall publish failed: rc={info.rc}")

        deadline = time.monotonic() + timeout
        with self._state_cond:
            while self._status_generation == generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SignedCommandError("Timed out waiting for push_status after pushall")
                self._state_cond.wait(remaining)

    def check_trust(self, timeout: float = 5.0) -> bool:
        """Ask the printer whether our app certificate is currently trusted."""
        seq = self._next_security_seq()
        wire = json.dumps(
            {"security": {"sequence_id": seq, "command": "app_cert_list"}},
            separators=(",", ":"),
        )
        response = self._publish_and_wait(
            "security",
            "app_cert_list",
            seq,
            wire,
            timeout,
        )
        cert_ids = response.get("cert_ids")
        if not isinstance(cert_ids, list):
            raise SignedCommandError("Printer app_cert_list response has no cert_ids array")
        trusted = self.signer.cert_id in {str(x) for x in cert_ids}
        if trusted:
            self._trusted.set()
            LOG.info("Printer already trusts app certificate")
        else:
            self._trusted.clear()
            LOG.info("Printer does not currently trust app certificate")
        return trusted

    def ensure_trust(self, timeout: float = 10.0) -> None:
        """Check volatile trust state and install only when needed."""
        if self._trusted.is_set():
            return
        try:
            if self.check_trust(timeout=min(timeout, 5.0)):
                return
        except SignedCommandError as exc:
            LOG.warning("Could not verify app certificate list: %s; installing certificate", exc)
        self.install_trust(timeout=timeout)

    def install_trust(self, timeout: float = 10.0) -> dict[str, Any]:
        """Install app certificate + CRL into the printer's volatile trust store."""
        seq = self._next_security_seq()
        response = self._publish_and_wait(
            "security",
            "app_cert_install",
            seq,
            self.signer.app_cert_install(seq),
            timeout,
        )
        result = str(response.get("result") or "").upper()
        if result != "SUCCESS":
            raise SignedCommandError(
                "Printer did not accept app certificate: "
                f"result={response.get('result')!r}"
            )
        printer_cert = response.get("printer_cert")
        if isinstance(printer_cert, str) and printer_cert.strip():
            try:
                self._device_public_key = self.signer.public_key_from_certificate(
                    printer_cert.encode("ascii")
                )
                LOG.info("Cached printer device public key")
            except Exception as exc:
                LOG.warning("Could not parse printer device certificate: %s", exc)
        self._trusted.set()
        LOG.info("Printer accepted app certificate for this session")
        return response

    def _wait_for_state(self, desired: set[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._state_cond:
            while True:
                if self._last_gcode_state in desired:
                    return self._last_gcode_state or ""
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SignedCommandError(
                        f"Printer state did not reach {sorted(desired)}; "
                        f"last state={self._last_gcode_state!r}"
                    )
                self._state_cond.wait(remaining)

    def _send_print_command(
        self,
        command: str,
        desired_states: set[str],
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        if command not in {"pause", "resume"}:
            raise ValueError(f"Command not enabled: {command}")
        if not self._trusted.is_set():
            self.ensure_trust(timeout=min(timeout, 10.0))

        seq = self._next_print_seq()
        wire = self.signer.sign_print(
            {
                "sequence_id": seq,
                "command": command,
            }
        )

        # Some firmware echoes the command without a useful result. The actual
        # push_status transition is therefore the authoritative success check.
        try:
            response = self._publish_and_wait(
                "print", command, seq, wire, min(timeout, 5.0)
            )
            result = str(response.get("result") or "").upper()
            if result and result != "SUCCESS":
                raise SignedCommandError(
                    f"Printer rejected {command}: result={response.get('result')!r}, "
                    f"reason={response.get('reason')!r}"
                )
        except SignedCommandError as exc:
            if not str(exc).startswith("No print."):
                raise
            response = {}

        self._wait_for_state(desired_states, timeout)
        return response

    def _wait_for_bed_target(self, target: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        with self._state_cond:
            while True:
                if self._last_bed_target == target:
                    return target
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SignedCommandError(
                        f"Bed target did not reach {target} C; "
                        f"last target={self._last_bed_target!r}"
                    )
                self._state_cond.wait(remaining)

    def _wait_for_nozzle_target(self, target: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        with self._state_cond:
            while True:
                if self._last_nozzle_target == target:
                    return target
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SignedCommandError(
                        f"Nozzle target did not reach {target} C; "
                        f"last target={self._last_nozzle_target!r}"
                    )
                self._state_cond.wait(remaining)

    def _mqtt_bed_temp_supported(self) -> bool:
        # Bambu Studio uses print.fun bit 39 to decide between structured
        # set_bed_temp and the M140 gcode_line fallback.
        return self._fun is not None and bool((self._fun >> 39) & 1)

    def _ensure_device_public_key(self, timeout: float) -> None:
        if self._device_public_key is not None:
            return
        # app_cert_install is idempotent and its SUCCESS reply carries
        # printer_cert, which provides the RSA public key needed for param_enc.
        self.install_trust(timeout=timeout)
        if self._device_public_key is None:
            raise SignedCommandError("Printer did not provide a usable device certificate")

    def _send_gcode_line(self, gcode: str, timeout: float) -> dict[str, Any]:
        self._ensure_device_public_key(timeout=min(timeout, 10.0))
        seq = self._next_print_seq()
        param_enc = self.signer.encrypt_field(self._device_public_key, gcode)
        wire = self.signer.sign_print(
            {
                "sequence_id": seq,
                "command": "gcode_line",
                "param_enc": param_enc,
            }
        )
        response = self._publish_and_wait(
            "print", "gcode_line", seq, wire, min(timeout, 5.0)
        )
        result = str(response.get("result") or "").upper()
        if result and result != "SUCCESS":
            raise SignedCommandError(
                f"Printer rejected gcode_line: result={response.get('result')!r}, "
                f"reason={response.get('reason')!r}, err_code={response.get('err_code')!r}"
            )
        return response

    def set_bed_temperature(self, target: int, timeout: float = 12.0) -> dict[str, Any]:
        """Set and verify bed target temperature without starting a print."""
        if not isinstance(target, int) or isinstance(target, bool):
            raise ValueError("Bed target must be an integer")
        if not 0 <= target <= 100:
            raise ValueError("Bed target must be between 0 and 100 C")
        if not self._trusted.is_set():
            self.ensure_trust(timeout=min(timeout, 10.0))

        if self._mqtt_bed_temp_supported():
            LOG.info("Using structured print.set_bed_temp")
            seq = self._next_print_seq()
            wire = self.signer.sign_print(
                {
                    "sequence_id": seq,
                    "command": "set_bed_temp",
                    "temp": target,
                }
            )
            response = self._publish_and_wait(
                "print", "set_bed_temp", seq, wire, min(timeout, 5.0)
            )
            result = str(response.get("result") or "").upper()
            if result and result != "SUCCESS":
                raise SignedCommandError(
                    "Printer rejected set_bed_temp: "
                    f"result={response.get('result')!r}, "
                    f"reason={response.get('reason')!r}"
                )
        else:
            LOG.info("Structured bed control is not advertised; using encrypted M140 fallback")
            response = self._send_gcode_line(f"M140 S{target}\n", timeout)

        self.request_full_state(timeout=min(timeout, 5.0))
        try:
            self._wait_for_bed_target(target, timeout)
        except SignedCommandError:
            raise SignedCommandError(
                f"Bed target change was not confirmed; target={target} C, "
                f"reported bed_target_temper={self._last_bed_target!r}"
            )
        return response

    def set_nozzle_temperature(self, target: int, timeout: float = 12.0) -> dict[str, Any]:
        """Set and verify nozzle target temperature without starting a print."""
        if not isinstance(target, int) or isinstance(target, bool):
            raise ValueError("Nozzle target must be an integer")
        if not 0 <= target <= 300:
            raise ValueError("Nozzle target must be between 0 and 300 C")
        if not self._trusted.is_set():
            self.ensure_trust(timeout=min(timeout, 10.0))

        response = self._send_gcode_line(f"M104 S{target}\n", timeout)
        self.request_full_state(timeout=min(timeout, 5.0))
        try:
            self._wait_for_nozzle_target(target, timeout)
        except SignedCommandError:
            raise SignedCommandError(
                f"Nozzle target change was not confirmed; target={target} C, "
                f"reported nozzle_target_temper={self._last_nozzle_target!r}"
            )
        return response

    def pause(self, timeout: float = 12.0) -> dict[str, Any]:
        return self._send_print_command("pause", {"PAUSE", "PAUSED"}, timeout)

    def resume(self, timeout: float = 12.0) -> dict[str, Any]:
        return self._send_print_command("resume", {"RUNNING", "RUN"}, timeout)
