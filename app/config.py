import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MULTISTATE_SOURCE_URL = os.getenv(
    "MULTISTATE_SOURCE_URL",
    "https://www.multistate.us/insider/2026/4/9/how-states-are-regulating-ai-in-education-this-legislative-session",
)
ARTICLE_DATE = "2026-04-09"
OUTPUT_DIR = ROOT / "output"
FIXTURE_PATH = ROOT / "data" / "fixtures" / "multistate_2026_04_09.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
