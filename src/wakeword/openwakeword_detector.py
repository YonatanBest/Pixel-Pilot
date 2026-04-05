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
    import torch
    from torch import nn
except Exception as exc:  # noqa: BLE001
    torch = None
    nn = None
    _TORCH_IMPORT_ERROR = str(exc)
else:
    _TORCH_IMPORT_ERROR = ""


if nn is not None:

    class PixieWakeWordClassifier(nn.Module):
        def __init__(self, modules: list[nn.Module]) -> None:
            super().__init__()
            self.network = nn.Sequential(*modules)

        def forward(self, x):
            return self.network(x)

        @classmethod
        def from_state_dict(cls, state_dict):
            linear_indices: list[int] = []
            for key, value in state_dict.items():
                parts = str(key).split(".")
                if len(parts) != 3 or parts[0] != "network" or parts[2] != "weight":
                    continue
                if getattr(value, "ndim", 0) != 2:
                    continue
                try:
                    linear_indices.append(int(parts[1]))
                except Exception:
                    continue

            linear_indices = sorted(set(linear_indices))
            if not linear_indices:
                raise RuntimeError("Could not infer Pixie classifier layers from the checkpoint.")

            modules: list[nn.Module] = []
            for position, layer_index in enumerate(linear_indices):
                weight = state_dict[f"network.{layer_index}.weight"]
                out_features, in_features = weight.shape
                modules.append(nn.Linear(int(in_features), int(out_features)))

                is_last_layer = position == len(linear_indices) - 1
                if is_last_layer:
                    modules.append(nn.Sigmoid())
                    continue

                batchnorm_index = layer_index + 1
                batchnorm_prefix = f"network.{batchnorm_index}"
                if (
                    f"{batchnorm_prefix}.weight" in state_dict
                    and f"{batchnorm_prefix}.bias" in state_dict
                    and f"{batchnorm_prefix}.running_mean" in state_dict
                    and f"{batchnorm_prefix}.running_var" in state_dict
                ):
                    modules.append(nn.BatchNorm1d(int(out_features)))

                modules.append(nn.ReLU())
                modules.append(nn.Dropout(0.3))

            return cls(modules)


else:

    class PixieWakeWordClassifier:  # type: ignore[no-redef]
        def __init__(self) -> None:
            raise RuntimeError("torch is required to load Pixie wake-word checkpoints.")

        @classmethod
        def from_state_dict(cls, state_dict):
            raise RuntimeError("torch is required to load Pixie wake-word checkpoints.")


def _normalize_pixie_state_dict(raw_state_dict):
    state_dict = raw_state_dict
    if isinstance(state_dict, dict):
        for nested_key in ("state_dict", "model_state_dict"):
            nested = state_dict.get(nested_key)
            if isinstance(nested, dict):
                state_dict = nested
                break
    if not isinstance(state_dict, dict):
        raise RuntimeError("Pixie wake-word checkpoint did not contain a valid state_dict.")

    normalized = {}
    for key, value in state_dict.items():
        normalized[str(key).removeprefix("module.")] = value
    return normalized


def load_pixie_wake_word_classifier(model_path: str | Path):
    if torch is None:
        raise RuntimeError("torch is required to load Pixie wake-word checkpoints.")
    state_dict = torch.load(
        str(model_path),
        map_location=torch.device("cpu"),
        weights_only=True,
    )
    normalized_state_dict = _normalize_pixie_state_dict(state_dict)
    classifier = PixieWakeWordClassifier.from_state_dict(normalized_state_dict)
    classifier.load_state_dict(normalized_state_dict)
    classifier.eval()
    return classifier


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
        runtime_dir / "wakeword" / "pixie_model.pth",
        runtime_dir / "wakeword" / "hey-pixie.onnx",
        runtime_dir / "wakeword" / "hey_pixie.onnx",
        runtime_dir / "wakeword" / "hey-pixie.tflite",
        runtime_dir / "wakeword" / "hey_pixie.tflite",
    ]
    project_candidates = [
        project_root / "model" / "pixie_model.pth",
        project_root / "model" / "hey-pixie.onnx",
        project_root / "model" / "hey_pixie.onnx",
        project_root / "model" / "hey-pixie.tflite",
        project_root / "model" / "hey_pixie.tflite",
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
        project_root / "model" / filename,
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
                'or add "hey-pixie.onnx" under resources.'
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


class TorchOpenWakeWordDetector(WakeWordDetector):
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
        self._classifier = None
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
                    name="WakeWordOpenWakeWordTorch",
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
        if torch is None or nn is None:
            return False, (
                "Wake-word classifier unavailable. Install torch to enable the local wake word."
                + (f" ({_TORCH_IMPORT_ERROR})" if _TORCH_IMPORT_ERROR else "")
            )
        if self._model_path is None:
            return False, (
                "Wake-word model not found. Set WAKE_WORD_OPENWAKEWORD_MODEL_PATH "
                'or add "pixie_model.pth" under model or runtime/wakeword.'
            )
        if not self._model_path.exists():
            return False, f"Wake-word model not found: {self._model_path}"
        if self._model_path.suffix.lower() != ".pth":
            return False, f"PyTorch wake-word models must be .pth. Received: {self._model_path.name}"
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
        if self._feature_extractor is not None and self._classifier is not None:
            return True
        if (
            not self.is_available
            or self._model_path is None
            or self._melspec_model_path is None
            or self._embedding_model_path is None
            or OpenWakeWordAudioFeatures is None
            or torch is None
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
            self._classifier = load_pixie_wake_word_classifier(self._model_path)
            return True
        except Exception as exc:  # noqa: BLE001
            self._close_model()
            self._set_state("unavailable", f"Failed to initialize wake-word engine: {exc}")
            return False

    def _ensure_audio_handles(self) -> bool:
        if self._classifier is None or self._feature_extractor is None:
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
        if self._feature_extractor is None or self._classifier is None or torch is None:
            return None
        features = self._feature_extractor.embed_clips(np.expand_dims(self._buffer, axis=0))
        if len(features) == 0:
            return None
        vector = self._flatten_feature_frames(features[0])
        if vector is None:
            return None
        x = torch.from_numpy(vector.reshape(1, -1)).float()
        with torch.inference_mode():
            return float(self._classifier(x).item())

    @classmethod
    def _flatten_feature_frames(cls, feature_frames: np.ndarray) -> np.ndarray | None:
        if feature_frames is None or getattr(feature_frames, "ndim", 0) != 2:
            return None
        frame_count = int(feature_frames.shape[0])
        if frame_count >= cls.FEATURE_FRAMES:
            selected = feature_frames[-cls.FEATURE_FRAMES :, :]
        else:
            pad_len = cls.FEATURE_FRAMES - frame_count
            selected = np.pad(feature_frames, ((pad_len, 0), (0, 0)), mode="constant")
        return selected.reshape(-1).astype(np.float32, copy=False)

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
        self._classifier = None
