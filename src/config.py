import os
import sys
import logging
from pathlib import Path
from enum import Enum
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class OperationMode(Enum):
    GUIDE = "guide"
    SAFE = "safe"
    AUTO = "auto"

logger = logging.getLogger("pixelpilot.config")


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return str(default).strip()
    value = raw.strip()
    return value or str(default).strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return bool(default)
    value = str(raw).strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    logger.warning("Invalid boolean value for %s=%r; use true or false.", name, raw)
    return bool(default)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _local_appdata_dir() -> Path:
    base_dir = os.environ.get("LOCALAPPDATA")
    if base_dir:
        return Path(base_dir).expanduser().resolve() / "PixelPilot"
    return Path.home().resolve() / "AppData" / "Local" / "PixelPilot"


def _runtime_writable_root() -> Path:
    if getattr(sys, "frozen", False):
        return _local_appdata_dir()
    return _project_root()


class Config:
    BACKEND_URL = _env_str(
        "BACKEND_URL",
        "https://pixelpilot-xk7c.onrender.com",
    )
    WEB_URL = _env_str("WEB_URL", "https://pixelpilotai.vercel.app")
    GEMINI_API_KEY = _env_str("GEMINI_API_KEY")
    OPENAI_API_KEY = _env_str("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = _env_str("ANTHROPIC_API_KEY")
    XAI_API_KEY = _env_str("XAI_API_KEY")
    OPENROUTER_API_KEY = _env_str("OPENROUTER_API_KEY")
    OPENAI_COMPATIBLE_API_KEY = _env_str("OPENAI_COMPATIBLE_API_KEY")
    VERCEL_AI_GATEWAY_API_KEY = _env_str("VERCEL_AI_GATEWAY_API_KEY")
    OLLAMA_BASE_URL = _env_str("OLLAMA_BASE_URL", "http://localhost:11434")
    OPENAI_COMPATIBLE_BASE_URL = _env_str("OPENAI_COMPATIBLE_BASE_URL")
    VERCEL_AI_GATEWAY_BASE_URL = _env_str("VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1")
    MODEL_PROVIDER = _env_str("PIXELPILOT_MODEL_PROVIDER", _env_str("AI_PROVIDER", "gemini")).lower()
    LIVE_PROVIDER = _env_str("PIXELPILOT_LIVE_PROVIDER", _env_str("LIVE_PROVIDER", MODEL_PROVIDER)).lower()
    MODEL_NAME = _env_str("PIXELPILOT_MODEL", _env_str("MODEL_NAME", "gemini-3-flash-preview"))
    GEMINI_MODEL = MODEL_NAME
    USE_DIRECT_API = bool(
        GEMINI_API_KEY
        or OPENAI_API_KEY
        or ANTHROPIC_API_KEY
        or XAI_API_KEY
        or OPENROUTER_API_KEY
        or OPENAI_COMPATIBLE_API_KEY
        or VERCEL_AI_GATEWAY_API_KEY
        or MODEL_PROVIDER == "ollama"
    )
    GEMINI_LIVE_MODEL = _env_str("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
    LIVE_MODEL = _env_str("PIXELPILOT_LIVE_MODEL", GEMINI_LIVE_MODEL)
    LIVE_ENABLE_IMAGE_INPUT = True
    LIVE_ENABLE_VIDEO_STREAM = True
    LIVE_ENABLE_CONTEXT_WINDOW_COMPRESSION = True
    LIVE_VOICE_NAME = "zephyr"
    LIVE_THINKING_LEVEL = ""
    LIVE_INCLUDE_THOUGHTS = False
    LIVE_VIDEO_FPS = 1
    LIVE_AUDIO_INPUT_RATE = 16000
    LIVE_AUDIO_OUTPUT_RATE = 24000
    LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS = 192
    LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS = LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS
    LIVE_AUDIO_SPEAKER_QUEUE_TRIM_TO_CHUNKS = max(
        4,
        min(
            LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS - 1,
            144,
        ),
    )
    LIVE_AUDIO_SPEAKER_BATCH_MAX_CHUNKS = 8
    LIVE_AUDIO_SPEAKER_BATCH_MAX_BYTES = 65536
    LIVE_AUDIO_LOSSLESS_MODE = True
    LIVE_AUDIO_MIC_SUPPRESS_TAIL_MS = 220
    LIVE_TEXT_SEND_TIMEOUT_SECONDS = 8.0
    LIVE_CONNECT_RETRY_BASE_DELAY_SECONDS = 0.75
    LIVE_GUIDANCE_OBSERVER_POLL_SECONDS = 1.0
    LIVE_GUIDANCE_OBSERVER_NUDGE_COOLDOWN_SECONDS = 2.5
    LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS = 0.15
    LIVE_TYPED_TURN_IDLE_FINISH_SECONDS = 1.5
    LIVE_DISCONNECT_AFTER_REPLY_TIMEOUT_SECONDS = _env_float("LIVE_DISCONNECT_AFTER_REPLY_TIMEOUT_SECONDS", 8.0)
    LIVE_SESSION_IDLE_DISCONNECT_SECONDS = _env_float("LIVE_SESSION_IDLE_DISCONNECT_SECONDS", 60.0)
    LIVE_ACTION_RESPONSE_WAIT_MS = 4000
    LIVE_FORWARD_ACTION_UPDATES = False
    LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_CHUNKS = max(1, min(LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS, 144))
    LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_COOLDOWN_SECONDS = 3.0
    LIVE_AUDIO_QUEUE_PUT_TIMEOUT_SECONDS = 0.20
    LIVE_AUDIO_QUEUE_DROP_LOG_COOLDOWN_SECONDS = 2.0
    LIVE_AUDIO_RESAMPLE_LOG_COOLDOWN_SECONDS = 5.0
    LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE = 105
    ENABLE_WAKE_WORD = True
    WAKE_WORD_PHRASE = _env_str("WAKE_WORD_PHRASE", "Hey Pixie")
    WAKE_WORD_OPENWAKEWORD_MODEL_PATH = _env_str("WAKE_WORD_OPENWAKEWORD_MODEL_PATH")
    WAKE_WORD_OPENWAKEWORD_THRESHOLD = _env_float("WAKE_WORD_OPENWAKEWORD_THRESHOLD", 0.003)
    WAKE_WORD_OPENWAKEWORD_VAD_THRESHOLD = _env_float("WAKE_WORD_OPENWAKEWORD_VAD_THRESHOLD", 0.005)
    WAKE_WORD_ONNX_RMS_THRESHOLD = _env_float("WAKE_WORD_ONNX_RMS_THRESHOLD", 80.0)
    WAKE_WORD_ONNX_SCORE_SMOOTHING_CHUNKS = max(1, int(_env_float("WAKE_WORD_ONNX_SCORE_SMOOTHING_CHUNKS", 5.0)))
    WAKE_WORD_NO_SPEECH_TIMEOUT_SECONDS = _env_float("WAKE_WORD_NO_SPEECH_TIMEOUT_SECONDS", 1.5)
    WAKE_WORD_RESUME_DELAY_SECONDS = _env_float("WAKE_WORD_RESUME_DELAY_SECONDS", 0.25)
    WAKE_WORD_ASR_FALLBACK_ENABLED = _env_bool("WAKE_WORD_ASR_FALLBACK_ENABLED", True)
    WAKE_WORD_ASR_FALLBACK_MIN_SCORE = _env_float("WAKE_WORD_ASR_FALLBACK_MIN_SCORE", 0.00015)
    WAKE_WORD_ASR_FALLBACK_COOLDOWN_SECONDS = _env_float("WAKE_WORD_ASR_FALLBACK_COOLDOWN_SECONDS", 2.0)
    VOICEPRINT_ENABLED = _env_bool("VOICEPRINT_ENABLED", False)
    VOICEPRINT_THRESHOLD = _env_float("VOICEPRINT_THRESHOLD", 0.78)
    VOICEPRINT_UNCERTAIN_THRESHOLD = _env_float("VOICEPRINT_UNCERTAIN_THRESHOLD", 0.72)
    VOICEPRINT_ENCODER_ONNX_PATH = _env_str("VOICEPRINT_ENCODER_ONNX_PATH")
    VOICEPRINT_MIN_ENROLLMENT_SAMPLES = max(1, int(_env_float("VOICEPRINT_MIN_ENROLLMENT_SAMPLES", 4.0)))
    VOICEPRINT_DEBUG_SAVE_AUDIO = _env_bool("VOICEPRINT_DEBUG_SAVE_AUDIO", False)
    VOICEPRINT_PATH = os.path.expanduser("~/.pixelpilot/voiceprint.json")
    ENABLE_GATEWAY = False
    GATEWAY_HOST = "localhost"
    GATEWAY_PORT = 8765
    GATEWAY_COMMAND_TIMEOUT_SECONDS = 120.0
    GATEWAY_TOKEN = ""

    DEFAULT_MODE = OperationMode.AUTO
    VISION_MODE = "ocr"

    USE_ROBOTICS_EYE = VISION_MODE in {"robo", "robotics", "er", "robotics-er"}
    ROBOTICS_USE_BOUNDING_BOXES = True

    LAZY_VISION = not USE_ROBOTICS_EYE
    INCREMENTAL_SCREENSHOTS = True
    ROBOTICS_MAX_ELEMENTS = 50
    ENABLE_REFERENCE_SHEET = True

    MAX_TASK_STEPS = 50
    MAX_RETRIES = 3
    ACTION_TIMEOUT = 30
    SCREENSHOT_DELAY = 0.5

    MAX_ELEMENTS_TO_ANALYZE = 100
    PROJECT_ROOT = _project_root()
    APP_DATA_DIR = _runtime_writable_root()
    MEDIA_DIR = str(APP_DATA_DIR / "media")
    SCREENSHOT_PATH = os.path.join(MEDIA_DIR, "screen.png")
    DEBUG_PATH = os.path.join(MEDIA_DIR, "debug_overlay.png")
    REF_PATH = os.path.join(MEDIA_DIR, "debug_reference.png")
    EDGE_PATH = os.path.join(MEDIA_DIR, "debug_edges.png")
    TEMP_SCREEN_PATH = os.path.join(MEDIA_DIR, "temp_screen.png")
    UAC_IPC_DIR = os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"),
        "PixelPilot",
        "uac",
    )
    UAC_REQUEST_MAX_AGE_SECONDS = 90.0
    UAC_RESPONSE_TIMEOUT_SECONDS = 15.0
    UAC_REQUEST_SNAPSHOT_WAIT_SECONDS = 22.0
    UAC_IPC_POLL_INTERVAL_SECONDS = 0.5
    UAC_PROMPT_CLEAR_TIMEOUT_SECONDS = 20.0
    UAC_USER_CONFIRM_TIMEOUT_SECONDS = 5.0
    UAC_EXPECTED_MATCH_MIN_CONFIDENCE = 0.85
    UAC_POST_ACTION_DETECT_WINDOW_SECONDS = 2.5
    UAC_POST_ACTION_DETECT_POLL_SECONDS = 0.1
    LIVE_USE_INTERNAL_UAC_DETECTOR = False
    LIVE_UAC_VIDEO_PAUSE_AUTO_HANDLE = True
    UAC_WATCHDOG_POLL_SECONDS = 0.2
    UAC_WATCHDOG_RETRY_COOLDOWN_SECONDS = 1.0
    UAC_ORCHESTRATOR_API_HOST = "127.0.0.1"
    UAC_ORCHESTRATOR_API_PORT = 8779
    UAC_ORCHESTRATOR_API_TIMEOUT_SECONDS = 3.0
    UAC_HELPER_INITIAL_CAPTURE_DELAY_SECONDS = 1.0
    UAC_HELPER_KEY_PRESS_DELAY_SECONDS = 0.05
    UAC_HELPER_POST_ACTION_DELAY_SECONDS = 0.1
    UAC_CAPTURE_SETTLE_AFTER_RESPONSE_SECONDS = 2.0

    REQUIRE_CONFIRMATION_FOR = [
        "delete",
        "remove",
        "uninstall",
        "format",
        "shutdown",
        "restart",
        "close",
    ]

    DANGEROUS_COMMANDS = ["rm -rf", "del /f", "format", "diskpart"]

    WAIT_AFTER_CLICK = 0.5
    WAIT_AFTER_TYPE = 0.3
    WAIT_AFTER_KEY = 0.2
    TYPING_INTERVAL = 0.05

    TURBO_MODE = True

    ENABLE_VERIFICATION = True
    VERIFICATION_MIN_CONFIDENCE = 0.7
    VERIFICATION_DELAY = 1.5

    ENABLE_LOOP_DETECTION = True
    LOOP_DETECTION_THRESHOLD = 3
    LOOP_SCREEN_SIMILARITY_THRESHOLD = 0.95

    APP_INDEX_PATH = os.path.expanduser("~/.pixelpilot/app_index.json")
    APP_INDEX_AUTO_REFRESH = True
    APP_INDEX_INCLUDE_PROCESSES = True

    ENABLE_CLARIFICATION = True
    CLARIFICATION_MIN_CONFIDENCE = 0.7
    CLARIFICATION_TIMEOUT = 60

    ENABLE_BLIND_MODE = True
    APP_LAUNCH_WAIT = 3
    ENABLE_UIA_BLIND_MODE = True
    ENABLE_UIA_FOR_AGENT_WORKSPACE = True
    UIA_MAX_ELEMENTS = 120
    UIA_MAX_WINDOWS = 40
    UIA_TEXT_MAX_CHARS = 4000
    UIA_TEXT_OCR_MIN_CHARS = 160
    UIA_TEXT_OCR_MAX_NOISE_RATIO = 0.18
    UIA_TEXT_USE_OCR_FALLBACK_DEFAULT = False

    BLIND_VERIFICATION_RETRIES = 2
    BLIND_VERIFICATION_RETRY_DELAY = 0.25

    SAVE_SCREENSHOTS = True
    VERBOSE_LOGGING = True

    ENABLE_AGENT_DESKTOP = True
    AGENT_DESKTOP_NAME = "PixelPilotAgent"
    SIDECAR_PREVIEW_FPS = 5
    SIDECAR_PREVIEW_WIDTH = 400
    SIDECAR_PREVIEW_HEIGHT = 300
    DEFAULT_WORKSPACE = "user"  # "user" or "agent"

    @classmethod
    def runtime_resource_dir(cls) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return cls.PROJECT_ROOT

    @classmethod
    def resolve_wake_word_openwakeword_model_path(cls) -> Optional[Path]:
        from wakeword.openwakeword_detector import resolve_openwakeword_model_path

        return resolve_openwakeword_model_path(
            project_root=cls.PROJECT_ROOT,
            runtime_dir=cls.runtime_resource_dir(),
            is_frozen=bool(getattr(sys, "frozen", False)),
            raw_model_path=str(cls.WAKE_WORD_OPENWAKEWORD_MODEL_PATH or ""),
        )

    @classmethod
    def resolve_wake_word_openwakeword_feature_model_paths(
        cls,
        *,
        model_path: Optional[Path] = None,
    ) -> tuple[Optional[Path], Optional[Path]]:
        from wakeword.openwakeword_detector import resolve_openwakeword_feature_model_paths

        return resolve_openwakeword_feature_model_paths(
            project_root=cls.PROJECT_ROOT,
            runtime_dir=cls.runtime_resource_dir(),
            is_frozen=bool(getattr(sys, "frozen", False)),
            model_path=model_path,
        )

    @classmethod
    def resolve_voiceprint_encoder_model_path(cls) -> Optional[Path]:
        configured = str(cls.VOICEPRINT_ENCODER_ONNX_PATH or "").strip()
        candidates: list[Path] = []
        if configured:
            raw = Path(os.path.expandvars(os.path.expanduser(configured)))
            if raw.is_absolute():
                candidates.append(raw)
            else:
                candidates.extend(
                    [
                        cls.PROJECT_ROOT / raw,
                        cls.runtime_resource_dir() / "speaker" / raw.name,
                        cls.runtime_resource_dir() / raw,
                    ]
                )
        candidates.extend(
            [
                Path.home().resolve() / ".pixelpilot" / "models" / "speaker-embedding.onnx",
                cls.PROJECT_ROOT / "models" / "speaker-embedding.onnx",
                cls.runtime_resource_dir() / "speaker" / "speaker-embedding.onnx",
            ]
        )
        seen: set[str] = set()
        deduped: list[Path] = []
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        for candidate in deduped:
            if candidate.exists():
                return candidate
        return deduped[0] if deduped else None

    @classmethod
    def get_mode(cls, mode_str: Optional[str] = None) -> OperationMode:
        """
        Get operation mode from string or environment variable.

        Args:
            mode_str: Mode string ('guide', 'safe', 'auto')

        Returns:
            OperationMode: The operation mode
        """
        if mode_str is None:
            mode_str = cls.DEFAULT_MODE.value
            
        if not isinstance(mode_str, str):
            mode_str = str(mode_str)

        mode_str = mode_str.lower()

        if mode_str == "guide":
            return OperationMode.GUIDE
        elif mode_str == "safe":
            return OperationMode.SAFE
        elif mode_str == "auto":
            return OperationMode.AUTO
        else:
            logger.warning(f"Unknown mode '{mode_str}', using default: {cls.DEFAULT_MODE.value}")
            return cls.DEFAULT_MODE

    @classmethod
    def is_dangerous_action(cls, action_description: str) -> bool:
        action_lower = action_description.lower()

        for cmd in cls.DANGEROUS_COMMANDS:
            if cmd.lower() in action_lower:
                return True

        for keyword in cls.REQUIRE_CONFIRMATION_FOR:
            if keyword in action_lower:
                return True

        return False

    @classmethod
    def should_ask_confirmation(cls, mode: OperationMode, action_description: str) -> bool:
        if mode == OperationMode.GUIDE:
            return False

        if mode == OperationMode.SAFE:
            return cls.is_dangerous_action(action_description)

        if mode == OperationMode.AUTO:
            return False

        return True

    @classmethod
    def validate(cls):
        logger.info("Configuration validated successfully")
        logger.debug(f"Model provider: {cls.MODEL_PROVIDER}")
        logger.debug(f"Model: {cls.MODEL_NAME}")
        logger.debug(f"Live provider: {cls.LIVE_PROVIDER}")
        logger.debug(f"Live model: {cls.LIVE_MODEL}")
        logger.debug(f"Backend: {cls.BACKEND_URL}")
        logger.debug(f"Default Mode: {cls.DEFAULT_MODE.value}")
        logger.debug(f"Turbo Mode: {'ENABLED' if cls.TURBO_MODE else 'DISABLED'}")

    @classmethod
    def clear_api_key(cls):
        """Clears direct-provider API keys from environment and .env file."""
        cls.GEMINI_API_KEY = None
        cls.OPENAI_API_KEY = ""
        cls.ANTHROPIC_API_KEY = ""
        cls.XAI_API_KEY = ""
        cls.OPENROUTER_API_KEY = ""
        cls.OPENAI_COMPATIBLE_API_KEY = ""
        cls.VERCEL_AI_GATEWAY_API_KEY = ""
        cls.USE_DIRECT_API = False
        for env_name in (
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "XAI_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENAI_COMPATIBLE_API_KEY",
            "VERCEL_AI_GATEWAY_API_KEY",
        ):
            os.environ.pop(env_name, None)

        env_path = cls.PROJECT_ROOT / ".env"
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                key_prefixes = (
                    "GEMINI_API_KEY=",
                    "OPENAI_API_KEY=",
                    "ANTHROPIC_API_KEY=",
                    "XAI_API_KEY=",
                    "OPENROUTER_API_KEY=",
                    "OPENAI_COMPATIBLE_API_KEY=",
                    "VERCEL_AI_GATEWAY_API_KEY=",
                )
                new_lines = [l for l in lines if not l.strip().startswith(key_prefixes)]
                
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                logger.info("Removed direct-provider API keys from .env")
            except Exception as e:
                logger.error(f"Failed to clear API key from .env: {e}")
