from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class WakeWordDetector(QObject):
    detected = Signal()
    availability_changed = Signal(bool, str)
    state_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._available = True
        self._unavailable_reason = ""
        self._state = "disabled"
        self._state_reason = ""

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    @property
    def state(self) -> str:
        return self._state

    @property
    def state_reason(self) -> str:
        return self._state_reason

    def start(self) -> bool:
        raise NotImplementedError

    def pause(self, *, wait_timeout_s: float = 1.0) -> bool:
        raise NotImplementedError

    def resume(self) -> bool:
        raise NotImplementedError

    def stop(self, *, wait_timeout_s: float = 1.0) -> bool:
        raise NotImplementedError

    def _set_availability(self, available: bool, reason: str = "") -> None:
        clean_reason = str(reason or "").strip()
        changed = False
        if bool(available) != self._available:
            self._available = bool(available)
            changed = True
        if clean_reason != self._unavailable_reason:
            self._unavailable_reason = clean_reason
            changed = True
        if changed:
            self.availability_changed.emit(self._available, self._unavailable_reason)

    def _set_state(self, state: str, reason: str = "") -> None:
        clean_state = str(state or "disabled").strip().lower() or "disabled"
        clean_reason = str(reason or "").strip()
        if clean_state == self._state and clean_reason == self._state_reason:
            return
        self._state = clean_state
        self._state_reason = clean_reason
        self.state_changed.emit(self._state, self._state_reason)
