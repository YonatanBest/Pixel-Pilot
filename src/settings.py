from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
class RuntimeSettings:
    sources: list[Path]
    raw: dict[str, Any]
    tool_policy: PermissionRuleSet
    session: SessionSettings
    extensions: dict[str, Any]

    @classmethod
    def load(cls, *, project_root: Path | str | None = None) -> "RuntimeSettings":
        root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
        merged: dict[str, Any] = {}
        loaded_sources: list[Path] = []

        for path in discover_settings_paths(root):
            payload = _read_json_object(path)
            if payload is None:
                continue
            _deep_merge(merged, payload)
            loaded_sources.append(path)

        tool_policy_payload = dict(merged.get("toolPolicy") or {})
        session_payload = dict(merged.get("session") or {})
        extensions_payload = dict(merged.get("extensions") or {})

        return cls(
            sources=loaded_sources,
            raw=merged,
            tool_policy=PermissionRuleSet(
                allow=_string_list(tool_policy_payload.get("allow")),
                deny=_string_list(tool_policy_payload.get("deny")),
                ask=_string_list(tool_policy_payload.get("ask")),
            ),
            session=SessionSettings(
                enabled=bool(session_payload.get("enabled", True)),
                summary_max_chars=max(
                    200,
                    int(session_payload.get("summaryMaxChars") or 1_200),
                ),
                summary_max_lines=max(
                    4,
                    int(session_payload.get("summaryMaxLines") or 24),
                ),
                max_records_before_compaction=max(
                    10,
                    int(session_payload.get("maxRecordsBeforeCompaction") or 40),
                ),
            ),
            extensions=extensions_payload,
        )


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


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("Failed reading settings file: %s", path, exc_info=True)
        return None

    if not contents.strip():
        return {}

    try:
        payload = json.loads(contents)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid JSON settings file: %s", path, exc_info=True)
        return None

    if not isinstance(payload, dict):
        logger.warning("Ignoring non-object settings file: %s", path)
        return None
    return payload


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
