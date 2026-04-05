from __future__ import annotations

from config import Config

from .base import WakeWordDetector
from .openwakeword_detector import OpenWakeWordDetector, TorchOpenWakeWordDetector


def create_wake_word_detector() -> WakeWordDetector:
    return _create_openwakeword_detector()


def _create_openwakeword_detector() -> WakeWordDetector:
    model_path = Config.resolve_wake_word_openwakeword_model_path()
    if model_path is not None and model_path.suffix.lower() == ".pth":
        melspec_model_path, embedding_model_path = Config.resolve_wake_word_openwakeword_feature_model_paths(
            model_path=model_path
        )
        return TorchOpenWakeWordDetector(
            model_path=model_path,
            threshold=Config.WAKE_WORD_OPENWAKEWORD_THRESHOLD,
            melspec_model_path=melspec_model_path,
            embedding_model_path=embedding_model_path,
        )
    return OpenWakeWordDetector(
        model_path=model_path,
        threshold=Config.WAKE_WORD_OPENWAKEWORD_THRESHOLD,
        vad_threshold=Config.WAKE_WORD_OPENWAKEWORD_VAD_THRESHOLD,
    )
