from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from settings import SessionSettings, pixelpilot_home

try:
    from runtime.perf import slow_operation
except Exception:  # pragma: no cover - fallback for unusual import paths
    from contextlib import contextmanager

    @contextmanager
    def slow_operation(*_args, **_kwargs):
        yield


def workspace_fingerprint(workspace_root: Path | str) -> str:
    resolved = Path(workspace_root).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).lower().encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    workspace_fingerprint: str
    kind: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "workspaceFingerprint": self.workspace_fingerprint,
            "kind": self.kind,
            "createdAt": self.created_at,
            "payload": dict(self.payload),
        }


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    workspace_fingerprint: str
    log_path: str
    last_activity_at: str
    record_count: int
    compaction_count: int
    summary_text: str = ""
    tail: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "workspaceFingerprint": self.workspace_fingerprint,
            "logPath": self.log_path,
            "lastActivityAt": self.last_activity_at,
            "recordCount": self.record_count,
            "compactionCount": self.compaction_count,
            "summaryText": self.summary_text,
            "tail": list(self.tail),
            "sources": list(self.sources),
        }


@dataclass(slots=True)
class SessionResumeMetadata:
    available: bool
    workspace_fingerprint: str
    session_id: str = ""
    last_activity_at: str = ""
    summary_text: str = ""
    resume_payload: dict[str, Any] = field(default_factory=dict)
    tail: list[dict[str, Any]] = field(default_factory=list)
    log_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "workspaceFingerprint": self.workspace_fingerprint,
            "sessionId": self.session_id,
            "lastActivityAt": self.last_activity_at,
            "summaryText": self.summary_text,
            "resumePayload": dict(self.resume_payload),
            "tail": list(self.tail),
            "logPath": self.log_path,
        }


class SessionStore:
    def __init__(
        self,
        *,
        workspace_root: Path | str,
        settings: SessionSettings,
        base_dir: Path | str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.settings = settings
        self.workspace_fingerprint = workspace_fingerprint(self.workspace_root)
        root = Path(base_dir).expanduser().resolve() if base_dir else pixelpilot_home() / "sessions"
        self.root_dir = root / self.workspace_fingerprint
        self.root_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.session_id = f"{timestamp}-{self.workspace_fingerprint[:8]}"
        self.log_path = self.root_dir / f"{self.session_id}.jsonl"
        self.latest_path = self.root_dir / "latest.json"

        self._record_count = 0
        self._compaction_count = 0
        self._records_since_compaction = 0
        self._recent_records: deque[SessionRecord] = deque(
            maxlen=max(12, int(self.settings.summary_max_lines) * 2)
        )
        self._latest_resume_payload: dict[str, Any] = {}
        self._latest_summary_text = ""
        self._latest_sources: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def append(self, kind: str, payload: dict[str, Any] | None = None) -> SessionRecord | None:
        if not self.enabled:
            return None
        clean_payload = sanitize_session_payload(dict(payload or {}))
        record = SessionRecord(
            session_id=self.session_id,
            workspace_fingerprint=self.workspace_fingerprint,
            kind=str(kind or "event").strip() or "event",
            created_at=_timestamp(),
            payload=clean_payload,
        )
        with slow_operation("session.append", threshold_ms=100, kind=record.kind):
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.as_dict(), ensure_ascii=True) + "\n")
        self._record_count += 1
        if record.kind != "compaction_summary":
            self._records_since_compaction += 1
        self._recent_records.append(record)
        self._latest_sources.add(record.kind)
        self._write_latest()
        if (
            record.kind != "compaction_summary"
            and self._records_since_compaction >= int(self.settings.max_records_before_compaction)
        ):
            self._write_compaction_summary()
        return record

    def record_user_text(self, text: str, *, source: str = "typed") -> SessionRecord | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        return self.append(
            "user_text",
            {
                "text": clean,
                "source": str(source or "typed").strip().lower() or "typed",
            },
        )

    def record_transcript(
        self,
        speaker: str,
        text: str,
        *,
        final: bool = True,
        source: str = "live",
    ) -> SessionRecord | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        return self.append(
            "transcript",
            {
                "speaker": str(speaker or "assistant").strip().lower() or "assistant",
                "text": clean,
                "final": bool(final),
                "source": str(source or "live").strip().lower() or "live",
            },
        )

    def record_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        call_id: str = "",
    ) -> SessionRecord | None:
        return self.append(
            "tool_call",
            {
                "toolName": str(tool_name or "").strip(),
                "args": dict(args or {}),
                "callId": str(call_id or "").strip(),
            },
        )

    def record_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any] | None = None,
        *,
        call_id: str = "",
    ) -> SessionRecord | None:
        return self.append(
            "tool_result",
            {
                "toolName": str(tool_name or "").strip(),
                "result": dict(result or {}),
                "callId": str(call_id or "").strip(),
            },
        )

    def record_action_update(self, payload: dict[str, Any] | None) -> SessionRecord | None:
        return self.append("action_update", dict(payload or {}))

    def record_session_event(
        self,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> SessionRecord | None:
        body = dict(payload or {})
        body["event"] = str(event or "event").strip() or "event"
        return self.append("session_event", body)

    def record_resume_metadata(self, payload: dict[str, Any] | str | None) -> SessionRecord | None:
        parsed: dict[str, Any] = {}
        if isinstance(payload, str):
            try:
                loaded = json.loads(payload)
            except json.JSONDecodeError:
                loaded = {}
            parsed = loaded if isinstance(loaded, dict) else {}
        elif isinstance(payload, dict):
            parsed = dict(payload)
        self._latest_resume_payload = parsed
        return self.append("resume_metadata", parsed)

    def latest_context(self) -> SessionResumeMetadata:
        try:
            raw = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return SessionResumeMetadata(
                available=False,
                workspace_fingerprint=self.workspace_fingerprint,
            )

        if not isinstance(raw, dict):
            return SessionResumeMetadata(
                available=False,
                workspace_fingerprint=self.workspace_fingerprint,
            )

        return SessionResumeMetadata(
            available=bool(raw.get("available", True)),
            workspace_fingerprint=str(raw.get("workspaceFingerprint") or self.workspace_fingerprint),
            session_id=str(raw.get("sessionId") or ""),
            last_activity_at=str(raw.get("lastActivityAt") or ""),
            summary_text=str(raw.get("summaryText") or ""),
            resume_payload=dict(raw.get("resumePayload") or {}),
            tail=list(raw.get("tail") or []),
            log_path=str(raw.get("logPath") or ""),
        )

    def resume_latest_context(self) -> SessionResumeMetadata:
        latest = self.latest_context()
        if latest.available:
            self.record_session_event(
                "manual_resume_requested",
                {
                    "resumeSessionId": latest.session_id,
                    "resumeLogPath": latest.log_path,
                },
            )
        return latest

    def current_summary(self) -> SessionSummary:
        return SessionSummary(
            session_id=self.session_id,
            workspace_fingerprint=self.workspace_fingerprint,
            log_path=str(self.log_path),
            last_activity_at=_timestamp(),
            record_count=self._record_count,
            compaction_count=self._compaction_count,
            summary_text=self._latest_summary_text,
            tail=[record.as_dict() for record in list(self._recent_records)[-8:]],
            sources=sorted(self._latest_sources),
        )

    def _write_compaction_summary(self) -> None:
        if not self._recent_records:
            return
        self._compaction_count += 1
        self._records_since_compaction = 0
        lines = []
        for record in list(self._recent_records)[-int(self.settings.summary_max_lines) :]:
            line = _summarize_record(record)
            if line:
                lines.append(line)
        summary = "\n".join(lines).strip()
        if len(summary) > int(self.settings.summary_max_chars):
            summary = summary[: int(self.settings.summary_max_chars) - 3].rstrip() + "..."
        self._latest_summary_text = summary
        self.append(
            "compaction_summary",
            {
                "summaryText": summary,
                "recordCount": self._record_count,
                "compactionCount": self._compaction_count,
            },
        )

    def _write_latest(self) -> None:
        payload = {
            "available": True,
            "workspaceFingerprint": self.workspace_fingerprint,
            "sessionId": self.session_id,
            "lastActivityAt": _timestamp(),
            "summaryText": self._latest_summary_text,
            "resumePayload": dict(self._latest_resume_payload),
            "tail": [record.as_dict() for record in list(self._recent_records)[-8:]],
            "logPath": str(self.log_path),
            "recordCount": self._record_count,
            "compactionCount": self._compaction_count,
            "sources": sorted(self._latest_sources),
        }
        with slow_operation("session.write_latest", threshold_ms=100):
            self.latest_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bridge[_-]?token|password|secret|cookie)",
    re.IGNORECASE,
)


def sanitize_session_payload(value: Any, *, max_string_chars: int = 8_000, max_items: int = 120) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:max_items]:
            key_text = str(key)
            if _SECRET_KEY_PATTERN.search(key_text):
                clean[key_text] = "[REDACTED]"
                continue
            clean[key_text] = sanitize_session_payload(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
            )
        if len(value) > max_items:
            clean["_truncated_items"] = len(value) - max_items
        return clean

    if isinstance(value, list):
        items = [
            sanitize_session_payload(item, max_string_chars=max_string_chars, max_items=max_items)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append({"_truncated_items": len(value) - max_items})
        return items

    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return value[: max_string_chars - 32].rstrip() + f"... [truncated {len(value) - max_string_chars + 32} chars]"

    return value


def _summarize_record(record: SessionRecord) -> str:
    payload = dict(record.payload)
    if record.kind == "user_text":
        return f"user: {str(payload.get('text') or '').strip()}"
    if record.kind == "transcript":
        speaker = str(payload.get("speaker") or "assistant").strip().lower()
        return f"{speaker}: {str(payload.get('text') or '').strip()}"
    if record.kind == "tool_call":
        return f"tool call: {str(payload.get('toolName') or '').strip()}"
    if record.kind == "tool_result":
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        status = str(result.get("status") or result.get("message") or "").strip()
        return f"tool result: {str(payload.get('toolName') or '').strip()} {status}".strip()
    if record.kind == "action_update":
        action_name = str(payload.get("name") or payload.get("action_id") or "").strip()
        action_status = str(payload.get("status") or "").strip()
        return f"action: {action_name} {action_status}".strip()
    if record.kind == "session_event":
        return f"session: {str(payload.get('event') or '').strip()}"
    if record.kind == "resume_metadata":
        goal = str(payload.get("goal") or "").strip()
        return f"resume: {goal}" if goal else "resume metadata updated"
    if record.kind == "compaction_summary":
        return ""
    return record.kind
