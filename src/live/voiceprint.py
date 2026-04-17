from __future__ import annotations

import audioop
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np


logger = logging.getLogger("pixelpilot.voiceprint")

VOICEPRINT_SCHEMA_VERSION = 1
TARGET_SAMPLE_RATE = 16_000
DEFAULT_RECORD_SECONDS = 2.0
MIN_AUDIO_SECONDS = 0.75
MAX_AUDIO_SECONDS = 3.0
MEL_FEATURE_DIM = 80
FRAME_LENGTH_SECONDS = 0.025
FRAME_SHIFT_SECONDS = 0.010
PREEMPHASIS_COEFFICIENT = 0.97
_MEL_FILTER_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


class VoiceprintError(RuntimeError):
    pass


class VoiceprintEncoderProtocol(Protocol):
    @property
    def model_path(self) -> Path | None:
        ...

    @property
    def model_id(self) -> str:
        ...

    @property
    def unavailable_reason(self) -> str:
        ...

    def is_available(self) -> bool:
        ...

    def embed_pcm16(self, pcm16: bytes, *, sample_rate: int = TARGET_SAMPLE_RATE) -> np.ndarray:
        ...


@dataclass(slots=True)
class VoiceprintRecord:
    enabled: bool
    threshold: float
    uncertain_threshold: float
    embedding: np.ndarray | None = None
    sample_count: int = 0
    model_id: str = ""
    model_hash: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def enrolled(self) -> bool:
        return self.embedding is not None and int(self.embedding.size) > 0


@dataclass(slots=True)
class VoiceprintDecision:
    decision: str
    accepted: bool
    score: float | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "accepted": self.accepted,
            "score": self.score,
            "reason": self.reason,
        }


class VoiceprintStore:
    def __init__(
        self,
        path: str | Path,
        *,
        default_enabled: bool = False,
        threshold: float = 0.78,
        uncertain_threshold: float = 0.72,
    ) -> None:
        self.path = Path(path).expanduser()
        self.default_enabled = bool(default_enabled)
        self.threshold = float(threshold)
        self.uncertain_threshold = float(uncertain_threshold)

    def load(self) -> VoiceprintRecord:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return VoiceprintRecord(
                enabled=self.default_enabled,
                threshold=self.threshold,
                uncertain_threshold=self.uncertain_threshold,
            )
        except Exception as exc:
            logger.warning("Failed loading voiceprint store: %s", exc, exc_info=True)
            return VoiceprintRecord(
                enabled=False,
                threshold=self.threshold,
                uncertain_threshold=self.uncertain_threshold,
            )

        if not isinstance(payload, dict):
            return VoiceprintRecord(
                enabled=False,
                threshold=self.threshold,
                uncertain_threshold=self.uncertain_threshold,
            )

        embedding = _coerce_embedding(payload.get("embedding"))

        return VoiceprintRecord(
            enabled=bool(payload.get("enabled", self.default_enabled)),
            threshold=_coerce_float(payload.get("threshold"), self.threshold),
            uncertain_threshold=_coerce_float(payload.get("uncertainThreshold"), self.uncertain_threshold),
            embedding=embedding,
            sample_count=max(0, _coerce_int(payload.get("sampleCount"), 0)),
            model_id=str(payload.get("modelId") or ""),
            model_hash=str(payload.get("modelHash") or ""),
            created_at=str(payload.get("createdAt") or ""),
            updated_at=str(payload.get("updatedAt") or ""),
        )

    def save_record(self, record: VoiceprintRecord) -> None:
        now = utc_timestamp()
        embedding = record.embedding
        payload = {
            "schemaVersion": VOICEPRINT_SCHEMA_VERSION,
            "enabled": bool(record.enabled),
            "threshold": float(record.threshold),
            "uncertainThreshold": float(record.uncertain_threshold),
            "embedding": embedding.astype(float).tolist() if embedding is not None else [],
            "embeddingDim": int(embedding.size) if embedding is not None else 0,
            "sampleCount": int(record.sample_count),
            "modelId": str(record.model_id or ""),
            "modelHash": str(record.model_hash or ""),
            "createdAt": record.created_at or now,
            "updatedAt": now,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def save_embedding(
        self,
        embedding: np.ndarray,
        *,
        sample_count: int,
        model_id: str,
        model_hash: str,
        enabled: bool = True,
    ) -> VoiceprintRecord:
        current = self.load()
        record = VoiceprintRecord(
            enabled=bool(enabled),
            threshold=current.threshold,
            uncertain_threshold=current.uncertain_threshold,
            embedding=l2_normalize(np.asarray(embedding, dtype=np.float32)),
            sample_count=max(1, int(sample_count or 1)),
            model_id=str(model_id or ""),
            model_hash=str(model_hash or ""),
            created_at=current.created_at or utc_timestamp(),
        )
        self.save_record(record)
        return record

    def set_enabled(self, enabled: bool) -> VoiceprintRecord:
        record = self.load()
        record.enabled = bool(enabled)
        self.save_record(record)
        return self.load()

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


class VoiceprintEncoder:
    def __init__(self, model_path: str | Path | None) -> None:
        self._model_path = Path(model_path).expanduser().resolve() if model_path else None
        self._session = None
        self._input_name = ""
        self._input_shape: list[Any] = []
        self._unavailable_reason = ""

    @property
    def model_path(self) -> Path | None:
        return self._model_path

    @property
    def model_id(self) -> str:
        return self._model_path.name if self._model_path is not None else ""

    @property
    def unavailable_reason(self) -> str:
        if self._unavailable_reason:
            return self._unavailable_reason
        if self._model_path is None:
            return "Speaker embedding model is not configured."
        if not self._model_path.exists():
            return f"Speaker embedding model not found: {self._model_path}"
        return ""

    def is_available(self) -> bool:
        if self._model_path is None or not self._model_path.exists():
            return False
        return self._ensure_session()

    def embed_pcm16(self, pcm16: bytes, *, sample_rate: int = TARGET_SAMPLE_RATE) -> np.ndarray:
        if not self._ensure_session() or self._session is None or not self._input_name:
            raise VoiceprintError(self.unavailable_reason or "Speaker embedding model is unavailable.")
        audio = preprocess_pcm16(pcm16, sample_rate=sample_rate)
        model_input = self._shape_model_input(audio)
        outputs = self._session.run(None, {self._input_name: model_input})
        if not outputs:
            raise VoiceprintError("Speaker embedding model returned no outputs.")
        embedding = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if embedding.size == 0:
            raise VoiceprintError("Speaker embedding model returned an empty embedding.")
        return l2_normalize(embedding)

    def _ensure_session(self) -> bool:
        if self._session is not None:
            return True
        if self._model_path is None:
            self._unavailable_reason = "Speaker embedding model is not configured."
            return False
        if not self._model_path.exists():
            self._unavailable_reason = f"Speaker embedding model not found: {self._model_path}"
            return False
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(str(self._model_path), providers=["CPUExecutionProvider"])
            inputs = self._session.get_inputs()
            if not inputs:
                raise VoiceprintError("Speaker embedding ONNX model exposes no inputs.")
            self._input_name = str(inputs[0].name)
            self._input_shape = list(getattr(inputs[0], "shape", []) or [])
            self._unavailable_reason = ""
            return True
        except Exception as exc:
            self._session = None
            self._input_name = ""
            self._input_shape = []
            self._unavailable_reason = f"Speaker embedding model failed to load: {exc}"
            logger.warning(self._unavailable_reason, exc_info=True)
            return False

    def _shape_model_input(self, audio: np.ndarray) -> np.ndarray:
        shape = list(self._input_shape or [])
        last_dim = _static_shape_dim(shape[-1]) if shape else None
        if len(shape) >= 2 and last_dim == MEL_FEATURE_DIM:
            features = waveform_to_log_mel_features(audio, sample_rate=TARGET_SAMPLE_RATE, n_mels=MEL_FEATURE_DIM)
            if len(shape) == 2:
                return features.astype(np.float32, copy=False)
            return features.reshape(1, features.shape[0], features.shape[1]).astype(np.float32, copy=False)
        if len(shape) == 1:
            return audio.astype(np.float32, copy=False)
        if len(shape) == 3:
            return audio.reshape(1, 1, -1).astype(np.float32, copy=False)
        return audio.reshape(1, -1).astype(np.float32, copy=False)


class VoiceVerifier:
    def __init__(self, encoder: VoiceprintEncoderProtocol) -> None:
        self.encoder = encoder

    def embed_clip(self, pcm16: bytes, *, sample_rate: int = TARGET_SAMPLE_RATE) -> np.ndarray:
        return self.encoder.embed_pcm16(pcm16, sample_rate=sample_rate)

    def build_voiceprint(self, embeddings: list[np.ndarray]) -> np.ndarray:
        if not embeddings:
            raise VoiceprintError("At least one enrollment sample is required.")
        stacked = np.stack([l2_normalize(np.asarray(item, dtype=np.float32)) for item in embeddings], axis=0)
        reference = np.mean(stacked, axis=0)
        if not np.all(np.isfinite(reference)) or float(np.linalg.norm(reference)) <= 1e-8:
            raise VoiceprintError("Enrollment samples did not produce a stable voiceprint.")
        return l2_normalize(reference)

    def verify(
        self,
        pcm16: bytes,
        reference_embedding: np.ndarray,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> float:
        live_embedding = self.embed_clip(pcm16, sample_rate=sample_rate)
        return cosine_similarity(live_embedding, reference_embedding)


class VoiceprintService:
    def __init__(
        self,
        *,
        store: VoiceprintStore,
        encoder: VoiceprintEncoderProtocol,
        min_samples: int = 4,
        debug_save_audio: bool = False,
        debug_dir: str | Path | None = None,
    ) -> None:
        self.store = store
        self.encoder = encoder
        self.verifier = VoiceVerifier(encoder)
        self.min_samples = max(1, int(min_samples or 4))
        self.debug_save_audio = bool(debug_save_audio)
        self.debug_dir = Path(debug_dir).expanduser() if debug_dir else None
        self._pending_embeddings: list[np.ndarray] = []
        self._last_score: float | None = None
        self._last_decision = ""
        self._last_reason = ""

    def status(self) -> dict[str, Any]:
        record = self.store.load()
        enabled = bool(record.enabled)
        enrolled = record.enrolled
        encoder_available = (
            self.encoder.is_available()
            if enabled
            else self.encoder.model_path is not None and self.encoder.model_path.exists()
        )
        unavailable_reason = ""
        status = "disabled"
        if enabled and not encoder_available:
            status = "unavailable"
            unavailable_reason = self.encoder.unavailable_reason
        elif enabled and not enrolled:
            status = "enrollment_required"
        elif enabled:
            status = "ready"

        return {
            "enabled": enabled,
            "enrolled": enrolled,
            "available": (not enabled) or encoder_available,
            "status": status,
            "lastScore": self._last_score,
            "lastDecision": self._last_decision,
            "lastReason": self._last_reason,
            "threshold": float(record.threshold),
            "uncertainThreshold": float(record.uncertain_threshold),
            "sampleCount": int(record.sample_count),
            "pendingSampleCount": len(self._pending_embeddings),
            "minEnrollmentSamples": self.min_samples,
            "embeddingDim": int(record.embedding.size) if record.embedding is not None else 0,
            "modelId": record.model_id or self.encoder.model_id,
            "modelPath": str(self.encoder.model_path or ""),
            "unavailableReason": unavailable_reason,
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.store.set_enabled(bool(enabled))
        return self.status()

    def clear(self) -> dict[str, Any]:
        self._pending_embeddings.clear()
        self._last_score = None
        self._last_decision = ""
        self._last_reason = ""
        self.store.clear()
        return self.status()

    def record_sample(
        self,
        *,
        seconds: float = DEFAULT_RECORD_SECONDS,
        recorder: Any = None,
    ) -> dict[str, Any]:
        if not self.encoder.is_available():
            raise VoiceprintError(self.encoder.unavailable_reason or "Speaker embedding model is unavailable.")
        pcm16, sample_rate = record_pcm16(seconds=seconds, recorder=recorder)
        if self.debug_save_audio:
            self._save_debug_audio(pcm16, sample_rate=sample_rate)
        embedding = self.verifier.embed_clip(pcm16, sample_rate=sample_rate)
        quality = audio_quality(pcm16, sample_rate=sample_rate)
        self._pending_embeddings.append(embedding)
        return {
            "ok": True,
            "sampleIndex": len(self._pending_embeddings),
            "pendingSampleCount": len(self._pending_embeddings),
            "minEnrollmentSamples": self.min_samples,
            "embeddingDim": int(embedding.size),
            "quality": quality,
            "voiceprint": self.status(),
        }

    def complete_enrollment(self) -> dict[str, Any]:
        if len(self._pending_embeddings) < self.min_samples:
            raise VoiceprintError(
                f"Record at least {self.min_samples} samples before completing enrollment."
            )
        reference = self.verifier.build_voiceprint(self._pending_embeddings)
        self.store.save_embedding(
            reference,
            sample_count=len(self._pending_embeddings),
            model_id=self.encoder.model_id,
            model_hash=model_hash(self.encoder.model_path),
            enabled=True,
        )
        self._pending_embeddings.clear()
        return self.status()

    def verify_trigger(self, payload: dict[str, Any] | None) -> VoiceprintDecision:
        record = self.store.load()
        if not record.enabled:
            return self._remember(VoiceprintDecision("accepted", True, reason="voiceprint_disabled"))
        if not record.enrolled or record.embedding is None:
            return self._remember(VoiceprintDecision("enrollment_required", False, reason="voiceprint_not_enrolled"))
        if not self.encoder.is_available():
            return self._remember(VoiceprintDecision("unavailable", False, reason=self.encoder.unavailable_reason))

        body = dict(payload or {})
        pcm16 = body.get("pcm16")
        if isinstance(pcm16, np.ndarray):
            pcm_bytes = np.asarray(pcm16, dtype=np.int16).tobytes()
        elif isinstance(pcm16, (bytes, bytearray, memoryview)):
            pcm_bytes = bytes(pcm16)
        else:
            return self._remember(VoiceprintDecision("rejected", False, reason="trigger_audio_missing"))

        try:
            sample_rate = int(body.get("sampleRate") or TARGET_SAMPLE_RATE)
            if sample_rate <= 0:
                raise ValueError("sample rate must be positive")
        except Exception:
            return self._remember(VoiceprintDecision("rejected", False, reason="invalid_sample_rate"))
        try:
            score = self.verifier.verify(pcm_bytes, record.embedding, sample_rate=sample_rate)
        except Exception as exc:
            return self._remember(VoiceprintDecision("unavailable", False, reason=str(exc)))

        if score >= record.threshold:
            return self._remember(VoiceprintDecision("accepted", True, score=score, reason="voice_match"))
        if score >= record.uncertain_threshold:
            return self._remember(VoiceprintDecision("uncertain", False, score=score, reason="voice_match_uncertain"))
        return self._remember(VoiceprintDecision("rejected", False, score=score, reason="voice_mismatch"))

    def _remember(self, decision: VoiceprintDecision) -> VoiceprintDecision:
        self._last_score = decision.score
        self._last_decision = decision.decision
        self._last_reason = decision.reason
        return decision

    def _save_debug_audio(self, pcm16: bytes, *, sample_rate: int) -> None:
        if self.debug_dir is None:
            return
        import wave

        self.debug_dir.mkdir(parents=True, exist_ok=True)
        target = self.debug_dir / f"voiceprint-sample-{int(time.time())}.wav"
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            handle.writeframes(bytes(pcm16))


def preprocess_pcm16(
    pcm16: bytes,
    *,
    sample_rate: int = TARGET_SAMPLE_RATE,
    target_rate: int = TARGET_SAMPLE_RATE,
    min_seconds: float = MIN_AUDIO_SECONDS,
    max_seconds: float = MAX_AUDIO_SECONDS,
) -> np.ndarray:
    payload = bytes(pcm16 or b"")
    if not payload:
        raise VoiceprintError("Audio sample is empty.")
    if len(payload) % 2:
        raise VoiceprintError("Audio sample is not valid 16-bit PCM.")
    if int(sample_rate or 0) <= 0 or int(target_rate or 0) <= 0:
        raise VoiceprintError("Audio sample rate must be positive.")
    if int(sample_rate or target_rate) != int(target_rate):
        payload = audioop.ratecv(payload, 2, 1, int(sample_rate), int(target_rate), None)[0]
    samples = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        raise VoiceprintError("Audio sample contains no PCM frames.")
    samples = trim_silence(samples)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1e-5:
        samples = np.clip(samples / max(1.0, peak / 0.95), -1.0, 1.0)
    min_samples = int(target_rate * max(0.1, float(min_seconds)))
    max_samples = int(target_rate * max(float(min_seconds), float(max_seconds)))
    if samples.size < min_samples:
        samples = np.pad(samples, (0, min_samples - samples.size), mode="constant")
    if samples.size > max_samples:
        samples = samples[-max_samples:]
    return samples.astype(np.float32, copy=False)


def waveform_to_log_mel_features(
    audio: np.ndarray,
    *,
    sample_rate: int = TARGET_SAMPLE_RATE,
    n_mels: int = MEL_FEATURE_DIM,
) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        raise VoiceprintError("Audio sample contains no frames for feature extraction.")

    frame_length = max(1, int(round(sample_rate * FRAME_LENGTH_SECONDS)))
    frame_shift = max(1, int(round(sample_rate * FRAME_SHIFT_SECONDS)))
    n_fft = 1
    while n_fft < frame_length:
        n_fft *= 2

    if samples.size < frame_length:
        samples = np.pad(samples, (0, frame_length - samples.size), mode="constant")

    emphasized = np.empty_like(samples, dtype=np.float32)
    emphasized[0] = samples[0]
    emphasized[1:] = samples[1:] - PREEMPHASIS_COEFFICIENT * samples[:-1]

    frame_count = 1 + int(np.ceil(max(0, emphasized.size - frame_length) / frame_shift))
    padded_length = (frame_count - 1) * frame_shift + frame_length
    if emphasized.size < padded_length:
        emphasized = np.pad(emphasized, (0, padded_length - emphasized.size), mode="constant")

    frame_offsets = np.arange(frame_count, dtype=np.int32)[:, None] * frame_shift
    sample_offsets = np.arange(frame_length, dtype=np.int32)[None, :]
    frames = emphasized[frame_offsets + sample_offsets]
    frames *= np.hamming(frame_length).astype(np.float32)

    spectrum = np.fft.rfft(frames, n=n_fft, axis=1)
    power = (np.abs(spectrum) ** 2).astype(np.float32) / float(n_fft)
    mel_filters = mel_filterbank(sample_rate=sample_rate, n_fft=n_fft, n_mels=n_mels)
    features = np.log(np.maximum(np.matmul(power, mel_filters.T), 1e-10)).astype(np.float32)
    features -= np.mean(features, axis=0, keepdims=True)
    return features.astype(np.float32, copy=False)


def mel_filterbank(
    *,
    sample_rate: int = TARGET_SAMPLE_RATE,
    n_fft: int = 512,
    n_mels: int = MEL_FEATURE_DIM,
    f_min: float = 20.0,
    f_max: float | None = None,
) -> np.ndarray:
    cache_key = (int(sample_rate), int(n_fft), int(n_mels))
    cached = _MEL_FILTER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    max_frequency = float(f_max if f_max is not None else sample_rate / 2)
    mel_points = np.linspace(_hz_to_mel(f_min), _hz_to_mel(max_frequency), n_mels + 2)
    hz_points = _mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(np.int32)
    bins = np.clip(bins, 0, n_fft // 2)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)

    for index in range(n_mels):
        left = int(bins[index])
        center = int(bins[index + 1])
        right = int(bins[index + 2])
        if center <= left:
            center = min(left + 1, filters.shape[1] - 1)
        if right <= center:
            right = min(center + 1, filters.shape[1])
        if center > left:
            filters[index, left:center] = (
                np.arange(left, center, dtype=np.float32) - float(left)
            ) / max(1.0, float(center - left))
        if right > center:
            filters[index, center:right] = (
                float(right) - np.arange(center, right, dtype=np.float32)
            ) / max(1.0, float(right - center))

    filters = np.maximum(filters, 0.0)
    _MEL_FILTER_CACHE[cache_key] = filters
    return filters


def trim_silence(samples: np.ndarray, *, threshold: float = 0.015, frame_size: int = 320) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.size <= frame_size:
        return audio
    active: list[int] = []
    for start in range(0, audio.size, frame_size):
        frame = audio[start : start + frame_size]
        if frame.size and float(np.sqrt(np.mean(np.square(frame)))) >= threshold:
            active.append(start)
    if not active:
        return audio
    start = max(0, active[0] - frame_size)
    end = min(audio.size, active[-1] + frame_size * 2)
    return audio[start:end]


def l2_normalize(x: np.ndarray) -> np.ndarray:
    value = np.asarray(x, dtype=np.float32)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-8:
        return value
    return value / norm


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(l2_normalize(a), l2_normalize(b)))


def audio_quality(pcm16: bytes, *, sample_rate: int = TARGET_SAMPLE_RATE) -> dict[str, Any]:
    samples = np.frombuffer(bytes(pcm16 or b""), dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return {"durationSeconds": 0.0, "rms": 0.0, "peak": 0.0}
    normalized = samples / 32768.0
    return {
        "durationSeconds": round(float(samples.size) / float(sample_rate or TARGET_SAMPLE_RATE), 3),
        "rms": round(float(np.sqrt(np.mean(np.square(normalized)))), 5),
        "peak": round(float(np.max(np.abs(normalized))), 5),
    }


def record_pcm16(
    *,
    seconds: float = DEFAULT_RECORD_SECONDS,
    rate: int = TARGET_SAMPLE_RATE,
    recorder: Any = None,
) -> tuple[bytes, int]:
    if recorder is not None:
        result = recorder(seconds=seconds, rate=rate)
        if isinstance(result, tuple):
            if len(result) != 2:
                raise VoiceprintError("Recorder must return PCM bytes and a sample rate.")
            pcm16, sample_rate = bytes(result[0]), int(result[1])
        else:
            pcm16, sample_rate = bytes(result), int(rate)
        if sample_rate <= 0:
            raise VoiceprintError("Recorder sample rate must be positive.")
        if len(pcm16) % 2:
            raise VoiceprintError("Recorder returned invalid 16-bit PCM.")
        return pcm16, sample_rate

    import pyaudio

    pa = pyaudio.PyAudio()
    stream = None
    frames: list[bytes] = []
    frames_per_buffer = 1024
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            input=True,
            frames_per_buffer=frames_per_buffer,
        )
        total_reads = max(1, int((float(seconds or DEFAULT_RECORD_SECONDS) * rate) / frames_per_buffer))
        for _ in range(total_reads):
            frames.append(stream.read(frames_per_buffer, exception_on_overflow=False))
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        pa.terminate()
    return b"".join(frames), int(rate)


def model_hash(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _coerce_embedding(value: Any) -> np.ndarray | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        embedding = np.asarray(value, dtype=np.float32).reshape(-1)
    except Exception:
        return None
    if embedding.size == 0 or not np.all(np.isfinite(embedding)):
        return None
    if float(np.linalg.norm(embedding)) <= 1e-8:
        return None
    return l2_normalize(embedding)


def _static_shape_dim(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _hz_to_mel(value: float | np.ndarray) -> float | np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(value, dtype=np.float32) / 700.0)


def _mel_to_hz(value: float | np.ndarray) -> float | np.ndarray:
    return 700.0 * (np.power(10.0, np.asarray(value, dtype=np.float32) / 2595.0) - 1.0)
