from __future__ import annotations

from config import Config

from .base import WakeWordDetector
from .openwakeword_detector import (
    OpenWakeWordDetector,
    OnnxFeatureWakeWordDetector,
    uses_feature_extractor_model,
)


def create_wake_word_detector() -> WakeWordDetector:
    return _create_openwakeword_detector()


def _create_openwakeword_detector() -> WakeWordDetector:
    model_path = Config.resolve_wake_word_openwakeword_model_path()
    if uses_feature_extractor_model(model_path):
        melspec_model_path, embedding_model_path = Config.resolve_wake_word_openwakeword_feature_model_paths(
            model_path=model_path
        )
        return OnnxFeatureWakeWordDetector(
            model_path=model_path,
            phrase=Config.WAKE_WORD_PHRASE,
            threshold=Config.WAKE_WORD_OPENWAKEWORD_THRESHOLD,
            rms_threshold=Config.WAKE_WORD_ONNX_RMS_THRESHOLD,
            score_smoothing_chunks=Config.WAKE_WORD_ONNX_SCORE_SMOOTHING_CHUNKS,
            asr_fallback_enabled=Config.WAKE_WORD_ASR_FALLBACK_ENABLED,
            asr_fallback_min_score=Config.WAKE_WORD_ASR_FALLBACK_MIN_SCORE,
            asr_fallback_cooldown_seconds=Config.WAKE_WORD_ASR_FALLBACK_COOLDOWN_SECONDS,
            melspec_model_path=melspec_model_path,
            embedding_model_path=embedding_model_path,
        )
    return OpenWakeWordDetector(
        model_path=model_path,
        threshold=Config.WAKE_WORD_OPENWAKEWORD_THRESHOLD,
        vad_threshold=Config.WAKE_WORD_OPENWAKEWORD_VAD_THRESHOLD,
    )
