import io
import logging
from typing import Any, Dict, List, Optional

from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from config import Config
from services.gemini import ConversationHistory, GeminiClient
from services.gemini.agent_tools import create_planning_registry, get_action_from_tool_calls

_gemini_client = GeminiClient(
    api_key=Config.GEMINI_API_KEY,
    default_model=Config.GEMINI_MODEL,
)

logger = logging.getLogger(__name__)


def get_gemini_client() -> GeminiClient:
    return _gemini_client


def _image_to_part(img: Image.Image, res: str = "low") -> types.Part:
    res_map = {"low": "MEDIA_RESOLUTION_LOW", "high": "MEDIA_RESOLUTION_HIGH"}
    res_enum = res_map.get(res.lower(), "MEDIA_RESOLUTION_LOW")

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    return types.Part.from_bytes(
        data=img_byte_arr.getvalue(),
        mime_type="image/png",
        media_resolution=res_enum,
    )


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


def plan_task(
    user_command: str,
    screen_elements: List[Dict],
    original_path: str,
    debug_path: str,
    reference_sheet,
    task_context: Optional[str] = None,
    magnification_hint: Optional[str] = None,
    history: Optional[ConversationHistory] = None,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
    media_resolution: str = "low",
    open_apps: Optional[List[str]] = None,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Enhanced brain function for multi-step task planning.
    Returns a structured action plan with multiple steps if needed.

    Args:
        user_command: The user's natural language command
        screen_elements: List of detected UI elements
        original_path: Path to original screenshot
        debug_path: Path to annotated screenshot
        reference_sheet: Reference sheet image
        task_context: Optional context about ongoing task
        magnification_hint: Optional info about current zoom state

    Returns:
        Dict with task plan or None if error
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception:
        logger.exception("Error loading images for plan_task")
        return None

    elements_str = "\n".join(
        [
            f"ID {el['id']}: {el['type']} '{el['label']}' at ({el['x']}, {el['y']})"
            for el in screen_elements[:150]
        ]
    )

    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    mag_section = ""
    if magnification_hint:
        mag_section = f"\n MAGNIFICATION NOTE: {magnification_hint}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"
    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES"
        if agent_desktop_available
        else "AGENT DESKTOP AVAILABLE: NO"
    )
    prompt_text = f"""
You are Pixel Pilot, a desktop assistant system that can plan and execute tasks using vision and blind control.

TURBO MODE STATUS: {turbo_status}

USER COMMAND: "{user_command}"
{context_section}{mag_section}
{workspace_section}
{agent_desktop_section}
SCREEN ELEMENTS DETECTED (OCR + Visual):
{elements_str}

ATTACHMENTS:
1. [Original Screen]: The user's actual desktop view
2. [Annotated Screen]: Green overlays with ID numbers on clickable elements. USE THIS to find IDs.
3. [Reference Sheet] (Optional): Zoomed view of icons/buttons.

YOUR TASK:
Analyze the user's command and current screen state. Use `emit_action` to return the NEXT action(s) to take.

COORDINATION RULES:
- You are the VISION agent. A BLIND agent exists and can take over when visual context is not needed.
- To hand off to the BLIND agent, set `needs_vision: false`.
- To request VISION (you), the BLIND agent will set `needs_vision: true`.
- You and the BLIND agent must stay aware of the CURRENT WORKSPACE and can switch when needed.

IDENTITY RULE:
- If the user asks who you are or what you are, answer as the Pixel Pilot system (the overall desktop assistant app). Do NOT describe yourself as the VISION or BLIND agent in the user-facing reply.

WORKSPACE RULES:
- Workspaces: "user" (user's live desktop) and "agent" (isolated Agent Desktop).
- Default to the agent desktop when the task does NOT require the user's live desktop and the user did not explicitly request the user desktop.
- Examples that usually belong on the agent desktop: background installs, downloads, browsing, and long-running tasks.
- Use the user desktop for tasks tied to the user's active apps, or when they need to see or interact with the result directly.
- Switch using action `switch_workspace` with params {{"workspace": "user"|"agent"}}.

AVAILABLE SKILLS (HYBRID MODE):
- The agent has "Skills" that use APIs instead of UI interaction. Use them PREFERENTIALLY for reliability.
- Skill: "media" (or "spotify")
  - method: "play" (params: {{"query": "song name"}}) -> Plays music/media.
  - method: "pause" -> Pauses.
  - method: "next" -> Skips track.
  - method: "previous" -> Previous track.
  - method: "status" -> Gets current media status.
- Skill: "browser"
  - method: "open" (params: {{"url": "google.com"}}) -> Opens URL in default browser.
  - method: "search" (params: {{"query": "search term"}}) -> Opens Google search.
- Skill: "system"
  - method: "volume" (params: {{"action": "up"|"down"|"mute"}}) -> Controls volume.
  - method: "lock" -> Locks the PC.
  - method: "minimize" -> Minimizes all windows (Show Desktop).
  - method: "settings" (params: {{"page": "display"}}) -> Opens Windows Settings.
- Skill: "timer"
  - method: "timer" -> Opens Windows Clock (Timer).
  - method: "alarm" -> Opens Windows Clock (Alarm).
  - method: "stopwatch" -> Opens Windows Clock (Stopwatch).

AVAILABLE ACTIONS:
- click: Click on a UI element by ID. Params: {{"element_id": <int>}}
- right_click: Right-click on a UI element by ID (opens context menu). Params: {{"element_id": <int>}}
- type_text: Type text into focused field. Params: {{"text": "<string>"}}
- press_key: Press a keyboard key (enter, tab, esc, win, etc.). Params: {{"key": "<string>"}}
- key_combo: Press key combination. Params: {{"keys": ["ctrl", "c"]}}
- wait: Wait for seconds. Params: {{"seconds": <int>}}
- search_web: Search the web. Params: {{"query": "<string>"}}
- open_app: Open an application via Start menu/Run. Params: {{"app_name": "<string>"}}
- magnify: Zoom in on a specific area to see small icons/text. Params: {{"element_id": <int>, "zoom_level": 2.0}}
- reply: Just answer the user's question directly. Params: {{"text": "<string>"}}
- call_skill: Execute a skill function. Params: {{"skill": "media", "method": "play", "args": {{"query": "..."}}}}
- switch_workspace: Switch between desktops. Params: {{"workspace": "user"|"agent"}}

TURBO MODE RULES:
- If {turbo_status} == ENABLED, you SHOULD combine multiple stable steps into 'action_sequence'.
- Example: Click 'Search', Wait 0.5s, Type 'Notepad', Press 'Enter'.
- Do NOT sequence actions if the first action changes the screen drastically (like opening a new window) and you need to see the result before acting.
- In 'action_sequence', the 'action_type' of the parent JSON MUST be "sequence".

FUNCTION CALLING:
- Use the tool `emit_action` to return your decision.
- Do NOT output JSON text directly.
- Include reasoning and control flags in the tool arguments.

CRITICAL GUIDELINES:
1. **ID Precision**: You MUST use the `element_id` from the [Annotated Screen] or the provided list. Do not hallucinate IDs.
2. **Launch First**: If the user wants to open an app (e.g., "Open Notepad"), always use `open_app` first. Do not try to find the icon manually unless `open_app` failed previously.
3. **Verification**: Set `task_complete` to true ONLY if you are sure the user's goal is fully achieved.
4. **Efficiency**: For trivial actions (like 'reply', 'wait', or simple confirmations) where a screenshot verification is overkill, SET `skip_verification: true`.
5. **Magnification**: If you cannot see an element clearly or the text is too small, use `magnify` on the approximate area.
6. **Robotics Fallback**: If OCR is failing to find an icon, mention "requesting robotics fallback" in your reasoning.
7. **Blind Mode Switching**: If you are entering a phase where you don't need to see the screen (e.g. typing a long text, waiting, or using skills/hotkeys), SET `needs_vision: false`. This will make the agent run faster by skipping screenshots for the next step.

"""

    parts = [
        {"text": prompt_text},
        _image_to_part(clean_image, res=media_resolution),
        _image_to_part(annotated_image, res=media_resolution),
    ]
    if reference_sheet:
        parts.append(_image_to_part(reference_sheet, res=media_resolution))

    history_manager = history if isinstance(history, ConversationHistory) else ConversationHistory()
    history_manager.add_user_message(parts)

    tool_registry = create_planning_registry()

    try:
        response, tool_records = get_gemini_client().generate_with_tools(
            contents=history_manager.get_messages_for_api(),
            tool_registry=tool_registry,
            response_schema=None,
        )

        model_part = response.candidates[0].content if response.candidates else None
        if model_part:
            history_manager.add_model_response(model_part.parts)

        action = get_action_from_tool_calls(tool_records)
        if action is None:
            return None

        return action, model_part
    except Exception:
        logger.exception("Error in plan_task")
        return None


def plan_task_blind(
    user_command: str,
    task_context: Optional[str] = None,
    history: Optional[ConversationHistory] = None,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Planning for 'Blind Mode' (No Vision).
    The agent attempts to solve the task using only OS skills, hotkeys, and shell commands.
    If it realizes it needs to see the screen (e.g. to click a button), it returns needs_vision=True.
    """

    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"
    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES"
        if agent_desktop_available
        else "AGENT DESKTOP AVAILABLE: NO"
    )

    prompt_text = f"""
You are Pixel Pilot operating in BLIND mode. You can control the computer using keyboard shortcuts, system skills, and commands, BUT YOU CANNOT SEE THE SCREEN.

USER COMMAND: "{user_command}"
{context_section}
{workspace_section}
{agent_desktop_section}

TURBO MODE STATUS: {turbo_status}

YOUR GOAL:
Try to fulfill the user's request using ONLY the available blind tools.
However, you must be extremely CAUTIOUS. "Blind Mode" is efficient but risky.

COORDINATION RULES:
- You are the BLIND agent. A VISION agent exists and can take over when visual context is needed.
- To request VISION, set `needs_vision: true`.
- If the task can proceed safely without vision, keep `needs_vision: false`.
- You and the VISION agent must stay aware of the CURRENT WORKSPACE and can switch when needed.

IDENTITY RULE:
- If the user asks who you are or what you are, answer as the Pixel Pilot system (the overall desktop assistant app). Do NOT describe yourself as the BLIND agent in the user-facing reply.

WORKSPACE RULES:
- Workspaces: "user" (user's live desktop) and "agent" (isolated Agent Desktop).
- Default to the agent desktop when the task does NOT require the user's live desktop and the user did not explicitly request the user desktop.
- Examples that usually belong on the agent desktop: background installs, downloads, browsing, and long-running tasks.
- Use the user desktop for tasks tied to the user's active apps, or when they need to see or interact with the result directly.
- Switch using action `switch_workspace` with params {{"workspace": "user"|"agent"}}.

CRITICAL "FEAR OF FAILURE" PROTOCOL:
1. **Safety First**: If you are not 100% sure that the app is open, focused, and ready for input, REQUEST VISION (`needs_vision: true`). Do not assume state.
2. **Verify Completion**: If you are about to complete a task (like sending a message or saving a file), and you haven't recently seen the screen to confirm it worked, REQUEST VISION to verify.
3. **Future Thinking**: Before performing a blind action, ask: "Will I need to see the result of this immediately?" If yes, switch to vision *now*.
4. **No Guessing**: If the user asks to "click the login button", do NOT try to tab-navigate blindly unless you are extremely confident. Just request vision.
5. **Context Awareness**: If the previous step failed or had low confidence, do NOT continue blindly. Switch to vision.

TURBO MODE RULES:
- If {turbo_status} == ENABLED, you SHOULD combine multiple stable steps into 'action_sequence'.
- Example: Click 'Search', Wait 0.5s, Type 'Notepad', Press 'Enter'.
- Do NOT sequence actions if the first action changes the screen drastically (like opening a new window) and you need to see the result before acting.
- In 'action_sequence', the 'action_type' of the parent JSON MUST be "sequence".

AVAILABLE BLIND ACTIONS:
- type_text: Type text. Params: {{"text": "..."}} (Assumes correct field is focused!)
- press_key: Press key. Params: {{"key": "win"}}
- key_combo: Key combo. Params: {{"keys": ["ctrl", "c"]}}
- wait: Wait. Params: {{"seconds": 1}}
- open_app: Open app via Run/Start. Params: {{"app_name": "notepad"}}
- search_web: Google search. Params: {{"query": "..."}}
- reply: Answer user. Params: {{"text": "..."}}
- call_skill: Use a skill (Media, Browser, System, Timer). Params: {{"skill": "...", "method": "...", "args": {{...}}}}
- switch_workspace: Switch between desktops. Params: {{"workspace": "user"|"agent"}}

UNAVAILABLE ACTIONS (Requires Vision):
- click (You have no coordinates!)
- magnify

RESPONSE RULES:
- Default to Vision: If in doubt, set `needs_vision: true`.
- If the task is "Play music", use call_skill("media", "play", ...).
- If the task is "Open Notepad", use open_app("Notepad").
- If the task is "Click the Submit button", set `needs_vision: true`.
- If the task is "What is on my screen?", set `needs_vision: true`.
- When using `reply`, ALWAYS put the answer in `params.text` (not `message`).

FUNCTION CALLING:
- Use the tool `emit_action` to return your decision.
- Do NOT output JSON text directly.
- Include reasoning and control flags in the tool arguments.
"""
    parts = [{"text": prompt_text}]

    history_manager = history if isinstance(history, ConversationHistory) else ConversationHistory()
    history_manager.add_user_message(parts)

    tool_registry = create_planning_registry()

    try:
        response, tool_records = get_gemini_client().generate_with_tools(
            contents=history_manager.get_messages_for_api(),
            tool_registry=tool_registry,
            response_schema=None,
        )
        model_part = response.candidates[0].content if response.candidates else None
        if model_part:
            history_manager.add_model_response(model_part.parts)

        action = get_action_from_tool_calls(tool_records)
        if action is None:
            return None

        return action, model_part
    except Exception:
        logger.exception("Error in plan_task_blind")
        return None


def plan_task_blind_first_step(
    user_command: str,
    history: Optional[ConversationHistory] = None,
    current_workspace: str = "user",
    agent_desktop_available: bool = False,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    First-step blind planning focused only on workspace selection.
    Must decide the workspace before any vision handoff.
    """

    workspace_section = f"CURRENT WORKSPACE: {current_workspace}"
    agent_desktop_section = (
        "AGENT DESKTOP AVAILABLE: YES"
        if agent_desktop_available
        else "AGENT DESKTOP AVAILABLE: NO"
    )

    class FirstStepDecision(BaseModel):
        intent: str = Field(
            description="Either 'conversational' for pure small talk, or 'actionable' for tasks needing actions"
        )
        workspace: str = Field(
            description="Target workspace choice: 'user' or 'agent'"
        )
        reply_text: str = Field(
            default="",
            description="If intent is conversational, provide the reply text. Otherwise leave empty.",
        )
        reasoning: str = Field(description="Why this intent/workspace was chosen")
        confidence: float = Field(
            default=1.0, description="Confidence score from 0.0 to 1.0"
        )

    prompt_text = f"""
You are Pixel Pilot inside the Pixel Pilot application. This is the FIRST STEP ONLY.

USER COMMAND: "{user_command}"
{workspace_section}
{agent_desktop_section}

YOUR ONLY JOB ON THIS STEP:
- Decide whether the message is PURELY conversational or an actionable task.
- If conversational, draft a short reply and do not switch workspaces.
- If actionable, select the correct workspace ("user" or "agent").
- Do NOT request vision on this step.

WORKSPACE RULES:
- Default to the agent desktop when the task does NOT require the user's live desktop and the user did not explicitly request the user desktop.
- Examples for agent desktop: installs, downloads, background tasks, browsing, long-running tasks.
- Use the user desktop for tasks tied to the user's active apps or when they must see or interact with the result directly.
- If the user explicitly mentions a desktop/workspace, follow it.
- If the task is conversational or informational (greeting, small talk, "who are you", "what can you do", help questions, general facts) and can be answered without UI action, mark intent as conversational.
- If the command contains any action intent (open, launch, run, click, type, search, install, play, open app, switch workspace, etc.), mark intent as actionable.
- If you are unsure, choose the safer workspace (agent for background/browsing/automation, user for their active apps or visibility).

RESPONSE FORMAT:
Return JSON that matches the schema. Set:
- intent: "conversational" or "actionable"
- workspace: "user" or "agent"
- reply_text: only if intent is conversational, otherwise empty
- reasoning: concise rationale
- confidence: 0.0 to 1.0
"""
    parts = [{"text": prompt_text}]

    history_manager = history if isinstance(history, ConversationHistory) else ConversationHistory()
    history_manager.add_user_message(parts)

    try:
        response = get_gemini_client().generate_structured(
            contents=history_manager.get_messages_for_api(),
            response_schema=FirstStepDecision.model_json_schema(),
        )
        result = FirstStepDecision.model_validate_json(response.text)
        intent = (result.intent or "").strip().lower()
        workspace = (result.workspace or current_workspace).strip().lower()

        if intent == "conversational":
            action = {
                "action_type": "reply",
                "params": {"text": result.reply_text or ""},
                "reasoning": result.reasoning,
                "confidence": result.confidence,
                "task_complete": True,
                "skip_verification": True,
                "needs_vision": False,
            }
        else:
            if workspace not in {"user", "agent"}:
                workspace = current_workspace
            action = {
                "action_type": "switch_workspace",
                "params": {"workspace": workspace},
                "reasoning": result.reasoning,
                "confidence": result.confidence,
                "task_complete": False,
                "skip_verification": True,
                "needs_vision": False,
            }

        return action, None
    except Exception:
        logger.exception("Error in plan_task_blind_first_step")
        return None
