import os

class Config:
    API_KEY = os.getenv("GEMINI_API_KEY", "")
    DEBUG = True
