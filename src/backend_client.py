import json
import base64
import logging
from typing import Any, Dict, Optional
from urllib import request as urlrequest, error as urlerror
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect as ws_connect

from config import Config
from model_providers import get_request_provider_config, litellm_model_name

logger = logging.getLogger("pixelpilot.client")
_PRIVATE_CONFIG_KEYS = {"_pixelpilot_require_live_session"}
_PRIVATE_CONFIG_KEYS.add("_pixelpilot_live_session_token")
_LIVE_SESSION_TOKEN_HEADER = "X-PixelPilot-Live-Session"
_active_live_session_token: Optional[str] = None


class RateLimitError(RuntimeError):
    def __init__(
        self,
        message: str,
        remaining: Optional[int] = None,
        limit: Optional[int] = None,
        window: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
    ):
        super().__init__(message)
        self.remaining = remaining
        self.limit = limit
        self.window = window
        self.retry_after_seconds = retry_after_seconds


def _parse_rate_limit_detail(detail_payload: Any) -> dict[str, Any]:
    if isinstance(detail_payload, dict):
        return {
            "message": str(detail_payload.get("message") or "Request failed"),
            "limit": detail_payload.get("limit"),
            "remaining": detail_payload.get("remaining"),
            "window": detail_payload.get("window"),
            "retry_after_seconds": detail_payload.get("retry_after_seconds"),
        }
    return {
        "message": str(detail_payload or "Request failed"),
        "limit": None,
        "remaining": None,
        "window": None,
        "retry_after_seconds": None,
    }


def _format_rate_limit_message(detail: dict[str, Any]) -> str:
    window = str(detail.get("window") or "").strip().lower()
    retry_after_seconds = detail.get("retry_after_seconds")
    if window == "minute" and retry_after_seconds is not None:
        return f"Rate limit exceeded. Try again in {max(1, int(retry_after_seconds))}s."
    return str(detail.get("message") or "Request failed")


class DirectGeminiClient:
    def __init__(self, api_key: Optional[str] = None):
        from google import genai

        self._api_key = api_key or Config.GEMINI_API_KEY
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=self._api_key)

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from google.genai import types

        processed_contents = []
        for c in contents:
            role = c.get("role", "user")
            parts_data = c.get("parts", [])
            real_parts = []
            if isinstance(parts_data, list):
                for p in parts_data:
                    real_parts.append(self._process_part(p, types))
            elif isinstance(parts_data, dict):
                real_parts.append(self._process_part(parts_data, types))
            else:
                real_parts.append(types.Part(text=str(parts_data)))
            processed_contents.append(types.Content(role=role, parts=real_parts))

        config_data = dict(config) if config else {}
        for key in _PRIVATE_CONFIG_KEYS:
            config_data.pop(key, None)

        tools_config = config_data.pop("tools", None)
        real_tools = None
        if tools_config:
            real_tools = []
            for t in tools_config:
                if "google_search" in t:
                    real_tools.append(types.Tool(google_search=types.GoogleSearch()))
                if "code_execution" in t:
                    real_tools.append(
                        types.Tool(code_execution=types.ToolCodeExecution())
                    )

        if "response_json_schema" in config_data:
            schema = config_data.pop("response_json_schema")
            config_data["response_schema"] = self._sanitize_schema(schema)

        thinking_conf = config_data.pop("thinking_config", None)
        real_thinking_config = None
        if thinking_conf:
            real_thinking_config = types.ThinkingConfig(**thinking_conf)

        conf = types.GenerateContentConfig(
            **config_data, tools=real_tools, thinking_config=real_thinking_config
        )

        try:
            response = self._client.models.generate_content(
                model=model, contents=processed_contents, config=conf
            )
            return {"text": response.text}
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}") from e
            raise RuntimeError(f"Gemini API error: {e}") from e

    @staticmethod
    def _process_part(part: dict, types_module) -> Any:
        if "text" in part:
            return types_module.Part(text=part["text"])
        if "data" in part and "mime_type" in part:
            return types_module.Part.from_bytes(
                data=base64.b64decode(part["data"]), mime_type=part["mime_type"]
            )
        return types_module.Part(text=str(part))

    @staticmethod
    def _sanitize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return schema
        new_schema = schema.copy()
        new_schema.pop("additionalProperties", None)
        for key, value in new_schema.items():
            if isinstance(value, dict):
                new_schema[key] = DirectGeminiClient._sanitize_schema(value)
            elif isinstance(value, list):
                new_schema[key] = [
                    (
                        DirectGeminiClient._sanitize_schema(item)
                        if isinstance(item, dict)
                        else item
                    )
                    for item in value
                ]
        return new_schema


class DirectModelClient:
    def __init__(self):
        self.provider = get_request_provider_config()
        try:
            import litellm  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            if self.provider.provider_id == "gemini":
                logger.warning("LiteLLM unavailable; falling back to direct Gemini client: %s", exc)
                self._fallback = DirectGeminiClient()
                self._litellm = None
                return
            raise RuntimeError("LiteLLM is required for non-Gemini direct providers.") from exc
        self._fallback = None
        self._litellm = litellm

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._fallback is not None:
            return self._fallback.generate_content(model=model, contents=contents, config=config)

        assert self._litellm is not None
        provider = self.provider
        selected_model = litellm_model_name(provider.provider_id, model or provider.model)
        messages = [_content_to_message(item) for item in contents or []]
        config_data = dict(config or {})
        for key in _PRIVATE_CONFIG_KEYS:
            config_data.pop(key, None)

        kwargs: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
        }
        if provider.api_key:
            kwargs["api_key"] = provider.api_key
        if provider.base_url:
            kwargs["api_base"] = provider.base_url.rstrip("/")

        tools = _normalize_openai_tools(config_data.pop("tools", None))
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if "response_json_schema" in config_data:
            config_data.pop("response_json_schema", None)
            config_data.setdefault("response_format", {"type": "json_object"})
        config_data.pop("thinking_config", None)
        kwargs.update(config_data)

        try:
            response = self._litellm.completion(**kwargs)
            text = _extract_litellm_text(response)
            return {"text": text}
        except Exception as e:  # noqa: BLE001
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}") from e
            raise RuntimeError(f"{provider.display_name} API error: {e}") from e


def _content_to_message(item: dict[str, Any]) -> dict[str, Any]:
    role = str(item.get("role") or "user").strip() or "user"
    parts = item.get("parts", [])
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        return {"role": role, "content": str(parts)}

    content: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        if "text" in part:
            text_parts.append(str(part.get("text") or ""))
            continue
        if "data" in part and "mime_type" in part:
            mime_type = str(part.get("mime_type") or "image/png")
            data = str(part.get("data") or "")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{data}"},
                }
            )
    text = "\n".join(chunk for chunk in text_parts if chunk)
    if content:
        if text:
            content.insert(0, {"type": "text", "text": text})
        return {"role": role, "content": content}
    return {"role": role, "content": text}


def _normalize_openai_tools(tools_config: Any) -> list[dict[str, Any]]:
    if not tools_config:
        return []
    normalized = []
    for item in tools_config if isinstance(tools_config, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function" and isinstance(item.get("function"), dict):
            normalized.append(item)
    return normalized


def _extract_litellm_text(response: Any) -> str:
    try:
        choices = response.get("choices") if isinstance(response, dict) else response.choices
        if choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else choices[0].message
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            return str(content or "")
    except Exception:
        pass
    return str(response or "")


class BackendClient:
    def __init__(self, base_url: Optional[str] = None):
        from auth_manager import get_auth_manager

        self._get_auth = get_auth_manager
        self.base_url = (base_url or Config.BACKEND_URL).rstrip("/")

    def _http_url(self, path: str) -> str:
        parts = urlsplit(self.base_url)
        if not parts.scheme or not parts.netloc:
            raise RuntimeError(f"Invalid BACKEND_URL: {self.base_url}")
        base_path = parts.path.rstrip("/")
        target_path = f"{base_path}{path}" if base_path else path
        return urlunsplit((parts.scheme, parts.netloc, target_path, "", ""))

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        *,
        extra_headers: Optional[dict[str, str]] = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth.access_token}",
        }
        if extra_headers:
            request_headers.update(extra_headers)
        req = urlrequest.Request(
            self._http_url(path),
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urlerror.HTTPError as exc:
            detail_payload: Any = "Request failed"
            try:
                body = exc.read().decode("utf-8")
                if body:
                    parsed = json.loads(body)
                    detail_payload = parsed.get("detail", detail_payload)
            except Exception:
                pass

            if exc.code == 401:
                auth.logout()
                raise RuntimeError("Session expired. Please log in again.") from exc
            if exc.code == 429:
                detail = _parse_rate_limit_detail(detail_payload)
                raise RateLimitError(
                    _format_rate_limit_message(detail),
                    remaining=detail.get("remaining"),
                    limit=detail.get("limit"),
                    window=detail.get("window"),
                    retry_after_seconds=detail.get("retry_after_seconds"),
                ) from exc
            raise RuntimeError(str(detail_payload)) from exc
        except urlerror.URLError as exc:
            raise RuntimeError("Backend unavailable. Is it running?") from exc

    def _ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        if not parts.scheme or not parts.netloc:
            raise RuntimeError(f"Invalid BACKEND_URL: {self.base_url}")

        scheme = "wss" if parts.scheme == "https" else "ws"
        base_path = parts.path.rstrip("/")
        ws_path = f"{base_path}/ws/generate" if base_path else "/ws/generate"
        return urlunsplit((scheme, parts.netloc, ws_path, "", ""))

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")

        config_payload = dict(config or {})
        if bool(config_payload.get("_pixelpilot_require_live_session")):
            token = get_backend_live_session_token()
            if token:
                config_payload["_pixelpilot_live_session_token"] = token

        ws_url = self._ws_url()
        try:
            with ws_connect(
                ws_url, open_timeout=10, close_timeout=5, max_size=20_000_000
            ) as ws:
                ws.send(json.dumps({"type": "auth", "token": auth.access_token}))
                auth_resp = json.loads(ws.recv())
                if auth_resp.get("type") == "error":
                    code = int(auth_resp.get("code", 500))
                    detail = auth_resp.get("detail", "Authentication failed")
                    if code == 401:
                        auth.logout()
                        raise RuntimeError("Session expired. Please log in again.")
                    raise RuntimeError(str(detail))
                if auth_resp.get("type") != "auth_ok":
                    raise RuntimeError("Backend authentication handshake failed")

                ws.send(
                    json.dumps(
                        {
                            "type": "generate",
                            "request": {
                                "model": model,
                                "contents": contents,
                                "config": config_payload,
                            },
                        }
                    )
                )
                response = json.loads(ws.recv())

                if response.get("type") == "error":
                    code = int(response.get("code", 500))
                    detail_payload = response.get("detail", "Request failed")
                    rate_limit_detail = _parse_rate_limit_detail(detail_payload)
                    detail = str(rate_limit_detail["message"])
                    limit = rate_limit_detail["limit"]
                    remaining = rate_limit_detail["remaining"]
                    window = rate_limit_detail["window"]
                    retry_after_seconds = rate_limit_detail["retry_after_seconds"]

                    if code == 401:
                        auth.logout()
                        raise RuntimeError("Session expired. Please log in again.")
                    if code == 429:
                        raise RateLimitError(
                            _format_rate_limit_message(rate_limit_detail),
                            remaining=remaining,
                            limit=limit,
                            window=window,
                            retry_after_seconds=retry_after_seconds,
                        )
                    raise RuntimeError(detail)

                if response.get("type") != "generate_result":
                    raise RuntimeError("Unexpected backend response type")

                result = response.get("data") or {}
                if not isinstance(result, dict):
                    return {"text": str(result)}
                return result
        except RateLimitError:
            raise
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError("Backend unavailable. Is it running?") from e

    def easyocr_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        lang: str = "en",
    ) -> Dict[str, Any]:
        payload = {
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": str(mime_type or "image/png"),
            "lang": str(lang or "en"),
        }
        extra_headers = {}
        token = get_backend_live_session_token()
        if token:
            extra_headers[_LIVE_SESSION_TOKEN_HEADER] = token
        response = self._request_json(
            "POST",
            "/v1/vision/easyocr",
            payload,
            extra_headers=extra_headers,
            timeout=60.0,
        )
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected OCR backend response type")
        return response

    def local_eye_elements(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        lang: str = "en",
    ) -> Dict[str, Any]:
        payload = {
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": str(mime_type or "image/png"),
            "lang": str(lang or "en"),
        }
        extra_headers = {}
        token = get_backend_live_session_token()
        if token:
            extra_headers[_LIVE_SESSION_TOKEN_HEADER] = token
        response = self._request_json(
            "POST",
            "/v1/vision/local-eye",
            payload,
            extra_headers=extra_headers,
            timeout=60.0,
        )
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected hosted eye backend response type")
        return response


_client_instance: Optional[Any] = None
_backend_proxy_client_instance: Optional[BackendClient] = None


def set_backend_live_session_token(token: Optional[str]) -> None:
    global _active_live_session_token
    clean = str(token or "").strip()
    _active_live_session_token = clean or None


def clear_backend_live_session_token() -> None:
    set_backend_live_session_token(None)


def get_backend_live_session_token() -> Optional[str]:
    return _active_live_session_token


def get_client():
    global _client_instance
    if _client_instance is None:
        if Config.USE_DIRECT_API:
            provider = get_request_provider_config()
            logger.info(
                "Using direct %s API (provider=%s model=%s)",
                provider.display_name,
                provider.provider_id,
                provider.model,
            )
            _client_instance = DirectModelClient()
        else:
            logger.info("Using backend proxy for model API")
            _client_instance = BackendClient()
    return _client_instance


def reset_client() -> None:
    global _client_instance
    _client_instance = None


def get_backend_proxy_client() -> BackendClient:
    global _backend_proxy_client_instance
    if _backend_proxy_client_instance is None:
        _backend_proxy_client_instance = BackendClient()
    return _backend_proxy_client_instance
