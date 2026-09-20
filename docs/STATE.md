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
- [x] Capture/redact actual A1 report fields
- [x] Define normalized printer state model
- [x] Implement Bambu MQTT connection/reconnect/state cache
- [~] Implement Obico server adapter
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


## A1 MQTT validation

Validated on a real Bambu Lab A1:
- TLS/MQTT connection to port 8883 succeeds with the printer's self-signed certificate handled locally.
- Subscription to `device/<serial>/report` succeeds.
- Reports are incremental/delta payloads rather than guaranteed full-state snapshots.
- Observed fields include `bed_temper` and `nozzle_temper`.
- Some messages contain only the `print` object without any currently selected probe fields.

Implementation consequence: the bridge must maintain a persistent state cache and merge every incoming `print` delta before mapping state to Obico. It must not treat an individual MQTT message as the complete printer state.


## Full-state validation

Validated `pushing.pushall` on the real A1 while retaining the current cloud-capable configuration.

Observed response:
- `command=push_status`, `msg=0`, 64 fields in the initial full snapshot.
- Subsequent `push_status` messages use `msg=1` and contain small deltas (typically 4-5 fields).
- Confirmed fields in the full snapshot: `gcode_state`, `mc_percent`, `mc_remaining_time`, `layer_num`, `total_layer_num`, nozzle/bed current and target temperatures, `print_type`, `stg_cur`, and `subtask_name`.
- Idle/completed sample reported `gcode_state=FINISH`, progress 100%, layer 90/90 and remaining time 0.

Conclusion: Developer/LAN-only mode is not required for the telemetry needed by the planned Obico monitoring path on this tested A1 configuration. The bridge can request an initial snapshot with `pushall`, then merge incremental reports into its state cache. Control commands remain out of scope until separately validated.


## Connection/state implementation

Added:
- `bambu_obico/state.py`: thread-safe snapshot/delta cache; `msg=0` replaces stale session state, later deltas are merged recursively.
- `bambu_obico/connection.py`: reusable read-only MQTT connection, TLS setup, reconnect backoff, subscription, automatic `pushall` after every successful connection, and state callbacks.
- `bambu_obico/monitor.py`: compact integration test that prints only changed high-value fields from the accumulated state.

Next gate: validate reconnect and delta merging against the real A1, then implement the Obico-facing adapter.


## Obico state mapping

Inspected upstream `moonraker-obico` `PrinterState.to_status()` and `ServerConn` before implementing the adapter.

Added `bambu_obico/obico_state.py` to map the accumulated Bambu state directly into the status schema expected by Obico. This avoids emulating a Moonraker API.

Initial mappings include:
- Bambu print state -> Obico Operational/Printing/Paused flags
- `mc_percent` -> progress completion
- `mc_remaining_time` (minutes) -> Obico printTimeLeft (seconds)
- current/total layer values
- nozzle and bed current/target temperatures
- `subtask_name` -> job filename

Added unit tests based on the validated real A1 FINISH snapshot plus a synthetic RUNNING snapshot. Server WebSocket/auth integration is the next sub-step.
