"""Tunable settings for the whole pipeline. Secrets never live here — they come from
environment variables (GitHub Secrets in CI, a local .env / shell exports when testing).
"""
from __future__ import annotations

import os
from pathlib import Path

# Load .env for local development so settings pick up local values
# (real environment variables always take precedence via setdefault).
for _env_path in (Path(__file__).resolve().parent.parent / ".env",
                  Path(__file__).resolve().parent.parent.parent / ".env"):
    if _env_path.is_file():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# --- Scheduling / expiry -----------------------------------------------------------
SCRAPE_INTERVAL_HOURS = 3          # must match .github/workflows/kismat-scheduled.yml
EXPIRY_MISSED_RUNS = 2             # a job missing from this many successful runs of its feed -> expired

# --- Freshness ---------------------------------------------------------------------------
MAX_JOB_AGE_DAYS = 60              # jobs whose posted_date is older are ignored (per-feed override)
WINDOW_FEED_STALE_DAYS = 21        # "latest N" feeds: expire when not seen for this many days

# --- Storage size control (the DB and jobs.json are committed to git) --------------
DESCRIPTION_MAX_CHARS = 800        # stored description is cut to this; skill matching uses the full text
EXPORT_SNIPPET_CHARS = 160         # description snippet written to jobs.json
PRUNE_EXPIRED_DAYS = 30            # expired jobs not seen for this long are deleted from jobs.db
PRUNE_RUNS_DAYS = 14               # scrape_runs rows older than this are deleted
DB_MAX_MB = 80                     # GitHub rejects files > 100 MB; retention is tightened until jobs.db fits

# --- Telegram -------------------------------------------------------------------------
MAX_NOTIFICATIONS_PER_RUN = 25     # extra matches are rolled into one summary message
NOTIFY_MAX_AGE_DAYS = 7            # don't ping for jobs whose posted_date is older than this
SITE_URL = os.environ.get("SITE_URL", "")  # optional, linked in summary messages

# --- Search terms for keyword-based sources (Adzuna, Jooble, India boards) ----------
# Each term costs API calls / page loads per run, so keep these lists short.
SEARCH_QUERIES = ["software developer", "python developer", "data analyst"]
INDIA_QUERIES = ["python", "software developer", "data analyst"]

ADZUNA_COUNTRY = os.environ.get("ADZUNA_COUNTRY", "in")   # in, gb, us, ...
ADZUNA_PAGES_PER_QUERY = 2
JOOBLE_LOCATION = os.environ.get("JOOBLE_LOCATION", "India")

# Pages fetched per paginated source per run.
API_MAX_PAGES = 5
INDIA_MAX_PAGES = 2

# --- Politeness -------------------------------------------------------------------------
SCRAPE_DELAY_RANGE = (2.0, 5.0)    # seconds between HTML-scraping requests (India boards)
ATS_DELAY_RANGE = (0.3, 0.8)       # seconds between ATS company requests (JSON APIs)
HTTP_TIMEOUT = 30

# --- NVIDIA Nemotron AI matching -------------------------------------------------------
# Use NVIDIA NIM as the only hosted AI provider (OpenAI-compatible endpoint).
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")          # required for AI scoring
NVIDIA_BASE_URL = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
# Verified from the NVIDIA API Catalog. Override with the NVIDIA_MODEL env var.
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "")
NVIDIA_TIMEOUT = float(os.environ.get("NVIDIA_TIMEOUT", "60"))

# AI scoring thresholds (per project plan):
# 90-100 excellent, 80-89 good -> notify; 60-79 store only; below 60 reject.
MIN_MATCH_SCORE = int(os.environ.get("MIN_MATCH_SCORE", "80"))   # notify when >= this
AI_MAX_JOBS_PER_RUN = int(os.environ.get("AI_MAX_JOBS_PER_RUN", "200"))  # cap scored per run

# --- Candidate profile -----------------------------------------------------------------
# Stored in the candidate_preferences table; these are the defaults used when the
# table is empty. Edit the profile via the DB (or Telegram /preferences) to tune.
CANDIDATE_PROFILE_DEFAULTS = {
    "target_roles": [
        "AI/ML Engineer", "AI Engineer", "Machine Learning Engineer",
        "Generative AI Engineer", "GenAI Developer", "LLM Engineer",
        "Python Engineer", "Python Developer", "FastAPI Developer", "Flask Developer",
        "Data Scientist", "Data Analyst",
        "AI/ML Intern", "Machine Learning Intern", "GenAI Intern",
        "Python Developer Intern", "Data Science Intern", "Research Intern",
    ],
    "technical_skills": [
        "Python", "SQL", "MySQL",
        "Artificial Intelligence", "Machine Learning", "Deep Learning",
        "Generative AI", "LLM", "Large Language Models", "RAG",
        "Retrieval-Augmented Generation", "LangChain", "Embeddings", "Semantic Search",
        "Sentence-Transformers", "ChromaDB", "Hugging Face API", "Prompt Engineering",
        "NLP", "Natural Language Processing",
        "Computer Vision", "YOLOv8", "OpenCV", "Image Processing",
        "Object Detection", "Image and Video Inference",
        "TensorFlow", "NumPy", "Pandas", "Scikit-learn", "Scikit Learn",
        "FastAPI", "Flask", "REST API", "REST API Development",
        "SQLite", "Git", "GitHub", "JWT Authentication",
        "Classification", "Regression", "Model Evaluation",
        "Feature Engineering", "Model Inference",
    ],
    "target_locations": [
        "India", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Mumbai",
        "Delhi NCR", "Remote", "Work from Home",
    ],
    "prioritize_locations": ["Bengaluru", "Remote"],
    "experience_levels": [
        "Fresher", "Internship", "Entry Level", "Graduate", "0-1 years", "0-2 years",
    ],
    "min_match_score": str(MIN_MATCH_SCORE),
}
