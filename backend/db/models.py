"""Standard job schema shared by every scraper, plus the SQLite DDL."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"

# jobs.job_type holds a comma-separated subset, e.g. "tech,remote" or "non-tech,internship".
JOB_TYPES = ("tech", "non-tech", "internship", "remote")

# score_type values stored on jobs / job_scores
SCORE_TYPE_AI = "ai"
SCORE_TYPE_FALLBACK = "fallback"
SCORE_TYPE_UNSCORED = ""

# New tables managed by the AI matching layer. Each is created with CREATE TABLE IF NOT EXISTS
# so fresh databases get them, and init_db creates any that are missing from an existing DB.
NEW_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS job_scores (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id       TEXT NOT NULL UNIQUE,        -- one score per job
        match_score  INTEGER NOT NULL,            -- 0-100
        skill_match  INTEGER NOT NULL,
        experience_match INTEGER NOT NULL,
        location_match INTEGER NOT NULL,
        role_match   INTEGER NOT NULL,
        matched_skills TEXT,                       -- comma-separated
        missing_skills TEXT,                       -- comma-separated
        explanation  TEXT,
        eligible     INTEGER NOT NULL DEFAULT 0,  -- 0/1
        score_type   TEXT NOT NULL,                 -- 'ai' | 'fallback'
        model        TEXT,                          -- model id used (AI scores only)
        scored_at    TEXT NOT NULL,
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
    );
    CREATE INDEX IF NOT EXISTS idx_job_scores_job_id ON job_scores(job_id);
    CREATE INDEX IF NOT EXISTS idx_job_scores_score ON job_scores(match_score);
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id            TEXT NOT NULL,
        telegram_chat_id  TEXT NOT NULL,
        notification_status TEXT NOT NULL,       -- 'sent' | 'failed' | 'pending'
        telegram_message_id INTEGER,
        sent_at           TEXT,
        retry_count       INTEGER DEFAULT 0,
        error_message     TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
    );
    CREATE INDEX IF NOT EXISTS idx_notifications_job ON notifications(job_id);
    CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(notification_status);
    """,
    """
    CREATE TABLE IF NOT EXISTS saved_jobs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id       TEXT NOT NULL,
        telegram_chat_id TEXT NOT NULL,
        saved_at     TEXT NOT NULL,
        UNIQUE(job_id, telegram_chat_id),
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
    );
    CREATE INDEX IF NOT EXISTS idx_saved_jobs_job ON saved_jobs(job_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_preferences (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL,
        source        TEXT NOT NULL,
        jobs_found    INTEGER DEFAULT 0,
        jobs_new      INTEGER DEFAULT 0,
        jobs_scored   INTEGER DEFAULT 0,
        jobs_notified INTEGER DEFAULT 0,
        errors        TEXT,
        started_at    TEXT,
        finished_at   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_scrape_history_run ON scrape_history(run_id);
    CREATE INDEX IF NOT EXISTS idx_scrape_history_source ON scrape_history(source);
    """,
    """
    CREATE TABLE IF NOT EXISTS source_health (
        source                 TEXT PRIMARY KEY,
        status                 TEXT NOT NULL DEFAULT 'unknown',   -- 'healthy' | 'degraded' | 'unhealthy'
        last_successful_scrape TEXT,
        error_count            INTEGER DEFAULT 0,
        jobs_collected         INTEGER DEFAULT 0,
        enabled                INTEGER NOT NULL DEFAULT 1,        -- 0/1
        updated_at             TEXT
    );
    """,
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,   -- hash of company+title+link, for dedup
    title        TEXT,
    company      TEXT,
    location     TEXT,
    job_type     TEXT,               -- comma-separated: tech|non-tech [,internship] [,remote]
    description  TEXT,               -- plain text, truncated to settings.DESCRIPTION_MAX_CHARS
    skills_tags  TEXT,               -- comma-separated matched skills
    salary       TEXT,
    posted_date  TEXT,               -- ISO-8601 UTC
    deadline     TEXT,               -- YYYY-MM-DD if explicitly stated, else NULL
    apply_link   TEXT,
    source       TEXT,               -- which API/site (display name)
    status       TEXT,               -- active / expired
    notified     INTEGER DEFAULT 0,  -- 0/1, avoid duplicate Telegram pings
    last_seen_at TEXT,               -- updated every scrape run it still appears
    scraped_at   TEXT,               -- first time we saw it
    feed         TEXT,               -- scrape unit that found it, e.g. "greenhouse:stripe" (used by expiry)
    match_score  INTEGER DEFAULT 0,  -- AI/fallback match score (0-100), denormalized from job_scores
    score_type   TEXT DEFAULT ''     -- 'ai' | 'fallback' | ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_status       ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);

-- One row per feed per pipeline run, for debugging and for expiry.
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,                -- groups all feeds of one main.py run
    source      TEXT,                -- the feed name, e.g. "remoteok", "greenhouse:stripe"
    started_at  TEXT,
    finished_at TEXT,
    jobs_found  INTEGER DEFAULT 0,
    jobs_new    INTEGER DEFAULT 0,
    error       TEXT                 -- NULL = full success
);

CREATE INDEX IF NOT EXISTS idx_runs_run_id ON scrape_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_source ON scrape_runs(source, started_at);

-- Small key/value store (e.g. fingerprint of MY_SKILLS to know when to re-tag).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first schema version: (table, column, type). init_db adds
# any that are missing, so older jobs.db files upgrade in place.
MIGRATIONS = [
    ("jobs", "feed", "TEXT"),
    ("jobs", "source", "TEXT"),
    ("jobs", "match_score", "INTEGER DEFAULT 0"),
    ("jobs", "score_type", "TEXT DEFAULT ''"),
]
CREATE_FEED_INDEX = "CREATE INDEX IF NOT EXISTS idx_jobs_feed ON jobs(feed, last_seen_at);"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_job_id(company: str, title: str, apply_link: str) -> str:
    key = "|".join(s.strip().lower() for s in (company or "", title or "", apply_link or ""))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@dataclass
class Job:
    title: str
    company: str
    apply_link: str
    source: str
    location: str = ""
    job_type: str = ""
    description: str = ""
    skills_tags: str = ""
    salary: str = ""
    posted_date: Optional[str] = None
    deadline: Optional[str] = None
    status: str = STATUS_ACTIVE
    job_id: str = ""
    feed: str = ""
    # Not stored: full untruncated text used for skill matching.
    match_text: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = make_job_id(self.company, self.title, self.apply_link)
