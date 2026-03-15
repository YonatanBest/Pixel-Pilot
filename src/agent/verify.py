import io
import base64
import json
from typing import Any, Dict, List, Optional
import logging
from PIL import Image
from config import Config
from backend_client import get_client
from pydantic import BaseModel, Field
from agent.prompts import VERIFY_TASK_BLIND_PROMPT, VERIFY_TASK_COMPLETION_PROMPT

logger = logging.getLogger("pixelpilot.verify")


def verify_task_completion(
    user_command: str,
    expected_result: str,
    screen_elements: List[Dict],
    original_path: str,
    debug_path: str,
    reference_sheet,
    task_history: List[Dict],
) -> Optional[Dict[str, Any]]:
    """
    Verify that a task was actually completed by analyzing the current screen state.
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception as e:
        logger.error(f"Error loading images for verification: {e}")
        return None

    class VerificationResult(BaseModel):
        is_complete: bool = Field(
            description="True if the task is verifiably complete based on visual evidence"
        )
        confidence: float = Field(description="Confidence score spanning 0.0 to 1.0")
        reasoning: str = Field(
            description="Explanation of what visual evidence supports this conclusion"
        )
        next_action: Optional[str] = Field(
            description="Suggestion for the next step if task is not complete, or null/None if complete"
        )

    safe_elements = screen_elements if isinstance(screen_elements, list) else []
    safe_history = task_history if isinstance(task_history, list) else []

    elements_lines = []
    for el in safe_elements[:100]:
        if not isinstance(el, dict):
            continue
        eid = el.get("id", "?")
        etype = el.get("type", "unknown")
        label = el.get("label", "")
        elements_lines.append(f"ID {eid}: {etype} '{label}'")
    elements_str = "\n".join(elements_lines)

    history_lines = []
    for i, action in enumerate(safe_history):
        if not isinstance(action, dict):
            continue
        action_type = action.get("action_type", "unknown")
        reasoning = action.get("reasoning", "")
        history_lines.append(f"Step {i + 1}: {action_type} - {reasoning}")
    history_str = "\n".join(history_lines)

    prompt_text = VERIFY_TASK_COMPLETION_PROMPT.format(
        user_command=user_command,
        expected_result=expected_result,
        history_str=history_str,
        elements_str=elements_str,
    )

    def img_to_dict(img):
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        return {
            "mime_type": "image/png",
            "data": base64.b64encode(img_byte_arr.getvalue()).decode("utf-8"),
        }

    parts = [
        {"text": prompt_text},
        img_to_dict(clean_image),
        img_to_dict(annotated_image),
    ]
    if reference_sheet:
        parts.append(img_to_dict(reference_sheet))

    contents = [{"role": "user", "parts": parts}]

    try:
        client = get_client()
        response_data = client.generate_content(
            model=Config.GEMINI_MODEL,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": VerificationResult.model_json_schema(),
            },
        )

        result_obj = VerificationResult.model_validate_json(response_data["text"])
        return result_obj.model_dump()

    except Exception as e:
        logger.error(f"Error during verification: {e}")
        return None


class BlindVerificationResult(BaseModel):
    is_complete: bool = Field(
        description="True if the task is proven complete from UI Automation state"
    )
    confidence: float = Field(description="Confidence from 0.0 to 1.0")
    needs_vision: bool = Field(
        description="True when UI Automation evidence is insufficient and vision is required"
    )
    reason: str = Field(description="Short justification grounded in UI Automation state")
    next_action_hint: Optional[str] = Field(
        default=None,
        description="Optional next-step hint if the task is not complete",
    )


def _truncate_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_uia_state_section(ui_snapshot: Optional[Dict[str, Any]]) -> str:
    if not ui_snapshot:
        return "- unavailable: no snapshot captured"
    if not ui_snapshot.get("available", False):
        return f"- unavailable: {ui_snapshot.get('error', 'unknown error')}"

    lines = [
        (
            f"- window='{_truncate_text(ui_snapshot.get('active_window_title', ''), 90)}' "
            f"class='{_truncate_text(ui_snapshot.get('active_window_class', ''), 60)}' "
            f"elements={ui_snapshot.get('elements_count', 0)}"
        )
    ]
    for element in (ui_snapshot.get("elements") or [])[:50]:
        rect = element.get("rect") or {}
        lines.append(
            "  - "
            f"{element.get('ui_element_id', '')}: "
            f"{_truncate_text(element.get('control_type', ''), 24)} "
            f"name='{_truncate_text(element.get('name', ''), 50)}' "
            f"automation_id='{_truncate_text(element.get('automation_id', ''), 40)}' "
            f"rect=({rect.get('left', '?')},{rect.get('top', '?')},"
            f"{rect.get('right', '?')},{rect.get('bottom', '?')})"
        )
    return "\n".join(lines)


def verify_task_blind(
    user_command: str,
    expected_result: str,
    ui_snapshot: Optional[Dict[str, Any]],
    task_history: List[Dict[str, Any]],
    current_workspace: str,
) -> Optional[Dict[str, Any]]:
    safe_history = task_history if isinstance(task_history, list) else []
    history_lines = []
    for item in safe_history:
        if isinstance(item, dict) and "step" in item:
            history_lines.append(
                f"Step {item.get('step')}: {item.get('action_type')} - "
                f"{item.get('reasoning')} - success: {item.get('success')}"
            )

    prompt_text = VERIFY_TASK_BLIND_PROMPT.format(
        user_command=user_command,
        expected_result=expected_result or "",
        current_workspace=current_workspace,
        history_str="\n".join(history_lines) or "(no prior actions recorded)",
        uia_state_section=_format_uia_state_section(ui_snapshot),
    )

    contents = [{"role": "user", "parts": [{"text": prompt_text}]}]

    try:
        client = get_client()
        response_data = client.generate_content(
            model=Config.GEMINI_MODEL,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": BlindVerificationResult.model_json_schema(),
            },
        )

        result = BlindVerificationResult.model_validate_json(response_data["text"])
        return result.model_dump(exclude_none=True)
    except Exception as exc:
        logger.error("Error during blind verification: %s", exc)
        logger.debug("Blind verification snapshot: %s", json.dumps(ui_snapshot or {}))
        return None
