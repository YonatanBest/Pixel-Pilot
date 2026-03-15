import io
import json
import base64
import logging
from typing import Any, Callable, Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from config import Config
from backend_client import get_client, RateLimitError
from agent.prompts import (
    PLAN_TASK_PROMPT,
    PLAN_TASK_BLIND_PROMPT,
    PLAN_TASK_BLIND_FIRST_STEP_PROMPT,
)

client = get_client()
model = Config.GEMINI_MODEL
logger = logging.getLogger("pixelpilot.brain")


class ModelWrapper:
    def __init__(self, model_name: str, callback: Optional[Callable[[str], None]] = None):
        self.model_name = model_name
        self.callback = callback

    def generate_content(self, contents, config=None):
        if self.callback:
            self.callback("Waiting for AI response...")
        logger.info("Waiting for AI response...")
        return client.generate_content(
            model=self.model_name, contents=contents, config=config
        )


def get_model(callback: Optional[Callable[[str], None]] = None):
    return ModelWrapper(model, callback=callback)


def create_reference_sheet(crops):
    if not crops:
        return None
    cell_w, cell_h = 100, 100
    cols = 8

    rows = (len(crops) + cols - 1) // cols

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), color=(30, 30, 30))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    for i, item in enumerate(crops):
        img = item["image"]
        img.thumbnail((cell_w - 10, cell_h - 30))

        c, r = i % cols, i // cols
        x, y = c * cell_w + 5, r * cell_h + 30

        sheet.paste(img, (x, y))
        draw.text((x, y - 25), f"ID:{item['id']}", fill=(0, 255, 0), font=font)

    return sheet


class ActionResponse(BaseModel):
    element_id: int = Field(
        description="The ID number of the element to interact with from labels."
    )
    reasoning: str = Field(description="Reasoning for choosing this specific element.")


class SkillArgs(BaseModel):
    query: Optional[str] = Field(None, description="For search/media")
    url: Optional[str] = Field(None, description="For browser open")
    browser: Optional[str] = Field(None, description="Preferred browser (e.g., 'chrome', 'edge')")
    action: Optional[str] = Field(None, description="For volume control")
    page: Optional[str] = Field(None, description="For settings")


class ActionParams(BaseModel):
    element_id: Optional[int] = Field(None, description="ID of UI element")
    target_id: Optional[int] = Field(None, description="Alias for element_id")
    ui_element_id: Optional[str] = Field(
        None,
        description="Stable UI Automation element id for blind-mode interaction",
    )
    text: Optional[str] = Field(None, description="Text to type or reply")
    key: Optional[str] = Field(None, description="Key to press")
    keys: Optional[List[str]] = Field(None, description="Keys for combo")
    seconds: Optional[float] = Field(None, description="Time to wait")
    app_name: Optional[str] = Field(None, description="App to open")
    zoom_level: Optional[float] = Field(None, description="Magnification level")
    skill: Optional[str] = Field(None, description="Skill name")
    method: Optional[str] = Field(None, description="Skill method")
    args: Optional[SkillArgs] = Field(None, description="Arguments for skill method")
    workspace: Optional[str] = Field(None, description="Target workspace")
    target: Optional[str] = Field(
        None,
        description="read_ui_text target: auto, focused, window, or element",
    )
    max_chars: Optional[int] = Field(
        None,
        description="Maximum characters for read_ui_text responses",
    )


class SubAction(BaseModel):
    action_type: str = Field(
        description=(
            "The type of action: click, type_text, press_key, key_combo, open_app, wait, "
            "magnify, switch_workspace, read_ui_text"
        )
    )
    params: ActionParams = Field(description="Parameters for the action.")
    reasoning: str = Field(description="Reasoning for this specific sub-action")


class PlannedAction(BaseModel):
    action_type: str = Field(
        description="The primary action type (or 'sequence' if using action_sequence)"
    )
    params: ActionParams = Field(description="Parameters for the action")
    reasoning: str = Field(description="Detailed logic for this decision")
    confidence: float = Field(
        description="Score between 0.0 and 1.0 indicating AI certainty"
    )
    clarification_needed: bool = Field(
        description="True if the AI needs more info from user"
    )
    clarification_question: Optional[str] = Field(
        description="Question to ask user if clarification_needed is True"
    )
    task_complete: bool = Field(description="True if the user's objective is finished")
    skip_verification: bool = Field(
        default=False,
        description="True if verification/screenshot is unnecessary (e.g., for trivial actions like 'reply' or 'wait')",
    )
    needs_vision: bool = Field(
        default=False,
        description="True if you cannot complete the task without seeing the screen (e.g. need to click buttons, find icons).",
    )
    expected_result: Optional[str] = Field(
        description="What should happen after this action"
    )
    action_sequence: Optional[List[SubAction]] = Field(
        default=None,
        description="A sequence of actions to execute at once (Turbo Mode)",
    )


def pil_to_dict(img: Image.Image) -> Dict[str, str]:
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    return {
        "mime_type": "image/png",
        "data": base64.b64encode(img_byte_arr.getvalue()).decode("utf-8"),
    }


def _normalize_history(
    history: Optional[List],
    *,
    include_blind_only: bool = True,
    include_uia_only: bool = True,
) -> List[Dict[str, Any]]:
    if not history:
        return []
    normalized = []
    for item in history:
        if (
            isinstance(item, dict)
            and item.get("blind_only")
            and not include_blind_only
        ):
            continue
        if (
            isinstance(item, dict)
            and item.get("uia_only")
            and not include_uia_only
        ):
            continue
        if isinstance(item, dict) and "role" in item and "parts" in item:
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append({"role": "model", "parts": [{"text": item}]})
    return normalized


def _history_to_task_context(history: Optional[List], task_context: Optional[str]) -> str:
    if task_context:
        return task_context

    debug_steps = []
    for item in history or []:
        if isinstance(item, dict) and "step" in item:
            debug_steps.append(
                f"Step {item['step']}: {item.get('action_type')} - "
                f"{item.get('reasoning')} - success: {item.get('success')}"
            )
    if not debug_steps:
        return ""
    return "PREVIOUS STEPS:\n" + "\n".join(debug_steps)


def _truncate_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_uia_state_section(ui_snapshot: Optional[Dict[str, Any]]) -> str:
    if not ui_snapshot:
        return "UI AUTOMATION STATE:\n- unavailable: no snapshot captured\n"

    if not ui_snapshot.get("available", False):
        reason = ui_snapshot.get("error", "unknown error")
        return f"UI AUTOMATION STATE:\n- unavailable: {reason}\n"

    lines = [
        "UI AUTOMATION STATE:",
        (
            f"- workspace={ui_snapshot.get('workspace', '')} "
            f"window='{_truncate_text(ui_snapshot.get('active_window_title', ''), 90)}' "
            f"class='{_truncate_text(ui_snapshot.get('active_window_class', ''), 60)}'"
        ),
        f"- visible_elements={ui_snapshot.get('elements_count', 0)}",
    ]

    for element in (ui_snapshot.get("elements") or [])[:50]:
        rect = element.get("rect") or {}
        lines.append(
            "  - "
            f"{element.get('ui_element_id', '')}: "
            f"{_truncate_text(element.get('control_type', ''), 24)} "
            f"name='{_truncate_text(element.get('name', ''), 50)}' "
            f"automation_id='{_truncate_text(element.get('automation_id', ''), 40)}' "
            f"class='{_truncate_text(element.get('class_name', ''), 30)}' "
            f"patterns={element.get('patterns', [])} "
            f"rect=({rect.get('left', '?')},{rect.get('top', '?')},"
            f"{rect.get('right', '?')},{rect.get('bottom', '?')})"
        )
    return "\n".join(lines) + "\n"

def plan_task(
    user_command: str,
    screen_elements: List[Dict],
    original_path: str,
    debug_path: str,
    reference_sheet,
    task_context: Optional[str] = None,
    magnification_hint: Optional[str] = None,
    history: Optional[List] = None,
    guidance_mode: bool = False,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
    media_resolution: str = "low",
    callback: Optional[Callable[[str], None]] = None,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Enhanced brain function for multi-step task planning.
    Returns a structured action plan with multiple steps if needed.
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception as e:
        logger.error(f"Error loading images: {e}")
        return None

    elements_str = "\n".join(
        [
            f"ID {el['id']}: {el['type']} '{el['label']}' at ({el['x']}, {el['y']})"
            for el in screen_elements[:150]
        ]
    )

    task_context = _history_to_task_context(history, task_context)
    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    mag_section = ""
    if magnification_hint:
        mag_section = f"\n MAGNIFICATION NOTE: {magnification_hint}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"
    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES" if agent_desktop_available else "AGENT DESKTOP AVAILABLE: NO"
    )
    prompt_text = PLAN_TASK_PROMPT.format(
        turbo_status=turbo_status,
        user_command=user_command,
        context_section=context_section,
        mag_section=mag_section,
        workspace_section=workspace_section,
        agent_desktop_section=agent_desktop_section,
        elements_str=elements_str
    )

    contents = [
        {
            "role": "user",
            "parts": [
                {"text": prompt_text},
                pil_to_dict(clean_image),
                pil_to_dict(annotated_image),
            ],
        }
    ]
    if reference_sheet:
        contents[0]["parts"].append(pil_to_dict(reference_sheet))

    contents_to_send = _normalize_history(
        history,
        include_blind_only=False,
        include_uia_only=False,
    )
    contents_to_send.append(contents[0])

    try:
        mw = get_model(callback)
        response_data = mw.generate_content(
            contents=contents_to_send,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": PlannedAction.model_json_schema(),
                "thinking_config": {"thinking_level": "high"},
                "tools": [{"google_search": {}}, {"code_execution": {}}],
            },
        )

        response_text = response_data["text"]
        action_dict = PlannedAction.model_validate_json(response_text).model_dump(
            exclude_none=True
        )
        model_part = {"role": "model", "parts": [{"text": response_text}]}
        return action_dict, model_part
    except RateLimitError:
        raise
    except Exception as e:
        logger.error(f"Error in plan_task: {e}")
        return None


def plan_task_blind(
    user_command: str,
    task_context: Optional[str] = None,
    history: Optional[List] = None,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
    ui_snapshot: Optional[Dict[str, Any]] = None,
    callback: Optional[Callable[[str], None]] = None,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Planning for 'Blind Mode' (No Vision).
    The agent attempts to solve the task using only OS skills, hotkeys, and shell commands.
    If it realizes it needs to see the screen (e.g. to click a button), it returns needs_vision=True.
    """

    task_context = _history_to_task_context(history, task_context)
    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"
    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES" if agent_desktop_available else "AGENT DESKTOP AVAILABLE: NO"
    )

    prompt_text = PLAN_TASK_BLIND_PROMPT.format(
        user_command=user_command,
        context_section=context_section,
        workspace_section=workspace_section,
        agent_desktop_section=agent_desktop_section,
        uia_state_section=_format_uia_state_section(ui_snapshot),
    )

    contents_to_send = _normalize_history(
        history,
        include_blind_only=True,
        include_uia_only=True,
    )
    contents_to_send.append({"role": "user", "parts": [{"text": prompt_text}]})

    try:
        mw = get_model(callback)
        response_data = mw.generate_content(
            contents=contents_to_send,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": PlannedAction.model_json_schema(),
            },
        )
        response_text = response_data["text"]
        action_dict = PlannedAction.model_validate_json(response_text).model_dump(
            exclude_none=True
        )
        model_part = {
            "role": "model",
            "parts": [{"text": response_text}],
            "blind_only": True,
        }
        return action_dict, model_part
    except RateLimitError:
        raise
    except Exception as e:
        logger.error(f"Error in plan_task_blind: {e}")
        return None


def plan_task_blind_first_step(
    user_command: str,
    history: Optional[List] = None,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
    callback: Optional[Callable[[str], None]] = None,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    First-step blind planning focused only on workspace selection.
    Must decide the workspace before any vision handoff.
    """

    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES" if agent_desktop_available else "AGENT DESKTOP AVAILABLE: NO"
    )

    prompt_text = PLAN_TASK_BLIND_FIRST_STEP_PROMPT.format(
        user_command=user_command,
        workspace_section=workspace_section,
        agent_desktop_section=agent_desktop_section
    )

    contents_to_send = _normalize_history(
        history,
        include_blind_only=True,
        include_uia_only=False,
    )
    contents_to_send.append({"role": "user", "parts": [{"text": prompt_text}]})

    try:
        mw = get_model(callback)
        response_data = mw.generate_content(
            contents=contents_to_send,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": PlannedAction.model_json_schema(),
            },
        )
        response_text = response_data["text"]
        action_dict = PlannedAction.model_validate_json(response_text).model_dump(
            exclude_none=True
        )
        model_part = {
            "role": "model",
            "parts": [{"text": response_text}],
            "blind_only": True,
        }
        return action_dict, model_part
    except RateLimitError:
        raise
    except Exception as e:
        print(f"Error in plan_task_blind_first_step: {e}")
        return None
