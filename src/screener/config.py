from pathlib import Path
from dotenv import load_dotenv
import os

from google import genai

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
COMPANIES_FILE = DATA_DIR / "companies.xlsx"
SEARCH_DIR = DATA_DIR / "search"
RESULTS_DIR = DATA_DIR / "results"
OUTPUT_DIR = DATA_DIR / "output"
AGENTS_DIR = Path(__file__).parent / "agents"

# Ensure directories exist
SEARCH_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Model
GEMINI_MODEL = "gemini-3-flash-preview"

# Rate limiting
MAX_CONCURRENT_REQUESTS = 10
REQUESTS_PER_MINUTE = 30


def create_gemini_client() -> genai.Client:
    """Create a Gemini API client using the standard (non-Vertex) API."""
    return genai.Client(api_key=GOOGLE_API_KEY)
