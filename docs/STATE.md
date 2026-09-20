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


### Mapper test validation

Validated locally on the target host with Python 3.11:
- `test_finished_snapshot`: PASS
- `test_running_snapshot`: PASS
- 2 tests executed in 0.001 s.

Added `tests/__init__.py` so plain `python -m unittest discover -v` discovers the suite.


## Direct Obico transport

Inspected upstream `moonraker-obico/server_conn.py`, `ws.py`, and `config.py` before implementing transport.

Confirmed upstream device transport semantics:
- device WebSocket endpoint: server URL + `/ws/dev/`
- HTTP(S) is converted to WS(S)
- WebSocket authentication header: `authorization: bearer <auth_token>`
- close code 4321 means a shared auth token was detected.

Implemented:
- `obico_conn.py`: direct authenticated Obico WebSocket client with bounded send queue and reconnect backoff.
- `bridge.py`: wires accumulated Bambu state through the Obico mapper into the WebSocket client.
- `OBICO_SERVER` and `OBICO_AUTH_TOKEN` are loaded from local environment only.
- `websocket-client` dependency added.

Important: the current bridge uses a temporary process-start `current_print_ts` solely for protocol validation. Correct print lifecycle/event tracking must be implemented before production cutover. Existing moonraker-obico remains untouched.


## Obico linking

Inspected upstream `moonraker_obico/link.py` and `utils.verify_link_code()`.

Manual linking protocol:
- obtain a 6-digit verification code from the Obico app/web UI;
- POST `<server>/api/v1/octo/verify/?code=<code>`;
- successful response contains `printer.auth_token`;
- persist that token locally and use it for the device WebSocket.

Implemented `python -m bambu_obico.link`. It never prints the returned token, writes it only to local `.env`, and changes the file mode to 0600. This deliberately implements manual 6-digit linking first; LAN auto-discovery/one-time-passcode is not required for the initial Bambu bridge.


### Real linking validation

Validated against the user's self-hosted Obico server on 2026-09-20:
- manual 6-digit verification-code flow succeeded;
- server returned a dedicated printer auth token;
- `bambu_obico.link` saved it locally to `.env` without printing it;
- Bambu A1 therefore now has its own Obico printer identity/token, separate from the existing Ender/moonraker-obico agent.

Next: validate the direct Bambu -> Obico WebSocket bridge using this dedicated token.


### Real end-to-end transport validation

Validated on 2026-09-20:
- direct Obico WebSocket connected successfully;
- A1 MQTT connected and `pushall` returned a 64-field full state;
- the newly linked BambuLab A1 appeared in the Obico app;
- Obico received the mapped filename `Shelly_Mini_DIN_Mount.gcode.3mf`.

This proves the basic data path:
`Bambu A1 MQTT -> StateCache -> Obico mapper -> authenticated /ws/dev/ -> self-hosted Obico`.

Known expected gaps at this stage:
- Obico reports no webcam because webcam settings/streamer have not been integrated yet.
- print lifecycle is not correct yet: bridge currently uses process startup time as temporary `current_print_ts`, so an already-finished job can appear as a newly started/last print.
- status/event transitions still need proper lifecycle tracking before production use.


## Print lifecycle implementation

Implemented `PrintLifecycle` and wired it into `bridge.py`.

Behavior:
- initial idle/FINISH snapshot does not create a fake print;
- idle -> RUNNING emits `PrintStarted`;
- RUNNING -> PAUSE emits `PrintPaused`;
- PAUSE -> RUNNING emits `PrintResumed`;
- active -> FINISH/COMPLETED/IDLE emits `PrintDone`;
- active -> FAILED/ERROR emits `PrintFailed`;
- terminal event retains the print timestamp for server association, then local lifecycle clears it;
- restart while already printing establishes a print session without inventing a `PrintStarted` event.

Limitation: the A1 start epoch has not yet been identified in observed MQTT fields, so a bridge restart during an active print uses bridge synchronization time as `current_print_ts`. A fresh print started while the bridge is running uses the observed transition time.

Added deterministic lifecycle unit tests. Real transition validation on the A1 is still pending.


### Lifecycle validation

Validated on the target host:
- unittest discovery now runs 5 tests; all 5 passed.
- starting the bridge while A1 reports FINISH no longer creates a fake print.
- with no active print, the bridge intentionally sends no print-status payload after initial synchronization.
- after bridge termination, Obico shows the agent/plugin offline; this is expected until the bridge is installed as a persistent service.

Next implementation focus: Obico settings/agent presence and existing Eufy/go2rtc webcam integration, followed by systemd persistence.


## Idle heartbeat

Implemented a 60-second Obico status heartbeat. The self-hosted server's printer-status cache TTL is 120 seconds, so the bridge now refreshes the latest accumulated Bambu status even when MQTT has no meaningful state changes.

Lifecycle/state access is protected with an RLock because MQTT callbacks, WebSocket reconnect callbacks, and heartbeat can access the cached state concurrently.

Pending real validation: leave bridge running idle for >2 minutes and confirm Obico remains online.


### Heartbeat validation

Validated on the target host: the BambuLab A1 remained Online in Obico after the idle heartbeat change. The 60-second refresh is therefore retained.

### Webcam implementation research

Inspected current upstream `webcam_stream.py`, `webcam_capture.py`, `janus.py`, and `janus_config_builder.py`.

For the existing Eufy source, upstream `h264_copy` uses:
`ffmpeg -re -i <h264_http_url> -c:v copy -an -f rtp rtp://127.0.0.1:<videoport>?pkt_size=1300`

Janus streaming plugin receives H264 RTP and exposes stream id 1. Janus signaling messages must be relayed bidirectionally through the Obico device WebSocket: Janus -> Obico as `{"janus": "<raw-json>"}`, and Obico -> local Janus for incoming `janus` messages. This signaling relay is mandatory; merely advertising webcam settings is insufficient.

The existing working host has Janus 1.1.2 and an intentional wrapper at `/usr/bin/janus`; system `janus.service` remains disabled. Do not enable it.


### Cutover decision

The existing Ender printer path is not needed in the final installation. The Bambu Lab A1 is the replacement target.

Migration rule:
- keep the existing moonraker-obico/Ender agent available only until the Bambu A1 bridge, webcam, and service operation are proven;
- after successful A1 validation, the old Ender agent may be stopped/retired;
- no requirement to support permanent simultaneous Ender + A1 operation.

This allows the final Bambu setup to reuse the host's existing system Janus/ffmpeg stack, while still avoiding destructive changes during validation.
