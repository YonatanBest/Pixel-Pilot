import logging
from typing import Any, Dict, List, Optional

from services.gemini.tools import ToolRegistry

logger = logging.getLogger(__name__)


def create_action_registry() -> ToolRegistry:
    """Create a ToolRegistry with all agent action tools registered.
    
    Returns:
        ToolRegistry populated with all available agent actions.
    """
    registry = ToolRegistry()
    
    # Register all action tools
    registry.register(click, "Click on a UI element identified by its ID number")
    registry.register(right_click, "Right-click on a UI element to open context menu")
    registry.register(type_text, "Type text into the currently focused field")
    registry.register(press_key, "Press a single keyboard key")
    registry.register(key_combo, "Press a keyboard shortcut combination")
    registry.register(wait, "Wait for a specified number of seconds")
    registry.register(search_web, "Search the web using Google")
    registry.register(open_app, "Open an application by name")
    registry.register(magnify, "Zoom in on a UI element for better precision")
    registry.register(reply, "Send a text response to the user")
    registry.register(call_skill, "Execute a predefined skill (media, browser, system, timer)")
    registry.register(switch_workspace, "Switch between user and agent desktop workspaces")
    registry.register(task_complete, "Signal that the user's task has been completed")
    registry.register(request_vision, "Request a new screenshot to see current screen state")

    registry.register(
        emit_action,
        "Emit a fully specified action plan with reasoning and control flags",
    )
    
    return registry


def create_planning_registry() -> ToolRegistry:
    """Create a ToolRegistry for planning decisions only.

    Returns:
        ToolRegistry populated with the emit_action tool.
    """
    registry = ToolRegistry()
    registry.register(
        emit_action,
        "Emit a fully specified action plan with reasoning and control flags",
        parameter_overrides={
            "action_type": {
                "description": "Primary action type to execute",
                "enum": [
                    "click",
                    "right_click",
                    "type_text",
                    "press_key",
                    "key_combo",
                    "open_app",
                    "search_web",
                    "wait",
                    "magnify",
                    "reply",
                    "call_skill",
                    "switch_workspace",
                    "sequence",
                ],
            },
            "action_sequence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action_type": {"type": "string"},
                        "params": {"type": "object"},
                        "reasoning": {"type": "string"},
                        "confidence": {"type": "number"},
                        "needs_vision": {"type": "boolean"},
                        "expected_result": {"type": "string"},
                    },
                    "required": ["action_type"],
                },
                "description": "List of action objects for turbo mode execution",
            },
        },
    )
    return registry


def click(element_id: int) -> Dict[str, Any]:
    """Click on a UI element.
    
    Args:
        element_id: The ID number of the element to click (from the annotated screenshot)
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "click",
        "params": {"element_id": element_id}
    }


def right_click(element_id: int) -> Dict[str, Any]:
    """Right-click on a UI element to open its context menu.
    
    Args:
        element_id: The ID number of the element to right-click
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "right_click",
        "params": {"element_id": element_id}
    }


def type_text(text: str) -> Dict[str, Any]:
    """Type text into the currently focused input field.
    
    Args:
        text: The text to type
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "type_text",
        "params": {"text": text}
    }


def press_key(key: str) -> Dict[str, Any]:
    """Press a single keyboard key.
    
    Args:
        key: The key to press (e.g., 'enter', 'escape', 'tab', 'win', 'f1')
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "press_key",
        "params": {"key": key}
    }


def key_combo(keys: List[str]) -> Dict[str, Any]:
    """Press a keyboard shortcut combination.
    
    Args:
        keys: List of keys to press together (e.g., ['ctrl', 'c'] for copy)
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "key_combo",
        "params": {"keys": keys}
    }


def wait(seconds: float) -> Dict[str, Any]:
    """Wait for a specified duration.
    
    Args:
        seconds: Number of seconds to wait (can be decimal, e.g., 0.5)
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "wait",
        "params": {"seconds": seconds}
    }


def search_web(query: str) -> Dict[str, Any]:
    """Search the web using Google.
    
    Args:
        query: The search query to look up
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "search_web",
        "params": {"query": query}
    }


def open_app(app_name: str) -> Dict[str, Any]:
    """Open an application by name.
    
    Args:
        app_name: Name of the application to open (e.g., 'notepad', 'chrome', 'calculator')
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "open_app",
        "params": {"app_name": app_name}
    }


def magnify(element_id: int) -> Dict[str, Any]:
    """Zoom in on a UI element for better precision clicking.
    
    Use this when elements are too small or clustered together.
    
    Args:
        element_id: The ID of the element to magnify around
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "magnify",
        "params": {"element_id": element_id}
    }


def reply(text: str) -> Dict[str, Any]:
    """Send a text response to the user.
    
    Use this for answering questions, providing information, or acknowledging completion.
    
    Args:
        text: The message to display to the user
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "reply",
        "params": {"text": text}
    }


def call_skill(skill: str, method: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a predefined system skill.
    
    Available skills:
    - media: Control media playback (play, pause, next, previous, volume)
    - browser: Browser automation (open_url, new_tab, close_tab)
    - system: System operations (screenshot, lock, shutdown, restart)
    - timer: Set timers and alarms
    
    Args:
        skill: The skill category (media, browser, system, timer)
        method: The specific method to call within the skill
        args: Optional arguments for the method
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "call_skill",
        "params": {
            "skill": skill,
            "method": method,
            "args": args or {}
        }
    }


def switch_workspace(workspace: str) -> Dict[str, Any]:
    """Switch between desktop workspaces.
    
    Args:
        workspace: Target workspace - 'user' for main desktop, 'agent' for isolated agent desktop
    
    Returns:
        Action specification for the agent executor
    """
    return {
        "action_type": "switch_workspace",
        "params": {"workspace": workspace}
    }


def task_complete(summary: str) -> Dict[str, Any]:
    """Signal that the user's task has been completed.
    
    Call this when the requested task is done and no more actions are needed.
    
    Args:
        summary: Brief description of what was accomplished
    
    Returns:
        Action specification with task_complete flag
    """
    return {
        "action_type": "reply",
        "params": {"text": summary},
        "task_complete": True
    }


def request_vision(reason: str) -> Dict[str, Any]:
    """Request a new screenshot to see the current screen state.
    
    Use this when you need to see what's on screen before taking action.
    
    Args:
        reason: Why you need to see the screen
    
    Returns:
        Action specification requesting vision
    """
    return {
        "action_type": "wait",
        "params": {"seconds": 0},
        "needs_vision": True,
        "reasoning": reason
    }


def emit_action(
    action_type: str,
    params: Dict[str, Any],
    reasoning: str,
    confidence: float = 1.0,
    clarification_needed: bool = False,
    clarification_question: str = "",
    task_complete: bool = False,
    skip_verification: bool = False,
    needs_vision: bool = True,
    expected_result: Optional[str] = None,
    action_sequence: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Emit a fully specified action plan.

    Args:
        action_type: Primary action type (or "sequence" for action_sequence)
        params: Parameters for the action
        reasoning: Rationale for the action
        confidence: Confidence score 0.0-1.0
        clarification_needed: Whether user clarification is required
        clarification_question: Optional clarification question to ask
        task_complete: Whether the task is complete
        skip_verification: Whether verification can be skipped
        needs_vision: Whether a screenshot is required next step
        expected_result: Expected outcome after the action
        action_sequence: Optional list of sub-actions for turbo mode

    Returns:
        Action specification for the agent executor
    """
    action: Dict[str, Any] = {
        "action_type": action_type,
        "params": params,
        "reasoning": reasoning,
        "confidence": confidence,
        "clarification_needed": clarification_needed,
        "task_complete": task_complete,
        "skip_verification": skip_verification,
        "needs_vision": needs_vision,
    }

    if clarification_question:
        action["clarification_question"] = clarification_question
    if expected_result is not None:
        action["expected_result"] = expected_result
    if action_sequence:
        action["action_sequence"] = action_sequence

    return action

def parse_tool_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a tool result into a standard action format.
    
    Ensures all required fields are present for the agent executor.
    
    Args:
        result: Raw result from tool execution
    
    Returns:
        Normalized action dictionary with all required fields
    """
    action = {
        "action_type": result.get("action_type", "unknown"),
        "params": result.get("params", {}),
        "reasoning": result.get("reasoning", ""),
        "confidence": result.get("confidence", 1.0),
        "clarification_needed": result.get("clarification_needed", False),
        "clarification_question": result.get("clarification_question"),
        "task_complete": result.get("task_complete", False),
        "skip_verification": result.get("skip_verification", False),
        "needs_vision": result.get("needs_vision", False),
        "expected_result": result.get("expected_result"),
        "action_sequence": result.get("action_sequence"),
    }
    return action


def get_action_from_tool_calls(tool_records: List[Dict]) -> Optional[Dict[str, Any]]:
    """Extract the primary action from a list of tool call records.
    
    Args:
        tool_records: List of tool call records from generate_with_tools
    
    Returns:
        The primary action to execute, or None if no valid action found
    """
    if not tool_records:
        return None
    
    for record in reversed(tool_records):
        if record.get("result") and not record.get("error"):
            result = record["result"]
            if isinstance(result, dict) and "action_type" in result:
                return parse_tool_result(result)
    
    return None
