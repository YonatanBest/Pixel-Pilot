import pyautogui

class AutomationSkill:
    @staticmethod
    def click(x, y):
        pyautogui.click(x, y)
    
    @staticmethod
    def type_text(text):
        pyautogui.write(text)
