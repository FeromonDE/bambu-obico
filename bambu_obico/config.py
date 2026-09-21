from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    host: str
    serial: str
    access_code: str
    port: int = 8883
    tls_insecure: bool = True
    dump_json: bool = False
    obico_server: str | None = None
    obico_auth_token: str | None = None
    webcam_snapshot_url: str | None = None
    webcam_h264_http_url: str | None = None
    signing_dir: Path | None = None

    @property
    def report_topic(self) -> str:
        return f"device/{self.serial}/report"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    load_dotenv()
    required = {
        "BAMBU_HOST": os.getenv("BAMBU_HOST"),
        "BAMBU_SERIAL": os.getenv("BAMBU_SERIAL"),
        "BAMBU_ACCESS_CODE": os.getenv("BAMBU_ACCESS_CODE"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))

    signing_raw = (os.getenv("BAMBU_SIGNING_DIR") or "").strip()

    return Config(
        host=required["BAMBU_HOST"],
        serial=required["BAMBU_SERIAL"],
        access_code=required["BAMBU_ACCESS_CODE"],
        port=int(os.getenv("BAMBU_MQTT_PORT", "8883")),
        tls_insecure=_bool("BAMBU_TLS_INSECURE", True),
        dump_json=_bool("BAMBU_DUMP_JSON", False),
        obico_server=(os.getenv("OBICO_SERVER") or "").strip().rstrip("/") or None,
        obico_auth_token=(os.getenv("OBICO_AUTH_TOKEN") or "").strip() or None,
        webcam_snapshot_url=(os.getenv("WEBCAM_SNAPSHOT_URL") or "").strip() or None,
        webcam_h264_http_url=(os.getenv("WEBCAM_H264_HTTP_URL") or "").strip() or None,
        signing_dir=Path(signing_raw).expanduser() if signing_raw else None,
    )
