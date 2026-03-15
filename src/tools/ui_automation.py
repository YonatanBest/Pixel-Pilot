from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter, deque
from typing import Any, Optional

from config import Config

logger = logging.getLogger("pixelpilot.uia")

try:
    import uiautomation as auto

    UIA_IMPORT_ERROR = ""
except Exception as exc:
    auto = None
    UIA_IMPORT_ERROR = str(exc)


def _unavailable_snapshot(workspace: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace": workspace,
        "available": False,
        "error": reason,
        "active_window_title": "",
        "active_window_class": "",
        "elements_count": 0,
        "elements": [],
    }


def snapshot_signature(snapshot: Optional[dict[str, Any]]) -> str:
    if not snapshot:
        return "blind:none"

    compact_elements = []
    for element in (snapshot.get("elements") or [])[:80]:
        rect = element.get("rect") or {}
        compact_elements.append(
            {
                "ui_element_id": element.get("ui_element_id", ""),
                "name": element.get("name", ""),
                "control_type": element.get("control_type", ""),
                "automation_id": element.get("automation_id", ""),
                "class_name": element.get("class_name", ""),
                "rect": {
                    "left": rect.get("left"),
                    "top": rect.get("top"),
                    "right": rect.get("right"),
                    "bottom": rect.get("bottom"),
                },
            }
        )

    compact = {
        "workspace": snapshot.get("workspace", ""),
        "available": bool(snapshot.get("available", False)),
        "error": snapshot.get("error", ""),
        "active_window_title": snapshot.get("active_window_title", ""),
        "active_window_class": snapshot.get("active_window_class", ""),
        "elements_count": int(snapshot.get("elements_count", 0) or 0),
        "elements": compact_elements,
    }
    raw = json.dumps(compact, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return f"blind:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _safe_attr(obj: Any, attr: str, default: Any = "") -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _rect_to_dict(rect: Any) -> dict[str, int] | None:
    if rect is None:
        return None
    try:
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
    except Exception:
        return None


def _safe_rect_visible(control: Any) -> bool:
    rect = _safe_attr(control, "BoundingRectangle", default=None)
    if rect is None:
        return False
    try:
        return int(rect.right) > int(rect.left) and int(rect.bottom) > int(rect.top)
    except Exception:
        return False


def _collect_patterns(control: Any) -> list[str]:
    patterns: list[str] = []
    try:
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy and _safe_attr(legacy, "DefaultAction", ""):
            patterns.append("DefaultAction")
    except Exception:
        pass

    for name in ("GetTextPattern", "GetValuePattern", "SendKeys"):
        if hasattr(control, name):
            patterns.append(name.replace("Get", "").replace("Pattern", ""))
    return patterns


def _score_candidate(
    *,
    name: str,
    control_type: str,
    automation_id: str,
    class_name: str,
    patterns: list[str],
    preferred_terms: list[str],
) -> float:
    score = 0.0
    type_weights = {
        "EditControl": 6.0,
        "TreeItemControl": 5.0,
        "ListItemControl": 5.0,
        "ButtonControl": 4.0,
        "MenuItemControl": 4.0,
        "SplitButtonControl": 4.0,
        "TabItemControl": 3.5,
        "ComboBoxControl": 3.0,
        "CheckBoxControl": 2.5,
        "RadioButtonControl": 2.5,
        "TextControl": 1.5,
        "PaneControl": 1.0,
    }
    score += type_weights.get(control_type, 0.5)

    if "DefaultAction" in patterns:
        score += 2.5
    if "Text" in patterns:
        score += 1.0
    if "Value" in patterns:
        score += 1.0

    name_l = (name or "").lower()
    auto_l = (automation_id or "").lower()
    class_l = (class_name or "").lower()
    combined = f"{name_l} {auto_l} {class_l}"

    if "search" in combined:
        score += 1.5
    if "address" in combined or "breadcrumb" in combined:
        score += 1.5

    for term in preferred_terms:
        if term and term in combined:
            score += 4.0

    if not (name or automation_id):
        score -= 1.0

    return score


def _base_element_key(node: dict[str, Any]) -> str:
    rect = node.get("rect") or {}
    rect_key = (
        f"{rect.get('left', '')}:{rect.get('top', '')}:"
        f"{rect.get('right', '')}:{rect.get('bottom', '')}"
    )
    raw = "|".join(
        [
            str(node.get("control_type", "")),
            str(node.get("name", "")),
            str(node.get("automation_id", "")),
            str(node.get("class_name", "")),
            rect_key,
        ]
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _normalize_terms(preferred_terms: Optional[list[str]]) -> list[str]:
    return [term.lower().strip() for term in (preferred_terms or []) if str(term).strip()]


def _safe_foreground_control(max_attempts: int = 8, delay: float = 0.06) -> Any:
    if auto is None:
        raise RuntimeError(f"uiautomation unavailable: {UIA_IMPORT_ERROR}")

    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            control = auto.GetForegroundControl()
            if control:
                return control
        except Exception as exc:
            last_exc = exc
        time.sleep(delay)

    try:
        return auto.GetRootControl()
    except Exception:
        if last_exc:
            raise last_exc
        raise RuntimeError("Unable to resolve foreground/root control")


def _annotate_candidates(
    candidates: list[tuple[float, dict[str, Any], Any]],
    *,
    workspace: str,
    title: str,
    class_name: str,
    max_nodes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_keys = [_base_element_key(node) for _, node, _ in candidates]
    counts = Counter(base_keys)
    seen: dict[str, int] = {}
    nodes: list[dict[str, Any]] = []
    control_index: dict[str, Any] = {}

    for (score, node, control), base_key in zip(candidates, base_keys):
        seen[base_key] = seen.get(base_key, 0) + 1
        suffix = f"_{seen[base_key]}" if counts[base_key] > 1 else ""
        ui_element_id = f"el_{base_key}{suffix}"

        annotated = dict(node)
        annotated["ui_element_id"] = ui_element_id
        annotated["rank_score"] = round(float(score), 3)
        nodes.append(annotated)
        control_index[ui_element_id] = control

    snapshot_nodes = nodes[:max_nodes]
    snapshot = {
        "schema_version": 1,
        "workspace": workspace,
        "available": True,
        "error": "",
        "active_window_title": title,
        "active_window_class": class_name,
        "elements_count": len(snapshot_nodes),
        "elements": snapshot_nodes,
    }
    return snapshot, control_index


def _scan_snapshot(
    *,
    workspace: str,
    max_nodes: int,
    preferred_terms: Optional[list[str]] = None,
    scan_limit: Optional[int] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preferred_terms = _normalize_terms(preferred_terms)
    window = _safe_foreground_control()
    title = _safe_attr(window, "Name", default="")
    class_name = _safe_attr(window, "ClassName", default="")

    queue: deque[Any] = deque([window])
    scanned = 0
    limit = scan_limit or max(max_nodes * 15, 300)
    candidates: list[tuple[float, dict[str, Any], Any]] = []

    while queue and scanned < limit:
        current = queue.popleft()
        scanned += 1

        if _safe_rect_visible(current):
            name = _safe_attr(current, "Name", default="")
            control_type = _safe_attr(current, "ControlTypeName", default="")
            automation_id = _safe_attr(current, "AutomationId", default="")
            current_class = _safe_attr(current, "ClassName", default="")
            patterns = _collect_patterns(current)

            node = {
                "name": name,
                "control_type": control_type,
                "automation_id": automation_id,
                "class_name": current_class,
                "native_window_handle": int(
                    _safe_attr(current, "NativeWindowHandle", default=0) or 0
                ),
                "patterns": patterns,
                "rect": _rect_to_dict(
                    _safe_attr(current, "BoundingRectangle", default=None)
                ),
            }
            score = _score_candidate(
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                class_name=current_class,
                patterns=patterns,
                preferred_terms=preferred_terms,
            )
            candidates.append((score, node, current))

        try:
            for child in current.GetChildren():
                queue.append(child)
        except Exception:
            continue

    candidates.sort(key=lambda item: item[0], reverse=True)
    return _annotate_candidates(
        candidates,
        workspace=workspace,
        title=title,
        class_name=class_name,
        max_nodes=max_nodes,
    )


def _run_in_workspace(
    workspace_name: str,
    desktop_manager: Any,
    func,
    *args,
    **kwargs,
):
    if workspace_name == "agent":
        if not desktop_manager or not getattr(desktop_manager, "is_created", False):
            raise RuntimeError("Agent Desktop unavailable")
        result = desktop_manager.run_on_desktop(func, *args, **kwargs)
        if result is None:
            raise RuntimeError("Agent Desktop UIA call returned no result")
        return result
    return func(*args, **kwargs)


def _apply_focus(control: Any) -> tuple[bool, str]:
    try:
        control.SetFocus()
        time.sleep(0.03)
        return True, "SetFocus"
    except Exception:
        pass

    try:
        control.SetActive()
        time.sleep(0.03)
        return True, "SetActive"
    except Exception:
        return False, ""


def _extract_text_from_control(control: Any) -> tuple[str, str]:
    try:
        text_pattern = control.GetTextPattern()
        if text_pattern and text_pattern.DocumentRange:
            text = str(text_pattern.DocumentRange.GetText(-1) or "").strip()
            if text:
                return text, "TextPattern.DocumentRange"
    except Exception:
        pass

    try:
        value_pattern = control.GetValuePattern()
        if value_pattern:
            text = str(value_pattern.Value or "").strip()
            if text:
                return text, "ValuePattern.Value"
    except Exception:
        pass

    try:
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy:
            value_text = str(getattr(legacy, "Value", "") or "").strip()
            if value_text:
                return value_text, "LegacyIAccessible.Value"
            name_text = str(getattr(legacy, "Name", "") or "").strip()
            if name_text:
                return name_text, "LegacyIAccessible.Name"
    except Exception:
        pass

    name = str(getattr(control, "Name", "") or "").strip()
    if name:
        return name, "Control.Name"

    return "", ""


def _control_unique_key(control: Any) -> tuple[Any, ...]:
    rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None)) or {}
    return (
        int(_safe_attr(control, "NativeWindowHandle", default=0) or 0),
        str(_safe_attr(control, "Name", default="") or ""),
        str(_safe_attr(control, "ControlTypeName", default="") or ""),
        str(_safe_attr(control, "AutomationId", default="") or ""),
        str(_safe_attr(control, "ClassName", default="") or ""),
        rect.get("left", 0),
        rect.get("top", 0),
        rect.get("right", 0),
        rect.get("bottom", 0),
    )


def _seed_controls(
    *,
    target: str,
    ui_element_id: Optional[str],
    control_index: dict[str, Any],
) -> list[tuple[str, Any]]:
    target = (target or "auto").strip().lower()
    if target not in {"auto", "focused", "window", "element"}:
        target = "auto"

    seeds: list[tuple[str, Any]] = []
    if target in {"auto", "element"} and ui_element_id:
        control = control_index.get(ui_element_id)
        if control is not None:
            seeds.append((f"element:{ui_element_id}", control))

    if target in {"auto", "focused"}:
        try:
            control = auto.GetFocusedControl()
            if control:
                seeds.append(("focused", control))
        except Exception:
            pass

    if target in {"auto", "window"}:
        try:
            control = auto.GetForegroundControl()
            if control:
                seeds.append(("window", control))
        except Exception:
            pass

    unique: list[tuple[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for source, control in seeds:
        marker = _control_unique_key(control)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append((source, control))
    return unique


def _scan_text_from_control(control: Any, max_chars: int) -> tuple[str, str]:
    queue: deque[Any] = deque([control])
    visited: set[tuple[Any, ...]] = set()
    best_text = ""
    best_source = ""
    scanned = 0

    while queue and scanned < 220:
        current = queue.popleft()
        marker = _control_unique_key(current)
        if marker in visited:
            continue
        visited.add(marker)
        scanned += 1

        text, source = _extract_text_from_control(current)
        if len(text) > len(best_text):
            best_text = text
            best_source = source

        try:
            for child in current.GetChildren():
                queue.append(child)
        except Exception:
            continue

    if len(best_text) > max_chars:
        best_text = best_text[:max_chars]
    return best_text, best_source


def ensure_foreground_focus(
    workspace: str,
    desktop_manager: Any,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _focus() -> dict[str, Any]:
        try:
            foreground = auto.GetForegroundControl()
        except Exception:
            foreground = None

        if foreground:
            success, method = _apply_focus(foreground)
            if success:
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "method": method,
                }

        try:
            root = auto.GetRootControl()
        except Exception:
            root = None

        if root:
            success, method = _apply_focus(root)
            if success:
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "method": method,
                }

        return {"success": False, "reason": "no_focus_target", "workspace": workspace}

    try:
        return _run_in_workspace(workspace, desktop_manager, _focus)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "error": str(exc),
        }


def focus_element(
    workspace: str,
    desktop_manager: Any,
    ui_element_id: str,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    target_id = str(ui_element_id or "").strip()
    if not target_id:
        return {"success": False, "reason": "missing_id", "workspace": workspace}
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _focus() -> dict[str, Any]:
        _, control_index = _scan_snapshot(
            workspace=workspace,
            max_nodes=max(Config.UIA_MAX_ELEMENTS * 3, 360),
            preferred_terms=None,
            scan_limit=max(Config.UIA_MAX_ELEMENTS * 30, 1200),
        )
        control = control_index.get(target_id)
        if control is None:
            return {
                "success": False,
                "reason": "not_found",
                "workspace": workspace,
                "ui_element_id": target_id,
            }

        rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None))
        success, method = _apply_focus(control)
        return {
            "success": success,
            "reason": "ok" if success else "focus_failed",
            "workspace": workspace,
            "ui_element_id": target_id,
            "method": method,
            "rect": rect,
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _focus)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "ui_element_id": target_id,
            "error": str(exc),
        }


def get_snapshot(
    workspace: str,
    desktop_manager: Any,
    max_nodes: int,
    preferred_terms: Optional[list[str]] = None,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return _unavailable_snapshot(workspace, "UIA blind mode disabled")
    if auto is None:
        return _unavailable_snapshot(workspace, f"uiautomation unavailable: {UIA_IMPORT_ERROR}")

    try:
        snapshot, _ = _run_in_workspace(
            workspace,
            desktop_manager,
            _scan_snapshot,
            workspace=workspace,
            max_nodes=max_nodes,
            preferred_terms=preferred_terms,
        )
        return snapshot
    except Exception as exc:
        logger.debug("UIA snapshot failed on %s workspace: %s", workspace, exc)
        return _unavailable_snapshot(workspace, str(exc))


def get_element_rect(
    workspace: str,
    desktop_manager: Any,
    ui_element_id: str,
) -> dict[str, int] | None:
    workspace = (workspace or "user").strip().lower()
    target_id = str(ui_element_id or "").strip()
    if not target_id or not Config.ENABLE_UIA_BLIND_MODE or auto is None:
        return None

    def _resolve() -> dict[str, int] | None:
        _, control_index = _scan_snapshot(
            workspace=workspace,
            max_nodes=max(Config.UIA_MAX_ELEMENTS * 3, 360),
            preferred_terms=None,
            scan_limit=max(Config.UIA_MAX_ELEMENTS * 30, 1200),
        )
        control = control_index.get(target_id)
        if control is None:
            return None
        return _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None))

    try:
        return _run_in_workspace(workspace, desktop_manager, _resolve)
    except Exception:
        return None


def read_text(
    workspace: str,
    desktop_manager: Any,
    target: str,
    ui_element_id: Optional[str] = None,
    max_chars: int = 4000,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    safe_target = (target or "auto").strip().lower()
    safe_max_chars = max(1, int(max_chars or Config.UIA_TEXT_MAX_CHARS))

    if not Config.ENABLE_UIA_BLIND_MODE:
        return {
            "available": False,
            "status": "error",
            "reason": "disabled",
            "text": "",
            "source": "",
            "workspace": workspace,
        }
    if auto is None:
        return {
            "available": False,
            "status": "error",
            "reason": "import_error",
            "text": "",
            "source": "",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _read() -> dict[str, Any]:
        snapshot, control_index = _scan_snapshot(
            workspace=workspace,
            max_nodes=max(Config.UIA_MAX_ELEMENTS * 2, 240),
            preferred_terms=None,
            scan_limit=max(Config.UIA_MAX_ELEMENTS * 25, 900),
        )
        seeds = _seed_controls(
            target=safe_target,
            ui_element_id=ui_element_id,
            control_index=control_index,
        )
        if not seeds:
            return {
                "available": True,
                "status": "error",
                "reason": "no_target",
                "text": "",
                "source": "",
                "workspace": workspace,
                "target": safe_target,
                "ui_element_id": ui_element_id,
                "active_window_title": snapshot.get("active_window_title", ""),
                "active_window_class": snapshot.get("active_window_class", ""),
            }

        best_text = ""
        best_source = ""
        best_seed = ""
        for seed_source, control in seeds:
            text, source = _scan_text_from_control(control, safe_max_chars)
            if len(text) > len(best_text):
                best_text = text
                best_source = source
                best_seed = seed_source

        status = "ok" if best_text else "error"
        reason = "ok" if best_text else "no_text"
        return {
            "available": True,
            "status": status,
            "reason": reason,
            "text": best_text,
            "source": best_source,
            "workspace": workspace,
            "target": safe_target,
            "ui_element_id": ui_element_id,
            "seed_source": best_seed,
            "active_window_title": snapshot.get("active_window_title", ""),
            "active_window_class": snapshot.get("active_window_class", ""),
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _read)
    except Exception as exc:
        return {
            "available": False,
            "status": "error",
            "reason": "runtime_error",
            "text": "",
            "source": "",
            "workspace": workspace,
            "target": safe_target,
            "ui_element_id": ui_element_id,
            "error": str(exc),
        }
