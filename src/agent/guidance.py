import time
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import BaseModel, Field

from agent.brain import get_model


class GuidanceCheck(BaseModel):
    step_complete: bool = Field(description="True if the expected result is visible")
    correction: Optional[str] = Field(
        default=None,
        description="Short corrective instruction if the user did the wrong thing",
    )
    reasoning: str = Field(description="Why the step is complete or not")


def infer_expected_result(action: Dict[str, Any]) -> str:
    action_type = (action.get("action_type") or "").strip().lower()
    params = action.get("params", {})

    if action_type == "open_app":
        app_name = params.get("app_name") or "the app"
        return f"{app_name} is open"
    if action_type == "click":
        return "The clicked item responds or opens"
    if action_type == "type_text":
        return "The text appears in the target field"
    if action_type == "press_key":
        return "The key press triggers the expected UI change"
    if action_type == "key_combo":
        return "The shortcut triggers the expected UI change"
    if action_type == "search_web":
        return "A browser opens the search results"
    if action_type == "call_skill":
        return "The system action completes"
    if action_type == "wait":
        return "The window or result appears after waiting"
    if action_type == "reply":
        return "The response is delivered to the user"

    return "The expected UI change is visible"


def _clean_label(label: str) -> str:
    text = (label or "").strip()
    if not text:
        return ""
    return text


def _position_hint(x: float, y: float, width: int, height: int) -> str:
    if not width or not height:
        return ""

    x_ratio = x / float(width)
    y_ratio = y / float(height)

    if x_ratio < 0.33:
        horiz = "left"
    elif x_ratio > 0.66:
        horiz = "right"
    else:
        horiz = "center"

    if y_ratio < 0.33:
        vert = "top"
    elif y_ratio > 0.66:
        vert = "bottom"
    else:
        vert = "middle"

    if horiz == "center" and vert == "middle":
        return "near the center"

    return f"near the {vert}-{horiz}"


def _format_keys(keys: List[str]) -> str:
    if not keys:
        return ""
    pretty = []
    for key in keys:
        k = str(key).strip().lower()
        if k in {"ctrl", "control"}:
            pretty.append("Ctrl")
        elif k in {"alt"}:
            pretty.append("Alt")
        elif k in {"shift"}:
            pretty.append("Shift")
        elif k == "win":
            pretty.append("Win")
        elif k == "enter":
            pretty.append("Enter")
        else:
            pretty.append(k.upper() if len(k) == 1 else k.title())
    return "+".join(pretty)


def describe_action(action: Dict[str, Any], elements: List[Dict], image_size: tuple[int, int]) -> str:
    action_type = (action.get("action_type") or "").strip().lower()
    params = action.get("params", {})
    width, height = image_size

    if action_type == "reply":
        return params.get("text", "") or "I can answer that directly."

    if action_type == "open_app":
        app_name = params.get("app_name") or "the app"
        return f"Press the Windows key, type '{app_name}', then press Enter."

    if action_type == "search_web":
        query = params.get("query") or ""
        if query:
            return f"Open your browser and search for: {query}."
        return "Open your browser and run the search."

    if action_type == "type_text":
        text = params.get("text") or ""
        if text:
            return f"Type: {text}"
        return "Type the required text."

    if action_type == "press_key":
        key = params.get("key") or ""
        if key:
            return f"Press {key}."
        return "Press the required key."

    if action_type == "key_combo":
        keys = params.get("keys", [])
        combo = _format_keys(keys)
        if combo:
            return f"Press {combo}."
        return "Press the required keyboard shortcut."

    if action_type == "wait":
        seconds = params.get("seconds")
        if seconds:
            return f"Wait about {seconds} seconds for the window to appear, then click Next."
        return "Wait for the window to appear, then click Next."

    if action_type == "call_skill":
        skill = params.get("skill") or "system"
        method = params.get("method") or "action"
        return f"Use the {skill} control to perform: {method}."

    if action_type == "click":
        element_id = params.get("element_id") or params.get("target_id")
        target = None
        if element_id is not None:
            target = next((el for el in elements if el.get("id") == element_id), None)

        if target:
            label = _clean_label(target.get("label"))
            el_type = (target.get("type") or "").strip()
            pos = _position_hint(target.get("x", 0), target.get("y", 0), width, height)
            if label:
                base = f"Click the '{label}'"
            elif el_type:
                base = f"Click the {el_type}"
            else:
                base = "Click the item"

            if pos:
                return f"{base} {pos}."
            return f"{base}."

        return "Click the target item on the screen."

    if action_type == "magnify":
        return "Zoom in on the target area to get a clearer view."

    return "Follow the on-screen step, then click Next."


def verify_guidance_step(
    user_command: str,
    expected_result: str,
    elements: List[Dict],
    original_path: str,
    debug_path: str,
) -> Optional[Dict[str, Any]]:
    if not expected_result:
        return {"step_complete": True, "correction": None, "reasoning": "No expected result."}

    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception:
        return None

    elements_str = "\n".join(
        [f"ID {el.get('id')}: {el.get('type')} '{el.get('label')}'" for el in elements[:120]]
    )

    prompt_text = f"""
You are guiding a user step-by-step.

USER COMMAND: "{user_command}"
EXPECTED RESULT FOR THIS STEP: "{expected_result}"

SCREEN ELEMENTS DETECTED:
{elements_str}

ATTACHMENTS:
1. [Original Screen]
2. [Annotated Screen] (IDs only for reference)

TASK:
Determine if the expected result is visible.
If it is not, provide a short corrective instruction to fix the mistake.

Return JSON:
{{
  "step_complete": true|false,
  "correction": "...",
  "reasoning": "..."
}}
"""

    try:
        model = get_model()
        response = model.generate_content(
            [prompt_text, clean_image, annotated_image],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": GuidanceCheck.model_json_schema(),
            },
        )
        return GuidanceCheck.model_validate_json(response.text).model_dump()
    except Exception:
        return None


def wait_for_user_next(chat_window, label: str, stop_check) -> bool:
    if not chat_window:
        input(f"Press Enter to continue ({label})... ")
        return True

    payload = {"result": False, "event": None}
    payload["event"] = _create_event()
    chat_window.request_guidance_next(label, payload)

    while True:
        stop_check()
        if payload["event"].wait(0.2):
            return bool(payload.get("result"))
        time.sleep(0.05)


def _create_event():
    import threading

    return threading.Event()
