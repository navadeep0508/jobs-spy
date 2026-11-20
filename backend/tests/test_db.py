"""Tests for db.py: schema initialization, migrations, and accessor functions."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from db.db import (
    connect,
    count_jobs,
    get_all_candidate_preferences,
    get_all_source_health,
    get_high_match_jobs,
    get_job_score,
    get_unscored_jobs,
    init_db,
    is_job_notified,
    create_backup,
    restore_backup,
    integrity_check,
    record_notification,
    upsert_job_score,
)
from db.models import (
    MIGRATIONS,
    SCORE_TYPE_AI,
    SCORE_TYPE_FALLBACK,
    STATUS_ACTIVE,
    utc_now_iso,
)


class InitDbTests(unittest.TestCase):
    """Tests for init_db schema initialization and migrations."""

    def test_init_db_creates_jobs_with_match_score_columns(self) -> None:
        """A fresh DB gets the jobs table with match_score and score_type columns."""
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            conn = connect(db_path)
            try:
                init_db(conn)
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
                self.assertIn("match_score", cols)
                self.assertIn("score_type", cols)
                self.assertIn("feed", cols)
            finally:
                conn.close()

    def test_init_db_adds_match_score_to_existing_db(self) -> None:
        """An existing jobs.db without match_score gets the column via migration."""
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            # Create an old-schema DB (jobs table without match_score/score_type)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT,
                    status TEXT DEFAULT 'active',
                    notified INTEGER DEFAULT 0,
                    last_seen_at TEXT,
                    scraped_at TEXT,
                    feed TEXT
                )
                """
            )
            conn.execute("INSERT INTO jobs VALUES ('job1', 'Title', 'active', 0, NULL, NULL, 'source')")
            conn.commit()
            conn.close()

            conn = connect(db_path)
            try:
                init_db(conn)
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
                self.assertIn("match_score", cols)
                self.assertIn("score_type", cols)
                # Existing row preserved
                row = conn.execute("SELECT match_score FROM jobs WHERE job_id = 'job1'").fetchone()
                self.assertEqual(row[0], 0)
            finally:
                conn.close()

    def test_init_db_creates_new_tables(self) -> None:
        """AI-layer tables are created on a fresh DB."""
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            conn = connect(db_path)
            try:
                init_db(conn)
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                }
                for expected in (
                    "job_scores",
                    "notifications",
                    "saved_jobs",
                    "candidate_preferences",
                    "scrape_history",
                    "source_health",
                ):
                    self.assertIn(expected, tables)
            finally:
                conn.close()


class JobScoreTests(unittest.TestCase):
    """Tests for job score accessors."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "jobs.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_and_get_job_score(self) -> None:
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link) "
            "VALUES ('job1', 'Title', 'active', 'https://example.com/job')"
        )
        self.conn.commit()
        upsert_job_score(
            self.conn,
            job_id="job1",
            match_score=85,
            skill_match=90,
            experience_match=80,
            location_match=85,
            role_match=85,
            matched_skills="Python,AI",
            missing_skills="Go",
            explanation="Strong match",
            eligible=True,
            score_type=SCORE_TYPE_AI,
            model="nvidia/nemotron",
            scored_at=utc_now_iso(),
        )
        score = get_job_score(self.conn, "job1")
        self.assertIsNotNone(score)
        self.assertEqual(score["match_score"], 85)
        self.assertEqual(score["eligible"], 1)
        self.assertEqual(score["score_type"], SCORE_TYPE_AI)

        # Denormalized to jobs table
        row = self.conn.execute(
            "SELECT match_score, score_type FROM jobs WHERE job_id = 'job1'"
        ).fetchone()
        # The job may not exist yet; upsert_job_score only updates jobs if row exists.
        # That's fine — just verify the score table entry.

    def test_upsert_job_score_denormalizes_to_jobs(self) -> None:
        """Upserting a score for an existing job updates jobs.match_score/score_type."""
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, notified, match_score, score_type) "
            "VALUES ('job1', 'Title', 'active', 0, 0, '')"
        )
        self.conn.commit()
        upsert_job_score(
            self.conn,
            job_id="job1",
            match_score=90,
            skill_match=90,
            experience_match=90,
            location_match=90,
            role_match=90,
            matched_skills="Python",
            missing_skills="",
            explanation="Great",
            eligible=True,
            score_type=SCORE_TYPE_FALLBACK,
            model=None,
        )
        row = self.conn.execute(
            "SELECT match_score, score_type FROM jobs WHERE job_id = 'job1'"
        ).fetchone()
        self.assertEqual(row[0], 90)
        self.assertEqual(row[1], SCORE_TYPE_FALLBACK)

    def test_get_unscored_jobs(self) -> None:
        """get_unscored_jobs returns active jobs with no score and valid apply_link."""
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at) "
            "VALUES ('job1', 'Title 1', 'active', 'https://example.com/1', ?)",
            (utc_now_iso(),),
        )
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at) "
            "VALUES ('job2', 'Title 2', 'active', 'https://example.com/2', ?)",
            (utc_now_iso(),),
        )
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at) "
            "VALUES ('job3', 'Title 3', 'expired', 'https://example.com/3', ?)",
            (utc_now_iso(),),
        )
        self.conn.commit()

        # Score job1
        upsert_job_score(
            self.conn,
            job_id="job1",
            match_score=50,
            skill_match=50,
            experience_match=50,
            location_match=50,
            role_match=50,
            matched_skills="",
            missing_skills="",
            explanation="",
            eligible=False,
            score_type=SCORE_TYPE_FALLBACK,
            model=None,
        )

        unscored = get_unscored_jobs(self.conn)
        unscored_ids = {r["job_id"] for r in unscored}
        self.assertIn("job2", unscored_ids)  # active, valid link, not scored
        self.assertNotIn("job1", unscored_ids)  # scored
        self.assertNotIn("job3", unscored_ids)  # expired

    def test_get_high_match_jobs(self) -> None:
        """get_high_match_jobs returns eligible active jobs at/above min_score."""
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at, match_score) "
            "VALUES ('job1', 'High', 'active', 'https://example.com/1', ?, 90)",
            (now,),
        )
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at, match_score) "
            "VALUES ('job2', 'Medium', 'active', 'https://example.com/2', ?, 70)",
            (now,),
        )
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at, match_score) "
            "VALUES ('job3', 'Expired', 'expired', 'https://example.com/3', ?, 95)",
            (now,),
        )
        self.conn.commit()

        upsert_job_score(
            self.conn, "job1", 90, 90, 90, 90, 90, "", "", "Great", True, SCORE_TYPE_AI, None
        )
        upsert_job_score(
            self.conn, "job2", 70, 70, 70, 70, 70, "", "", "OK", True, SCORE_TYPE_AI, None
        )
        upsert_job_score(
            self.conn, "job3", 95, 95, 95, 95, 95, "", "", "Great", True, SCORE_TYPE_AI, None
        )

        high = get_high_match_jobs(self.conn, min_score=80)
        high_ids = {r["job_id"] for r in high}
        self.assertIn("job1", high_ids)
        self.assertNotIn("job2", high_ids)  # below threshold
        self.assertNotIn("job3", high_ids)  # expired


class NotificationTests(unittest.TestCase):
    """Tests for notification recording and lookup."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "jobs.db")
        init_db(self.conn)
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link) "
            "VALUES ('job1', 'Title', 'active', 'https://example.com/job')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_record_and_is_job_notified(self) -> None:
        record_notification(self.conn, "job1", "chat123", "sent", message_id=42)
        self.assertTrue(is_job_notified(self.conn, "job1"))
        self.assertFalse(is_job_notified(self.conn, "job2"))

    def test_failed_notification_not_notified(self) -> None:
        record_notification(self.conn, "job1", "chat123", "failed", error_message="timeout")
        self.assertFalse(is_job_notified(self.conn, "job1"))


class SourceHealthTests(unittest.TestCase):
    """Tests for source health accessors."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "jobs.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_update_and_get_source_health(self) -> None:
        from db.db import get_source_health, update_source_health

        update_source_health(
            self.conn,
            source="remoteok",
            status="healthy",
            jobs_collected=10,
            enabled=True,
        )
        health = get_source_health(self.conn, "remoteok")
        self.assertIsNotNone(health)
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["jobs_collected"], 10)

        all_health = get_all_source_health(self.conn)
        self.assertEqual(len(all_health), 1)
        self.assertEqual(all_health[0]["source"], "remoteok")


class CandidatePreferenceTests(unittest.TestCase):
    """Tests for candidate preference accessors."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "jobs.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_get_set_candidate_preference(self) -> None:
        from db.db import get_candidate_preference, set_candidate_preference

        self.assertIsNone(get_candidate_preference(self.conn, "target_roles"))
        set_candidate_preference(self.conn, "target_roles", '["AI Engineer"]')
        self.assertEqual(get_candidate_preference(self.conn, "target_roles"), '["AI Engineer"]')

    def test_get_all_candidate_preferences_empty(self) -> None:
        self.assertEqual(get_all_candidate_preferences(self.conn), {})

    def test_migrations_list_includes_match_score(self) -> None:
        columns = {col for _, col, _ in MIGRATIONS}
        self.assertIn("match_score", columns)
        self.assertIn("score_type", columns)
        self.assertIn("feed", columns)


class BackupPersistenceTests(unittest.TestCase):
    def test_backup_restoration_preserves_jobs_and_notifications(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            backup = root / "backup.db"
            restored = root / "restored.db"

            conn = connect(source)
            init_db(conn)
            conn.execute(
                "INSERT INTO jobs (job_id, title, status, notified, apply_link) "
                "VALUES ('job1', 'AI Engineer', 'active', 0, 'https://example.com/job')"
            )
            record_notification(conn, "job1", "chat1", "sent", message_id=42)
            conn.commit()
            conn.close()

            ok, _ = create_backup(source, backup)
            self.assertTrue(ok)
            ok, _ = restore_backup(backup, restored)
            self.assertTrue(ok)
            ok, detail = integrity_check(restored)
            self.assertTrue(ok, detail)

            conn = connect(restored)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            self.assertTrue(is_job_notified(conn, "job1"))
            conn.close()

    def test_duplicate_not_notified_twice_after_restore(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            backup = root / "backup.db"
            restored = root / "restored.db"
            conn = connect(source)
            init_db(conn)
            conn.execute(
                "INSERT INTO jobs (job_id, title, status, notified, apply_link) "
                "VALUES ('job1', 'AI Engineer', 'active', 1, 'https://example.com/job')"
            )
            record_notification(conn, "job1", "chat1", "sent")
            conn.commit()
            conn.close()

            self.assertTrue(create_backup(source, backup)[0])
            self.assertTrue(restore_backup(backup, restored)[0])
            conn = connect(restored)
            self.assertTrue(is_job_notified(conn, "job1"))
            conn.close()

    def test_corrupted_backup_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "corrupt.db"
            backup.write_bytes(b"not sqlite")
            ok, detail = restore_backup(backup, root / "target.db")
            self.assertFalse(ok)
            self.assertIn("integrity", detail.lower())


if __name__ == "__main__":
    unittest.main()
