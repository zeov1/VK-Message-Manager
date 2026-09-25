import os

from dotenv import load_dotenv

load_dotenv()

VK_ACCESS_TOKEN: str = os.environ["VK_ACCESS_TOKEN"]
VK_API_VERSION: str = os.getenv("VK_API_VERSION", "5.199")
REQUEST_DELAY_SECONDS: float = float(os.getenv("REQUEST_DELAY_SECONDS", "1.0"))
