from __future__ import annotations

import os
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pyaudio

from .base import WakeWordDetector


logger = logging.getLogger("pixelpilot.wakeword.openwakeword")

try:
    from openwakeword.model import Model as OpenWakeWordModel
except Exception as exc:  # noqa: BLE001
    OpenWakeWordModel = None
    _MODEL_IMPORT_ERROR = str(exc)
else:
    _MODEL_IMPORT_ERROR = ""

try:
    from openwakeword.utils import AudioFeatures as OpenWakeWordAudioFeatures
except Exception as exc:  # noqa: BLE001
    OpenWakeWordAudioFeatures = None
    _FEATURE_IMPORT_ERROR = str(exc)
else:
    _FEATURE_IMPORT_ERROR = ""

try:
    import onnxruntime as ort
except Exception as exc:  # noqa: BLE001
    ort = None
    _ONNX_IMPORT_ERROR = str(exc)
else:
    _ONNX_IMPORT_ERROR = ""

FEATURE_EXTRACTOR_MODEL_NAMES = {"pixie.onnx"}
FEATURE_EXTRACTOR_DATA_FILENAMES = ("pixie.onnx.data",)


def uses_feature_extractor_model(model_path: Path | None) -> bool:
    if model_path is None:
        return False
    return model_path.name.lower() in FEATURE_EXTRACTOR_MODEL_NAMES


def resolve_feature_extractor_data_path(model_path: Path | None) -> Optional[Path]:
    if model_path is None:
        return None
    for filename in FEATURE_EXTRACTOR_DATA_FILENAMES:
        candidate = model_path.parent / filename
        if candidate.exists():
            return candidate
    if FEATURE_EXTRACTOR_DATA_FILENAMES:
        return model_path.parent / FEATURE_EXTRACTOR_DATA_FILENAMES[0]
    return None


def _sigmoid_logit(value: float) -> float:
    return float(1.0 / (1.0 + np.exp(-float(value))))


def _dedupe_paths(candidates: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _resolve_first_existing_path(candidates: list[Path]) -> Optional[Path]:
    deduped = _dedupe_paths(candidates)
    for candidate in deduped:
        if candidate.exists():
            return candidate
    if deduped:
        return deduped[0]
    return None


def _configured_path_candidates(
    raw_path: str,
    *,
    project_root: Path,
    runtime_dir: Path,
    is_frozen: bool,
    runtime_subdir: Optional[str] = None,
) -> list[Path]:
    candidate = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if candidate.is_absolute():
        return [candidate]

    if is_frozen:
        candidates: list[Path] = []
        if runtime_subdir:
            candidates.append(runtime_dir / runtime_subdir / candidate.name)
        candidates.extend(
            [
                runtime_dir / candidate,
                project_root / candidate,
            ]
        )
        return _dedupe_paths(candidates)

    candidates = [project_root / candidate]
    if runtime_subdir:
        candidates.append(runtime_dir / runtime_subdir / candidate.name)
    candidates.append(runtime_dir / candidate)
    return _dedupe_paths(candidates)


def resolve_openwakeword_model_path(
    *,
    project_root: Path,
    runtime_dir: Path,
    is_frozen: bool,
    raw_model_path: str,
) -> Optional[Path]:
    clean_raw_model_path = str(raw_model_path or "").strip()
    if clean_raw_model_path:
        return _resolve_first_existing_path(
            _configured_path_candidates(
                clean_raw_model_path,
                project_root=project_root,
                runtime_dir=runtime_dir,
                is_frozen=is_frozen,
                runtime_subdir="wakeword",
            )
        )

    runtime_candidates = [
        runtime_dir / "wakeword" / "pixie.onnx",
        runtime_dir / "wakeword" / "hey-pixie.onnx",
        runtime_dir / "wakeword" / "hey_pixie.onnx",
        runtime_dir / "wakeword" / "hey-pixie.tflite",
        runtime_dir / "wakeword" / "hey_pixie.tflite",
    ]
    project_candidates = [
        project_root / "models" / "pixie.onnx",
        project_root / "models" / "hey-pixie.onnx",
        project_root / "models" / "hey_pixie.onnx",
        project_root / "models" / "hey-pixie.tflite",
        project_root / "models" / "hey_pixie.tflite",
        project_root / "resources" / "wakeword" / "hey-pixie.onnx",
        project_root / "resources" / "wakeword" / "hey_pixie.onnx",
        project_root / "resources" / "wakeword" / "hey-pixie.tflite",
        project_root / "resources" / "wakeword" / "hey_pixie.tflite",
    ]

    candidates = runtime_candidates + project_candidates
    if not is_frozen:
        candidates = project_candidates + runtime_candidates
    return _resolve_first_existing_path(candidates)


def _support_asset_candidates(
    filename: str,
    *,
    project_root: Path,
    runtime_dir: Path,
    is_frozen: bool,
    model_path: Optional[Path] = None,
) -> list[Path]:
    candidates: list[Path] = []
    if model_path is not None:
        candidates.append(Path(model_path).parent / filename)

    runtime_candidates = [
        runtime_dir / "wakeword" / filename,
        runtime_dir / filename,
    ]
    project_candidates = [
        project_root / "models" / filename,
        project_root / "resources" / "wakeword" / filename,
    ]

    if is_frozen:
        candidates.extend(runtime_candidates)
        candidates.extend(project_candidates)
    else:
        candidates.extend(project_candidates)
        candidates.extend(runtime_candidates)
    return _dedupe_paths(candidates)


def resolve_openwakeword_feature_model_paths(
    *,
    project_root: Path,
    runtime_dir: Path,
    is_frozen: bool,
    model_path: Optional[Path] = None,
) -> tuple[Optional[Path], Optional[Path]]:
    return (
        _resolve_first_existing_path(
            _support_asset_candidates(
                "melspectrogram.onnx",
                project_root=project_root,
                runtime_dir=runtime_dir,
                is_frozen=is_frozen,
                model_path=model_path,
            )
        ),
        _resolve_first_existing_path(
            _support_asset_candidates(
                "embedding_model.onnx",
                project_root=project_root,
                runtime_dir=runtime_dir,
                is_frozen=is_frozen,
                model_path=model_path,
            )
        ),
    )


class OpenWakeWordDetector(WakeWordDetector):
    SAMPLE_RATE = 16000
    FRAME_LENGTH = 1280

    def __init__(
        self,
        *,
        model_path: Path | None,
        threshold: float,
        vad_threshold: float,
    ) -> None:
        super().__init__()
        self._model_path = Path(model_path) if model_path is not None else None
        self._threshold = max(0.0, min(1.0, float(threshold or 0.0)))
        self._vad_threshold = max(0.0, min(1.0, float(vad_threshold or 0.0)))
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._mic_released = threading.Event()
        self._mic_released.set()
        self._lock = threading.Lock()
        self._audio: pyaudio.PyAudio | None = None
        self._stream = None
        self._model = None

        available, reason = self._compute_static_availability()
        self._set_availability(available, reason)

    def start(self) -> bool:
        if not self.is_available:
            self._set_state("unavailable", self.unavailable_reason)
            return False

        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._paused.clear()
                self._mic_released.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name="WakeWordOpenWakeWord",
                    daemon=True,
                )
                self._thread.start()
                self._set_state("starting")
                return True

            self._paused.clear()
            self._mic_released.clear()
        self._reset_model()
        self._set_state("starting")
        return True

    def pause(self, *, wait_timeout_s: float = 1.0) -> bool:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._mic_released.set()
                self._reset_model()
                self._set_state("paused")
                return True
            self._paused.set()
        released = self._mic_released.wait(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        if released:
            self._reset_model()
            self._set_state("paused")
        return released

    def resume(self) -> bool:
        return self.start()

    def stop(self, *, wait_timeout_s: float = 1.0) -> bool:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            self._paused.set()
        self._mic_released.wait(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        with self._lock:
            self._thread = None
        self._close_audio_handles()
        self._reset_model()
        self._close_model()
        self._set_state("disabled")
        return True

    def _compute_static_availability(self) -> tuple[bool, str]:
        if OpenWakeWordModel is None:
            return False, (
                "Wake-word engine unavailable. Install openwakeword to enable the local wake word."
                + (f" ({_MODEL_IMPORT_ERROR})" if _MODEL_IMPORT_ERROR else "")
            )
        if self._model_path is None:
            return False, (
                "Wake-word model not found. Set WAKE_WORD_OPENWAKEWORD_MODEL_PATH "
                'or add a supported wake-word model under models or runtime/wakeword.'
            )
        if not self._model_path.exists():
            return False, f"Wake-word model not found: {self._model_path}"
        if self._model_path.suffix.lower() not in {".onnx", ".tflite"}:
            return False, (
                "Native openWakeWord models must be .onnx or .tflite. "
                f"Received: {self._model_path.name}"
            )
        return True, ""

    def _run(self) -> None:
        try:
            if not self._ensure_model():
                return
            while not self._stop_event.is_set():
                if self._paused.is_set():
                    self._close_audio_handles()
                    self._reset_model()
                    self._mic_released.set()
                    self._set_state("paused")
                    time.sleep(0.05)
                    continue

                if not self._ensure_audio_handles():
                    self._paused.set()
                    self._mic_released.set()
                    time.sleep(0.25)
                    continue

                self._mic_released.clear()
                self._set_state("armed")
                pcm = self._stream.read(  # type: ignore[union-attr]
                    self.FRAME_LENGTH,
                    exception_on_overflow=False,
                )
                if not pcm:
                    continue

                scores = self._predict(pcm)
                if scores and max(scores.values()) >= self._threshold:
                    self._paused.set()
                    self._close_audio_handles()
                    self._reset_model()
                    self._mic_released.set()
                    self._set_state("paused")
                    self.detected.emit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Wake-word listener failed")
            self._set_state("unavailable", f"Wake-word listener failed: {exc}")
        finally:
            self._close_audio_handles()
            self._mic_released.set()
            self._reset_model()
            self._close_model()
            with self._lock:
                self._thread = None

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if not self.is_available or self._model_path is None or OpenWakeWordModel is None:
            self._set_state("unavailable", self.unavailable_reason)
            return False

        inference_framework = "tflite" if self._model_path.suffix.lower() == ".tflite" else "onnx"
        try:
            self._model = OpenWakeWordModel(
                wakeword_models=[str(self._model_path)],
                inference_framework=inference_framework,
                vad_threshold=self._vad_threshold,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._set_state("unavailable", f"Failed to initialize wake-word engine: {exc}")
            return False

    def _ensure_audio_handles(self) -> bool:
        if self._model is None:
            return False
        if self._audio is None:
            try:
                self._audio = pyaudio.PyAudio()
            except Exception as exc:  # noqa: BLE001
                self._set_state("unavailable", f"Wake-word audio initialization failed: {exc}")
                return False
        if self._stream is None:
            try:
                self._stream = self._audio.open(
                    rate=self.SAMPLE_RATE,
                    channels=1,
                    format=pyaudio.paInt16,
                    input=True,
                    frames_per_buffer=self.FRAME_LENGTH,
                )
            except Exception as exc:  # noqa: BLE001
                self._close_audio_handles()
                self._set_state("unavailable", f"Wake-word microphone unavailable: {exc}")
                return False
        return True

    def _predict(self, pcm: bytes) -> dict[str, float]:
        if self._model is None:
            return {}
        samples = np.frombuffer(pcm, dtype=np.int16)
        raw_scores = self._model.predict(samples)
        scores: dict[str, float] = {}
        if not isinstance(raw_scores, dict):
            return scores
        for name, score in raw_scores.items():
            try:
                scores[str(name)] = float(score)
            except Exception:
                continue
        return scores

    def _reset_model(self) -> None:
        if self._model is None:
            return
        try:
            self._model.reset()
        except Exception:
            logger.debug("Failed to reset openWakeWord detector", exc_info=True)

    def _close_audio_handles(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                logger.debug("Failed to close wake-word microphone stream", exc_info=True)
        self._stream = None
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                logger.debug("Failed to terminate wake-word microphone handle", exc_info=True)
        self._audio = None

    def _close_model(self) -> None:
        self._model = None


class OnnxFeatureWakeWordDetector(WakeWordDetector):
    SAMPLE_RATE = 16000
    CHUNK = 4000
    BUFFER_SAMPLES = 48000
    FEATURE_FRAMES = 16
    RMS_THRESHOLD = 150.0
    COOLDOWN_SECONDS = 3.0
    COOLDOWN_CHUNKS = max(1, int(round((COOLDOWN_SECONDS * SAMPLE_RATE) / CHUNK)))

    def __init__(
        self,
        *,
        model_path: Path | None,
        threshold: float,
        melspec_model_path: Path | None,
        embedding_model_path: Path | None,
    ) -> None:
        super().__init__()
        self._model_path = Path(model_path) if model_path is not None else None
        self._threshold = max(0.0, min(1.0, float(threshold or 0.0)))
        self._melspec_model_path = Path(melspec_model_path) if melspec_model_path is not None else None
        self._embedding_model_path = (
            Path(embedding_model_path) if embedding_model_path is not None else None
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._mic_released = threading.Event()
        self._mic_released.set()
        self._lock = threading.Lock()
        self._audio: pyaudio.PyAudio | None = None
        self._stream = None
        self._feature_extractor = None
        self._classifier_session = None
        self._classifier_input_name: str | None = None
        self._model_data_path = resolve_feature_extractor_data_path(self._model_path)
        self._buffer = np.zeros(self.BUFFER_SAMPLES, dtype=np.int16)
        self._cooldown_chunks = 0

        available, reason = self._compute_static_availability()
        self._set_availability(available, reason)

    def start(self) -> bool:
        if not self.is_available:
            self._set_state("unavailable", self.unavailable_reason)
            return False

        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._paused.clear()
                self._mic_released.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name="WakeWordOpenWakeWordOnnx",
                    daemon=True,
                )
                self._thread.start()
                self._set_state("starting")
                return True

            self._paused.clear()
            self._mic_released.clear()
        self._reset_runtime_state(clear_cooldown=False)
        self._set_state("starting")
        return True

    def pause(self, *, wait_timeout_s: float = 1.0) -> bool:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._mic_released.set()
                self._set_state("paused")
                return True
            self._paused.set()
        released = self._mic_released.wait(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        if released:
            self._reset_runtime_state(clear_cooldown=False)
            self._set_state("paused")
        return released

    def resume(self) -> bool:
        return self.start()

    def stop(self, *, wait_timeout_s: float = 1.0) -> bool:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            self._paused.set()
        self._mic_released.wait(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(wait_timeout_s or 0.0)))
        with self._lock:
            self._thread = None
        self._close_audio_handles()
        self._reset_runtime_state(clear_cooldown=True)
        self._close_model()
        self._set_state("disabled")
        return True

    def _compute_static_availability(self) -> tuple[bool, str]:
        if OpenWakeWordAudioFeatures is None:
            return False, (
                "Wake-word feature extractor unavailable. Install openwakeword to enable the local wake word."
                + (f" ({_FEATURE_IMPORT_ERROR})" if _FEATURE_IMPORT_ERROR else "")
            )
        if ort is None:
            return False, (
                "Wake-word ONNX runtime unavailable. Install onnxruntime to enable the local wake word."
                + (f" ({_ONNX_IMPORT_ERROR})" if _ONNX_IMPORT_ERROR else "")
            )
        if self._model_path is None:
            return False, (
                "Wake-word model not found. Set WAKE_WORD_OPENWAKEWORD_MODEL_PATH "
                'or add "pixie.onnx" under models or runtime/wakeword.'
            )
        if not self._model_path.exists():
            return False, f"Wake-word model not found: {self._model_path}"
        if self._model_path.suffix.lower() != ".onnx":
            return False, f"Feature-extractor wake-word models must be .onnx. Received: {self._model_path.name}"
        if self._model_data_path is None:
            return False, 'Wake-word model weights not found: "pixie.onnx.data"'
        if not self._model_data_path.exists():
            return False, f"Wake-word model weights not found: {self._model_data_path}"
        if self._melspec_model_path is None:
            return False, 'Wake-word feature model not found: "melspectrogram.onnx"'
        if not self._melspec_model_path.exists():
            return False, f"Wake-word feature model not found: {self._melspec_model_path}"
        if self._embedding_model_path is None:
            return False, 'Wake-word embedding model not found: "embedding_model.onnx"'
        if not self._embedding_model_path.exists():
            return False, f"Wake-word embedding model not found: {self._embedding_model_path}"
        return True, ""

    def _run(self) -> None:
        try:
            if not self._ensure_model():
                return
            while not self._stop_event.is_set():
                if self._paused.is_set():
                    self._close_audio_handles()
                    self._reset_runtime_state(clear_cooldown=False)
                    self._mic_released.set()
                    self._set_state("paused")
                    time.sleep(0.05)
                    continue

                if not self._ensure_audio_handles():
                    self._paused.set()
                    self._reset_runtime_state(clear_cooldown=False)
                    self._mic_released.set()
                    time.sleep(0.25)
                    continue

                self._mic_released.clear()
                self._set_state("armed")
                pcm = self._stream.read(  # type: ignore[union-attr]
                    self.CHUNK,
                    exception_on_overflow=False,
                )
                if not pcm:
                    continue

                audio_chunk = np.frombuffer(pcm, dtype=np.int16)
                if audio_chunk.size == 0:
                    continue

                self._push_audio_chunk(audio_chunk)

                if self._cooldown_chunks > 0:
                    self._cooldown_chunks -= 1
                    continue

                if self._compute_rms(audio_chunk) < self.RMS_THRESHOLD:
                    continue

                score = self._predict_from_buffer()
                if score is None:
                    continue

                if score >= self._threshold:
                    self._cooldown_chunks = self.COOLDOWN_CHUNKS
                    self._paused.set()
                    self._close_audio_handles()
                    self._mic_released.set()
                    self._set_state("paused")
                    self.detected.emit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Wake-word listener failed")
            self._set_state("unavailable", f"Wake-word listener failed: {exc}")
        finally:
            self._close_audio_handles()
            self._mic_released.set()
            self._close_model()
            with self._lock:
                self._thread = None

    def _ensure_model(self) -> bool:
        if (
            self._feature_extractor is not None
            and self._classifier_session is not None
            and self._classifier_input_name is not None
        ):
            return True
        if (
            not self.is_available
            or self._model_path is None
            or self._melspec_model_path is None
            or self._embedding_model_path is None
            or OpenWakeWordAudioFeatures is None
            or ort is None
        ):
            self._set_state("unavailable", self.unavailable_reason)
            return False

        try:
            self._feature_extractor = OpenWakeWordAudioFeatures(
                melspec_model_path=str(self._melspec_model_path),
                embedding_model_path=str(self._embedding_model_path),
                inference_framework="onnx",
                device="cpu",
            )
            self._classifier_session = ort.InferenceSession(str(self._model_path))
            classifier_inputs = self._classifier_session.get_inputs()
            if not classifier_inputs:
                raise RuntimeError("Wake-word classifier ONNX model does not expose any inputs.")
            self._classifier_input_name = classifier_inputs[0].name
            return True
        except Exception as exc:  # noqa: BLE001
            self._close_model()
            self._set_state("unavailable", f"Failed to initialize wake-word engine: {exc}")
            return False

    def _ensure_audio_handles(self) -> bool:
        if self._classifier_session is None or self._feature_extractor is None:
            return False
        if self._audio is None:
            try:
                self._audio = pyaudio.PyAudio()
            except Exception as exc:  # noqa: BLE001
                self._set_state("unavailable", f"Wake-word audio initialization failed: {exc}")
                return False
        if self._stream is None:
            try:
                self._stream = self._audio.open(
                    rate=self.SAMPLE_RATE,
                    channels=1,
                    format=pyaudio.paInt16,
                    input=True,
                    frames_per_buffer=self.CHUNK,
                )
            except Exception as exc:  # noqa: BLE001
                self._close_audio_handles()
                self._set_state("unavailable", f"Wake-word microphone unavailable: {exc}")
                return False
        return True

    def _push_audio_chunk(self, audio_chunk: np.ndarray) -> None:
        chunk_length = int(audio_chunk.size)
        if chunk_length <= 0:
            return
        if chunk_length >= self.BUFFER_SAMPLES:
            self._buffer[:] = audio_chunk[-self.BUFFER_SAMPLES :]
            return
        self._buffer[:-chunk_length] = self._buffer[chunk_length:]
        self._buffer[-chunk_length:] = audio_chunk

    def _predict_from_buffer(self) -> float | None:
        if (
            self._feature_extractor is None
            or self._classifier_session is None
            or self._classifier_input_name is None
        ):
            return None
        features = self._feature_extractor.embed_clips(np.expand_dims(self._buffer, axis=0))
        if len(features) == 0:
            return None
        prepared_frames = self._prepare_feature_frames(features[0])
        if prepared_frames is None:
            return None
        outputs = self._classifier_session.run(
            None,
            {self._classifier_input_name: prepared_frames},
        )
        if not outputs:
            return None
        flattened_scores = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if flattened_scores.size == 0:
            return None
        return _sigmoid_logit(float(flattened_scores[0]))

    @classmethod
    def _prepare_feature_frames(cls, feature_frames: np.ndarray) -> np.ndarray | None:
        if feature_frames is None or getattr(feature_frames, "ndim", 0) != 2:
            return None
        frame_count = int(feature_frames.shape[0])
        if frame_count >= cls.FEATURE_FRAMES:
            selected = feature_frames[-cls.FEATURE_FRAMES :, :]
        else:
            pad_len = cls.FEATURE_FRAMES - frame_count
            selected = np.pad(feature_frames, ((pad_len, 0), (0, 0)), mode="constant")
        return selected.reshape(1, cls.FEATURE_FRAMES, selected.shape[1]).astype(np.float32, copy=False)

    @staticmethod
    def _compute_rms(audio_chunk: np.ndarray) -> float:
        if audio_chunk.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(audio_chunk.astype(np.float32)))))

    def _reset_runtime_state(self, *, clear_cooldown: bool) -> None:
        self._buffer = np.zeros(self.BUFFER_SAMPLES, dtype=np.int16)
        if clear_cooldown:
            self._cooldown_chunks = 0

    def _close_audio_handles(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                logger.debug("Failed to close wake-word microphone stream", exc_info=True)
        self._stream = None
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                logger.debug("Failed to terminate wake-word microphone handle", exc_info=True)
        self._audio = None

    def _close_model(self) -> None:
        self._feature_extractor = None
        self._classifier_session = None
        self._classifier_input_name = None
