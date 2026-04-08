from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tool_policy import PermissionMode, permission_mode_from_label


@dataclass(slots=True)
class PluginToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    command: list[str]
    permission_mode: PermissionMode = PermissionMode.READ_ONLY
    timeout_ms: int = 30_000
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PluginToolDefinition":
        return cls(
            name=str(payload.get("name") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            parameters=dict(payload.get("parameters") or {}),
            command=[str(item).strip() for item in list(payload.get("command") or []) if str(item).strip()],
            permission_mode=permission_mode_from_label(payload.get("permissionMode")),
            timeout_ms=max(1_000, int(payload.get("timeoutMs") or 30_000)),
            cwd=str(payload.get("cwd") or "").strip(),
            env={
                str(key): str(value)
                for key, value in dict(payload.get("env") or {}).items()
            },
        )


@dataclass(slots=True)
class PluginManifest:
    plugin_id: str
    root_dir: Path
    tools: list[PluginToolDefinition] = field(default_factory=list)
    hooks: dict[str, list[list[str]]] = field(default_factory=dict)


@dataclass(slots=True)
class McpServerConfig:
    name: str
    command: list[str]
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    discovery_permission_mode: PermissionMode = PermissionMode.READ_ONLY
    tool_permission_modes: dict[str, PermissionMode] = field(default_factory=dict)


@dataclass(slots=True)
class ExtensionToolSpec:
    qualified_name: str
    source: str
    description: str
    parameters: dict[str, Any]
    permission_mode: PermissionMode
    plugin_manifest: PluginManifest | None = None
    plugin_tool: PluginToolDefinition | None = None
    mcp_server: McpServerConfig | None = None
    mcp_tool_name: str = ""


@dataclass(slots=True)
class ExtensionInvocationPlan:
    spec: ExtensionToolSpec
    args: dict[str, Any]
    permission_decision: str = ""
    permission_reason: str = ""
    message: str = ""
