from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Mapping


class StateCache:
    """Thread-safe cache for Bambu push_status snapshots and deltas."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, Any] = {}
        self._initialized = False

    def clear(self) -> None:
        with self._lock:
            self._state.clear()
            self._initialized = False

    def apply(self, report: Mapping[str, Any]) -> dict[str, Any]:
        if report.get("command") != "push_status":
            return self.snapshot()

        with self._lock:
            # msg=0 is the full pushall snapshot. Replace stale state instead of
            # merging it with data from a previous MQTT session.
            if report.get("msg") == 0:
                self._state = deepcopy(dict(report))
                self._initialized = True
            elif self._initialized:
                self._merge(self._state, report)

            return deepcopy(self._state)

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._initialized

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    @classmethod
    def _merge(cls, target: dict[str, Any], patch: Mapping[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                cls._merge(target[key], value)
            else:
                target[key] = deepcopy(value)
