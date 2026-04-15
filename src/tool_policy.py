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
    matched_rule_source: str = ""
    subject: str = ""
    required_mode: str = ""
    active_mode: str = ""

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
    subject: str
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
        self.validation_errors: list[dict[str, str]] = []
        self._allow_rules = self._parse_rules(rule_set.allow, "allow")
        self._deny_rules = self._parse_rules(rule_set.deny, "deny")
        self._ask_rules = self._parse_rules(rule_set.ask, "ask")
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
        subject = extract_permission_subject(payload)
        context = ToolPolicyContext(
            tool_name=clean_tool_name,
            tool_input=payload,
            subject=subject,
            required_mode=required_mode,
            active_mode=active_mode,
            operation_mode=_operation_mode_key(operation_mode),
            workspace=str(workspace or "user").strip().lower() or "user",
        )

        matched_deny = self._find_match(self._deny_rules, clean_tool_name, subject)
        if matched_deny is not None:
            return (
                self._decision(
                    context,
                    decision="deny",
                    reason=f"Denied by rule '{matched_deny.raw}'.",
                    matched_rule=matched_deny.raw,
                    matched_rule_source="deny",
                ),
                context,
            )

        if hook_override and hook_override.decision == "deny":
            return (
                self._decision(
                    context,
                    decision="deny",
                    reason=hook_override.reason or f"Tool '{clean_tool_name}' denied by hook.",
                    matched_rule_source="hook",
                ),
                context,
            )

        if context.operation_mode == OperationMode.GUIDE.value and required_mode != PermissionMode.READ_ONLY:
            return (
                self._decision(
                    context,
                    decision="deny",
                    reason="Guidance mode is read-only.",
                    matched_rule_source="mode",
                ),
                context,
            )

        matched_ask = self._find_match(self._ask_rules, clean_tool_name, subject)
        matched_allow = self._find_match(self._allow_rules, clean_tool_name, subject)

        if hook_override and hook_override.decision == "ask":
            return (
                self._decision(
                    context,
                    decision="prompt",
                    reason=hook_override.reason or f"Tool '{clean_tool_name}' requires approval.",
                    matched_rule_source="hook",
                ),
                context,
            )

        if matched_ask is not None:
            return (
                self._decision(
                    context,
                    decision="prompt",
                    reason=f"Approval required by rule '{matched_ask.raw}'.",
                    matched_rule=matched_ask.raw,
                    matched_rule_source="ask",
                ),
                context,
            )

        if _MODE_RANK[active_mode] < _MODE_RANK[required_mode]:
            if context.operation_mode == OperationMode.SAFE.value:
                return (
                    self._decision(
                        context,
                        decision="prompt",
                        reason=(
                            f"SAFE mode must approve escalation from {active_mode.value} "
                            f"to {required_mode.value}."
                        ),
                        matched_rule_source="mode",
                    ),
                    context,
                )
            return (
                self._decision(
                    context,
                    decision="deny",
                    reason=(
                        f"Tool '{clean_tool_name}' requires {required_mode.value}; "
                        f"current mode is {active_mode.value}."
                    ),
                    matched_rule_source="mode",
                ),
                context,
            )

        if context.operation_mode == OperationMode.SAFE.value:
            if clean_tool_name in self._mutating_tools and matched_allow is None and not (
                hook_override and hook_override.decision == "allow"
            ):
                return (
                    self._decision(
                        context,
                        decision="prompt",
                        reason="SAFE mode requires confirmation before mutating desktop actions.",
                        matched_rule_source="mode",
                    ),
                    context,
                )

        return (
            self._decision(
                context,
                decision="allow",
                reason=(
                    f"Allowed by rule '{matched_allow.raw}'."
                    if matched_allow is not None
                    else ""
                ),
                matched_rule=matched_allow.raw if matched_allow is not None else "",
                matched_rule_source="allow" if matched_allow is not None else "",
            ),
            context,
        )

    def _parse_rules(self, rules: list[str], source: str) -> list[_PermissionRule]:
        parsed: list[_PermissionRule] = []
        for item in list(rules or []):
            error = validate_permission_rule(item)
            if error:
                self.validation_errors.append(
                    {
                        "source": source,
                        "rule": str(item or ""),
                        "message": error,
                    }
                )
                continue
            parsed.append(_PermissionRule.parse(item))
        return parsed

    @staticmethod
    def _decision(
        context: ToolPolicyContext,
        *,
        decision: str,
        reason: str = "",
        matched_rule: str = "",
        matched_rule_source: str = "",
    ) -> PermissionDecision:
        return PermissionDecision(
            decision=decision,
            reason=reason,
            matched_rule=matched_rule,
            matched_rule_source=matched_rule_source,
            subject=context.subject,
            required_mode=context.required_mode.value,
            active_mode=context.active_mode.value,
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


def validate_permission_rule(raw: Any) -> str:
    clean = str(raw or "").strip()
    if not clean:
        return "Permission rule must be a non-empty string."
    if "(" not in clean:
        return ""
    if clean.count("(") != 1 or not clean.endswith(")"):
        return "Permission rule must use Tool(subject) syntax."
    open_index = clean.find("(")
    tool_name = clean[:open_index].strip()
    subject = clean[open_index + 1 : -1].strip()
    if not tool_name:
        return "Permission rule is missing a tool name."
    if not subject:
        return "Permission rule is missing a subject; use * to match any subject."
    if ")" in subject:
        return "Permission rule subject contains an unsupported closing parenthesis."
    return ""


def validate_permission_rules(rule_set: PermissionRuleSet) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for source, rules in (
        ("allow", rule_set.allow),
        ("deny", rule_set.deny),
        ("ask", rule_set.ask),
    ):
        for item in list(rules or []):
            error = validate_permission_rule(item)
            if error:
                errors.append(
                    {
                        "source": source,
                        "rule": str(item or ""),
                        "message": error,
                    }
                )
    return errors


def _operation_mode_key(value: object) -> str:
    if isinstance(value, OperationMode):
        return value.value
    enum_value = getattr(value, "value", value)
    return str(enum_value or OperationMode.AUTO.value).strip().lower() or OperationMode.AUTO.value
