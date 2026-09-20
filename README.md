# bambu-obico

Bridge project for connecting a Bambu Lab A1 to a self-hosted Obico installation.

## Current phase

Phase 1 is deliberately read-only: connect to the printer's LAN MQTT service, subscribe to its report topic, and capture the real A1 payload schema before implementing the Obico adapter.

No printer control commands are sent in this phase.

## Target deployment

- Printer: Bambu Lab A1
- Printer transport: local MQTT over TLS
- Obico: self-hosted
- Camera: existing external camera through go2rtc
- Video path: existing go2rtc -> ffmpeg H.264 copy -> Janus -> Obico
- Host: Debian/systemd

## Quick start

```bash
git clone https://github.com/FeromonDE/bambu-obico.git
cd bambu-obico
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# edit .env locally; never commit credentials
.venv/bin/python -m bambu_obico.probe
```

The probe prints a compact, redacted summary by default. Set `BAMBU_DUMP_JSON=true` only when a raw payload is needed for development; review it before sharing.

## Security

Never commit or paste the LAN Access Code, Obico auth token, or other credentials. `.env` is ignored by Git.

## Project state

See [docs/STATE.md](docs/STATE.md).

## systemd

After manual validation, install the service:

```bash
sudo cp systemd/bambu-obico.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bambu-obico
sudo systemctl status bambu-obico
```

Follow logs with:

```bash
journalctl -u bambu-obico -f
```

Do not run the bridge manually while the systemd service is active.
