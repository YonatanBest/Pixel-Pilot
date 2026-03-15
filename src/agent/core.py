import os
import ctypes
import pyautogui
import logging
import re
import time
import threading
from typing import Any, Dict, List, Optional
from tools.app_indexer import AppIndexer
from agent.brain import plan_task
from agent.clarification import ClarificationManager
from config import Config, OperationMode
from tools.eye import LocalCVEye
from tools.keyboard import KeyboardController
from tools.loop import LoopDetector
from skills import MediaSkill, BrowserSkill, SystemSkill, TimerSkill
from tools.mouse import click_at
from agent.actions import ActionExecutor
from agent.action_guard import ActionGuard
from agent.capture import ScreenCapture
from agent.guidance import GuidanceSession
from agent.verify import verify_task_blind, verify_task_completion
import tools.ui_automation as ui_automation

class StopRequested(Exception):
    pass

class AgentOrchestrator:
    """
    Main AI Agent that orchestrates multi-step task execution.
    Implements vision + control loop with three operation modes.
    Handles UAC (Secure Desktop) transitions automatically.
    """

    def __init__(self, mode: OperationMode = None, chat_window=None, robotics_eye=None):
        """
        Initialize the AI Agent.

        Args:
            mode: Operation mode (GUIDE, SAFE, AUTO). Defaults to config default.
            chat_window: Optional ChatWindow instance for GUI mode
            robotics_eye: Optional instance of GeminiRoboticsEye
        """
        self.mode = mode or Config.DEFAULT_MODE
        self.robotics_eye = robotics_eye
        self.local_eye = LocalCVEye()
        self.keyboard = KeyboardController()
        self.task_history = []
        self.current_task = None
        self.step_count = 0
        self.max_steps = Config.MAX_TASK_STEPS
        self.chat_window = chat_window

        self._stop_event = threading.Event()

        if Config.ENABLE_LOOP_DETECTION:
            self.loop_detector = LoopDetector(
                threshold=Config.LOOP_DETECTION_THRESHOLD,
                similarity_threshold=Config.LOOP_SCREEN_SIMILARITY_THRESHOLD,
            )
        else:
            self.loop_detector = None

        self.app_indexer = AppIndexer(
            cache_path=Config.APP_INDEX_PATH,
            auto_refresh=False,
            include_processes=Config.APP_INDEX_INCLUDE_PROCESSES,
        )
        if Config.APP_INDEX_AUTO_REFRESH:
            threading.Thread(target=self.app_indexer.refresh, daemon=True).start()

        self.media_skill = MediaSkill()
        self.browser_skill = BrowserSkill()
        self.system_skill = SystemSkill()
        self.timer_skill = TimerSkill()

        self.skills = {
            "media": self.media_skill,
            "spotify": self.media_skill,
            "browser": self.browser_skill,
            "system": self.system_skill,
            "timer": self.timer_skill,
        }

        self.clarification_manager = (
            ClarificationManager(chat_window=chat_window, mode=self.mode)
            if Config.ENABLE_CLARIFICATION
            else None
        )

        self.zoom_center = None
        self.zoom_level = 1.0
        self.zoom_offset = (0, 0)
        self.is_magnified = False
        self.model_history = []
        self.visual_memory = {}
        os.makedirs(Config.MEDIA_DIR, exist_ok=True)

        self.desktop_manager = None
        self.active_workspace = Config.DEFAULT_WORKSPACE
        
        self.action_executor = ActionExecutor(self)
        self.action_guard = ActionGuard()
        self.screen_capture = ScreenCapture(self)

        self.log(f"AI Agent initialized in {self.mode.value.upper()} mode")
        self.deferred_reply = None
        self.current_blind_snapshot = None

    def request_stop(self):
        self._stop_event.set()

    def set_mode(self, mode):
        """Update the operation mode."""
        self.mode = mode
        self.log(f"Mode changed to {mode.value.upper()}")

    def _check_stop(self):
        if self._stop_event.is_set():
            raise StopRequested()

    def execute_action(self, action: Dict[str, Any], elements: List[Dict]) -> Dict[str, Any]:
        """
        Execute a single action via ActionExecutor.
        """
        return self.action_executor.execute(action, elements)

    def _goal_terms(self) -> List[str]:
        words = re.findall(r"[a-zA-Z0-9]+", (self.current_task or "").lower())
        stop = {
            "open",
            "the",
            "a",
            "an",
            "in",
            "on",
            "to",
            "my",
            "of",
            "and",
            "for",
            "please",
        }
        return [word for word in words if len(word) >= 3 and word not in stop]

    def _capture_blind_snapshot(self) -> Dict[str, Any]:
        self._ensure_workspace_active()
        snapshot = ui_automation.get_snapshot(
            self.active_workspace,
            self.desktop_manager,
            Config.UIA_MAX_ELEMENTS,
            self._goal_terms(),
        )
        self.current_blind_snapshot = snapshot
        if not snapshot.get("available", False):
            self.log(
                f"UI Automation unavailable on {self.active_workspace} workspace: "
                f"{snapshot.get('error', 'unknown error')}"
            )
        return snapshot

    def _append_history_message(
        self,
        text: str,
        *,
        blind_only: bool = False,
        uia_only: bool = False,
    ) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        entry = {"role": "user", "parts": [{"text": clean}]}
        if blind_only:
            entry["blind_only"] = True
        if uia_only:
            entry["uia_only"] = True
        self.task_history.append(entry)

    def _append_blind_observation(self, action: Dict[str, Any], result: Dict[str, Any]) -> None:
        payload = result.get("payload") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            return

        if action.get("action_type") == "read_ui_text":
            text = str(payload.get("text") or "").strip()
            if text:
                source = payload.get("seed_source") or payload.get("target") or "uia"
                message = f"BLIND OBSERVATION ({source}): {text}"
                self._append_history_message(message, blind_only=True, uia_only=True)
                return

            source = payload.get("seed_source") or payload.get("target") or "uia"
            reason = str(payload.get("reason") or result.get("message") or "unknown").strip()
            window_title = str(payload.get("active_window_title") or "").strip()
            window_class = str(payload.get("active_window_class") or "").strip()
            message = f"BLIND OBSERVATION ({source}): read_ui_text failed reason={reason}"
            if window_title:
                message += f" window='{window_title}'"
            if window_class:
                message += f" class='{window_class}'"
            self._append_history_message(message, blind_only=True, uia_only=True)

    def _finalize_success(self) -> bool:
        if self.deferred_reply and self.chat_window:
            try:
                self.chat_window.add_final_answer(self.deferred_reply)
                self.deferred_reply = None
            except Exception as e:
                self.log(f"Error displaying final answer: {e}")
        self.log("Task marked as complete by AI.")
        return True

    def _log_reason(self, reason_code: str, message: str) -> None:
        clean_code = str(reason_code or "").strip() or "unknown"
        clean_message = str(message or "").strip() or "no details"
        self.log(f"[{clean_code}] {clean_message}")

    @staticmethod
    def _skip_verification_allowed(action_type: str) -> bool:
        return str(action_type or "").strip().lower() in {"reply", "wait"}

    def _verify_blind_completion(
        self,
        *,
        user_command: str,
        expected_result: str,
    ) -> tuple[Optional[Dict[str, Any]], bool]:
        retries = max(0, int(Config.BLIND_VERIFICATION_RETRIES))
        retry_delay = max(0.0, float(Config.BLIND_VERIFICATION_RETRY_DELAY))

        for attempt in range(retries + 1):
            blind_snapshot = self._capture_blind_snapshot()
            blind_verification = verify_task_blind(
                user_command=user_command,
                expected_result=expected_result,
                ui_snapshot=blind_snapshot,
                task_history=self.task_history,
                current_workspace=self.active_workspace,
            )
            if blind_verification and blind_verification.get("is_complete"):
                return blind_verification, False

            if blind_verification and not bool(blind_verification.get("needs_vision", True)):
                return blind_verification, False

            if attempt >= retries:
                return blind_verification, True

            self._log_reason(
                "uia_insufficient",
                f"Blind verification mismatch; refreshing UIA snapshot ({attempt + 1}/{retries})",
            )
            try:
                ui_automation.ensure_foreground_focus(
                    self.active_workspace,
                    self.desktop_manager if self.active_workspace == "agent" else None,
                )
            except Exception:
                pass
            if retry_delay > 0:
                time.sleep(retry_delay)

        return None, True

    def _verify_visual_completion(
        self,
        *,
        user_command: str,
        expected_result: str,
    ) -> Optional[Dict[str, Any]]:
        elements, ref_sheet = self.capture_screen()
        screenshot_path = Config.SCREENSHOT_PATH

        if not elements and (not screenshot_path or not os.path.exists(screenshot_path)):
            elements, ref_sheet = self.capture_screen(force_robotics=True)
            screenshot_path = Config.SCREENSHOT_PATH

        if not screenshot_path or not os.path.exists(screenshot_path):
            return None
        if not os.path.exists(Config.DEBUG_PATH):
            return None

        return verify_task_completion(
            user_command=user_command,
            expected_result=expected_result,
            screen_elements=elements or [],
            original_path=screenshot_path,
            debug_path=Config.DEBUG_PATH,
            reference_sheet=ref_sheet,
            task_history=self.task_history,
        )

    def run_task(self, user_command: str) -> bool:
        """
        Main control loop for executing a user task.
        """
        self.current_task = user_command
        self.task_history = []
        self.step_count = 0
        self.deferred_reply = None
        self.current_blind_snapshot = None
        self._check_stop()

        if self.chat_window:
            try:
                self.chat_window.set_click_through(False)
            except Exception:
                pass

        needs_vision = True
        self.is_magnified = False
        self.zoom_center = None

        self.log(f"Starting task: {user_command}")

        def ai_status_callback(msg: str):
            self.log(msg)

        if self.mode == OperationMode.GUIDE:
            self.log("Entering GUIDANCE mode (Interactive Tutorial)")
            session = GuidanceSession(
                user_goal=user_command,
                chat_window=self.chat_window,
                capture_func=self.capture_screen,
                stop_check_func=self._check_stop
            )
            return session.run()

        while self.step_count < self.max_steps:
            self._check_stop()
            self.step_count += 1
            self.log(f"--- Step {self.step_count} ---")

            elements = []
            screenshot_path = None
            ref_sheet = None

            if self.step_count == 1:
                self.log("Step 1: Planning blind first step...")
                from agent.brain import plan_task_blind_first_step
                action_data = plan_task_blind_first_step(
                    user_command,
                    history=self.task_history,
                    current_workspace=self.active_workspace,
                    agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created),
                    callback=ai_status_callback
                )
                needs_vision = False
            else:
                if needs_vision:
                    self.current_blind_snapshot = None
                    elements, ref_sheet = self.capture_screen()
                    screenshot_path = Config.SCREENSHOT_PATH
                    
                    if not elements and (not screenshot_path or not os.path.exists(screenshot_path)):
                        self.log("Vision capture failed. Retrying with force robotics...")
                        time.sleep(1)
                        elements, ref_sheet = self.capture_screen(force_robotics=True)
                        if not elements:
                            self.log("CRITICAL: Screen capture failed repeatedly. Cannot proceed with vision.")
                            return False
                    
                    current_hash = self.screen_capture.last_hash
                    last_meta = next((h for h in reversed(self.task_history) if isinstance(h, dict) and "action_type" in h), {})
                    if last_meta.get("action_type") == "wait":
                        if getattr(self, "_last_run_hash", None) == current_hash:
                            self.log("Screen unchanged after wait. Extending wait...")
                            time.sleep(1)
                            continue
                    self._last_run_hash = current_hash
                else:
                    self.log("Blind mode: skipping screen capture")

                self._check_stop()
                if needs_vision:
                    action_data = plan_task(
                        user_command,
                        elements,
                        screenshot_path,
                        Config.DEBUG_PATH,
                        ref_sheet,
                        history=self.task_history,
                        current_workspace=self.active_workspace,
                        agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created),
                        callback=ai_status_callback
                    )
                else:
                    blind_snapshot = self._capture_blind_snapshot()
                    from agent.brain import plan_task_blind
                    action_data = plan_task_blind(
                        user_command,
                        history=self.task_history,
                        current_workspace=self.active_workspace,
                        agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created),
                        ui_snapshot=blind_snapshot,
                        callback=ai_status_callback
                    )

            if not action_data:
                self.log("Brain failed to provide a plan.")
                return False

            action, model_part = action_data
            self.log(f"AI Reasoning: {action.get('reasoning', 'N/A')}")

            if self.clarification_manager and self.clarification_manager.should_ask_clarification(action):
                user_answer = self.clarification_manager.ask_question(action, user_command)
                if user_answer:
                    refined_action = self.clarification_manager.integrate_answer(
                        action, user_answer, user_command
                    )
                    if refined_action:
                        self.log("Action refined based on user feedback.")
                        action = refined_action

            guard_result = self.action_guard.guard(
                action,
                callback=ai_status_callback,
                source="vision_runtime" if needs_vision else "blind_runtime",
            )
            if not guard_result.valid:
                self._log_reason(
                    guard_result.reason_code or "invalid_action",
                    guard_result.error or guard_result.message,
                )
                if not needs_vision:
                    self._log_reason(
                        "escalated_to_vision",
                        "Invalid blind action blocked; escalating to vision planner.",
                    )
                    needs_vision = True
                    self.current_blind_snapshot = None
                self.task_history.append({
                    "step": self.step_count,
                    "action_type": action.get("action_type"),
                    "params": action.get("params"),
                    "reasoning": action.get("reasoning"),
                    "success": False,
                    "result_message": guard_result.message,
                    "guard_error": guard_result.error,
                    "reason_code": guard_result.reason_code,
                })
                if model_part:
                    self.task_history.append(model_part)
                continue

            if guard_result.repaired:
                self.log("Runtime ActionGuard repaired planner output before execution.")
            action = guard_result.action or action

            if self.loop_detector and action.get("action_type") != "wait":
                current_hash = (
                    self.screen_capture.last_hash
                    if needs_vision
                    else ui_automation.snapshot_signature(self.current_blind_snapshot)
                )
                if self.loop_detector.track_action(action, current_hash):
                    self.log("LOOP WARNING: Repeating pattern detected!")
                    
                    if self.clarification_manager:
                        loop_info = self.loop_detector.get_loop_info() or {
                            "count": 0,
                            "pattern": "repeated_action",
                        }
                        suggestions = self.clarification_manager.generate_loop_suggestions(
                            action, user_command, loop_info
                        )
                        
                        user_help = self.clarification_manager.handle_loop_clarification(
                            loop_info,
                            user_command,
                            suggestions
                        )
                        if user_help:
                            if user_help.lower() in ["cancel", "stop", "quit"]:
                                self.log("User requested to stop during loop resolution.")
                                return False
                                
                            action = {
                                "action_type": "reply",
                                "params": {"text": f"Understood. I will attempt: {user_help}"},
                                "reasoning": f"User intervention after loop detection: {user_help}",
                                "needs_vision": True,
                                "task_complete": False
                            }
            
            if action.get("action_type") == "switch_workspace":
                params = action.get("params", {})
                target = params.get("workspace")
                if target:
                    self._set_workspace(target, reason=action.get("reasoning"))
                    self.current_blind_snapshot = None
                    needs_vision = action.get("needs_vision", needs_vision)
                    
                    self.task_history.append({
                        "step": self.step_count,
                        "action_type": action.get("action_type"),
                        "params": action.get("params"),
                        "reasoning": action.get("reasoning"),
                        "success": True,
                        "result_message": f"Switched workspace to {target}",
                    })
                    if model_part:
                        self.task_history.append(model_part)
                    continue
            
            if self.step_count == 1:
                self._set_workspace(self.active_workspace)

            self._check_stop()
            
            if action.get("action_type") == "sequence":
                sequence = action.get("action_sequence", [])
                self.log(f"Executing sequence of {len(sequence)} actions...")
                success = True
                sequence_results = []
                for i, sub_action in enumerate(sequence):
                    self._check_stop()
                    self.log(f"Sequence Step {i+1}/{len(sequence)}: {sub_action.get('action_type')}")
                    sub_result = self.execute_action(sub_action, elements)
                    sequence_results.append(sub_result)
                    if not needs_vision:
                        self._append_blind_observation(sub_action, sub_result)
                    if not sub_result.get("success"):
                        success = False
                        self.log(f"Sequence failure: {sub_result.get('message')}")
                        self.log(f"Sequence failed at step {i+1}")
                        break
                action_result = {
                    "success": success,
                    "message": "Sequence completed" if success else "Sequence failed",
                    "payload": {"sequence_results": sequence_results},
                }
            else:
                action_result = self.execute_action(action, elements)
                success = bool(action_result.get("success"))
                if not success:
                    self.log(f"Action failed: {action_result.get('message')}")
                if not needs_vision:
                    self._append_blind_observation(action, action_result)

            requested_task_complete = bool(action.get("task_complete"))
            if requested_task_complete and not success:
                self._log_reason(
                    "verification_rejected",
                    "Ignoring task_complete because the latest action failed.",
                )
            task_complete_effective = bool(requested_task_complete and success)

            requested_skip_verification = bool(action.get("skip_verification"))
            action_type = str(action.get("action_type") or "").strip().lower()
            skip_verification_effective = bool(
                requested_skip_verification and self._skip_verification_allowed(action_type)
            )
            if requested_skip_verification and not skip_verification_effective:
                self._log_reason(
                    "verification_rejected",
                    f"Ignoring skip_verification for action type '{action_type or 'unknown'}'.",
                )
            
            self.task_history.append({
                "step": self.step_count,
                "action_type": action.get("action_type"),
                "params": action.get("params"),
                "reasoning": action.get("reasoning"),
                "success": success,
                "sequence": action.get("action_sequence") if action.get("action_type") == "sequence" else None,
                "result_message": action_result.get("message"),
                "result_payload": action_result.get("payload"),
                "task_complete_requested": requested_task_complete,
                "task_complete_effective": task_complete_effective,
                "skip_verification_requested": requested_skip_verification,
                "skip_verification_effective": skip_verification_effective,
                "guard_repaired": bool(guard_result.repaired),
            })
            if model_part:
                self.task_history.append(model_part)

            if task_complete_effective:
                if skip_verification_effective:
                    return self._finalize_success()

                expected_result = str(action.get("expected_result") or "")

                if not needs_vision:
                    self.log("Task marked complete in blind mode. Running blind verification.")
                    blind_verification, escalate_to_vision = self._verify_blind_completion(
                        user_command=user_command,
                        expected_result=expected_result,
                    )
                    if blind_verification and blind_verification.get("is_complete"):
                        self.log(
                            "Blind verification confirmed completion: "
                            f"{blind_verification.get('reason', '')}"
                        )
                        return self._finalize_success()

                    reason = "Blind verification could not confirm completion"
                    hint = ""
                    if blind_verification:
                        reason = str(
                            blind_verification.get("reason")
                            or "Blind verification could not confirm completion"
                        )
                        hint = str(blind_verification.get("next_action_hint") or "").strip()

                    self._log_reason("verification_rejected", f"Blind verification blocked completion: {reason}")
                    observation = f"BLIND VERIFIER: {reason}"
                    if hint:
                        observation += f" Next action hint: {hint}"
                    self._append_history_message(observation, blind_only=True, uia_only=True)

                    if escalate_to_vision:
                        self._log_reason(
                            "escalated_to_vision",
                            "UIA evidence remained insufficient after blind verification retries.",
                        )
                        needs_vision = True
                        self.current_blind_snapshot = None
                    else:
                        needs_vision = bool(blind_verification and blind_verification.get("needs_vision", False))
                    continue

                self.log("Task marked complete in vision mode. Running visual verification.")
                visual_verification = self._verify_visual_completion(
                    user_command=user_command,
                    expected_result=expected_result,
                )
                if visual_verification and visual_verification.get("is_complete"):
                    self.log(
                        "Visual verification confirmed completion: "
                        f"{visual_verification.get('reasoning', '')}"
                    )
                    return self._finalize_success()

                reason = "Visual verification unavailable"
                next_action = ""
                if visual_verification:
                    reason = str(
                        visual_verification.get("reasoning")
                        or "Visual verification could not confirm completion"
                    )
                    next_action = str(visual_verification.get("next_action") or "").strip()
                self._log_reason("verification_rejected", f"Visual verification blocked completion: {reason}")
                observation = f"VISUAL VERIFIER: {reason}"
                if next_action:
                    observation += f" Next action hint: {next_action}"
                self._append_history_message(observation)
                needs_vision = True
                self.current_blind_snapshot = None
                continue

            needs_vision = action.get("needs_vision", True)
            if needs_vision:
                self.current_blind_snapshot = None
            
            if action.get("action_type") == "magnify":
                self.is_magnified = True
                params = action.get("params", {})
                eid = params.get("element_id")
                if eid is not None:
                    for el in elements:
                        if el["id"] == eid:
                            self.zoom_center = (el["x"], el["y"])
                            break
                self.zoom_level = params.get("zoom_level", 2.0)
            elif action.get("action_type") != "wait":
                 self.is_magnified = False

        self.log("Max steps reached. Task timed out.")
        return False
        
    def capture_screen(self, force_robotics: bool = False) -> tuple[List[Dict], Optional[Any]]:
        """
        Delegate capture to ScreenCapture module.
        """
        return self.screen_capture.capture_screen(force_robotics)

    def _set_workspace(self, target: str, reason: Optional[str] = None) -> None:
        target = (target or "").strip().lower()
        if target not in {"user", "agent"}:
            return

        changed = (self.active_workspace != target)
        self.active_workspace = target

        if changed:
            if reason:
                self.log(f"Workspace set to {target}: {reason}")
            else:
                self.log(f"Workspace set to {target}")

        if self.chat_window:
            try:
                if hasattr(self.chat_window, "notify_workspace_changed"):
                    self.chat_window.notify_workspace_changed(target)
                if target == "user":
                    self.chat_window.set_click_through(True)
                else:
                    self.chat_window.set_click_through(False)
            except Exception:
                pass

    def _init_agent_desktop(self) -> bool:
        if not Config.ENABLE_AGENT_DESKTOP:
            return False

        if self.desktop_manager and self.desktop_manager.is_created:
            return True

        try:
            from desktop.desktop_manager import AgentDesktopManager

            self.desktop_manager = AgentDesktopManager(Config.AGENT_DESKTOP_NAME)
            if not self.desktop_manager.create_desktop():
                self.desktop_manager = None
                return False

            self.desktop_manager.initialize_shell()
            return True
        except Exception:
            self.desktop_manager = None
            return False

    def _ensure_workspace_active(self) -> None:
        if self.active_workspace != "agent":
            return

        if not self.desktop_manager or not self.desktop_manager.is_created:
            if not self._init_agent_desktop():
                self._set_workspace(
                    "user",
                    reason="Agent Desktop unavailable; continuing on user desktop",
                )

    def _restore_default_workspace(self, reason: str) -> None:
        target = (Config.DEFAULT_WORKSPACE or "user").strip().lower()
        if target not in {"user", "agent"}:
            target = "user"

        self._set_workspace(target, reason=reason)
        self._ensure_workspace_active()

    def log(self, message: str):
        """Log message to file and (sparingly) to the GUI.

        Policy:
        - GUI shows only high-level, human-readable updates.
        - Detailed trace lines (indented / bracketed) go to the log file (DEBUG).
        """

        logger = logging.getLogger("pixelpilot.agent")
        raw = "" if message is None else str(message)
        clean = raw.strip()
        if not clean:
            return

        is_trace = (
            raw.startswith(" ") or clean.startswith("[") or clean.startswith("->")
        )
        if is_trace:
            logger.debug(clean)
        else:
            logger.info(clean)

        if self.chat_window:
            if is_trace or clean.startswith("="):
                return
            self.chat_window.add_system_message(clean)
            return

        print(message)

    def get_scale_factor(self):
        """
        Fixes mouse drift caused by Windows Display Scaling.
        """
        try:
            user32 = ctypes.windll.user32
            if not self.chat_window:
                user32.SetProcessDPIAware()
            w_physical = user32.GetSystemMetrics(0)
            h_physical = user32.GetSystemMetrics(1)
            w_logical, h_logical = pyautogui.size()
            return w_logical / w_physical, h_logical / h_physical
        except Exception:
            return 1.0, 1.0




