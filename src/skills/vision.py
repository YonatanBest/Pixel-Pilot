# import cv2
# import numpy as np
import pyautogui

class VisionSkill:
    @staticmethod
    def capture_screen():
        screenshot = pyautogui.screenshot()
        return screenshot
    
    @staticmethod
    def analyze_screen(image):
        # TODO: Implement analysis logic
        pass
