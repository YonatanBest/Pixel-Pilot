from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("pixelpilot.settings")


@dataclass(slots=True)
class PermissionRuleSet:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionSettings:
    enabled: bool = True
    summary_max_chars: int = 1_200
    summary_max_lines: int = 24
    max_records_before_compaction: int = 40


@dataclass(slots=True)
class SettingsValidationIssue:
    source: str
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "field": self.field,
            "message": self.message,
        }


@dataclass(slots=True)
class RuntimeSettings:
    sources: list[Path]
    raw: dict[str, Any]
    tool_policy: PermissionRuleSet
    session: SessionSettings
    extensions: dict[str, Any]
    validation_errors: list[SettingsValidationIssue] = field(default_factory=list)

    @classmethod
    def load(cls, *, project_root: Path | str | None = None) -> "RuntimeSettings":
        root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
        merged: dict[str, Any] = {}
        loaded_sources: list[Path] = []
        validation_errors: list[SettingsValidationIssue] = []

        for path in discover_settings_paths(root):
            payload, issues = _read_json_object(path)
            validation_errors.extend(issues)
            if payload is None:
                continue
            _deep_merge(merged, payload)
            loaded_sources.append(path)

        try:
            model = _RuntimeSettingsModel.model_validate(merged)
        except ValidationError as exc:
            validation_errors.extend(_validation_issues(exc, source="merged_settings"))
            model = _RuntimeSettingsModel.model_validate(_coerce_runtime_settings_payload(merged))

        tool_policy, rule_issues = _validated_permission_rule_set(model.tool_policy)
        validation_errors.extend(rule_issues)

        return cls(
            sources=loaded_sources,
            raw=merged,
            tool_policy=tool_policy,
            session=SessionSettings(
                enabled=bool(model.session.enabled),
                summary_max_chars=max(200, int(model.session.summary_max_chars)),
                summary_max_lines=max(4, int(model.session.summary_max_lines)),
                max_records_before_compaction=max(10, int(model.session.max_records_before_compaction)),
            ),
            extensions=dict(model.extensions or {}),
            validation_errors=validation_errors,
        )

    def validation_error_dicts(self) -> list[dict[str, str]]:
        return [item.as_dict() for item in self.validation_errors]


class _PermissionRuleSetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)


class _SessionSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    summary_max_chars: int = Field(default=1_200, alias="summaryMaxChars", ge=200)
    summary_max_lines: int = Field(default=24, alias="summaryMaxLines", ge=4)
    max_records_before_compaction: int = Field(
        default=40,
        alias="maxRecordsBeforeCompaction",
        ge=10,
    )


class _RuntimeSettingsModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    tool_policy: _PermissionRuleSetModel = Field(default_factory=_PermissionRuleSetModel, alias="toolPolicy")
    session: _SessionSettingsModel = Field(default_factory=_SessionSettingsModel)
    extensions: dict[str, Any] = Field(default_factory=dict)


def pixelpilot_home() -> Path:
    configured = str(os.environ.get("PIXELPILOT_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home().resolve() / ".pixelpilot"


def discover_settings_paths(project_root: Path | str | None = None) -> list[Path]:
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    user_home = pixelpilot_home()
    return [
        user_home / "settings.json",
        root / ".pixelpilot" / "settings.json",
        root / ".pixelpilot" / "settings.local.json",
    ]


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, list[SettingsValidationIssue]]:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, []
    except Exception as exc:
        logger.warning("Failed reading settings file: %s", path, exc_info=True)
        return None, [
            SettingsValidationIssue(
                source=str(path),
                field="",
                message=f"Failed reading settings file: {exc}",
            )
        ]

    if not contents.strip():
        return {}, []

    try:
        payload = json.loads(contents)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring invalid JSON settings file: %s", path, exc_info=True)
        return None, [
            SettingsValidationIssue(
                source=str(path),
                field="",
                message=f"Invalid JSON: {exc.msg}",
            )
        ]

    if not isinstance(payload, dict):
        logger.warning("Ignoring non-object settings file: %s", path)
        return None, [
            SettingsValidationIssue(
                source=str(path),
                field="",
                message="Settings file must contain a JSON object.",
            )
        ]
    return payload, []


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
            continue
        target[key] = value


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _coerce_runtime_settings_payload(raw: dict[str, Any]) -> dict[str, Any]:
    raw_tool_policy = raw.get("toolPolicy") or {}
    raw_session = raw.get("session") or {}
    tool_policy_payload = dict(raw_tool_policy) if isinstance(raw_tool_policy, dict) else {}
    session_payload = dict(raw_session) if isinstance(raw_session, dict) else {}
    extensions_payload = raw.get("extensions")
    if not isinstance(extensions_payload, dict):
        extensions_payload = {}

    return {
        **dict(raw or {}),
        "toolPolicy": {
            "allow": _string_list(tool_policy_payload.get("allow")),
            "deny": _string_list(tool_policy_payload.get("deny")),
            "ask": _string_list(tool_policy_payload.get("ask")),
        },
        "session": {
            "enabled": bool(session_payload.get("enabled", True)),
            "summaryMaxChars": _safe_int(session_payload.get("summaryMaxChars"), 1_200),
            "summaryMaxLines": _safe_int(session_payload.get("summaryMaxLines"), 24),
            "maxRecordsBeforeCompaction": _safe_int(
                session_payload.get("maxRecordsBeforeCompaction"),
                40,
            ),
        },
        "extensions": dict(extensions_payload),
    }


def _validated_permission_rule_set(
    model: _PermissionRuleSetModel,
) -> tuple[PermissionRuleSet, list[SettingsValidationIssue]]:
    issues: list[SettingsValidationIssue] = []
    cleaned: dict[str, list[str]] = {"allow": [], "deny": [], "ask": []}
    for source in ("allow", "deny", "ask"):
        for rule in list(getattr(model, source) or []):
            clean = str(rule or "").strip()
            if not clean:
                continue
            error = _validate_permission_rule_syntax(clean)
            if error:
                issues.append(
                    SettingsValidationIssue(
                        source="toolPolicy",
                        field=source,
                        message=f"Ignoring invalid permission rule '{clean}': {error}",
                    )
                )
                continue
            cleaned[source].append(clean)
    return PermissionRuleSet(**cleaned), issues


def _validate_permission_rule_syntax(raw: str) -> str:
    clean = str(raw or "").strip()
    if not clean:
        return "rule must be non-empty"
    if "(" not in clean:
        return ""
    if clean.count("(") != 1 or not clean.endswith(")"):
        return "expected Tool(subject) syntax"
    open_index = clean.find("(")
    if not clean[:open_index].strip():
        return "missing tool name"
    if not clean[open_index + 1 : -1].strip():
        return "missing subject; use * to match any subject"
    return ""


def _validation_issues(exc: ValidationError, *, source: str) -> list[SettingsValidationIssue]:
    issues: list[SettingsValidationIssue] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", []))
        issues.append(
            SettingsValidationIssue(
                source=source,
                field=loc,
                message=str(error.get("msg") or "Invalid settings value."),
            )
        )
    return issues


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)
