import json
import base64
import io
from typing import Any, Dict, List, Optional
from PIL import Image
from agent.brain import get_model
from pydantic import BaseModel, Field


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
        print(f"Error loading images for verification: {e}")
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

    elements_str = "\n".join(
        [f"ID {el['id']}: {el['type']} '{el['label']}'" for el in screen_elements[:100]]
    )

    history_str = "\n".join(
        [
            f"Step {i + 1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(task_history)
        ]
    )

    prompt_text = f"""
You are an AI OS Agent tasked with VERIFYING task completion.

ORIGINAL USER COMMAND: "{user_command}"
EXPECTED RESULT: "{expected_result}"

ACTIONS TAKEN:
{history_str}

CURRENT SCREEN ELEMENTS:
{elements_str}

ATTACHMENTS:
1. [Current Screen]: Shows the current state after all actions
2. [Annotated Screen]: Shows UI elements with IDs (use for reference)

YOUR TASK:
Carefully analyze the current screen state and determine if the user's original command
has been ACTUALLY COMPLETED.

VERIFICATION CRITERIA:
- Does the screen show evidence that the task was completed?
- Are the expected UI elements visible? (e.g., if "Open Notepad", is Notepad visible?)
- Does the current state match what was expected?

RESPONSE FORMAT:
Return a JSON object satisfying the VerificationResult schema.
"""

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
        model = get_model()
        response_data = model.generate_content(
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": VerificationResult.model_json_schema(),
            },
        )

        result_obj = VerificationResult.model_validate_json(response_data["text"])
        return result_obj.model_dump()

    except Exception as e:
        print(f"Error during verification: {e}")
        return None
