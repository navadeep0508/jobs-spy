"""Tests for notifier/telegram.py: formatting and notification logic."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

from db.db import init_db
from notifier import telegram


class TelegramFormatTests(unittest.TestCase):
    """Tests for job message formatting."""

    def test_format_job(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT 'Senior AI Engineer' AS title,
                   'Acme Corp' AS company,
                   'Bengaluru' AS location,
                   'tech,remote' AS job_type,
                   'remoteok' AS source,
                   'Python, AI' AS skills_tags,
                   NULL AS salary,
                   NULL AS deadline,
                   'https://example.com/apply' AS apply_link
            """
        ).fetchone()
        text = telegram.format_job(row)
        self.assertIn("Senior AI Engineer", text)
        self.assertIn("Acme Corp", text)
        self.assertIn("Bengaluru", text)
        self.assertIn("https://example.com/apply", text)

    def test_format_scored_job_includes_score(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT 'AI Engineer' AS title,
                   'Acme' AS company,
                   'Remote' AS location,
                   'tech' AS job_type,
                   'remoteok' AS source,
                   'Python' AS skills_tags,
                   NULL AS salary,
                   NULL AS deadline,
                   'https://example.com/apply' AS apply_link,
                   90 AS match_score,
                   'ai' AS score_type,
                   'Python' AS matched_skills,
                   'Go' AS missing_skills,
                   'Strong match' AS explanation
            """
        ).fetchone()
        text = telegram._format_scored_job(row)
        self.assertIn("🤖 AI score: 90/100", text)
        self.assertIn("Matched skills: Python", text)
        self.assertIn("Missing: Go", text)
        self.assertIn("Strong match", text)


class TelegramCandidateTests(unittest.TestCase):
    """Tests for candidate selection logic."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "jobs.db"))
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_no_jobs_returns_empty(self) -> None:
        rows = telegram._candidates(self.conn)
        self.assertEqual(rows, [])

    def test_notified_jobs_excluded(self) -> None:
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, notified, posted_date, apply_link) "
            "VALUES ('job1', 'Title', 'active', 1, datetime('now'), 'https://example.com/1')"
        )
        self.conn.commit()
        rows = telegram._candidates(self.conn)
        self.assertEqual(rows, [])


class TelegramSendTests(unittest.TestCase):
    """Tests for send_message with mocked requests."""

    @patch("notifier.telegram.requests.post")
    def test_send_message_success(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        result = telegram.send_message("Hello")
        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("notifier.telegram.requests.post")
    def test_send_message_failure(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.ok = False
        mock_resp.json.return_value = {"description": "Bad request"}
        mock_post.return_value = mock_resp

        result = telegram.send_message("Hello")
        self.assertFalse(result)

    def test_is_configured_false_without_env(self) -> None:
        import os

        old_token = os.environ.pop("TELEGRAM_TOKEN", None)
        old_chat = os.environ.pop("TELEGRAM_CHAT_ID", None)
        try:
            self.assertFalse(telegram.is_configured())
        finally:
            if old_token is not None:
                os.environ["TELEGRAM_TOKEN"] = old_token
            if old_chat is not None:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat


if __name__ == "__main__":
    unittest.main()
