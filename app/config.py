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


# AI Laws by State — Education AI Tracker.
AI_LAWS_EDUCATION_SOURCE_URL = os.getenv(
    "AI_LAWS_EDUCATION_SOURCE_URL",
    "https://www.ailawsbystate.com/tools/education-ai-tracker",
)
AI_LAWS_EDUCATION_FIXTURE_PATH = ROOT / "data" / "fixtures" / "ai_laws_education" / "sample.json"


# EdChoice — School Choice in America Dashboard.
EDCHOICE_SCHOOL_CHOICE_SOURCE_URL = os.getenv(
    "EDCHOICE_SCHOOL_CHOICE_SOURCE_URL",
    "https://www.edchoice.org/school-choice/dashboard/",
)
EDCHOICE_FIXTURE_PATH = ROOT / "data" / "fixtures" / "edchoice_school_choice" / "sample.json"
EDCHOICE_OFFICIAL_SOURCE_OVERRIDES_PATH = (
    ROOT / "data" / "edchoice_official_source_overrides.json"
)
