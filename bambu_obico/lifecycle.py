from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from .obico_state import FAILED, FINISHED, PAUSED, PRINTING, to_obico_status


@dataclass
class LifecycleUpdate:
    message: dict[str, Any] | None
    transition: str | None = None


class PrintLifecycle:
    """Track one Bambu print and emit Obico-compatible status/events."""

    def __init__(self) -> None:
        self.previous_state: str | None = None
        self.current_print_ts: float | None = None
        self.last_positive_remaining_seconds: float | None = None

    @staticmethod
    def _raw(state: Mapping[str, Any]) -> str:
        return str(state.get("gcode_state") or "").upper()

    @staticmethod
    def _active(raw: str) -> bool:
        return raw in PRINTING or raw in PAUSED

    def update(self, state: Mapping[str, Any], now: float | None = None) -> LifecycleUpdate:
        now = time.time() if now is None else now
        raw = self._raw(state)
        state = dict(state)
        remaining = state.get("mc_remaining_time")
        prev = self.previous_state
        self.previous_state = raw

        # Start a new lifecycle before processing its remaining-time sample.
        # Otherwise the first positive sample is immediately cleared below.
        starts_new_print = raw in PRINTING and prev is not None and not self._active(prev)
        if starts_new_print:
            self.last_positive_remaining_seconds = None

        if self._active(raw):
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                if remaining > 0:
                    self.last_positive_remaining_seconds = remaining * 60
                elif remaining == 0 and self.last_positive_remaining_seconds is not None:
                    state["mc_remaining_time"] = self.last_positive_remaining_seconds / 60

        # First full snapshot is synchronization, not a transition. If the
        # bridge starts during a print, establish a session without inventing
        # PrintStarted. We currently lack an authoritative Bambu start epoch.
        if prev is None:
            if self._active(raw):
                self.current_print_ts = now
                return LifecycleUpdate(self._message(state))
            self.current_print_ts = None
            return LifecycleUpdate({
                "current_print_ts": -1,
                "status": to_obico_status(state),
            })

        event = None
        if raw in PRINTING:
            if not self._active(prev):
                self.current_print_ts = now
                event = "PrintStarted"
            elif prev in PAUSED:
                event = "PrintResumed"
        elif raw in PAUSED:
            if self.current_print_ts is None:
                self.current_print_ts = now
            if prev in PRINTING:
                event = "PrintPaused"
        elif self._active(prev):
            # Keep the print timestamp in the terminal event so Obico can
            # associate it with the active print, then clear local lifecycle.
            if raw in FAILED:
                event = "PrintFailed"
            elif raw in FINISHED:
                event = "PrintDone"
            else:
                event = "PrintFailed"

            message = self._message(state, event)
            self.current_print_ts = None
            self.last_positive_remaining_seconds = None
            return LifecycleUpdate(message, event)

        if self.current_print_ts is None:
            return LifecycleUpdate({
                "current_print_ts": -1,
                "status": to_obico_status(state),
            }, event)
        return LifecycleUpdate(self._message(state, event), event)

    def _message(self, state: Mapping[str, Any], event: str | None = None) -> dict[str, Any]:
        message = {
            "current_print_ts": self.current_print_ts,
            "status": to_obico_status(state),
        }
        if event:
            message["event"] = {"event_type": event}
        return message
