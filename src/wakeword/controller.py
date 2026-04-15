from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Slot

from config import Config
from .base import WakeWordDetector


class WakeWordController(QObject):
    def __init__(
        self,
        *,
        detector: WakeWordDetector,
        phrase: str,
        is_live_available: Callable[[], bool],
        live_unavailable_reason: Callable[[], str],
        is_live_enabled: Callable[[], bool],
        is_live_voice_active: Callable[[], bool],
        start_one_shot_voice: Callable[[], bool],
        ensure_live_connected: Callable[[str, str], None] | None,
        publish_enabled: Callable[[bool], None],
        publish_phrase: Callable[[str], None],
        publish_state: Callable[[str, str], None],
        add_activity_message: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.detector = detector
        self._phrase = str(phrase or "Hey Pixie").strip() or "Hey Pixie"
        self._enabled = bool(Config.ENABLE_WAKE_WORD)
        self._is_live_available = is_live_available
        self._live_unavailable_reason = live_unavailable_reason
        self._is_live_enabled = is_live_enabled
        self._is_live_voice_active = is_live_voice_active
        self._start_one_shot_voice = start_one_shot_voice
        self._ensure_live_connected = ensure_live_connected
        self._publish_enabled = publish_enabled
        self._publish_phrase = publish_phrase
        self._publish_state = publish_state
        self._add_activity_message = add_activity_message

        self.detector.detected.connect(self._handle_detected)
        self.detector.state_changed.connect(self._handle_detector_state_changed)
        self.detector.availability_changed.connect(self._handle_detector_availability_changed)

        self._publish_enabled(self._enabled)
        self._publish_phrase(self._phrase)
        self._publish_state("starting" if self._enabled else "disabled", "")
        self.reconcile()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(Config.ENABLE_WAKE_WORD and enabled)
        self._publish_enabled(self._enabled)
        self.reconcile()

    def _request_live_connection_fallback(self, trigger: str, reason: str = "") -> None:
        callback = self._ensure_live_connected
        if callable(callback):
            callback(str(trigger or "wakeword"), str(reason or ""))

    def reconcile(self) -> None:
        self._publish_phrase(self._phrase)
        if not Config.ENABLE_WAKE_WORD or not self._enabled:
            self.detector.stop()
            self._publish_state("disabled", "")
            self._request_live_connection_fallback("wakeword_disabled", "Wake word is disabled.")
            return

        if not self.detector.is_available:
            self.detector.stop()
            reason = self.detector.unavailable_reason
            self._publish_state("unavailable", reason)
            self._request_live_connection_fallback("wakeword_unavailable", reason)
            return

        if not self._is_live_available():
            self.detector.stop()
            self._publish_state(
                "unavailable",
                self._live_unavailable_reason() or "PixelPilot Live is unavailable.",
            )
            return

        if self._is_live_voice_active():
            self.detector.pause()
            self._publish_state("paused", "")
            return

        if self.detector.resume():
            state = self.detector.state
            reason = self.detector.state_reason
            if state == "unavailable":
                self._publish_state("unavailable", reason)
            elif state == "armed":
                self._publish_state("armed", "")
            else:
                self._publish_state("starting", "")
            return

        self._publish_state(
            "unavailable",
            self.detector.unavailable_reason or self.detector.state_reason,
        )
        self._request_live_connection_fallback(
            "wakeword_not_arming",
            self.detector.unavailable_reason or self.detector.state_reason,
        )

    def shutdown(self) -> None:
        self.detector.stop()

    @Slot()
    def _handle_detected(self) -> None:
        if (
            not self._enabled
            or not self._is_live_available()
            or self._is_live_voice_active()
        ):
            return
        self.detector.pause()
        self._publish_state("paused", "")
        if callable(self._add_activity_message):
            self._add_activity_message(
                f'Wake word detected. PixelPilot Live is listening after "{self._phrase}".'
            )
        if not self._start_one_shot_voice():
            self.reconcile()

    @Slot(str, str)
    def _handle_detector_state_changed(self, state: str, reason: str) -> None:
        if not self._enabled:
            return
        normalized = str(state or "disabled").strip().lower() or "disabled"
        if normalized == "armed" and not self._is_live_voice_active():
            self._publish_state("armed", "")
            return
        if normalized == "paused":
            self._publish_state("paused", "")
            return
        if normalized == "unavailable":
            self._publish_state("unavailable", reason)
            self._request_live_connection_fallback("wakeword_unavailable", reason)

    @Slot(bool, str)
    def _handle_detector_availability_changed(self, available: bool, reason: str) -> None:
        if not available:
            self._publish_state("unavailable", reason)
            self._request_live_connection_fallback("wakeword_unavailable", reason)
        self.reconcile()
