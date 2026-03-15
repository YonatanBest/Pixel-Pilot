from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from agent.brain import validate_or_repair_action


@dataclass
class GuardResult:
    valid: bool
    action: Optional[Dict[str, Any]]
    reason_code: str
    message: str
    repaired: bool = False
    error: str = ""


class ActionGuard:
    """
    Runtime safety gate for planner outputs.
    Pipeline: validate -> optional base-model repair -> revalidate -> execute.
    """

    def guard(
        self,
        action: Dict[str, Any],
        *,
        callback: Optional[Callable[[str], None]] = None,
        source: str = "runtime",
    ) -> GuardResult:
        validated, meta = validate_or_repair_action(
            action,
            callback=callback,
            allow_repair=True,
            source=source,
        )
        if validated is None:
            return GuardResult(
                valid=False,
                action=None,
                reason_code="invalid_action",
                message="Action failed strict validation and could not be repaired.",
                repaired=False,
                error=str(meta.get("error") or ""),
            )

        repaired = bool(meta.get("repaired", False))
        return GuardResult(
            valid=True,
            action=validated,
            reason_code="",
            message="Action validated",
            repaired=repaired,
            error=str(meta.get("error") or ""),
        )
