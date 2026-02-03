from .modes import AgentMode

class Agent:
    def __init__(self):
        self.current_mode = AgentMode.ADVISOR
    
    def process_request(self, text, image=None):
        # TODO: Implement logic based on mode
        pass
