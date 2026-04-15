import base64
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_PRIVATE_CONFIG_KEYS = {"_pixelpilot_require_live_session", "_pixelpilot_live_session_token"}


class GenerationRequest(BaseModel):
    model: str
    contents: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = None


def _provider() -> str:
    return str(os.getenv("PIXELPILOT_MODEL_PROVIDER") or os.getenv("AI_PROVIDER") or "gemini").strip().lower()


def _api_key_for(provider: str) -> str:
    env_name = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
    }.get(provider, "")
    return str(os.getenv(env_name, "")).strip() if env_name else ""


def _base_url_for(provider: str) -> str:
    if provider == "ollama":
        return str(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).strip()
    if provider == "openai_compatible":
        return str(os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")).strip()
    if provider == "vercel_ai_gateway":
        return str(os.getenv("VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1")).strip()
    return ""


def _litellm_model_name(provider: str, model: str) -> str:
    clean = str(model or "").strip()
    if "/" in clean:
        return clean
    if provider == "gemini":
        return f"gemini/{clean}"
    if provider in {"anthropic", "claude"}:
        return f"anthropic/{clean}"
    if provider == "xai":
        return f"xai/{clean}"
    if provider == "openrouter":
        return f"openrouter/{clean}"
    if provider == "ollama":
        return f"ollama/{clean}"
    return clean


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
        "model": _litellm_model_name(provider, request.model),
        "messages": [_content_to_message(item) for item in request.contents],
        **config_data,
    }
    api_key = _api_key_for(provider)
    base_url = _base_url_for(provider)
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url.rstrip("/")

    response = await litellm.acompletion(**kwargs)
    return {"text": _extract_text(response)}
