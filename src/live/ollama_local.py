from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
import numpy as np

from config import Config

logger = logging.getLogger("pixelpilot.live.ollama_local")


@dataclass(frozen=True, slots=True)
class OllamaRuntimeStatus:
    ok: bool
    reachable: bool
    model_available: bool
    model: str
    base_url: str
    message: str
    pull_command: str
    available_models: tuple[str, ...] = ()
    asr_available: bool = True
    asr_message: str = ""
    tts_available: bool = True
    tts_message: str = ""
    frame_loop_enabled: bool = False


class OllamaError(RuntimeError):
    """Raised when Ollama cannot complete a local model request."""


def strip_data_url_prefix(value: str) -> str:
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def build_user_message(content: str, image_b64: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "user", "content": content}
    if image_b64:
        message["images"] = [strip_data_url_prefix(image_b64)]
    return message


def extract_native_tool_calls(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    message = chunk.get("message") or {}
    calls = message.get("tool_calls") or []
    return [item for call in calls if (item := normalize_tool_call(call))]


def extract_tool_calls_from_content(content: str) -> list[dict[str, Any]]:
    parsed = _parse_possible_json(content)
    if not isinstance(parsed, dict):
        return []

    raw_calls = parsed.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw in raw_calls:
        item = normalize_tool_call(raw)
        if item:
            normalized.append(item)
    return normalized


def normalize_tool_call(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    function_data = raw.get("function")
    raw_arguments = raw.get("arguments")
    if isinstance(function_data, str):
        name = function_data
        raw_arguments = raw.get("args") if raw_arguments is None else raw_arguments
    elif isinstance(function_data, dict):
        name = function_data.get("name") or raw.get("name")
        raw_arguments = function_data.get(
            "arguments",
            function_data.get("args", raw.get("args", raw_arguments)),
        )
    else:
        name = raw.get("name") or raw.get("tool_name")
        raw_arguments = raw.get("args") if raw_arguments is None else raw_arguments

    parsed_arguments = raw_arguments
    if isinstance(parsed_arguments, str):
        try:
            parsed_arguments = json.loads(parsed_arguments)
        except json.JSONDecodeError:
            parsed_arguments = {"value": parsed_arguments}

    if not isinstance(parsed_arguments, dict):
        parsed_arguments = {}

    if not isinstance(name, str) or not name:
        return None

    result = {
        "id": raw.get("id") or f"local-{name}",
        "name": name,
        "args": parsed_arguments,
    }
    if raw_arguments is not None:
        try:
            result["arguments"] = json.dumps(parsed_arguments or {})
        except TypeError:
            result["arguments"] = "{}"
    return result


def diagnose_ollama_runtime(
    *,
    base_url: str,
    model: str,
    require_local_asr: bool,
    asr_model: str,
    require_local_tts: bool = False,
    tts_enabled: bool | None = None,
    model_path: Path | None = None,
    voices_path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    frame_loop_enabled: bool = False,
) -> OllamaRuntimeStatus:
    clean_base_url = str(base_url or "").rstrip("/")
    clean_model = str(model or "").strip()
    pull_command = f"ollama pull {clean_model}" if clean_model else ""

    try:
        with httpx.Client(timeout=5.0, transport=transport) as client:
            response = client.get(f"{clean_base_url}/api/tags")
            response.raise_for_status()
        payload = response.json()
        available_models = tuple(
            str(item.get("name") or "").strip()
            for item in payload.get("models", [])
            if str(item.get("name") or "").strip()
        )
        reachable = True
    except Exception as exc:  # noqa: BLE001
        return OllamaRuntimeStatus(
            ok=False,
            reachable=False,
            model_available=False,
            model=clean_model,
            base_url=clean_base_url,
            message=f"Ollama is not reachable at {clean_base_url}: {exc}",
            pull_command=pull_command,
            asr_available=not require_local_asr,
            tts_available=not require_local_tts,
            frame_loop_enabled=bool(frame_loop_enabled),
        )

    model_available = clean_model in available_models
    if not model_available:
        return OllamaRuntimeStatus(
            ok=False,
            reachable=reachable,
            model_available=False,
            model=clean_model,
            base_url=clean_base_url,
            message=f"Model {clean_model} is not installed. Run: {pull_command}",
            pull_command=pull_command,
            available_models=available_models,
            asr_available=not require_local_asr,
            tts_available=not require_local_tts,
            frame_loop_enabled=bool(frame_loop_enabled),
        )

    asr_available = True
    asr_message = ""
    if require_local_asr:
        asr_available, asr_message = LocalAsrTranscriber.availability(model_name=asr_model)
        if not asr_available:
            return OllamaRuntimeStatus(
                ok=False,
                reachable=True,
                model_available=True,
                model=clean_model,
                base_url=clean_base_url,
                message=asr_message,
                pull_command=pull_command,
                available_models=available_models,
                asr_available=False,
                asr_message=asr_message,
                tts_available=not require_local_tts,
                frame_loop_enabled=bool(frame_loop_enabled),
            )

    effective_tts_required = bool(require_local_tts or tts_enabled)
    tts_available = True
    tts_message = ""
    if effective_tts_required:
        tts_available, tts_message = KokoroTtsSynthesizer.availability(
            enabled=bool(tts_enabled if tts_enabled is not None else True),
            model_path=model_path,
            voices_path=voices_path,
        )
        if not tts_available:
            return OllamaRuntimeStatus(
                ok=False,
                reachable=True,
                model_available=True,
                model=clean_model,
                base_url=clean_base_url,
                message=tts_message,
                pull_command=pull_command,
                available_models=available_models,
                asr_available=asr_available,
                asr_message=asr_message,
                tts_available=False,
                tts_message=tts_message,
                frame_loop_enabled=bool(frame_loop_enabled),
            )

    return OllamaRuntimeStatus(
        ok=True,
        reachable=True,
        model_available=True,
        model=clean_model,
        base_url=clean_base_url,
        message=f"Ollama is reachable and {clean_model} is installed.",
        pull_command=pull_command,
        available_models=available_models,
        asr_available=asr_available,
        asr_message=asr_message,
        tts_available=tts_available,
        tts_message=tts_message,
        frame_loop_enabled=bool(frame_loop_enabled),
    )


class OllamaChatClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.model = str(model or "").strip()
        self.timeout = timeout
        self.transport = transport

    async def chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        yield json.loads(line)
        except httpx.HTTPStatusError as exc:
            try:
                detail = (await exc.response.aread()).decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = ""
            raise OllamaError(
                f"Ollama /api/chat failed with {exc.response.status_code}: {detail}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise OllamaError(f"Ollama /api/chat failed: {exc}") from exc


class LocalAsrTranscriber:
    _MODEL_CACHE: dict[str, Any] = {}

    @classmethod
    def availability(cls, *, model_name: str) -> tuple[bool, str]:
        if not str(model_name or "").strip():
            return False, "LOCAL_ASR_MODEL is not configured."
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            return (
                False,
                "Local ASR needs faster-whisper. Install it with: "
                "python -m pip install -r requirements.txt"
                f" ({exc})",
            )
        if WhisperModel is None:
            return False, "faster-whisper could not be loaded."
        return True, ""

    @classmethod
    async def transcribe_pcm16_async(
        cls,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        model_name: str,
    ) -> str:
        return await asyncio.to_thread(
            cls.transcribe_pcm16_sync,
            audio_bytes,
            sample_rate=sample_rate,
            model_name=model_name,
        )

    @classmethod
    def transcribe_pcm16_sync(
        cls,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        model_name: str,
    ) -> str:
        available, message = cls.availability(model_name=model_name)
        if not available:
            raise RuntimeError(message)
        if not audio_bytes:
            return ""

        audio_dir = Path(Config.MEDIA_DIR) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".wav",
                dir=audio_dir,
            ) as temp_file:
                temp_path = temp_file.name
            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(int(sample_rate))
                wav_file.writeframes(audio_bytes)

            model = cls._get_model(model_name)
            try:
                segments, _info = model.transcribe(temp_path, beam_size=1)
                parts = [
                    str(segment.text).strip()
                    for segment in segments
                    if str(getattr(segment, "text", "")).strip()
                ]
            except RuntimeError as exc:
                if "cublas" in str(exc).lower() or "cudnn" in str(exc).lower() or "cuda" in str(exc).lower():
                    logger.warning("Local ASR transcription failed on GPU; retrying on CPU. Error: %s", exc)
                    cls._MODEL_CACHE.pop(model_name, None)
                    model = cls._get_model(model_name)
                    segments, _info = model.transcribe(temp_path, beam_size=1)
                    parts = [
                        str(segment.text).strip()
                        for segment in segments
                        if str(getattr(segment, "text", "")).strip()
                    ]
                else:
                    raise
            return " ".join(parts).strip()
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @classmethod
    def _get_model(cls, model_name: str) -> Any:
        cached = cls._MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        from faster_whisper import WhisperModel  # noqa: PLC0415

        try:
            model = WhisperModel(model_name, device="auto", compute_type="auto")
        except RuntimeError as exc:
            if "cublas" in str(exc).lower() or "cudnn" in str(exc).lower() or "cuda" in str(exc).lower():
                logger.warning("Local ASR failed to load on GPU (missing DLLs?); falling back to CPU. Error: %s", exc)
                model = WhisperModel(model_name, device="cpu", compute_type="int8")
            else:
                raise

        cls._MODEL_CACHE[model_name] = model
        return model


class KokoroTtsSynthesizer:
    _ENGINE_CACHE: dict[tuple[str, str], Any] = {}

    @classmethod
    def availability(
        cls,
        *,
        enabled: bool,
        model_path: Path | None = None,
        voices_path: Path | None = None,
    ) -> tuple[bool, str]:
        if not enabled:
            return False, "Local TTS is disabled."
        try:
            from kokoro_onnx import Kokoro  # noqa: PLC0415,F401
        except Exception as exc:  # noqa: BLE001
            return (
                False,
                "Local TTS needs kokoro-onnx. Install it with: "
                "python -m pip install -r requirements.txt"
                f" ({exc})",
            )

        resolved_model = model_path or Config.resolve_local_tts_model_path()
        resolved_voices = voices_path or Config.resolve_local_tts_voices_path()
        if resolved_model is None or not resolved_model.exists():
            return False, f"Kokoro model file is missing: {resolved_model or 'unconfigured'}"
        if resolved_voices is None or not resolved_voices.exists():
            return False, f"Kokoro voices file is missing: {resolved_voices or 'unconfigured'}"
        return True, ""

    @classmethod
    async def synthesize_text_async(
        cls,
        text: str,
    ) -> list[tuple[bytes, int]]:
        return await asyncio.to_thread(cls.synthesize_text, text)

    @classmethod
    def synthesize_text(cls, text: str) -> list[tuple[bytes, int]]:
        content = str(text or "").strip()
        if not content or not Config.LOCAL_TTS_ENABLED:
            return []

        available, message = cls.availability(
            enabled=Config.LOCAL_TTS_ENABLED,
            model_path=Config.resolve_local_tts_model_path(),
            voices_path=Config.resolve_local_tts_voices_path(),
        )
        if not available:
            raise RuntimeError(message)

        engine = cls._get_engine()
        chunks = cls._split_text(content)
        results: list[tuple[bytes, int]] = []
        for chunk in chunks:
            samples, sample_rate = engine.create(
                chunk,
                voice=str(Config.LOCAL_TTS_VOICE or "af_sarah"),
                speed=float(Config.LOCAL_TTS_SPEED or 1.0),
                lang="en-us",
            )
            results.append((cls._samples_to_pcm16(samples), int(sample_rate or 24_000)))
        return results

    @classmethod
    def _get_engine(cls) -> Any:
        from kokoro_onnx import Kokoro  # noqa: PLC0415

        model_path = Config.resolve_local_tts_model_path()
        voices_path = Config.resolve_local_tts_voices_path()
        if model_path is None or voices_path is None:
            raise RuntimeError("Kokoro model assets are not configured.")
        cache_key = (str(model_path), str(voices_path))
        cached = cls._ENGINE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        engine = Kokoro(str(model_path), str(voices_path))
        cls._ENGINE_CACHE[cache_key] = engine
        return engine

    @staticmethod
    def _split_text(text: str) -> list[str]:
        chunks = [
            chunk.strip()
            for chunk in re.split(r"(?<=[.!?;:])\s+", str(text or "").strip())
            if chunk.strip()
        ]
        return chunks or [str(text or "").strip()]

    @staticmethod
    def _samples_to_pcm16(samples: Any) -> bytes:
        array = np.asarray(samples)
        if array.dtype.kind == "f":
            array = np.clip(array, -1.0, 1.0)
            array = (array * 32767.0).astype(np.int16)
        elif array.dtype != np.int16:
            array = array.astype(np.int16)
        return array.tobytes()


def _parse_possible_json(content: str) -> Any:
    text = str(content or "").strip()
    if not text:
        return None

    fence_match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
