# Project state

## Goal

Replace the Klipper/Moonraker printer-data side of the existing Obico setup with Bambu Lab A1 LAN telemetry while retaining the already-working external-camera video path.

## Decisions

1. Ender/Klipper is not part of the target setup.
2. Existing external Eufy camera and go2rtc are retained.
3. Existing H.264/Janus path should be reused rather than using the A1 camera.
4. Credentials stay local and must never be committed.
5. Phase 1 is read-only MQTT discovery. No Bambu control commands until the observed A1 payload is mapped and reviewed.
6. GitHub is the durable project state; keep chat context minimal.

## Phase plan

- [x] Repository bootstrap
- [x] Read-only MQTT probe
- [ ] Capture/redact actual A1 report fields
- [ ] Define normalized printer state model
- [ ] Implement Bambu MQTT connection/reconnect/state cache
- [ ] Implement Obico server adapter
- [ ] Reuse existing go2rtc/Janus webcam pipeline
- [ ] Add pause/resume/cancel after read-only validation
- [ ] Add systemd installer/configuration
- [ ] End-to-end test against self-hosted Obico
- [ ] Retire moonraker-obico only after successful cutover

## Existing camera endpoints

Deployment-specific endpoints are intentionally not committed here. Configure them locally during the webcam phase.

## Next test

Run:

```bash
.venv/bin/python -m bambu_obico.probe
```

Expected first milestone: successful TLS/MQTT connection and compact JSON summaries from the printer report topic.
