from google import genai
from config import Config

class LLMService:
    def __init__(self):
        self.api_key = Config.API_KEY
        # self.client = genai.Client(api_key=self.api_key)

    def generate_response(self, prompt, context=None):
        # TODO: Implement Gemini-3 interaction
        pass
