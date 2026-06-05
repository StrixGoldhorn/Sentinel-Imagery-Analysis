import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    USERNAME = os.getenv("COP_USERNAME")
    PASSWORD = os.getenv("COP_PASSWORD")