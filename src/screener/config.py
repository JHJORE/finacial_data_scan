from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os

from google import genai
from google.genai.types import HttpOptions

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
COMPANIES_FILE = DATA_DIR / "companies.xlsx"
RUNS_DIR = DATA_DIR / "runs"
AGENTS_DIR = Path(__file__).parent / "agents"

# These are set by init_run() before the pipeline starts.
SEARCH_DIR: Path = RUNS_DIR / "_default" / "search"
RESULTS_DIR: Path = RUNS_DIR / "_default" / "results"
OUTPUT_DIR: Path = RUNS_DIR / "_default" / "output"
DEBUG_DIR: Path = RUNS_DIR / "_default" / "debug"

# API - Vertex AI (uses Application Default Credentials)
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# Model
GEMINI_MODEL = "gemini-3-flash-preview"
THINKING_LEVEL = "medium"

# Search target year — set via .env or CLI --year flag; defaults to current year
TARGET_YEAR: int = int(os.getenv("TARGET_YEAR", 0)) or datetime.now().year

# Rate limiting
MAX_CONCURRENT_REQUESTS = 50

# Reader — skip classification if url_context returns too few tokens
MIN_VIABLE_TOKENS = 10_000

# SEC-specific: more retries (transient url_context failures) and lower token
# threshold (10-K filings are structured; even partial reads contain M&A sections)
SEC_MAX_RETRIES = 3
SEC_MIN_VIABLE_TOKENS = 5_000

# SEC document size guard (character count after HTML parsing)
SEC_MIN_CHARS = 5_000     # below this, download likely failed

_LATEST_POINTER = RUNS_DIR / "latest"


def _resolve_latest_run_dir() -> Path | None:
    """Return the run directory referenced by `latest`, if valid."""
    if not _LATEST_POINTER.exists() and not _LATEST_POINTER.is_symlink():
        return None
    if _LATEST_POINTER.is_symlink():
        resolved = _LATEST_POINTER.resolve()
        return resolved if resolved.is_dir() else None
    if _LATEST_POINTER.is_file():
        name = _LATEST_POINTER.read_text(encoding="utf-8").strip()
        if name:
            candidate = RUNS_DIR / name
            return candidate if candidate.is_dir() else None
    return None

def init_run(create_new: bool = True) -> Path:
    """Initialize a run directory with timestamped subfolders.

    Args:
        create_new: If True, creates a new timestamped directory.
                    If False, reuses the latest existing run.

    Returns:
        The run directory path.
    """
    global SEARCH_DIR, RESULTS_DIR, OUTPUT_DIR, DEBUG_DIR

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    if create_new:
        run_dir = RUNS_DIR / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    else:
        run_dir = _resolve_latest_run_dir()
        if run_dir is None:
            run_dir = RUNS_DIR / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    SEARCH_DIR = run_dir / "search"
    RESULTS_DIR = run_dir / "results"
    OUTPUT_DIR = run_dir / "output"
    DEBUG_DIR = run_dir / "debug"

    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    if _LATEST_POINTER.is_symlink() or _LATEST_POINTER.exists():
        _LATEST_POINTER.unlink()
    _LATEST_POINTER.write_text(f"{run_dir.name}\n", encoding="utf-8")

    print(f"Run directory: {run_dir}")
    return run_dir


def create_gemini_client(api_version: str = "v1beta1") -> genai.Client:
    """Create a Gemini API client using Vertex AI."""
    return genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
        http_options=HttpOptions(api_version=api_version),
    )
