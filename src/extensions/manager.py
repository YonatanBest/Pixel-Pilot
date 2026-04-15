from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from config import Config
from tool_policy import PermissionMode, permission_mode_from_label
from runtime.perf import slow_operation

from .types import (
    ExtensionInvocationPlan,
    ExtensionToolSpec,
    McpServerConfig,
    PluginManifest,
    PluginToolDefinition,
)


EXTENSION_STDOUT_MAX_CHARS = 24_000
EXTENSION_STDERR_MAX_CHARS = 8_000
MCP_REQUEST_TIMEOUT_SECONDS = 30.0


class ExtensionManager:
    def __init__(
        self,
        *,
        settings: dict[str, Any] | None = None,
        project_root: Path | str | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path(Config.PROJECT_ROOT).resolve()
        )
        self.settings = dict(settings or {})
        self._tool_specs: dict[str, ExtensionToolSpec] = {}
        self._plugins: list[PluginManifest] = []
        self._mcp_servers: list[McpServerConfig] = []
        self._validation_errors: list[str] = []
        self.reload()

    def reload(self) -> None:
        self._validation_errors = []
        self._tool_specs = {}
        self._plugins = self._load_plugins()
        self._mcp_servers = self._load_mcp_servers()
        self._register_plugin_tools()
        self._register_mcp_tools()

    def summary(self) -> dict[str, Any]:
        return {
            "status": "warn" if self._validation_errors else "ready",
            "pluginCount": len(self._plugins),
            "mcpServerCount": len(self._mcp_servers),
            "toolCount": len(self._tool_specs),
            "pluginIds": [manifest.plugin_id for manifest in self._plugins],
            "mcpServerNames": [server.name for server in self._mcp_servers],
            "toolNames": sorted(self._tool_specs),
            "validationErrors": list(self._validation_errors),
        }

    def get_tool_spec(self, qualified_name: str) -> ExtensionToolSpec | None:
        return self._tool_specs.get(str(qualified_name or "").strip())

    def is_extension_tool(self, qualified_name: str) -> bool:
        return self.get_tool_spec(qualified_name) is not None

    def get_declarations(self, *, read_only_only: bool = False) -> list[dict[str, Any]]:
        declarations = []
        for qualified_name in sorted(self._tool_specs):
            spec = self._tool_specs[qualified_name]
            if read_only_only and spec.permission_mode != PermissionMode.READ_ONLY:
                continue
            declarations.append(
                {
                    "name": spec.qualified_name,
                    "description": spec.description,
                    "parameters": dict(spec.parameters),
                }
            )
        return declarations

    def prepare_tool_invocation(
        self,
        qualified_name: str,
        args: dict[str, Any] | None = None,
    ) -> ExtensionInvocationPlan | None:
        spec = self.get_tool_spec(qualified_name)
        if spec is None:
            return None
        payload = dict(args or {})
        hook_payload = {
            "event": "preToolUse",
            "toolName": spec.qualified_name,
            "source": spec.source,
            "input": payload,
        }
        hook_result = self._run_hooks(spec, "preToolUse", hook_payload)
        updated_input = hook_result.get("updatedInput")
        if isinstance(updated_input, dict):
            payload = dict(updated_input)
        return ExtensionInvocationPlan(
            spec=spec,
            args=payload,
            permission_decision=str(hook_result.get("permissionDecision") or "").strip().lower(),
            permission_reason=str(hook_result.get("permissionDecisionReason") or "").strip(),
            message=str(hook_result.get("message") or "").strip(),
        )

    def execute_tool(
        self,
        qualified_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = self.get_tool_spec(qualified_name)
        if spec is None:
            return {
                "tool_name": str(qualified_name or "").strip(),
                "ok": False,
                "success": False,
                "status": "failed",
                "message": f"Unknown extension tool: {qualified_name}",
                "error": "unknown_extension_tool",
            }

        payload = dict(args or {})
        try:
            if spec.source == "plugin":
                result = self._execute_plugin_tool(spec, payload)
            else:
                result = self._execute_mcp_tool(spec, payload)
            self._run_hooks(
                spec,
                "postToolUse",
                {
                    "event": "postToolUse",
                    "toolName": spec.qualified_name,
                    "source": spec.source,
                    "input": payload,
                    "result": result,
                },
            )
            return result
        except Exception as exc:
            self._run_hooks(
                spec,
                "postToolUseFailure",
                {
                    "event": "postToolUseFailure",
                    "toolName": spec.qualified_name,
                    "source": spec.source,
                    "input": payload,
                    "error": str(exc),
                },
            )
            return {
                "tool_name": spec.qualified_name,
                "ok": False,
                "success": False,
                "status": "failed",
                "message": f"Extension tool failed: {exc}",
                "error": "extension_execution_failed",
            }

    def _load_plugins(self) -> list[PluginManifest]:
        manifests: list[PluginManifest] = []
        directories = self.settings.get("pluginDirectories") or []
        if isinstance(directories, str):
            directories = [directories]
        if not isinstance(directories, list):
            self._validation_errors.append("extensions.pluginDirectories must be an array or string.")
            directories = []
        for raw in directories:
            base = _resolve_path(raw, self.project_root)
            if not base.exists():
                self._validation_errors.append(f"Plugin directory does not exist: {base}")
                continue
            candidates = [base] if _manifest_path_for(base) else [item for item in base.iterdir() if item.is_dir()]
            for candidate in candidates:
                manifest_path = _manifest_path_for(candidate)
                if manifest_path is None:
                    continue
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self._validation_errors.append(f"Invalid plugin manifest {manifest_path}: {exc}")
                    continue
                if not isinstance(payload, dict):
                    self._validation_errors.append(f"Plugin manifest must be an object: {manifest_path}")
                    continue
                hooks = _normalize_hooks(payload.get("hooks") or {})
                tools = []
                for tool_payload in list(payload.get("tools") or []):
                    if not isinstance(tool_payload, dict):
                        self._validation_errors.append(f"Skipping non-object tool in {manifest_path}")
                        continue
                    command = _normalize_command(tool_payload.get("command"))
                    if not command:
                        self._validation_errors.append(
                            f"Skipping plugin tool without a command in {manifest_path}"
                        )
                        continue
                    try:
                        tool = PluginToolDefinition.from_payload(
                            {
                                **dict(tool_payload or {}),
                                "command": command,
                                "parameters": _normalize_schema(tool_payload.get("parameters") or {}),
                            }
                        )
                    except Exception as exc:
                        self._validation_errors.append(
                            f"Skipping invalid plugin tool in {manifest_path}: {exc}"
                        )
                        continue
                    if tool.name:
                        tools.append(tool)
                    else:
                        self._validation_errors.append(
                            f"Skipping plugin tool without a name in {manifest_path}"
                        )
                if not tools:
                    self._validation_errors.append(f"Plugin manifest has no runnable tools: {manifest_path}")
                    continue
                manifests.append(
                    PluginManifest(
                        plugin_id=str(payload.get("id") or payload.get("pluginId") or candidate.name).strip(),
                        root_dir=candidate.resolve(),
                        tools=tools,
                        hooks=hooks,
                    )
                )
        return manifests

    def _load_mcp_servers(self) -> list[McpServerConfig]:
        servers: list[McpServerConfig] = []
        configured_servers = self.settings.get("mcpServers") or {}
        if not isinstance(configured_servers, dict):
            self._validation_errors.append("extensions.mcpServers must be an object.")
            configured_servers = {}
        raw_servers = dict(configured_servers)
        for name, payload in raw_servers.items():
            if not isinstance(payload, dict):
                self._validation_errors.append(f"MCP server '{name}' configuration must be an object.")
                continue
            body = dict(payload or {})
            command = _normalize_command(body.get("command"))
            if not command:
                self._validation_errors.append(f"MCP server '{name}' is missing a command.")
                continue
            raw_tool_modes = body.get("toolPermissionModes") or {}
            if not isinstance(raw_tool_modes, dict):
                self._validation_errors.append(f"MCP server '{name}' toolPermissionModes must be an object.")
                raw_tool_modes = {}
            tool_modes = {
                str(tool_name): permission_mode_from_label(mode)
                for tool_name, mode in dict(raw_tool_modes).items()
            }
            servers.append(
                McpServerConfig(
                    name=str(name).strip(),
                    command=command,
                    cwd=str(body.get("cwd") or "").strip(),
                    env={
                        str(key): str(value)
                        for key, value in dict(body.get("env") or {}).items()
                    },
                    enabled=bool(body.get("enabled", True)),
                    discovery_permission_mode=permission_mode_from_label(
                        body.get("discoveryPermissionMode")
                    ),
                    tool_permission_modes=tool_modes,
                )
            )
        return [server for server in servers if server.enabled]

    def _register_plugin_tools(self) -> None:
        for manifest in self._plugins:
            for tool in manifest.tools:
                qualified_name = f"plugin__{manifest.plugin_id}__{tool.name}"
                self._tool_specs[qualified_name] = ExtensionToolSpec(
                    qualified_name=qualified_name,
                    source="plugin",
                    description=tool.description,
                    parameters=dict(tool.parameters),
                    permission_mode=tool.permission_mode,
                    plugin_manifest=manifest,
                    plugin_tool=tool,
                )

    def _register_mcp_tools(self) -> None:
        for server in self._mcp_servers:
            try:
                tools = self._mcp_list_tools(server)
            except Exception as exc:
                self._validation_errors.append(f"MCP server '{server.name}' discovery failed: {exc}")
                continue
            for tool in tools:
                tool_name = str(tool.get("name") or "").strip()
                if not tool_name:
                    continue
                qualified_name = f"mcp__{server.name}__{tool_name}"
                self._tool_specs[qualified_name] = ExtensionToolSpec(
                    qualified_name=qualified_name,
                    source="mcp",
                    description=str(tool.get("description") or "").strip(),
                    parameters=_normalize_schema(tool.get("inputSchema") or {}),
                    permission_mode=server.tool_permission_modes.get(
                        tool_name,
                        server.discovery_permission_mode,
                    ),
                    mcp_server=server,
                    mcp_tool_name=tool_name,
                )

    def _execute_plugin_tool(
        self,
        spec: ExtensionToolSpec,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        tool = spec.plugin_tool
        manifest = spec.plugin_manifest
        if tool is None or manifest is None:
            raise RuntimeError("Plugin tool metadata is incomplete.")
        cwd = _resolve_path_within(tool.cwd, manifest.root_dir, "plugin tool cwd") if tool.cwd else manifest.root_dir
        env = dict(os.environ)
        env.update(tool.env)
        with slow_operation("extension.plugin_tool", threshold_ms=500, tool=spec.qualified_name):
            completed = subprocess.run(
                tool.command,
                input=json.dumps(
                    {
                        "toolName": spec.qualified_name,
                        "input": dict(args or {}),
                    },
                    ensure_ascii=True,
                ),
                text=True,
                capture_output=True,
                cwd=str(cwd),
                env=env,
                timeout=max(1.0, tool.timeout_ms / 1000.0),
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(_limit_text(completed.stderr, EXTENSION_STDERR_MAX_CHARS).strip() or f"exit {completed.returncode}")
        parsed = _maybe_parse_json(_limit_text(completed.stdout, EXTENSION_STDOUT_MAX_CHARS))
        if isinstance(parsed, dict):
            parsed.setdefault("tool_name", spec.qualified_name)
            parsed.setdefault("ok", bool(parsed.get("success", True)))
            parsed.setdefault("success", bool(parsed.get("ok", True)))
            parsed.setdefault("status", "succeeded" if parsed.get("success", True) else "failed")
            return parsed
        message = str(completed.stdout or "").strip()
        return {
            "tool_name": spec.qualified_name,
            "ok": True,
            "success": True,
            "status": "succeeded",
            "message": message or f"{spec.qualified_name} completed.",
            "result": parsed,
            "error": None,
        }

    def _execute_mcp_tool(
        self,
        spec: ExtensionToolSpec,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        server = spec.mcp_server
        if server is None:
            raise RuntimeError("MCP server metadata is incomplete.")
        result = _mcp_request(
            server,
            method="tools/call",
            params={
                "name": spec.mcp_tool_name,
                "arguments": dict(args or {}),
            },
        )
        success = not bool(result.get("isError"))
        return {
            "tool_name": spec.qualified_name,
            "ok": success,
            "success": success,
            "status": "succeeded" if success else "failed",
            "message": "MCP tool completed." if success else "MCP tool reported an error.",
            "result": result,
            "error": None if success else "mcp_tool_error",
        }

    def _mcp_list_tools(self, server: McpServerConfig) -> list[dict[str, Any]]:
        payload = _mcp_request(server, method="tools/list", params={})
        tools = payload.get("tools") if isinstance(payload, dict) else []
        return [dict(item) for item in list(tools or []) if isinstance(item, dict)]

    def _run_hooks(
        self,
        spec: ExtensionToolSpec,
        hook_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = spec.plugin_manifest
        if manifest is None:
            return {}
        commands = list(manifest.hooks.get(hook_name) or [])
        merged: dict[str, Any] = {}
        for command in commands:
            with slow_operation("extension.hook", threshold_ms=500, hook=hook_name):
                completed = subprocess.run(
                    command,
                    input=json.dumps(payload, ensure_ascii=True),
                    text=True,
                    capture_output=True,
                    cwd=str(manifest.root_dir),
                    timeout=10.0,
                    check=False,
                )
            if completed.returncode != 0:
                continue
            parsed = _maybe_parse_json(completed.stdout)
            if isinstance(parsed, dict):
                merged.update(parsed)
        return merged


def _resolve_path(raw: Any, root: Path) -> Path:
    path = Path(str(raw or "").strip()).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _resolve_path_within(raw: Any, root: Path, label: str) -> Path:
    base = root.resolve()
    resolved = _resolve_path(raw, base)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError(f"{label} must stay inside {base}: {resolved}") from exc
    return resolved


def _manifest_path_for(path: Path) -> Path | None:
    for name in ("plugin.json", ".pixelpilot-plugin.json"):
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def _normalize_command(raw: Any) -> list[str]:
    if isinstance(raw, str):
        clean = raw.strip()
        return [clean] if clean else []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_hooks(raw: dict[str, Any]) -> dict[str, list[list[str]]]:
    hooks: dict[str, list[list[str]]] = {}
    for hook_name, commands in dict(raw or {}).items():
        normalized: list[list[str]] = []
        source = commands if isinstance(commands, list) else [commands]
        for item in source:
            command = _normalize_command(item)
            if command:
                normalized.append(command)
        if normalized:
            hooks[str(hook_name)] = normalized
    return hooks


def _normalize_schema(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"type": "OBJECT", "properties": {}}
    payload = json.loads(json.dumps(raw))
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


def _maybe_parse_json(text: str) -> Any:
    clean = str(text or "").strip()
    if not clean:
        return {}
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return clean


def _mcp_request(server: McpServerConfig, *, method: str, params: dict[str, Any]) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(server.env)
    cwd = _resolve_path_within(server.cwd, Path(Config.PROJECT_ROOT), "MCP server cwd") if server.cwd else None
    process = subprocess.Popen(
        server.command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
    )
    try:
        with slow_operation("extension.mcp_request", threshold_ms=500, server=server.name, method=method):
            _mcp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "pixelpilot", "version": "0.1"},
                    },
                },
            )
            _mcp_read_with_timeout(process, MCP_REQUEST_TIMEOUT_SECONDS)
            _mcp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": method,
                    "params": dict(params),
                },
            )
            response = _mcp_read_with_timeout(process, MCP_REQUEST_TIMEOUT_SECONDS)
        if "error" in response:
            raise RuntimeError(str(response.get("error")))
        result = response.get("result")
        return dict(result or {}) if isinstance(result, dict) else {"result": result}
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _mcp_send(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    assert process.stdin is not None
    process.stdin.write(header + body)
    process.stdin.flush()


def _mcp_read(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    assert process.stdout is not None
    content_length = 0
    while True:
        line = process.stdout.readline()
        if not line:
            stderr = b""
            if process.stderr is not None:
                try:
                    stderr = process.stderr.read()
                except Exception:
                    stderr = b""
            raise RuntimeError(stderr.decode("utf-8", errors="replace") or "MCP server closed the pipe.")
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode("ascii", errors="ignore").partition(":")
        if key.lower().strip() == "content-length":
            content_length = int(value.strip() or "0")
    if content_length <= 0:
        raise RuntimeError("MCP server returned an empty response.")
    payload = process.stdout.read(content_length)
    parsed = json.loads(payload.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _mcp_read_with_timeout(process: subprocess.Popen[bytes], timeout_seconds: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def _reader() -> None:
        nonlocal result
        try:
            result = _mcp_read(process)
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=_reader, name="PixelPilotMcpRead", daemon=True)
    thread.start()
    thread.join(max(0.1, float(timeout_seconds or MCP_REQUEST_TIMEOUT_SECONDS)))
    if thread.is_alive():
        try:
            process.kill()
        except Exception:
            pass
        raise TimeoutError("MCP server response timed out.")
    if error:
        raise error[0]
    return result


def _limit_text(value: str, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 32)].rstrip() + f"... [truncated {len(text) - max_chars + 32} chars]"
