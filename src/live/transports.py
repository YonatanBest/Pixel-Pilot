from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import websockets

from backend_client import (
    RateLimitError,
    clear_backend_live_session_token,
    set_backend_live_session_token,
)
from config import Config
from model_providers import get_live_provider_config, litellm_model_name
from .tool_schema import openai_realtime_tools_from_declarations, openai_tools_from_declarations

logger = logging.getLogger("pixelpilot.live.transport")

REQUEST_MODE_SYSTEM_APPENDIX = """
Request-mode tool contract:
- You are controlling the real PixelPilot desktop through the provided tools, not a simulation or virtual desktop.
- Do not say you lack system access when tools are available; inspect with tools and act from their results.
- Do not output private reasoning, "thought" JSON, "tool_calls" JSON, or "response" JSON as the final visible answer.
- To act, call the provided tools. If the provider cannot emit native tool calls, output JSON only in this shape:
  {"tool_calls":[{"function":"tool_name","args":{...}}]}
- If one read-only lookup finds nothing, broaden your inspection before giving up.
- For generic media requests such as pausing music, if the active player is ambiguous, use keyboard_press_key
  with key "playpause" rather than claiming you cannot access media controls.
- Keep final user-facing replies as plain natural language, not JSON.
"""

try:
    from google import genai
    from google.genai import types
except Exception as exc:  # noqa: BLE001
    genai = None
    types = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""


def _parse_rate_limit_detail(detail_payload: Any) -> dict[str, Any]:
    if isinstance(detail_payload, dict):
        return {
            "message": str(detail_payload.get("message") or "Request failed"),
            "limit": detail_payload.get("limit"),
            "remaining": detail_payload.get("remaining"),
            "window": detail_payload.get("window"),
            "retry_after_seconds": detail_payload.get("retry_after_seconds"),
            "scope": detail_payload.get("scope"),
        }
    return {
        "message": str(detail_payload or "Request failed"),
        "limit": None,
        "remaining": None,
        "window": None,
        "retry_after_seconds": None,
        "scope": None,
    }


def _format_rate_limit_message(detail: dict[str, Any]) -> str:
    window = str(detail.get("window") or "").strip().lower()
    retry_after_seconds = detail.get("retry_after_seconds")
    if window == "minute" and retry_after_seconds is not None:
        return f"Rate limit exceeded. Try again in {max(1, int(retry_after_seconds))}s."
    return str(detail.get("message") or "Request failed")


def _build_backend_ws_url(base_url: str, path: str) -> str:
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.netloc:
        raise RuntimeError(f"Invalid BACKEND_URL: {base_url}")

    scheme = "wss" if parts.scheme == "https" else "ws"
    base_path = parts.path.rstrip("/")
    ws_path = f"{base_path}{path}" if base_path else path
    return urlunsplit((scheme, parts.netloc, ws_path, "", ""))


def _normalize_function_call_args(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _is_clean_live_close_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    message = str(exc or "").strip().lower()
    name = exc.__class__.__name__.lower()
    if status == 1000:
        return True
    if "connectionclosedok" in name:
        return True
    if "sent 1000 (ok)" in message or "received 1000 (ok)" in message:
        return True
    if message in {"1000 none", "1000 none."}:
        return True
    return False


def _normalize_provider_response(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    session_resumption_update = getattr(response, "session_resumption_update", None)
    if session_resumption_update:
        handle = getattr(session_resumption_update, "new_handle", None) or getattr(
            session_resumption_update, "resumption_handle", None
        )
        if handle:
            update_payload = {"handle": str(handle)}
            resumable = getattr(session_resumption_update, "resumable", None)
            if resumable is not None:
                update_payload["resumable"] = bool(resumable)
            payload["session_resumption_update"] = update_payload

    tool_call = getattr(response, "tool_call", None)
    function_calls = []
    if tool_call:
        for function_call in getattr(tool_call, "function_calls", None) or []:
            function_calls.append(
                {
                    "id": getattr(function_call, "id", None),
                    "name": str(getattr(function_call, "name", "") or ""),
                    "args": _normalize_function_call_args(
                        getattr(function_call, "args", None)
                    )
                    or _normalize_function_call_args(
                        getattr(function_call, "arguments", None)
                    ),
                }
            )
    if function_calls:
        payload["tool_call"] = {"function_calls": function_calls}

    server_content = getattr(response, "server_content", None)
    if server_content:
        server_payload: dict[str, Any] = {}
        input_transcription = getattr(server_content, "input_transcription", None)
        if input_transcription:
            text = str(getattr(input_transcription, "text", "") or "")
            if text:
                server_payload["input_transcription"] = {"text": text}

        output_transcription = getattr(server_content, "output_transcription", None)
        if output_transcription:
            text = str(getattr(output_transcription, "text", "") or "")
            if text:
                server_payload["output_transcription"] = {"text": text}

        model_turn = getattr(server_content, "model_turn", None)
        parts_payload = []
        if model_turn:
            for part in getattr(model_turn, "parts", None) or []:
                item: dict[str, Any] = {}
                text = str(getattr(part, "text", "") or "")
                if text:
                    item["text"] = text
                if bool(getattr(part, "thought", False)):
                    item["thought"] = True
                inline_data = getattr(part, "inline_data", None)
                data = getattr(inline_data, "data", None) if inline_data is not None else None
                mime_type = (
                    str(getattr(inline_data, "mime_type", "") or "")
                    if inline_data is not None
                    else ""
                )
                if data is not None:
                    item["inline_data"] = {
                        "data": bytes(data),
                        "mime_type": mime_type,
                    }
                if item:
                    parts_payload.append(item)
        if parts_payload:
            server_payload["model_turn"] = {"parts": parts_payload}

        if bool(getattr(server_content, "interrupted", False)):
            server_payload["interrupted"] = True
        if bool(getattr(server_content, "generation_complete", False)):
            server_payload["generation_complete"] = True
        if bool(getattr(server_content, "turn_complete", False)):
            server_payload["turn_complete"] = True

        if server_payload:
            payload["server_content"] = server_payload

    go_away = getattr(response, "go_away", None)
    if go_away:
        message = str(getattr(go_away, "message", "") or "")
        time_left = getattr(go_away, "time_left", None)
        go_away_payload: dict[str, Any] = {}
        if message:
            go_away_payload["message"] = message
        if time_left is not None:
            go_away_payload["time_left"] = str(time_left)
        if go_away_payload:
            payload["go_away"] = go_away_payload

    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is not None:
        usage_payload: dict[str, Any] = {}
        total_token_count = getattr(usage_metadata, "total_token_count", None)
        if total_token_count is not None:
            usage_payload["total_token_count"] = int(total_token_count)
        if usage_payload:
            payload["usage_metadata"] = usage_payload

    return payload


def _decode_backend_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event or {})
    server_content = payload.get("server_content")
    if isinstance(server_content, dict):
        model_turn = server_content.get("model_turn")
        if isinstance(model_turn, dict):
            decoded_parts = []
            for part in model_turn.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                item = dict(part)
                inline_data = item.get("inline_data")
                if isinstance(inline_data, dict) and inline_data.get("data") is not None:
                    try:
                        item["inline_data"] = {
                            "data": base64.b64decode(str(inline_data.get("data") or "")),
                            "mime_type": str(inline_data.get("mime_type") or ""),
                        }
                    except Exception:
                        item["inline_data"] = {
                            "data": b"",
                            "mime_type": str(inline_data.get("mime_type") or ""),
                        }
                decoded_parts.append(item)
            model_turn["parts"] = decoded_parts
    return payload


class BaseLiveTransport:
    should_rotate_sessions = True

    @classmethod
    def is_supported(cls) -> bool:
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        return ""

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        raise NotImplementedError

    async def send_text(self, text: str) -> None:
        raise NotImplementedError

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        raise NotImplementedError

    async def send_video(self, data: bytes, mime_type: str) -> None:
        raise NotImplementedError

    async def send_audio_stream_end(self) -> None:
        raise NotImplementedError

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def close(self, *, close_client: bool = False) -> None:
        raise NotImplementedError


class DirectGeminiLiveTransport(BaseLiveTransport):
    def __init__(self, *, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or Config.GEMINI_API_KEY
        self._client = None
        self._session = None
        self._session_cm = None

    @classmethod
    def is_supported(cls) -> bool:
        return bool(genai is not None and types is not None and Config.GEMINI_API_KEY)

    @classmethod
    def unavailable_reason(cls) -> str:
        if not Config.GEMINI_API_KEY:
            return "PixelPilot Live requires a local Gemini API key."
        if genai is None or types is None:
            return f"PixelPilot Live dependencies unavailable: {_IMPORT_ERROR}"
        return ""

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        if genai is None or types is None:
            raise RuntimeError(self.unavailable_reason())
        if not self._api_key:
            raise RuntimeError(self.unavailable_reason())
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        self._session_cm = self._client.aio.live.connect(model=model, config=config)
        self._session = await self._session_cm.__aenter__()

    async def send_text(self, text: str) -> None:
        if self._session is None:
            raise RuntimeError("Live session is not connected.")
        await self._session.send_realtime_input(text=str(text or ""))

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        if self._session is None:
            raise RuntimeError("Live session is not connected.")
        blob = types.Blob(data=data, mime_type=mime_type)
        await self._session.send_realtime_input(audio=blob)

    async def send_video(self, data: bytes, mime_type: str) -> None:
        if self._session is None:
            raise RuntimeError("Live session is not connected.")
        blob = types.Blob(data=data, mime_type=mime_type)
        await self._session.send_realtime_input(video=blob)

    async def send_audio_stream_end(self) -> None:
        if self._session is None:
            raise RuntimeError("Live session is not connected.")
        await self._session.send_realtime_input(audio_stream_end=True)

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        if self._session is None or not responses:
            return
        provider_responses = []
        for item in responses:
            provider_responses.append(
                types.FunctionResponse(
                    id=item.get("id"),
                    name=str(item.get("name") or ""),
                    response=dict(item.get("response") or {}),
                )
            )
        await self._session.send_tool_response(function_responses=provider_responses)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._session is None:
            return
        try:
            async for response in self._session.receive():
                yield _normalize_provider_response(response)
        except Exception as exc:  # noqa: BLE001
            if _is_clean_live_close_error(exc):
                logger.info("Direct Gemini Live stream closed cleanly (status=1000).")
                return
            raise

    async def close(self, *, close_client: bool = False) -> None:
        del close_client
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("Failed to close direct live session", exc_info=True)
        self._session_cm = None
        self._session = None
        if self._client is not None:
            try:
                await self._client.aio.aclose()
            except Exception:
                logger.debug("Failed to close direct Gemini client", exc_info=True)
            self._client = None


class BackendGeminiLiveTransport(BaseLiveTransport):
    should_rotate_sessions = False

    def __init__(self, *, base_url: Optional[str] = None) -> None:
        from auth_manager import get_auth_manager

        self._get_auth = get_auth_manager
        self._base_url = (base_url or Config.BACKEND_URL).rstrip("/")
        self._ws = None
        self._connected = False
        self._live_session_token: Optional[str] = None

    @classmethod
    def is_supported(cls) -> bool:
        from auth_manager import get_auth_manager

        auth = get_auth_manager()
        return bool(auth.access_token and Config.BACKEND_URL)

    @classmethod
    def unavailable_reason(cls) -> str:
        from auth_manager import get_auth_manager

        if not Config.BACKEND_URL:
            return "PixelPilot Live backend URL is not configured."
        if not get_auth_manager().access_token:
            return "Sign in to use PixelPilot Live through the backend."
        return ""

    def _ws_url(self) -> str:
        return _build_backend_ws_url(self._base_url, "/ws/live")

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")
        self._live_session_token = None
        clear_backend_live_session_token()

        self._ws = await websockets.connect(
            self._ws_url(),
            open_timeout=10,
            close_timeout=5,
            max_size=20_000_000,
        )
        await self._ws.send(json.dumps({"type": "auth", "token": auth.access_token}))
        auth_response = json.loads(await self._ws.recv())
        self._handle_backend_message(auth_response, during_connect=True)

        await self._ws.send(
            json.dumps(
                {
                    "type": "live_start",
                    "request": {
                        "model": str(model or "").strip(),
                        "config": dict(config or {}),
                    },
                }
            )
        )
        while True:
            response = json.loads(await self._ws.recv())
            if response.get("type") == "live_started":
                self._live_session_token = str(
                    response.get("live_session_token") or ""
                ).strip() or None
                if not self._live_session_token:
                    raise RuntimeError("Backend live session did not return a session token.")
                set_backend_live_session_token(self._live_session_token)
                self._connected = True
                return
            self._handle_backend_message(response, during_connect=True)

    async def send_text(self, text: str) -> None:
        await self._send_live_input({"text": str(text or "")})

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        await self._send_live_input(
            {
                "audio": {
                    "data": base64.b64encode(data).decode("ascii"),
                    "mime_type": mime_type,
                }
            }
        )

    async def send_video(self, data: bytes, mime_type: str) -> None:
        await self._send_live_input(
            {
                "video": {
                    "data": base64.b64encode(data).decode("ascii"),
                    "mime_type": mime_type,
                }
            }
        )

    async def send_audio_stream_end(self) -> None:
        await self._send_live_input({"audio_stream_end": True})

    async def _send_live_input(self, payload: dict[str, Any]) -> None:
        if self._ws is None or not self._connected:
            raise RuntimeError("Gemini Live backend session is not connected.")
        await self._ws.send(json.dumps({"type": "live_input", **payload}))

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        if self._ws is None or not self._connected or not responses:
            return
        await self._ws.send(
            json.dumps({"type": "live_tool_response", "responses": responses})
        )

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            return
        while True:
            try:
                raw = await self._ws.recv()
            except asyncio.CancelledError:
                raise
            if raw is None:
                return
            message = json.loads(raw)
            msg_type = str(message.get("type") or "")
            if msg_type == "live_event":
                yield _decode_backend_event(dict(message.get("event") or {}))
                continue
            if msg_type == "pong":
                continue
            if msg_type == "live_closed":
                self._connected = False
                self._live_session_token = None
                clear_backend_live_session_token()
                return
            self._handle_backend_message(message, during_connect=False)

    async def close(self, *, close_client: bool = False) -> None:
        del close_client
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("Failed to close backend live websocket", exc_info=True)
        self._ws = None
        self._connected = False
        self._live_session_token = None
        clear_backend_live_session_token()

    def _handle_backend_message(
        self,
        message: dict[str, Any],
        *,
        during_connect: bool,
    ) -> None:
        msg_type = str(message.get("type") or "")
        if msg_type == "auth_ok":
            return
        if msg_type == "error":
            code = int(message.get("code", 500))
            detail_payload = message.get("detail", "Request failed")
            detail = _parse_rate_limit_detail(detail_payload)
            auth = self._get_auth()
            if code == 401:
                auth.logout()
                raise RuntimeError("Session expired. Please log in again.")
            if code == 429:
                raise RateLimitError(
                    _format_rate_limit_message(detail),
                    remaining=detail.get("remaining"),
                    limit=detail.get("limit"),
                    window=detail.get("window"),
                    retry_after_seconds=detail.get("retry_after_seconds"),
                )
            raise RuntimeError(str(detail.get("message") or detail_payload or "Request failed"))
        if msg_type == "live_started":
            return
        if during_connect:
            raise RuntimeError("Backend live authentication handshake failed")


class OpenAIRealtimeTransport(BaseLiveTransport):
    should_rotate_sessions = False

    def __init__(self, *, api_key: Optional[str] = None, base_url: str = "wss://api.openai.com/v1/realtime") -> None:
        self._provider = get_live_provider_config(provider_id="openai")
        self._api_key = api_key or self._provider.api_key or Config.OPENAI_API_KEY
        self._base_url = str(base_url or "wss://api.openai.com/v1/realtime").rstrip("/")
        self._ws = None
        self._connected = False
        self._pending_audio = False
        self._tool_name_by_call_id: dict[str, str] = {}

    @classmethod
    def is_supported(cls) -> bool:
        return bool(Config.OPENAI_API_KEY)

    @classmethod
    def unavailable_reason(cls) -> str:
        if not Config.OPENAI_API_KEY:
            return "OpenAI Realtime requires OPENAI_API_KEY."
        return ""

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        if not self._api_key:
            raise RuntimeError(self.unavailable_reason())
        url = f"{self._base_url}?model={str(model or self._provider.model).strip()}"
        self._ws = await websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
            open_timeout=10,
            close_timeout=5,
            max_size=20_000_000,
        )
        tools = openai_realtime_tools_from_declarations(
            ((config.get("tools") or [{}])[0] or {}).get("function_declarations") or []
        )
        session_payload = {
            "type": "session.update",
            "session": {
                "instructions": str(config.get("system_instruction") or ""),
                "modalities": ["text", "audio"],
                "voice": str(Config.LIVE_VOICE_NAME or "alloy"),
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "tools": tools,
                "tool_choice": "auto" if tools else "none",
            },
        }
        await self._ws.send(json.dumps(session_payload))
        self._connected = True

    async def send_text(self, text: str) -> None:
        if self._ws is None or not self._connected:
            raise RuntimeError("OpenAI Realtime session is not connected.")
        item_id = f"msg_{uuid.uuid4().hex}"
        await self._ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": str(text or "")}],
                    },
                }
            )
        )
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        del mime_type
        if self._ws is None or not self._connected:
            raise RuntimeError("OpenAI Realtime session is not connected.")
        self._pending_audio = True
        await self._ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(data).decode("ascii"),
                }
            )
        )

    async def send_video(self, data: bytes, mime_type: str) -> None:
        del data, mime_type
        raise RuntimeError("OpenAI Realtime video input is not supported by this transport.")

    async def send_audio_stream_end(self) -> None:
        if self._ws is None or not self._connected or not self._pending_audio:
            return
        self._pending_audio = False
        await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        if self._ws is None or not self._connected:
            return
        for item in responses or []:
            call_id = str(item.get("id") or "").strip()
            if not call_id:
                continue
            await self._ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(item.get("response") or {}),
                        },
                    }
                )
            )
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            return
        while True:
            raw = await self._ws.recv()
            event = json.loads(raw)
            normalized = self._normalize_event(event)
            if normalized:
                yield normalized

    async def close(self, *, close_client: bool = False) -> None:
        del close_client
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("Failed to close OpenAI Realtime websocket", exc_info=True)
        self._ws = None
        self._connected = False
        self._pending_audio = False

    def _normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "")
        if event_type in {"response.audio_transcript.delta", "response.text.delta", "response.output_text.delta"}:
            text = str(event.get("delta") or "")
            return {"server_content": {"output_transcription": {"text": text}}} if text else {}
        if event_type == "conversation.item.input_audio_transcription.completed":
            text = str(event.get("transcript") or "")
            return {"server_content": {"input_transcription": {"text": text}}} if text else {}
        if event_type == "response.audio.delta":
            data = base64.b64decode(str(event.get("delta") or ""))
            return {"server_content": {"model_turn": {"parts": [{"inline_data": {"data": data, "mime_type": "audio/pcm;rate=24000"}}]}}}
        if event_type == "response.output_item.done":
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or "").strip()
                name = str(item.get("name") or "").strip()
                args = _normalize_function_call_args(item.get("arguments"))
                if call_id and name:
                    self._tool_name_by_call_id[call_id] = name
                    return {"tool_call": {"function_calls": [{"id": call_id, "name": name, "args": args}]}}
        if event_type in {"response.done", "response.audio_transcript.done", "response.text.done", "response.output_text.done"}:
            return {"server_content": {"generation_complete": True, "turn_complete": True}}
        if event_type == "error":
            error = event.get("error") or {}
            message = str(error.get("message") if isinstance(error, dict) else error)
            raise RuntimeError(message or "OpenAI Realtime session failed.")
        return {}


class LiteLLMRequestLiveTransport(BaseLiveTransport):
    should_rotate_sessions = False

    def __init__(self) -> None:
        self._provider = get_live_provider_config()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._model = ""
        self._closed = False
        self._pending_tool_future: Optional[asyncio.Future[list[dict[str, Any]]]] = None

    @classmethod
    def is_supported(cls) -> bool:
        provider = get_live_provider_config()
        return bool(provider.is_local or provider.api_key)

    @classmethod
    def unavailable_reason(cls) -> str:
        provider = get_live_provider_config()
        if provider.is_local:
            return ""
        if not provider.api_key:
            return f"{provider.display_name} request mode requires {provider.api_key_env}."
        return ""

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        self._provider = get_live_provider_config()
        self._model = litellm_model_name(self._provider.provider_id, model or self._provider.model)
        instructions = str(config.get("system_instruction") or "").strip()
        instructions = f"{instructions}\n\n{REQUEST_MODE_SYSTEM_APPENDIX}".strip()
        self._messages = [{"role": "system", "content": instructions}] if instructions else []
        declarations = ((config.get("tools") or [{}])[0] or {}).get("function_declarations") or []
        self._tools = openai_tools_from_declarations(declarations)
        self._closed = False

    async def send_text(self, text: str) -> None:
        payload = str(text or "").strip()
        if not payload:
            return
        asyncio.create_task(self._run_turn(payload))

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        del data, mime_type
        await self._events.put(
            {
                "server_content": {
                    "model_turn": {
                        "parts": [
                            {
                                "text": (
                                    f"Voice is not available for {self._provider.display_name} with the current "
                                    "PixelPilot audio transport. Please type your instruction."
                                )
                            }
                        ]
                    },
                    "generation_complete": True,
                    "turn_complete": True,
                }
            }
        )

    async def send_video(self, data: bytes, mime_type: str) -> None:
        del data, mime_type

    async def send_audio_stream_end(self) -> None:
        return

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        if self._pending_tool_future is not None and not self._pending_tool_future.done():
            self._pending_tool_future.set_result(list(responses or []))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            event = await self._events.get()
            if event.get("type") == "__closed__":
                return
            yield event

    async def close(self, *, close_client: bool = False) -> None:
        del close_client
        self._closed = True
        if self._pending_tool_future is not None and not self._pending_tool_future.done():
            self._pending_tool_future.cancel()
        await self._events.put({"type": "__closed__"})

    async def _run_turn(self, text: str) -> None:
        try:
            import litellm  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            await self._events.put({"server_content": {"model_turn": {"parts": [{"text": f"LiteLLM is unavailable: {exc}"}]}, "generation_complete": True, "turn_complete": True}})
            return

        self._messages.append({"role": "user", "content": text})
        thought_retry_count = 0
        while not self._closed:
            try:
                kwargs: dict[str, Any] = {"model": self._model, "messages": self._messages}
                if self._provider.api_key:
                    kwargs["api_key"] = self._provider.api_key
                if self._provider.base_url:
                    kwargs["api_base"] = self._provider.base_url.rstrip("/")
                if self._tools:
                    kwargs["tools"] = self._tools
                    kwargs["tool_choice"] = "auto"
                response = await litellm.acompletion(**kwargs)
                message = _extract_choice_message(response)
                tool_calls = _extract_openai_tool_calls(message)
                content = _extract_openai_message_content(message)
                if not tool_calls and content:
                    tool_calls = _extract_text_tool_calls(content, self._tools)
                    if tool_calls:
                        content = ""
                    else:
                        if _is_text_thought_only(content):
                            tool_calls = _fallback_tool_calls_for_user_text(text, self._tools)
                            if tool_calls:
                                content = ""
                            thought_retry_count += 1
                            if not tool_calls and thought_retry_count <= 2:
                                self._messages.append(
                                    {
                                        "role": "assistant",
                                        "content": content,
                                    }
                                )
                                self._messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "That was private reasoning JSON. Do not expose thoughts. "
                                            "Use the available tools to continue, or answer the user in plain language. "
                                            "Do not claim this is a simulation or that you lack access to the desktop."
                                        ),
                                    }
                                )
                                continue
                            content = _extract_text_response(content) if not tool_calls else ""
                            if not content and not tool_calls:
                                content = "I could not complete that with the current local model response."
                        else:
                            content = _extract_text_response(content)
                            if not content:
                                tool_calls = _fallback_tool_calls_for_user_text(text, self._tools)
                                if tool_calls:
                                    content = ""
            except Exception as exc:  # noqa: BLE001
                await self._events.put(
                    {
                        "server_content": {
                            "model_turn": {"parts": [{"text": f"Model request failed: {exc}"}]},
                            "generation_complete": True,
                            "turn_complete": True,
                        }
                    }
                )
                return
            if tool_calls:
                self._messages.append(_assistant_tool_message(message, tool_calls, content))
                function_calls = [
                    {"id": item["id"], "name": item["name"], "args": item["args"]}
                    for item in tool_calls
                ]
                loop = asyncio.get_running_loop()
                self._pending_tool_future = loop.create_future()
                await self._events.put({"tool_call": {"function_calls": function_calls}})
                try:
                    responses = await asyncio.wait_for(self._pending_tool_future, timeout=60.0)
                except Exception as exc:  # noqa: BLE001
                    await self._events.put(
                        {
                            "server_content": {
                                "model_turn": {"parts": [{"text": f"Tool response failed: {exc}"}]},
                                "generation_complete": True,
                                "turn_complete": True,
                            }
                        }
                    )
                    return
                finally:
                    self._pending_tool_future = None
                for item in responses or []:
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(item.get("id") or ""),
                            "name": str(item.get("name") or ""),
                            "content": json.dumps(item.get("response") or {}),
                        }
                    )
                continue

            if content:
                self._messages.append({"role": "assistant", "content": content})
            await self._events.put(
                {
                    "server_content": {
                        "model_turn": {"parts": [{"text": content}] if content else []},
                        "generation_complete": True,
                        "turn_complete": True,
                    }
                }
            )
            return


def _extract_choice_message(response: Any) -> Any:
    choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
    if not choices:
        return {}
    choice = choices[0]
    return choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", {})


def _extract_openai_message_content(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    return str(content or "")


def _extract_openai_tool_calls(message: Any) -> list[dict[str, Any]]:
    raw_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    calls: list[dict[str, Any]] = []
    for call in raw_calls or []:
        raw_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
        call_id = str(raw_id or "")
        function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        raw_name = function.get("name") if isinstance(function, dict) else getattr(function, "name", "")
        name = str(raw_name or "")
        arguments = function.get("arguments") if isinstance(function, dict) else getattr(function, "arguments", None)
        if call_id and name:
            calls.append({"id": call_id, "name": name, "args": _normalize_function_call_args(arguments), "arguments": arguments})
    return calls


def _extract_text_tool_calls(content: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        return []
    raw_calls = parsed.get("tool_calls") or parsed.get("function_calls")
    if not raw_calls and (
        parsed.get("tool")
        or parsed.get("function")
        or parsed.get("name")
        or parsed.get("tool_name")
        or parsed.get("action") == "call"
    ):
        raw_calls = [parsed]
    if not isinstance(raw_calls, list):
        return []

    tool_names = _tool_names(tools)
    calls: list[dict[str, Any]] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        name, args = _text_tool_call_name_args(raw_call)
        name = _normalize_text_tool_name(name, args=args, tool_names=tool_names)
        if not name or name not in tool_names:
            continue
        call_id = str(raw_call.get("id") or f"text_tool_{uuid.uuid4().hex}_{index}")
        calls.append(
            {
                "id": call_id,
                "name": name,
                "args": args,
                "arguments": json.dumps(args),
            }
        )
    return calls


def _extract_text_response(content: str) -> str:
    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        return content
    if "thought" in parsed and not any(key in parsed for key in ("response", "text", "message")):
        return ""
    if _looks_like_structured_tool_attempt(parsed) and not any(key in parsed for key in ("response", "text", "message")):
        return ""
    response = parsed.get("response")
    if response is None:
        response = parsed.get("text") or parsed.get("message")
    if isinstance(response, str):
        return response.strip()
    return content


def _fallback_tool_calls_for_user_text(text: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_names = _tool_names(tools)
    normalized = str(text or "").strip().lower()
    if "keyboard_press_key" not in tool_names:
        return []
    if any(phrase in normalized for phrase in ("pause the music", "pause music", "pause the video", "pause video", "pause youtube", "pause the youtube")):
        return [
            {
                "id": f"text_tool_{uuid.uuid4().hex}_fallback_media",
                "name": "keyboard_press_key",
                "args": {"key": "playpause"},
                "arguments": json.dumps({"key": "playpause"}),
            }
        ]
    return []


def _looks_like_structured_tool_attempt(parsed: dict[str, Any]) -> bool:
    return any(
        key in parsed
        for key in (
            "tool_calls",
            "function_calls",
            "tool",
            "function",
            "tool_name",
            "parameters",
        )
    ) or parsed.get("action") == "call"


def _is_text_thought_only(content: str) -> bool:
    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        return False
    return "thought" in parsed and not any(
        key in parsed
        for key in (
            "tool_calls",
            "function_calls",
            "tool",
            "function",
            "name",
            "response",
            "text",
            "message",
        )
    )


def _parse_json_object(content: str) -> Any:
    payload = str(content or "").strip()
    if not payload:
        return None
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        payload = "\n".join(lines).strip()
    try:
        return json.loads(payload)
    except Exception:
        return None


def _text_tool_call_name_args(raw_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = raw_call.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or "").strip()
        args = _normalize_function_call_args(function.get("arguments"))
        if not args and isinstance(function.get("args"), dict):
            args = dict(function.get("args") or {})
        return name, args
    if isinstance(function, str):
        name = function.strip()
    else:
        name = str(raw_call.get("tool") or raw_call.get("tool_name") or raw_call.get("name") or "").strip()
    args = raw_call.get("args") or raw_call.get("arguments") or raw_call.get("parameters") or {}
    return name, _normalize_function_call_args(args)


def _normalize_text_tool_name(name: str, *, args: dict[str, Any], tool_names: set[str]) -> str:
    normalized = str(name or "").strip()
    normalized_key = normalized.lower()
    if normalized_key in {"mediacontrols", "media_controls", "media"}:
        media_action = str(args.get("action") or args.get("command") or "").strip().lower()
        media_key_by_action = {
            "pause": "playpause",
            "play": "playpause",
            "playpause": "playpause",
            "toggle": "playpause",
            "stop": "stop",
            "next": "nexttrack",
            "previous": "prevtrack",
            "prev": "prevtrack",
            "mute": "volumemute",
        }
        media_key = media_key_by_action.get(media_action)
        if media_key and "keyboard_press_key" in tool_names:
            args.clear()
            args["key"] = media_key
            return "keyboard_press_key"
    aliases = {
        "click": "mouse_click",
        "tap": "mouse_click",
        "type": "keyboard_type_text",
        "press_key": "keyboard_press_key",
        "key_press": "keyboard_press_key",
        "hotkey": "keyboard_key_combo",
        "open_app": "app_open",
        "open": "app_open",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "mouse_click" and "element_id" in args and "ui_element_id" not in args:
        args["ui_element_id"] = args.pop("element_id")
    return normalized if normalized in tool_names else ""


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
            if name:
                names.add(name)
    return names


def _assistant_tool_message(message: Any, tool_calls: list[dict[str, Any]], content: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": item["id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item.get("arguments") or json.dumps(item["args"])},
            }
            for item in tool_calls
        ],
    }
