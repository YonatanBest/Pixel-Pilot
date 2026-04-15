from __future__ import annotations

from typing import Any


def normalize_json_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key == "type" and isinstance(item, str):
            normalized[key] = item.lower()
            continue
        normalized[key] = normalize_json_schema(item)
    if normalized.get("type") == "object":
        normalized.setdefault("properties", {})
    return normalized


def openai_tools_from_declarations(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for declaration in declarations or []:
        if not isinstance(declaration, dict):
            continue
        name = str(declaration.get("name") or "").strip()
        if not name:
            continue
        parameters = declaration.get("parameters")
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(declaration.get("description") or ""),
                    "parameters": normalize_json_schema(parameters or {"type": "object", "properties": {}}),
                },
            }
        )
    return tools


def openai_realtime_tools_from_declarations(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in openai_tools_from_declarations(declarations):
        function = dict(tool.get("function") or {})
        if function.get("name"):
            tools.append(
                {
                    "type": "function",
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                }
            )
    return tools
