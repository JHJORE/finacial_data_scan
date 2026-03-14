from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
COMPANIES_FILE = DATA_DIR / "companies.xlsx"
RESEARCH_DIR = DATA_DIR / "research"
CLASSIFICATIONS_DIR = DATA_DIR / "classifications"
OUTPUT_DIR = DATA_DIR / "output"

# Ensure directories exist
RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
CLASSIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Model
GEMINI_MODEL = "gemini-3-flash-preview"

# Rate limiting
MAX_CONCURRENT_REQUESTS = 10
REQUESTS_PER_MINUTE = 30
