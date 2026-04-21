import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.provider_catalog import (
    api_key_for,
    base_url_for,
    litellm_model_name,
    normalize_provider_id,
)

load_dotenv()

_PRIVATE_CONFIG_KEYS = {"_pixelpilot_require_live_session", "_pixelpilot_live_session_token"}


class GenerationRequest(BaseModel):
    model: str
    contents: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = None


def _provider() -> str:
    raw = os.getenv("PIXELPILOT_MODEL_PROVIDER") or os.getenv("AI_PROVIDER") or "gemini"
    return normalize_provider_id(raw)


def _content_to_message(item: dict[str, Any]) -> dict[str, Any]:
    role = str(item.get("role") or "user").strip() or "user"
    parts = item.get("parts", [])
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        return {"role": role, "content": str(parts)}

    text_parts: list[str] = []
    content: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        if "text" in part:
            text_parts.append(str(part.get("text") or ""))
        elif "data" in part and "mime_type" in part:
            mime_type = str(part.get("mime_type") or "image/png")
            data = str(part.get("data") or "")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}})

    text = "\n".join(chunk for chunk in text_parts if chunk)
    if content:
        if text:
            content.insert(0, {"type": "text", "text": text})
        return {"role": role, "content": content}
    return {"role": role, "content": text}


def _extract_text(response: Any) -> str:
    try:
        choices = response.get("choices") if isinstance(response, dict) else response.choices
        if choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else choices[0].message
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            return str(content or "")
    except Exception:
        pass
    return str(response or "")


async def generate_content(request: GenerationRequest):
    import litellm

    provider = _provider()
    config_data = dict(request.config or {})
    for key in _PRIVATE_CONFIG_KEYS:
        config_data.pop(key, None)

    tools_config = config_data.pop("tools", None)
    if tools_config:
        config_data["tools"] = [
            item
            for item in tools_config
            if isinstance(item, dict) and item.get("type") == "function"
        ]
        if config_data["tools"]:
            config_data["tool_choice"] = "auto"
        else:
            config_data.pop("tools", None)

    if "response_json_schema" in config_data:
        config_data.pop("response_json_schema", None)
        config_data.setdefault("response_format", {"type": "json_object"})
    config_data.pop("thinking_config", None)

    kwargs: dict[str, Any] = {
        "model": litellm_model_name(provider, request.model),
        "messages": [_content_to_message(item) for item in request.contents],
        **config_data,
    }
    api_key = api_key_for(provider, os.getenv)
    base_url = base_url_for(provider, os.getenv)
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url.rstrip("/")

    response = await litellm.acompletion(**kwargs)
    return {"text": _extract_text(response)}
