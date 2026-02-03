from enum import Enum

class AgentMode(Enum):
    ADVISOR = "advisor" # Only answers questions, no actions
    HELPER = "helper" # Takes action but asks for permission
    AUTOMATION = "automation" # Full automation
