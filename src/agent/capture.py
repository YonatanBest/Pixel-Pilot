import hashlib
import os
import shutil
import time
import cv2
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import pyautogui
import mss
import logging
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend_client import get_client
from config import Config
from tools.eye import LocalCVEye
from agent.prompts import UAC_APPROVAL_PROMPT
from uac.ipc import cleanup_request_artifacts, create_request, write_response

logger = logging.getLogger("pixelpilot.capture")
client = get_client()


class _ModelWrapper:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def generate_content(self, contents, config=None):
        logger.info("Waiting for AI response...")
        return client.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )


def _get_model():
    return _ModelWrapper(Config.GEMINI_MODEL)


def _create_reference_sheet(crops):
    if not crops:
        return None

    cell_w, cell_h = 100, 100
    cols = 8
    rows = (len(crops) + cols - 1) // cols
    sheet = PIL.Image.new("RGB", (cols * cell_w, rows * cell_h), color=(30, 30, 30))
    draw = PIL.ImageDraw.Draw(sheet)

    try:
        font = PIL.ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = PIL.ImageFont.load_default()

    for index, item in enumerate(crops):
        image = item["image"]
        image.thumbnail((cell_w - 10, cell_h - 30))
        col, row = index % cols, index // cols
        x = col * cell_w + 5
        y = row * cell_h + 30
        sheet.paste(image, (x, y))
        draw.text((x, y - 25), f"ID:{item['id']}", fill=(0, 255, 0), font=font)

    return sheet

class ScreenCapture:
    """
    Handles screen capture, UAC detection, and image analysis.
    """
    def __init__(self, agent_orchestrator):
        self.agent = agent_orchestrator
        self.local_eye = LocalCVEye()
    
    def log(self, message: str):
        self.agent.log(message)

    def progress(self, message: str):
        self.agent.log(message)
        chat_window = getattr(self.agent, "chat_window", None)
        if chat_window and hasattr(chat_window, "add_activity_message"):
            try:
                chat_window.add_activity_message(str(message))
            except Exception:
                pass

    @property
    def desktop_manager(self):
        if self.agent.active_workspace == "agent":
            return self.agent.desktop_manager
        return None

    @property
    def last_hash(self) -> str:
        return getattr(self, "_last_hash", "")

    def _get_screen_hash(self, image_path: str) -> str:
        """Calculate a hash of the screenshot to detect changes."""
        try:
            with open(image_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _is_screen_changed(self, current_path: str, previous_hash: str) -> bool:
        """Check if the screen has changed significantly."""
        if not previous_hash:
            return True
        current_hash = self._get_screen_hash(current_path)
        return current_hash != previous_hash

    def _is_black_screen(self, image: PIL.Image.Image) -> bool:
        """Check if the image is mostly black (UAC secure desktop symptom)."""
        try:
            extrema = image.convert("L").getextrema()
            if extrema[1] < 20:
                return True
            return False
        except Exception:
            return False

    def _capture_raw_image(self) -> PIL.Image.Image:
        """
        Capture raw screen execution-style.
        """
        if self.desktop_manager:
            try:
                img = self.desktop_manager.capture_desktop()
                if img is not None:
                    return img
            except Exception as e:
                logging.getLogger("pixelpilot.agent").debug(
                    f"Agent Desktop capture failed: {e}"
                )

        try:
            if not self.agent.chat_window:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    sct_img = sct.grab(monitor)
                    img = PIL.Image.frombytes(
                        "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
                    )
                    return img
        except Exception:
            pass
        return pyautogui.screenshot()

    def _ask_uac_brain(self, image_path: str) -> dict[str, Any]:
        """Ask the Brain specifically about a UAC prompt."""
        fallback = {
            "allow": False,
            "reasoning": "Unable to verify the secure desktop prompt safely.",
        }
        try:
            self.log("   [UAC] Asking AI for approval assessment...")

            class UACApproval(BaseModel):
                allow: bool = Field(description="Whether the elevation request should be allowed.")
                reasoning: str = Field(
                    description="Why this approval choice was made. Analyze the publisher and program name."
                )

            prompt = UAC_APPROVAL_PROMPT

            def img_to_dict(img_obj):
                import io
                import base64
                img_byte_arr = io.BytesIO()
                img_obj.save(img_byte_arr, format="PNG")
                return {
                    "mime_type": "image/png",
                    "data": base64.b64encode(img_byte_arr.getvalue()).decode("utf-8"),
                }

            model = _get_model()
            img = PIL.Image.open(image_path)

            contents = [{"role": "user", "parts": [{"text": prompt}, img_to_dict(img)]}]

            response_data = model.generate_content(
                contents,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": UACApproval.model_json_schema(),
                },
            )

            try:
                result = json.loads(response_data["text"])
                allow = result.get("allow")
                if not isinstance(allow, bool):
                    return fallback
                reasoning = result.get("reasoning", "No reasoning provided")
                logger.info(f"UAC Reasoning: {reasoning}")

                return {
                    "allow": allow,
                    "reasoning": str(reasoning or "").strip() or fallback["reasoning"],
                }
            except Exception:
                return fallback

        except Exception as e:
            logger.error(f"UAC approval assessment failed: {e}")
            fallback["reasoning"] = f"AI approval assessment failed: {e}"
            return fallback

    def _check_and_trigger_uac(self):
        """
        Create a one-shot UAC request for the orchestrator/helper pair.
        """
        try:
            return create_request()
        except Exception as e:
            logger.error(f"Could not start UAC Orchestrator: {e}")
            return None

    def _confirm_uac_allow(self, reasoning: str) -> bool:
        chat_window = getattr(self.agent, "chat_window", None)
        if not chat_window or not hasattr(chat_window, "ask_confirmation"):
            self.log("UAC confirmation unavailable in this session. Defaulting to DENY.")
            return False

        prompt = (
            "Windows is asking for elevated access on the secure desktop.\n\n"
            f"AI assessment:\n{str(reasoning or 'No reasoning provided.')}\n\n"
            "Allow this elevation request?"
        )
        try:
            return bool(chat_window.ask_confirmation("Approve Elevation", prompt))
        except Exception as exc:
            logger.error("Failed to collect UAC confirmation: %s", exc)
            return False

    def _create_annotated_image(self, original_path, elements, output_path):
        """Draw Green IDs on the screenshot for Gemini."""
        try:
            img = cv2.imread(original_path)
            if img is None:
                return

            for el in elements:
                x, y = int(el["x"]), int(el["y"])
                w = int(el.get("w", 20))
                h = int(el.get("h", 20))

                cv2.rectangle(
                    img,
                    (x - w // 2, y - h // 2),
                    (x + w // 2, y + h // 2),
                    (0, 255, 0),
                    2,
                )

                label = str(el["id"])
                cv2.rectangle(img, (x, y - 25), (x + 30, y), (0, 0, 0), -1)
                cv2.putText(
                    img,
                    label,
                    (x, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

            cv2.imwrite(output_path, img)
        except Exception as e:
            logger.debug(f"Could not create annotated image: {e}")

    def _safe_get_local_elements(
        self,
        screenshot_path: str,
        progress_callback=None,
    ) -> List[Dict]:
        """Run Eye extraction safely and return an empty list on failure."""
        try:
            return (
                self.local_eye.get_screen_elements(
                    screenshot_path, progress_callback=progress_callback
                )
                or []
            )
        except Exception as e:
            logger.error(f"Eye extraction failed: {e}")
            return []

    def _safe_get_robotics_elements(
        self, screenshot_path: str, task_context: Optional[str], current_step: Optional[str]
    ) -> List[Dict]:
        """Run Robotics-ER safely and return an empty list on failure."""
        if not self.agent.robotics_eye:
            return []
        try:
            if Config.ROBOTICS_USE_BOUNDING_BOXES:
                return self.agent.robotics_eye.get_screen_elements_with_boxes(
                    screenshot_path,
                    max_elements=Config.ROBOTICS_MAX_ELEMENTS,
                ) or []
            return self.agent.robotics_eye.get_screen_elements(
                screenshot_path,
                max_elements=Config.ROBOTICS_MAX_ELEMENTS,
                task_context=task_context,
                current_step=current_step,
            ) or []
        except Exception as e:
            logger.error(f"Robotics-ER extraction failed: {e}")
            return []

    def capture_screen(
        self, force_robotics: bool = False
    ) -> tuple[List[Dict], Optional[Any]]:
        """
        Capture and analyze the current screen.
        """
        self.agent._ensure_workspace_active()

        if self.agent.chat_window and self.agent.active_workspace == "user":
            self.agent.chat_window.prepare_for_screenshot()

        self.agent._check_stop()
        self.progress("Taking screenshot...")

        max_retries = 3
        capture_successful = False

        for attempt in range(max_retries):
            self.agent._check_stop()
            try:
                if os.path.exists(Config.SCREENSHOT_PATH):
                    try:
                        os.remove(Config.SCREENSHOT_PATH)
                    except Exception:
                        pass

                time.sleep(0.1)

                full_img = self._capture_raw_image()

                self.agent._check_stop()

                if self.agent.is_magnified and self.agent.zoom_center:
                    w, h = full_img.size

                    crop_w = int(w / self.agent.zoom_level)
                    crop_h = int(h / self.agent.zoom_level)

                    left = max(0, int(self.agent.zoom_center[0] - crop_w // 2))
                    top = max(0, int(self.agent.zoom_center[1] - crop_h // 2))
                    right = min(w, left + crop_w)
                    bottom = min(h, top + crop_h)

                    if right == w:
                        left = max(0, w - crop_w)
                    if bottom == h:
                        top = max(0, h - crop_h)

                    self.agent.zoom_offset = (left, top)
                    zoom_crop = full_img.crop((left, top, right, bottom))

                    magnified_img = zoom_crop.resize(
                        (w, h), PIL.Image.Resampling.LANCZOS
                    )
                    magnified_img.save(Config.SCREENSHOT_PATH)
                else:
                    full_img.save(Config.SCREENSHOT_PATH)
                    self.agent.zoom_offset = (0, 0)

                self._last_hash = self._get_screen_hash(Config.SCREENSHOT_PATH)

                if self._is_black_screen(full_img):
                    raise Exception("Screen is black (likely Secure Desktop/UAC)")

                time.sleep(Config.SCREENSHOT_DELAY)

                if (
                    os.path.exists(Config.SCREENSHOT_PATH)
                    and os.path.getsize(Config.SCREENSHOT_PATH) > 0
                ):
                    capture_successful = True
                    break

            except Exception as e:
                err_msg = str(e)
                logger.warning(f"Screenshot attempt {attempt + 1} failed: {err_msg}")

                if (
                    "OpenInputDesktop failed" in err_msg
                    or "screen grab failed" in err_msg
                    or "Access is denied" in err_msg
                    or "Screen is black" in err_msg
                ):
                    self.log(
                        "UAC DETECTED: Standard screenshot failed. Initiating Orchestrator protocol..."
                    )

                    request_payload = self._check_and_trigger_uac()
                    if not request_payload:
                        time.sleep(Config.UAC_IPC_POLL_INTERVAL_SECONDS)
                        continue

                    self.log(
                        "WAITING: allowing UAC Agent to run on Secure Desktop..."
                    )

                    uac_snap_path = str(request_payload.get("snapshot_path") or "").strip()
                    found_snapshot = False
                    max_checks = max(
                        1,
                        int(
                            max(
                                float(Config.UAC_IPC_POLL_INTERVAL_SECONDS),
                                float(Config.UAC_RESPONSE_TIMEOUT_SECONDS),
                            )
                            / float(Config.UAC_IPC_POLL_INTERVAL_SECONDS)
                        ),
                    )
                    for _ in range(max_checks):
                        if uac_snap_path and os.path.exists(uac_snap_path):
                            found_snapshot = True
                            break
                        time.sleep(Config.UAC_IPC_POLL_INTERVAL_SECONDS)

                    if found_snapshot:
                        self.log("Secure Desktop snapshot found!")
                        try:
                            self.agent._check_stop()
                            assessment = self._ask_uac_brain(uac_snap_path)
                            suggested_allow = bool(assessment.get("allow"))
                            reasoning = str(assessment.get("reasoning") or "").strip()
                            suggested_label = "ALLOW" if suggested_allow else "DENY"
                            self.log(f"UAC ASSESSMENT: AI suggests {suggested_label}")

                            user_confirmed = False
                            final_allow = False
                            if suggested_allow:
                                user_confirmed = self._confirm_uac_allow(reasoning)
                                final_allow = bool(user_confirmed)
                            else:
                                self.log("UAC ASSESSMENT: defaulting to DENY.")

                            write_response(
                                request_payload,
                                allow=final_allow,
                                user_confirmed=user_confirmed,
                                reasoning=reasoning,
                            )
                            final_label = "ALLOW" if final_allow else "DENY"
                            self.log(f"UAC RESPONSE: {final_label}")

                            time.sleep(Config.UAC_CAPTURE_SETTLE_AFTER_RESPONSE_SECONDS)

                            shutil.copy(uac_snap_path, Config.SCREENSHOT_PATH)
                            capture_successful = True
                            break
                        except Exception as copy_err:
                            logger.error(f"Failed during UAC handling: {copy_err}")
                        finally:
                            cleanup_request_artifacts(request_payload)
                    else:
                        logger.warning(
                            "No UAC snapshot found. The Orchestrator may not have launched the agent."
                        )
                        cleanup_request_artifacts(request_payload)

                time.sleep(Config.UAC_IPC_POLL_INTERVAL_SECONDS)

        if self.agent.chat_window and self.agent.active_workspace == "user":
            self.agent.chat_window.restore_after_screenshot()

        if not capture_successful or not os.path.exists(Config.SCREENSHOT_PATH):
            logger.error("Could not capture screen after multiple attempts.")
            return [], None

        elements = []
        vision_method = "None"

        if not Config.USE_ROBOTICS_EYE or Config.LAZY_VISION:
            elements = self._safe_get_local_elements(
                Config.SCREENSHOT_PATH,
                progress_callback=self.progress,
            )
            vision_method = self.local_eye.current_vision_label()

        needs_robotics = force_robotics
        if Config.LAZY_VISION and not force_robotics:
            has_unknown_icons = any(
                el.get("label") == "unknown_icon" for el in elements
            )
            text_count = sum(1 for el in elements if el["type"] == "text")

            if (text_count < 1 and len(elements) < 2) or (
                has_unknown_icons and text_count < 1
            ):
                self.log(
                    "Lazy vision fallback to Robotics (sparse context)..."
                )
                needs_robotics = True

        if Config.USE_ROBOTICS_EYE and (needs_robotics or not Config.LAZY_VISION):
            self.log("Scanning UI elements with Gemini Robotics-ER...")
            task_context = self.agent.current_task if self.agent.current_task else None
            current_step = None
            if self.agent.task_history:
                last_action = next(
                    (
                        h
                        for h in reversed(self.agent.task_history)
                        if isinstance(h, dict) and "action_type" in h
                    ),
                    None,
                )
                if last_action:
                    current_step = (
                        f"{last_action['action_type']}: {last_action['reasoning']}"
                    )

            if self.agent.robotics_eye:
                robo_elements = self._safe_get_robotics_elements(
                    Config.SCREENSHOT_PATH, task_context, current_step
                )
                if robo_elements:
                    elements = robo_elements
                    vision_method = "Gemini Robotics-ER"
                else:
                    logger.warning("Robotics-ER returned no usable elements. Falling back to OCR.")
                    if not elements:
                        elements = self._safe_get_local_elements(
                            Config.SCREENSHOT_PATH,
                            progress_callback=self.progress,
                        )
                        vision_method = f"{self.local_eye.current_vision_label()} (Fallback)"
            else:
                logger.warning("Robotics Eye requested but not initialized. Falling back to OCR.")
                if not elements:
                    elements = self._safe_get_local_elements(
                        Config.SCREENSHOT_PATH,
                        progress_callback=self.progress,
                    )
                    vision_method = f"{self.local_eye.current_vision_label()} (Fallback)"

        self._create_annotated_image(
            Config.SCREENSHOT_PATH, elements, Config.DEBUG_PATH
        )

        reference_sheet = None
        if Config.ENABLE_REFERENCE_SHEET:
            try:
                crops = self.local_eye.get_crops_for_context(
                    Config.SCREENSHOT_PATH, elements
                )
                reference_sheet = _create_reference_sheet(crops)
                if reference_sheet and Config.SAVE_SCREENSHOTS:
                    reference_sheet.save(Config.REF_PATH)
            except Exception as e:
                logger.error(f"Reference sheet creation failed: {e}")

        self.progress("Capture complete.")
        self.log(f"Found {len(elements)} UI elements ({vision_method})")
        return elements, reference_sheet
