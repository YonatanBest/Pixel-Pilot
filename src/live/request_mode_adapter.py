from __future__ import annotations

import json
import uuid
from typing import Any


def extract_choice_message(response: Any) -> Any:
    choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
    if not choices:
        return {}
    choice = choices[0]
    return choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", {})


def extract_openai_message_content(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    return str(content or "")


def extract_openai_tool_calls(message: Any) -> list[dict[str, Any]]:
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
            calls.append({
                "id": call_id,
                "name": name,
                "args": normalize_function_call_args(arguments),
                "arguments": arguments,
            })
    return calls


def normalize_function_call_args(raw_args: Any) -> dict[str, Any]:
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


def extract_text_tool_calls(content: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = parse_json_object(content)
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

    tool_names = tool_name_set(tools)
    calls: list[dict[str, Any]] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        name, args = text_tool_call_name_args(raw_call)
        name = normalize_text_tool_name(name, args=args, tool_names=tool_names)
        if not name or name not in tool_names:
            continue
        call_id = str(raw_call.get("id") or f"text_tool_{uuid.uuid4().hex}_{index}")
        calls.append({"id": call_id, "name": name, "args": args, "arguments": json.dumps(args)})
    return calls


def extract_text_response(content: str) -> str:
    parsed = parse_json_object(content)
    if not isinstance(parsed, dict):
        return content
    if "thought" in parsed and not any(key in parsed for key in ("response", "text", "message")):
        return ""
    if looks_like_structured_tool_attempt(parsed) and not any(key in parsed for key in ("response", "text", "message")):
        return ""
    response = parsed.get("response")
    if response is None:
        response = parsed.get("text") or parsed.get("message")
    if isinstance(response, str):
        return response.strip()
    return content


def fallback_tool_calls_for_user_text(text: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_names = tool_name_set(tools)
    normalized = str(text or "").strip().lower()
    if "keyboard_press_key" not in tool_names:
        return []
    if any(phrase in normalized for phrase in (
        "pause the music", "pause music", "pause the video", "pause video",
        "pause youtube", "pause the youtube",
    )):
        return [{
            "id": f"text_tool_{uuid.uuid4().hex}_fallback_media",
            "name": "keyboard_press_key",
            "args": {"key": "playpause"},
            "arguments": json.dumps({"key": "playpause"}),
        }]
    return []


def is_text_thought_only(content: str) -> bool:
    parsed = parse_json_object(content)
    if not isinstance(parsed, dict):
        return False
    return "thought" in parsed and not any(
        key in parsed
        for key in ("tool_calls", "function_calls", "tool", "function", "name", "response", "text", "message")
    )


def looks_like_structured_tool_attempt(parsed: dict[str, Any]) -> bool:
    return any(
        key in parsed
        for key in ("tool_calls", "function_calls", "tool", "function", "tool_name", "parameters")
    ) or parsed.get("action") == "call"


def parse_json_object(content: str) -> Any:
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


def text_tool_call_name_args(raw_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = raw_call.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or "").strip()
        args = normalize_function_call_args(function.get("arguments"))
        if not args and isinstance(function.get("args"), dict):
            args = dict(function.get("args") or {})
        return name, args
    if isinstance(function, str):
        name = function.strip()
    else:
        name = str(raw_call.get("tool") or raw_call.get("tool_name") or raw_call.get("name") or "").strip()
    args = raw_call.get("args") or raw_call.get("arguments") or raw_call.get("parameters") or {}
    return name, normalize_function_call_args(args)


def normalize_text_tool_name(name: str, *, args: dict[str, Any], tool_names: set[str]) -> str:
    normalized = str(name or "").strip()
    normalized_key = normalized.lower()
    if normalized_key in {"mediacontrols", "media_controls", "media"}:
        media_action = str(args.get("action") or args.get("command") or "").strip().lower()
        media_key_by_action = {
            "pause": "playpause", "play": "playpause", "playpause": "playpause",
            "toggle": "playpause", "stop": "stop", "next": "nexttrack",
            "previous": "prevtrack", "prev": "prevtrack", "mute": "volumemute",
        }
        media_key = media_key_by_action.get(media_action)
        if media_key and "keyboard_press_key" in tool_names:
            args.clear()
            args["key"] = media_key
            return "keyboard_press_key"
    aliases = {
        "click": "mouse_click", "tap": "mouse_click", "type": "keyboard_type_text",
        "press_key": "keyboard_press_key", "key_press": "keyboard_press_key",
        "hotkey": "keyboard_key_combo", "open_app": "app_open", "open": "app_open",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "mouse_click" and "element_id" in args and "ui_element_id" not in args:
        args["ui_element_id"] = args.pop("element_id")
    return normalized if normalized in tool_names else ""


def tool_name_set(tools: list[dict[str, Any]]) -> set[str]:
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


def assistant_tool_message(message: Any, tool_calls: list[dict[str, Any]], content: str) -> dict[str, Any]:
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
