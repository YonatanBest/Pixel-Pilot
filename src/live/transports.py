from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import websockets

from backend_client import RateLimitError
from config import Config

logger = logging.getLogger("pixelpilot.live.transport")

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


def _normalize_provider_response(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    session_resumption_update = getattr(response, "session_resumption_update", None)
    if session_resumption_update:
        handle = getattr(session_resumption_update, "new_handle", None) or getattr(
            session_resumption_update, "resumption_handle", None
        )
        if handle:
            payload["session_resumption_update"] = {"handle": str(handle)}

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
        if bool(getattr(server_content, "turn_complete", False)):
            server_payload["turn_complete"] = True

        go_away = getattr(server_content, "go_away", None)
        if go_away:
            message = str(getattr(go_away, "message", "") or "")
            if message:
                server_payload["go_away"] = {"message": message}

        if server_payload:
            payload["server_content"] = server_payload

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
            return "Gemini Live requires a local Gemini API key."
        if genai is None or types is None:
            return f"Gemini Live dependencies unavailable: {_IMPORT_ERROR}"
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
        async for response in self._session.receive():
            yield _normalize_provider_response(response)

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

    @classmethod
    def is_supported(cls) -> bool:
        from auth_manager import get_auth_manager

        auth = get_auth_manager()
        return bool(auth.access_token and Config.BACKEND_URL)

    @classmethod
    def unavailable_reason(cls) -> str:
        from auth_manager import get_auth_manager

        if not Config.BACKEND_URL:
            return "Gemini Live backend URL is not configured."
        if not get_auth_manager().access_token:
            return "Sign in to use Gemini Live through the backend."
        return ""

    def _ws_url(self) -> str:
        return _build_backend_ws_url(self._base_url, "/ws/live")

    async def connect(self, *, model: str, config: dict[str, Any]) -> None:
        del model
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")

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
            json.dumps({"type": "live_start", "request": {"config": dict(config or {})}})
        )
        while True:
            response = json.loads(await self._ws.recv())
            if response.get("type") == "live_started":
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
