from __future__ import annotations

import pathlib
import time
from typing import Any, Mapping


PRINTING = {"RUNNING", "PRINTING", "PREPARE"}
PAUSED = {"PAUSE", "PAUSED"}
FAILED = {"FAILED", "ERROR"}
FINISHED = {"FINISH", "COMPLETED", "IDLE"}


def _number(value: Any, default=None):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _state_text(raw: str) -> str:
    state = raw.upper()
    if state in PRINTING:
        return "Printing"
    if state in PAUSED:
        return "Paused"
    return "Operational"


def to_obico_status(state: Mapping[str, Any]) -> dict[str, Any]:
    """Map an accumulated Bambu push_status state to Obico's status schema.

    This deliberately mirrors the status shape emitted by moonraker-obico's
    PrinterState.to_status(), without pretending to be Moonraker.
    """
    raw_state = str(state.get("gcode_state") or "").upper()
    text = _state_text(raw_state)
    printing = text == "Printing"
    paused = text == "Paused"
    error = raw_state in FAILED

    filename = state.get("subtask_name")
    display_name = pathlib.Path(filename).name if isinstance(filename, str) else None

    completion = _number(state.get("mc_percent"))
    remaining_minutes = _number(state.get("mc_remaining_time"))
    remaining_seconds = remaining_minutes * 60 if remaining_minutes is not None else None

    current_layer = _number(state.get("layer_num"))
    total_layers = _number(state.get("total_layer_num"))

    nozzle = _number(state.get("nozzle_temper"), 0.0)
    nozzle_target = _number(state.get("nozzle_target_temper"), 0.0)
    bed = _number(state.get("bed_temper"), 0.0)
    bed_target = _number(state.get("bed_target_temper"), 0.0)

    return {
        "_ts": time.time(),
        "state": {
            "text": text,
            "flags": {
                "operational": True,
                "paused": paused,
                "printing": printing,
                "cancelling": False,
                "pausing": False,
                "error": error,
                "ready": not printing and not paused,
                "closedOrError": False,
            },
            "error": "Bambu printer reported an error state" if error else None,
        },
        "currentZ": None,
        "job": {
            "file": {
                "name": display_name,
                "path": filename,
                "display": display_name,
                "obico_g_code_file_id": None,
            },
            "estimatedPrintTime": None,
            "user": None,
        },
        "progress": {
            "completion": completion,
            "filepos": 0,
            "printTime": None,
            "printTimeLeft": remaining_seconds,
            "filamentUsed": None,
        },
        "temperatures": {
            "tool0": {"actual": round(nozzle, 2), "offset": 0, "target": nozzle_target},
            "bed": {"actual": round(bed, 2), "offset": 0, "target": bed_target},
        },
        "file_metadata": {
            "analysis": {"printingArea": {"maxZ": None}},
            "obico": {"totalLayerCount": total_layers},
        },
        # moonraker-obico currently uses this key for the current layer number.
        "currentLayerHeight": current_layer,
        "currentFeedRate": None,
        "currentFlowRate": None,
        "currentFanSpeed": None,
        "display_status": {},
    }


def to_obico_message(state: Mapping[str, Any], current_print_ts: float | None) -> dict[str, Any]:
    if current_print_ts is None:
        return {}
    return {
        "current_print_ts": current_print_ts,
        "status": to_obico_status(state),
    }
