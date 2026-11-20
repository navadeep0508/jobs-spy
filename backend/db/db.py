"""SQLite access: connection, schema setup, dedup upsert, last_seen_at tracking."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from db.models import (
    CREATE_FEED_INDEX, MIGRATIONS, NEW_TABLES, SCHEMA, STATUS_ACTIVE,
    SCORE_TYPE_AI, SCORE_TYPE_FALLBACK, Job, utc_now_iso,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("JOBS_DB_PATH", str(REPOSITORY_ROOT / "data" / "jobs.db")))


def migrate_legacy_database(target: Path | str = DB_PATH) -> Optional[Path]:
    """Copy a legacy local database once, before normal use begins."""
    target = Path(target)
    if target.exists():
        return None
    candidates = [
        REPOSITORY_ROOT / "backend" / "db" / "jobs.db",
        REPOSITORY_ROOT / "backend" / "jobs.db",
        REPOSITORY_ROOT / "jobs.db",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            return candidate
    return None


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Create any AI-layer tables missing from an existing (already-committed) DB.
    for ddl in NEW_TABLES:
        # The first statement in each DDL block is the CREATE TABLE; extract its name.
        first = ddl.strip().split("\n", 1)[0]
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", first)
        if m and not _table_exists(conn, m.group(1)):
            conn.executescript(ddl)
    for table, column, col_type in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    conn.execute(CREATE_FEED_INDEX)
    # match_score / source columns may have just been added by migration; create their indexes now.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
    conn.commit()


def integrity_check(path: Path | str) -> tuple[bool, str]:
    path = Path(path)
    if not path.is_file():
        return False, "database file does not exist"
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=30)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return (row is not None and row[0] == "ok"), str(row[0] if row else "no result")
    except sqlite3.Error as exc:
        return False, f"database integrity check failed: {exc}"
    finally:
        if conn is not None:
            conn.close()


def create_backup(source: Path | str, destination: Path | str) -> tuple[bool, str]:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        return False, "source database does not exist"
    ok, result = integrity_check(source)
    if not ok:
        return False, f"source integrity check failed: {result}"
    temp = destination.with_suffix(destination.suffix + ".tmp")
    try:
        src = sqlite3.connect(str(source), timeout=30)
        dst = sqlite3.connect(str(temp), timeout=30)
        src.backup(dst)
        dst.close()
        src.close()
        ok, result = integrity_check(temp)
        if not ok:
            temp.unlink(missing_ok=True)
            return False, f"backup integrity check failed: {result}"
        os.replace(temp, destination)
        return True, str(destination)
    except (OSError, sqlite3.Error) as exc:
        temp.unlink(missing_ok=True)
        return False, str(exc)


def restore_backup(backup: Path | str, target: Path | str) -> tuple[bool, str]:
    backup = Path(backup)
    target = Path(target)
    if not backup.is_file():
        return False, "backup file does not exist"
    ok, result = integrity_check(backup)
    if not ok:
        return False, f"backup integrity check failed: {result}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".restore.tmp")
    try:
        src = sqlite3.connect(str(backup), timeout=30)
        dst = sqlite3.connect(str(temp), timeout=30)
        src.backup(dst)
        dst.close()
        src.close()
        ok, result = integrity_check(temp)
        if not ok:
            temp.unlink(missing_ok=True)
            return False, f"restored database integrity check failed: {result}"
        os.replace(temp, target)
        return True, str(target)
    except sqlite3.Error as exc:
        temp.unlink(missing_ok=True)
        return False, str(exc)


def count_jobs(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def upsert_job(conn: sqlite3.Connection, job: Job, seen_at: Optional[str] = None) -> bool:
    """Insert a new job or refresh an existing one. Returns True if the job is new.

    Existing rows keep notified, scraped_at and posted_date; last_seen_at is bumped,
    the row is (re)activated, and skills/description/etc. are refreshed. A reactivated
    job whose deadline has passed is expired again by the expiry checker.
    """
    now = seen_at or utc_now_iso()
    exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE jobs SET
                last_seen_at = ?,
                status       = ?,
                feed         = ?,
                skills_tags  = ?,
                description  = COALESCE(NULLIF(?, ''), description),
                salary       = COALESCE(NULLIF(?, ''), salary),
                deadline     = COALESCE(?, deadline),
                location     = COALESCE(NULLIF(?, ''), location),
                posted_date  = COALESCE(posted_date, ?)
            WHERE job_id = ?
            """,
            (now, STATUS_ACTIVE, job.feed, job.skills_tags, job.description, job.salary,
             job.deadline, job.location, job.posted_date, job.job_id),
        )
        return False

    conn.execute(
        """
        INSERT INTO jobs (job_id, title, company, location, job_type, description,
                          skills_tags, salary, posted_date, deadline, apply_link,
                          source, status, notified, last_seen_at, scraped_at, feed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            job.job_id, job.title, job.company, job.location, job.job_type,
            job.description, job.skills_tags, job.salary, job.posted_date,
            job.deadline, job.apply_link, job.source, job.status, now, now, job.feed,
        ),
    )
    return True


def upsert_jobs(conn: sqlite3.Connection, jobs: Iterable[Job], seen_at: Optional[str] = None) -> int:
    """Upsert many jobs in one transaction. Returns the number of new jobs."""
    new_count = 0
    with conn:
        for job in jobs:
            if upsert_job(conn, job, seen_at):
                new_count += 1
    return new_count


def log_run(conn: sqlite3.Connection, run_id: str, feed: str, started_at: str,
            jobs_found: int, jobs_new: int, error: Optional[str]) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO scrape_runs (run_id, source, started_at, finished_at,
                                     jobs_found, jobs_new, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, feed, started_at, utc_now_iso(), jobs_found, jobs_new, error),
        )


def last_success_at(conn: sqlite3.Connection, feed: str) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(started_at) FROM scrape_runs WHERE source = ? AND error IS NULL", (feed,)
    ).fetchone()
    return row[0] if row else None


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Job scores (AI / fallback match scoring)
# ---------------------------------------------------------------------------

def get_job_score(conn: sqlite3.Connection, job_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM job_scores WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def upsert_job_score(
    conn: sqlite3.Connection,
    job_id: str,
    match_score: int,
    skill_match: int,
    experience_match: int,
    location_match: int,
    role_match: int,
    matched_skills: str,
    missing_skills: str,
    explanation: str,
    eligible: bool,
    score_type: str,
    model: Optional[str],
    scored_at: Optional[str] = None,
) -> None:
    now = scored_at or utc_now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO job_scores
                (job_id, match_score, skill_match, experience_match, location_match, role_match,
                 matched_skills, missing_skills, explanation, eligible, score_type, model, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                match_score  = excluded.match_score,
                skill_match  = excluded.skill_match,
                experience_match = excluded.experience_match,
                location_match = excluded.location_match,
                role_match   = excluded.role_match,
                matched_skills = excluded.matched_skills,
                missing_skills = excluded.missing_skills,
                explanation  = excluded.explanation,
                eligible     = excluded.eligible,
                score_type   = excluded.score_type,
                model        = excluded.model,
                scored_at    = excluded.scored_at
            """,
            (
                job_id, match_score, skill_match, experience_match, location_match, role_match,
                matched_skills, missing_skills, explanation, int(eligible), score_type, model, now,
            ),
        )
        conn.execute(
            "UPDATE jobs SET match_score = ?, score_type = ? WHERE job_id = ?",
            (match_score, score_type, job_id),
        )


def get_unscored_job_ids(
    conn: sqlite3.Connection, limit: Optional[int] = None
) -> list[str]:
    """Job ids that are active, not expired, have no score yet, and have a valid apply link."""
    sql = """
        SELECT j.job_id FROM jobs j
        LEFT JOIN job_scores s ON s.job_id = j.job_id
        WHERE j.status = ? AND s.job_id IS NULL AND j.apply_link IS NOT NULL AND j.apply_link != ''
        ORDER BY j.scraped_at DESC
    """
    params: list = [STATUS_ACTIVE]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def get_unscored_jobs(
    conn: sqlite3.Connection, limit: Optional[int] = None
) -> list[dict]:
    """Active jobs with a valid apply link that have not been AI/fallback scored yet."""
    sql = """
        SELECT j.* FROM jobs j
        LEFT JOIN job_scores s ON s.job_id = j.job_id
        WHERE j.status = ? AND s.job_id IS NULL AND j.apply_link IS NOT NULL AND j.apply_link != ''
        ORDER BY j.scraped_at DESC
    """
    params: list = [STATUS_ACTIVE]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_high_match_jobs(
    conn: sqlite3.Connection, min_score: int, limit: Optional[int] = None
) -> list[dict]:
    """Scored, active, eligible jobs at or above min_score, best first."""
    sql = """
        SELECT j.*, s.skill_match, s.experience_match, s.location_match, s.role_match,
               s.matched_skills, s.missing_skills, s.explanation, s.eligible, s.score_type, s.model
        FROM jobs j
        JOIN job_scores s ON s.job_id = j.job_id
        WHERE j.status = ? AND s.match_score >= ? AND s.eligible = 1
        ORDER BY s.match_score DESC, j.scraped_at DESC
    """
    params: list = [STATUS_ACTIVE, min_score]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# Notifications (Telegram delivery history)
# ---------------------------------------------------------------------------

def record_notification(
    conn: sqlite3.Connection,
    job_id: str,
    chat_id: str,
    status: str,
    message_id: Optional[int] = None,
    sent_at: Optional[str] = None,
    retry_count: int = 0,
    error_message: Optional[str] = None,
) -> None:
    now = sent_at or utc_now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO notifications
                (job_id, telegram_chat_id, notification_status, telegram_message_id,
                 sent_at, retry_count, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, chat_id, status, message_id, now, retry_count, error_message),
        )


def is_job_notified(conn: sqlite3.Connection, job_id: str) -> bool:
    """True if a successful (sent) notification exists for this job."""
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE job_id = ? AND notification_status = 'sent' LIMIT 1",
        (job_id,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Saved jobs
# ---------------------------------------------------------------------------

def save_job(conn: sqlite3.Connection, job_id: str, chat_id: str, saved_at: Optional[str] = None) -> bool:
    now = saved_at or utc_now_iso()
    try:
        with conn:
            conn.execute(
                "INSERT INTO saved_jobs (job_id, telegram_chat_id, saved_at) VALUES (?, ?, ?)",
                (job_id, chat_id, now),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def unsave_job(conn: sqlite3.Connection, job_id: str, chat_id: str) -> int:
    with conn:
        cur = conn.execute(
            "DELETE FROM saved_jobs WHERE job_id = ? AND telegram_chat_id = ?", (job_id, chat_id)
        )
    return cur.rowcount


def get_saved_job_ids(conn: sqlite3.Connection, chat_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT job_id FROM saved_jobs WHERE telegram_chat_id = ? ORDER BY saved_at DESC",
        (chat_id,),
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Candidate preferences (configurable profile)
# ---------------------------------------------------------------------------

def get_candidate_preference(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM candidate_preferences WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_candidate_preference(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO candidate_preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_candidate_preferences(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM candidate_preferences").fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Scrape history
# ---------------------------------------------------------------------------

def record_scrape_history(
    conn: sqlite3.Connection,
    run_id: str,
    source: str,
    jobs_found: int = 0,
    jobs_new: int = 0,
    jobs_scored: int = 0,
    jobs_notified: int = 0,
    errors: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO scrape_history
                (run_id, source, jobs_found, jobs_new, jobs_scored, jobs_notified,
                 errors, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, source, jobs_found, jobs_new, jobs_scored, jobs_notified,
             errors, started_at, finished_at),
        )


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------

def update_source_health(
    conn: sqlite3.Connection,
    source: str,
    status: str,
    *,
    last_successful_scrape: Optional[str] = None,
    error_count: Optional[int] = None,
    jobs_collected: Optional[int] = None,
    enabled: Optional[bool] = None,
    updated_at: Optional[str] = None,
) -> None:
    now = updated_at or utc_now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO source_health
                (source, status, last_successful_scrape, error_count, jobs_collected, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                status = excluded.status,
                last_successful_scrape = COALESCE(excluded.last_successful_scrape, source_health.last_successful_scrape),
                error_count = COALESCE(excluded.error_count, source_health.error_count),
                jobs_collected = COALESCE(excluded.jobs_collected, source_health.jobs_collected),
                enabled = COALESCE(excluded.enabled, source_health.enabled),
                updated_at = excluded.updated_at
            """,
            (
                source, status, last_successful_scrape, error_count, jobs_collected,
                int(enabled) if enabled is not None else None, now,
            ),
        )


def get_source_health(conn: sqlite3.Connection, source: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM source_health WHERE source = ?", (source,)).fetchone()
    return dict(row) if row else None


def get_all_source_health(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
    return [dict(r) for r in rows]
