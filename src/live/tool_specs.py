from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tool_policy import PermissionMode


DEFAULT_MAX_RESULT_CHARS = 24_000


class ToolValidationError(ValueError):
    """Raised when a tool call does not match its declared input contract."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    permission_mode: PermissionMode
    read_only: bool = False
    mutating: bool = False
    concurrency_safe: bool = False
    timeout_ms: int = 30_000
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS

    def declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }

    def validate_args(self, args: dict[str, Any] | None) -> dict[str, Any]:
        if args is None:
            payload: dict[str, Any] = {}
        elif isinstance(args, dict):
            payload = dict(args)
        else:
            raise ToolValidationError(f"{self.name} expects an object argument payload.")

        _validate_object_schema(self.parameters, payload, path=self.name)
        return payload


def build_tool_specs(
    declarations: list[dict[str, Any]],
    *,
    required_modes: dict[str, PermissionMode],
    read_only_tools: set[str],
    mutating_tools: set[str],
    concurrency_safe_tools: set[str] | None = None,
    default_max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}
    concurrency_safe = set(concurrency_safe_tools or set())
    for declaration in declarations:
        name = str(declaration.get("name") or "").strip()
        if not name:
            continue
        specs[name] = ToolSpec(
            name=name,
            description=str(declaration.get("description") or "").strip(),
            parameters=_normalize_schema(dict(declaration.get("parameters") or {})),
            permission_mode=required_modes.get(name, PermissionMode.DANGER_FULL_ACCESS),
            read_only=name in read_only_tools,
            mutating=name in mutating_tools,
            concurrency_safe=name in concurrency_safe,
            max_result_chars=int(default_max_result_chars),
        )
    return specs


def normalize_tool_result(
    tool_name: str,
    result: Any,
    *,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> dict[str, Any]:
    clean_name = str(tool_name or "").strip() or "unknown_tool"
    payload = dict(result) if isinstance(result, dict) else {"result": result}

    status = str(payload.get("status") or "").strip()
    if "ok" in payload or "success" in payload:
        ok = bool(payload.get("ok", payload.get("success", True)))
    else:
        ok = status.lower() not in {"failed", "cancelled", "error"}
    status = str(payload.get("status") or ("succeeded" if ok else "failed")).strip()
    error = payload.get("error")
    code = str(payload.get("code") or error or "").strip()

    payload["toolName"] = str(payload.get("toolName") or payload.get("tool_name") or clean_name)
    payload["tool_name"] = str(payload.get("tool_name") or payload.get("toolName") or clean_name)
    payload["ok"] = ok
    payload["success"] = ok
    payload["status"] = status
    payload["error"] = None if ok and error in {"", None} else error
    payload["code"] = code
    payload.setdefault("result", None)

    return _truncate_payload_result(payload, max(1_000, int(max_result_chars or DEFAULT_MAX_RESULT_CHARS)))


def _truncate_payload_result(payload: dict[str, Any], max_result_chars: int) -> dict[str, Any]:
    try:
        encoded = json.dumps(payload.get("result"), ensure_ascii=True, sort_keys=True)
    except Exception:
        encoded = str(payload.get("result"))

    if len(encoded) <= max_result_chars:
        return payload

    preview = encoded[: max(0, max_result_chars - 120)].rstrip()
    payload["result"] = {
        "truncated": True,
        "preview": preview,
        "omittedChars": max(0, len(encoded) - len(preview)),
    }
    payload["truncated"] = True
    return payload


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(schema or {}))
    _normalize_schema_node(payload)
    if "type" not in payload:
        payload["type"] = "OBJECT"
    if payload.get("type") == "OBJECT" and "properties" not in payload:
        payload["properties"] = {}
    return payload


def _normalize_schema_node(node: Any) -> None:
    if isinstance(node, dict):
        value_type = node.get("type")
        if isinstance(value_type, str):
            node["type"] = value_type.upper()
        for value in node.values():
            _normalize_schema_node(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_schema_node(item)


def _validate_object_schema(schema: dict[str, Any], payload: dict[str, Any], *, path: str) -> None:
    schema_type = str(schema.get("type") or "OBJECT").upper()
    if schema_type != "OBJECT":
        _validate_schema_value(schema, payload, path=path)
        return

    required = {str(item) for item in list(schema.get("required") or [])}
    missing = sorted(item for item in required if item not in payload)
    if missing:
        raise ToolValidationError(f"{path} is missing required field(s): {', '.join(missing)}.")

    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key, value in payload.items():
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            _validate_schema_value(child_schema, value, path=f"{path}.{key}")


def _validate_schema_value(schema: dict[str, Any], value: Any, *, path: str) -> None:
    if value is None:
        return

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        raise ToolValidationError(f"{path} must be one of: {', '.join(map(str, enum_values))}.")

    expected_type = str(schema.get("type") or "").upper()
    if not expected_type:
        return

    if expected_type == "STRING":
        if not isinstance(value, str):
            raise ToolValidationError(f"{path} must be a string.")
        return

    if expected_type == "INTEGER":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolValidationError(f"{path} must be an integer.")
        return

    if expected_type == "NUMBER":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolValidationError(f"{path} must be a number.")
        return

    if expected_type == "BOOLEAN":
        if not isinstance(value, bool):
            raise ToolValidationError(f"{path} must be a boolean.")
        return

    if expected_type == "ARRAY":
        if not isinstance(value, list):
            raise ToolValidationError(f"{path} must be an array.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_value(item_schema, item, path=f"{path}[{index}]")
        return

    if expected_type == "OBJECT":
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path} must be an object.")
        _validate_object_schema(schema, value, path=path)
