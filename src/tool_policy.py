from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from config import OperationMode
from settings import PermissionRuleSet


class PermissionMode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


_MODE_RANK = {
    PermissionMode.READ_ONLY: 0,
    PermissionMode.WORKSPACE_WRITE: 1,
    PermissionMode.DANGER_FULL_ACCESS: 2,
}


@dataclass(slots=True)
class PermissionDecision:
    decision: str
    reason: str = ""
    matched_rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def requires_prompt(self) -> bool:
        return self.decision == "prompt"

    @property
    def denied(self) -> bool:
        return self.decision == "deny"


@dataclass(slots=True)
class ToolPolicyContext:
    tool_name: str
    tool_input: dict[str, Any]
    required_mode: PermissionMode
    active_mode: PermissionMode
    operation_mode: str
    workspace: str


@dataclass(slots=True)
class HookOverride:
    decision: str = ""
    reason: str = ""


@dataclass(slots=True)
class _PermissionRule:
    raw: str
    tool_name: str
    matcher: str
    subject: str

    @classmethod
    def parse(cls, raw: str) -> "_PermissionRule":
        clean = str(raw or "").strip()
        if "(" not in clean or not clean.endswith(")"):
            return cls(raw=clean, tool_name=clean, matcher="any", subject="")

        open_index = clean.find("(")
        tool_name = clean[:open_index].strip()
        subject = clean[open_index + 1 : -1].strip()
        if not tool_name:
            return cls(raw=clean, tool_name=clean, matcher="any", subject="")
        if not subject or subject == "*":
            return cls(raw=clean, tool_name=tool_name, matcher="any", subject="")
        if subject.endswith(":*"):
            return cls(raw=clean, tool_name=tool_name, matcher="prefix", subject=subject[:-2])
        return cls(raw=clean, tool_name=tool_name, matcher="exact", subject=subject)

    def matches(self, tool_name: str, subject: str) -> bool:
        if self.tool_name != tool_name:
            return False
        if self.matcher == "any":
            return True
        if self.matcher == "exact":
            return subject == self.subject
        if self.matcher == "prefix":
            return subject.startswith(self.subject)
        return False


class ToolPolicyEvaluator:
    def __init__(
        self,
        *,
        rule_set: PermissionRuleSet,
        required_modes: dict[str, PermissionMode],
        mutating_tools: set[str],
    ) -> None:
        self._allow_rules = [_PermissionRule.parse(item) for item in rule_set.allow]
        self._deny_rules = [_PermissionRule.parse(item) for item in rule_set.deny]
        self._ask_rules = [_PermissionRule.parse(item) for item in rule_set.ask]
        self._required_modes = dict(required_modes)
        self._mutating_tools = set(mutating_tools)

    def required_mode_for(self, tool_name: str) -> PermissionMode:
        return self._required_modes.get(tool_name, PermissionMode.DANGER_FULL_ACCESS)

    def active_mode_for_operation(self, operation_mode: object) -> PermissionMode:
        mode_key = _operation_mode_key(operation_mode)
        if mode_key == OperationMode.GUIDE.value:
            return PermissionMode.READ_ONLY
        if mode_key == OperationMode.SAFE.value:
            return PermissionMode.WORKSPACE_WRITE
        return PermissionMode.DANGER_FULL_ACCESS

    def authorize(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None,
        operation_mode: object,
        workspace: str = "user",
        required_mode: Optional[PermissionMode] = None,
        hook_override: Optional[HookOverride] = None,
    ) -> tuple[PermissionDecision, ToolPolicyContext]:
        clean_tool_name = str(tool_name or "").strip()
        payload = dict(tool_input or {})
        required_mode = required_mode or self.required_mode_for(clean_tool_name)
        active_mode = self.active_mode_for_operation(operation_mode)
        context = ToolPolicyContext(
            tool_name=clean_tool_name,
            tool_input=payload,
            required_mode=required_mode,
            active_mode=active_mode,
            operation_mode=_operation_mode_key(operation_mode),
            workspace=str(workspace or "user").strip().lower() or "user",
        )
        subject = extract_permission_subject(payload)

        matched_deny = self._find_match(self._deny_rules, clean_tool_name, subject)
        if matched_deny is not None:
            return (
                PermissionDecision(
                    decision="deny",
                    reason=f"Denied by rule '{matched_deny.raw}'.",
                    matched_rule=matched_deny.raw,
                ),
                context,
            )

        if hook_override and hook_override.decision == "deny":
            return (
                PermissionDecision(
                    decision="deny",
                    reason=hook_override.reason or f"Tool '{clean_tool_name}' denied by hook.",
                ),
                context,
            )

        if context.operation_mode == OperationMode.GUIDE.value and required_mode != PermissionMode.READ_ONLY:
            return (
                PermissionDecision(
                    decision="deny",
                    reason="Guidance mode is read-only.",
                ),
                context,
            )

        matched_ask = self._find_match(self._ask_rules, clean_tool_name, subject)
        matched_allow = self._find_match(self._allow_rules, clean_tool_name, subject)

        if hook_override and hook_override.decision == "ask":
            return (
                PermissionDecision(
                    decision="prompt",
                    reason=hook_override.reason or f"Tool '{clean_tool_name}' requires approval.",
                ),
                context,
            )

        if matched_ask is not None:
            return (
                PermissionDecision(
                    decision="prompt",
                    reason=f"Approval required by rule '{matched_ask.raw}'.",
                    matched_rule=matched_ask.raw,
                ),
                context,
            )

        if _MODE_RANK[active_mode] < _MODE_RANK[required_mode]:
            if context.operation_mode == OperationMode.SAFE.value:
                return (
                    PermissionDecision(
                        decision="prompt",
                        reason=(
                            f"SAFE mode must approve escalation from {active_mode.value} "
                            f"to {required_mode.value}."
                        ),
                    ),
                    context,
                )
            return (
                PermissionDecision(
                    decision="deny",
                    reason=(
                        f"Tool '{clean_tool_name}' requires {required_mode.value}; "
                        f"current mode is {active_mode.value}."
                    ),
                ),
                context,
            )

        if context.operation_mode == OperationMode.SAFE.value:
            if clean_tool_name in self._mutating_tools and matched_allow is None and not (
                hook_override and hook_override.decision == "allow"
            ):
                return (
                    PermissionDecision(
                        decision="prompt",
                        reason="SAFE mode requires confirmation before mutating desktop actions.",
                    ),
                    context,
                )

        return (
            PermissionDecision(
                decision="allow",
                reason=(
                    f"Allowed by rule '{matched_allow.raw}'."
                    if matched_allow is not None
                    else ""
                ),
                matched_rule=matched_allow.raw if matched_allow is not None else "",
            ),
            context,
        )

    @staticmethod
    def _find_match(
        rules: list[_PermissionRule],
        tool_name: str,
        subject: str,
    ) -> Optional[_PermissionRule]:
        for rule in rules:
            if rule.matches(tool_name, subject):
                return rule
        return None


def extract_permission_subject(tool_input: dict[str, Any] | str | None) -> str:
    if isinstance(tool_input, str):
        try:
            parsed = json.loads(tool_input)
        except json.JSONDecodeError:
            return tool_input.strip()
        if isinstance(parsed, dict):
            return extract_permission_subject(parsed)
        return tool_input.strip()

    if not isinstance(tool_input, dict):
        return ""

    for key in (
        "command",
        "path",
        "file_path",
        "filePath",
        "url",
        "app_name",
        "workspace",
        "window_id",
        "ui_element_id",
        "process_name",
        "title_contains",
        "key",
        "text",
        "tool_name",
        "query",
    ):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(tool_input, ensure_ascii=True, sort_keys=True)


def permission_mode_from_label(label: Any) -> PermissionMode:
    clean = str(label or "").strip().lower()
    if clean in {"read_only", "read-only", "readonly"}:
        return PermissionMode.READ_ONLY
    if clean in {"workspace_write", "workspace-write"}:
        return PermissionMode.WORKSPACE_WRITE
    return PermissionMode.DANGER_FULL_ACCESS


def _operation_mode_key(value: object) -> str:
    if isinstance(value, OperationMode):
        return value.value
    enum_value = getattr(value, "value", value)
    return str(enum_value or OperationMode.AUTO.value).strip().lower() or OperationMode.AUTO.value
