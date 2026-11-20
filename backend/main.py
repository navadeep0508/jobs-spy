"""Pipeline entry point. Run from the backend/ folder:

    python main.py                      # full run (scrape -> score -> notify -> export)
    python main.py --list               # show all feeds and exit
    python main.py --only remoteok      # just one source (prefix match: --only greenhouse)
    python main.py --skip-group india   # everything except the India scrapers
    python main.py --skip naukri,foundit
    python main.py --no-notify          # scrape + export, no Telegram
    python main.py --test-telegram      # send one test Telegram message and exit
    python main.py --test-nvidia        # test the NVIDIA NIM connection and exit
    python main.py --stats              # print scraping / matching statistics

Order: re-tag if skills changed -> scrape (each feed isolated) -> normalize -> tag skills ->
       upsert/dedupe -> expire -> score new jobs with NVIDIA Nemotron (cached) ->
       notify high-match eligible jobs -> export jobs.json
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai.client import NVIDIAClient
from ai.matcher import AIMatchingService
from ai.profile import CandidateProfile
from config import settings
from db.db import (
    DB_PATH,
    connect,
    count_jobs,
    get_high_match_jobs,
    get_unscored_jobs,
    init_db,
    last_success_at,
    log_run,
    migrate_legacy_database,
    record_scrape_history,
    upsert_jobs,
    update_source_health,
)
from db.models import utc_now_iso
from expiry import checker as expiry
from exporter import export_json
from filters import skill_matcher
from notifier import telegram
from scrapers.base import Feed, PartialResult
from scrapers.registry import all_feeds, select_feeds

log = logging.getLogger("main")
LOG_DIR = Path(__file__).resolve().parent / "logs"


def setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    for noisy in ("urllib3", "asyncio", "openai", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_dotenv() -> None:
    """Load KEY=VALUE lines from a local .env (repo root or backend/) for local testing.
    Real environment variables win. In GitHub Actions secrets come from the workflow."""
    for path in (Path(__file__).resolve().parent / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if os.environ.get("TELEGRAM_TOKEN") and not os.environ.get("TELEGRAM_BOT_TOKEN"):
        os.environ["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_TOKEN"]


def _hours_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600


def run_feed(conn, feed: Feed, run_id: str, ignore_throttle: bool) -> dict:
    """Run one feed in isolation. Never raises."""
    result = {"feed": feed.name, "found": 0, "new": 0, "error": None, "skipped": None}
    missing = [v for v in feed.requires_env if not os.environ.get(v)]
    if missing:
        result["skipped"] = f"missing env {', '.join(missing)}"
        log.info("[%s] skipped: %s", feed.name, result["skipped"])
        return result
    if feed.min_interval_hours and not ignore_throttle:
        last = last_success_at(conn, feed.name)
        if last and _hours_since(last) < feed.min_interval_hours:
            result["skipped"] = f"throttled (runs every {feed.min_interval_hours:g}h)"
            log.info("[%s] skipped: %s", feed.name, result["skipped"])
            return result

    started_at = utc_now_iso()
    t0 = time.monotonic()
    jobs, error = [], None
    try:
        jobs = feed.fetch()
    except PartialResult as partial:
        jobs, error = partial.jobs, f"partial: {partial}"
    except Exception as exc:  # isolation: one broken source never stops the run
        error = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("[%s] failed: %s", feed.name, error, exc_info=log.isEnabledFor(logging.DEBUG))

    cutoff = None
    if feed.max_age_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=feed.max_age_days)).isoformat()
    unique = {}
    stale = 0
    for job in jobs:
        if cutoff and job.posted_date and job.posted_date < cutoff:
            stale += 1
            continue
        job.feed = feed.name
        skill_matcher.tag_job(job)
        unique[job.job_id] = job
    try:
        new = upsert_jobs(conn, unique.values(), seen_at=started_at)
    except Exception as exc:
        new, error = 0, f"DB error: {exc}"[:500]
        log.exception("[%s] DB write failed", feed.name)

    log_run(conn, run_id, feed.name, started_at, len(unique), new, error)
    result.update(found=len(unique), new=new, error=error)
    log.info("[%s] found=%d new=%d%s%s (%.1fs)", feed.name, len(unique), new,
             f" stale_dropped={stale}" if stale else "",
             f" error={error}" if error else "", time.monotonic() - t0)
    return result


def print_summary(results: list[dict]) -> None:
    ran = [r for r in results if not r["skipped"]]
    log.info("-" * 72)
    log.info("%-32s %7s %7s  %s", "feed", "found", "new", "status")
    for r in results:
        status = r["skipped"] and f"skipped ({r['skipped']})" or (r["error"] and f"ERROR {r['error'][:60]}") or "ok"
        log.info("%-32s %7s %7s  %s", r["feed"][:32], r["found"], r["new"], status)
    log.info("-" * 72)
    log.info("feeds run=%d ok=%d failed=%d skipped=%d | jobs found=%d new=%d",
             len(ran), sum(1 for r in ran if not r["error"]), sum(1 for r in ran if r["error"]),
             len(results) - len(ran), sum(r["found"] for r in ran), sum(r["new"] for r in ran))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Job notifier pipeline")
    p.add_argument("--list", action="store_true", help="list feeds and exit")
    p.add_argument("--only", help="comma-separated feed names or prefixes")
    p.add_argument("--skip", help="comma-separated feed names or prefixes to leave out")
    p.add_argument("--group", help="comma-separated groups to run (api,ats,rss,india)")
    p.add_argument("--skip-group", help="comma-separated groups to skip")
    p.add_argument("--no-notify", action="store_true", help="don't send Telegram alerts")
    p.add_argument("--no-export", action="store_true", help="don't write frontend/jobs.json")
    p.add_argument("--ignore-throttle", action="store_true", help="run throttled feeds anyway")
    p.add_argument("--test-telegram", action="store_true", help="send a test message and exit")
    p.add_argument("--test-nvidia", action="store_true", help="test the NVIDIA NIM connection and exit")
    p.add_argument("--stats", action="store_true", help="print scraping/matching stats and exit")
    p.add_argument("--db", default=str(DB_PATH), help="path to the SQLite file")
    return p.parse_args(argv)


def _split(value):
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _init_candidate_profile(conn, was_empty: bool) -> CandidateProfile:
    """Load (or seed on a brand-new DB) the candidate profile preferences."""
    profile = CandidateProfile.from_db(conn)
    if was_empty:
        profile.to_db(conn)
        log.info("Seeded default candidate preferences into the database")
    return profile


def _score_new_jobs(conn, profile: CandidateProfile, limit: int) -> int:
    """Score every active, unscored job with a valid apply link. Returns count scored."""
    scorer = AIMatchingService(profile=profile)
    jobs = get_unscored_jobs(conn, limit=limit)
    scored = 0
    for job in jobs:
        try:
            scorer.score_job(
                conn,
                job_id=job["job_id"],
                title=job["title"] or "",
                company=job["company"] or "",
                location=job["location"] or "",
                description=job["description"] or "",
                job_type=job["job_type"] or "",
            )
            scored += 1
        except Exception as exc:  # one job must never block scoring the rest
            log.warning("Failed to score job %s (%s): %s", job["job_id"], job["title"], exc)
    log.info("AI scoring: scored %d new job(s) (limit=%d)", scored, limit)
    return scored


def _track_source_health(conn, run_id: str, results: list[dict]) -> None:
    """Record per-feed health and scrape history after the run."""
    for r in results:
        name = r["feed"]
        if r["skipped"]:
            status = "skipped"
            error_count = 0
            jobs_collected = 0
        elif r["error"]:
            status = "unhealthy"
            error_count = 1
            jobs_collected = r["found"]
        else:
            status = "healthy"
            error_count = 0
            jobs_collected = r["found"]
        update_source_health(
            conn,
            source=name,
            status=status,
            error_count=error_count,
            jobs_collected=jobs_collected,
            enabled=True,
        )
        record_scrape_history(
            conn,
            run_id=run_id,
            source=name,
            jobs_found=r["found"],
            jobs_new=r["new"],
            started_at=None,
            finished_at=None,
            errors=r["error"],
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()
    setup_logging()

    if args.test_telegram:
        if not telegram.is_configured():
            log.error("Set TELEGRAM_BOT_TOKEN (or legacy TELEGRAM_TOKEN) and TELEGRAM_CHAT_ID first")
            return 1
        ok = telegram.send_message("✅ Test message from your job notifier.")
        log.info("Telegram test %s", "sent" if ok else "FAILED")
        return 0 if ok else 1

    if args.test_nvidia:
        client = NVIDIAClient()
        ok, msg = client.test_connection()
        log.info("NVIDIA test: %s", msg)
        if not ok:
            log.error("Set NVIDIA_API_KEY (and optionally NVIDIA_MODEL / NVIDIA_BASE_URL) first")
        return 0 if ok else 1

    if args.stats:
        if args.db == str(DB_PATH):
            legacy = migrate_legacy_database(args.db)
            if legacy:
                log.info("Migrated legacy SQLite database from %s to %s", legacy, args.db)
        conn = connect(args.db)
        init_db(conn)
        try:
            _print_stats(conn)
        finally:
            conn.close()
        return 0

    feeds = select_feeds(all_feeds(), _split(args.only), _split(args.group), _split(args.skip_group),
                         _split(args.skip))
    if args.list:
        for f in feeds:
            extra = []
            if f.requires_env:
                extra.append(f"needs {','.join(f.requires_env)}")
            if f.min_interval_hours:
                extra.append(f"every {f.min_interval_hours:g}h")
            print(f"{f.group:6} {f.name:36} {f.source:18} {' '.join(extra)}")
        print(f"{len(feeds)} feeds")
        return 0

    conn = connect(args.db)
    init_db(conn)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    was_empty = count_jobs(conn) == 0
    log.info("Run %s starting: %d feeds, DB=%s", run_id, len(feeds), args.db)

    profile = _init_candidate_profile(conn, was_empty)

    # Before scraping, so freshly scraped jobs keep tags computed from their full text.
    skill_matcher.retag_all(conn)

    results = [run_feed(conn, f, run_id, args.ignore_throttle) for f in feeds]
    print_summary(results)
    _track_source_health(conn, run_id, results)

    complete = {f.name for f in feeds if f.complete}
    succeeded = [r["feed"] for r in results
                 if r["feed"] in complete and not r["skipped"] and not r["error"] and r["found"] > 0]
    expiry.run(conn, succeeded)
    expiry.prune(conn)

    # --- AI matching step: score every new unscored job ---------------------
    scored = _score_new_jobs(conn, profile, limit=settings.AI_MAX_JOBS_PER_RUN)

    if was_empty and count_jobs(conn) > 0:
        # First ever run: load everything silently instead of sending thousands of alerts.
        n = telegram.mark_all_notified(conn)
        log.info("First run: marked %d jobs as already notified (no alerts)", n)
        if not args.no_notify:
            telegram.send_bootstrap_summary(n)
    elif not args.no_notify:
        telegram.notify_scored_jobs(conn, settings.MIN_MATCH_SCORE)

    if not args.no_export:
        try:
            export_json.export(conn)
        except Exception:
            log.exception("Export to jobs.json failed")

    conn.close()
    ran = [r for r in results if not r["skipped"]]
    if ran and all(r["error"] and not r["found"] for r in ran):
        log.error("Run %s finished, but every feed failed (network problem?)", run_id)
        return 1
    log.info("Run %s finished (scored=%d)", run_id, scored)
    return 0


def _print_stats(conn) -> None:
    """Print scraping, matching and notification statistics (--stats)."""
    from db.db import get_all_source_health, get_all_candidate_preferences

    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'active'").fetchone()[0]
    expired = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'expired'").fetchone()[0]
    scored = conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0]
    notified = conn.execute("SELECT COUNT(*) FROM jobs WHERE notified = 1").fetchone()[0]
    high = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE match_score >= ? AND status = 'active'",
        (settings.MIN_MATCH_SCORE,),
    ).fetchone()[0]
    by_type = conn.execute(
        "SELECT score_type, COUNT(*) FROM job_scores GROUP BY score_type"
    ).fetchall()
    log.info("=== Job Radar statistics ===")
    log.info("Jobs: total=%d active=%d expired=%d", total_jobs, active, expired)
    log.info("Scored: total=%d high_match(>=%d)=%d", scored, settings.MIN_MATCH_SCORE, high)
    log.info("Notified (jobs.notified=1): %d", notified)
    if by_type:
        log.info("Scores by type: %s", ", ".join(f"{r[0]}={r[1]}" for r in by_type))
    health = get_all_source_health(conn)
    if health:
        log.info("Source health: %s", ", ".join(f"{h['source']}={h['status']}" for h in health))
    prefs = get_all_candidate_preferences(conn)
    if prefs:
        log.info("Candidate preferences seeded: %d keys", len(prefs))


if __name__ == "__main__":
    sys.exit(main())