"""Tests for ai/matcher.py: AIMatchingService with mocked NVIDIA client."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

from ai.matcher import AIMatchingService, _extract_json, _validate_result
from ai.profile import CandidateProfile
from db.db import init_db, upsert_job_score


class AIMatchingServiceTests(unittest.TestCase):
    """Tests for AIMatchingService scoring with mocked AI client."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "jobs.db"))
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.profile = CandidateProfile()
        self.scorer = AIMatchingService(profile=self.profile)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _insert_job(self, job_id: str) -> None:
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, status, apply_link, scraped_at) "
            "VALUES (?, 'Title', 'active', 'https://example.com/job', datetime('now'))",
            (job_id,),
        )
        self.conn.commit()

    def test_score_job_uses_cache(self) -> None:
        """A cached score is returned without calling the AI client."""
        self._insert_job("job1")
        upsert_job_score(
            self.conn,
            job_id="job1",
            match_score=77,
            skill_match=77,
            experience_match=77,
            location_match=77,
            role_match=77,
            matched_skills="",
            missing_skills="",
            explanation="cached",
            eligible=True,
            score_type="ai",
            model="nvidia/nemotron",
        )

        result = self.scorer.score_job(
            self.conn,
            job_id="job1",
            title="Title",
            company="Acme",
            location="Remote",
            description="description",
            job_type="tech",
        )
        self.assertEqual(result.match_score, 77)
        self.assertEqual(result.score_type, "ai")

    def test_score_job_falls_back_when_nvidia_unavailable(self) -> None:
        """When NVIDIA client raises, the fallback matcher is used."""
        self._insert_job("job1")

        with patch.object(self.scorer.client, "chat_completion", side_effect=Exception("API down")):
            result = self.scorer.score_job(
                self.conn,
                job_id="job1",
                title="Machine Learning Engineer",
                company="Acme",
                location="Remote",
                description="Python, PyTorch, LLM, RAG role.",
                job_type="tech,remote",
            )

        self.assertEqual(result.score_type, "fallback")
        self.assertGreaterEqual(result.match_score, 0)
        # Score was cached in job_scores
        cached = self.conn.execute(
            "SELECT match_score, score_type FROM job_scores WHERE job_id = 'job1'"
        ).fetchone()
        self.assertIsNotNone(cached)
        self.assertEqual(cached["score_type"], "fallback")

    def test_extract_json(self) -> None:
        """_extract_json extracts a JSON object from text wrapped in markdown."""
        text = 'Here is the result:\n```json\n{"match_score": 85}\n```'
        result = _extract_json(text)
        self.assertEqual(result["match_score"], 85)

    def test_extract_json_plain(self) -> None:
        """_extract_json parses plain JSON text."""
        text = '{"match_score": 90, "eligible": true}'
        result = _extract_json(text)
        self.assertEqual(result["match_score"], 90)
        self.assertTrue(result["eligible"])

    def test_validate_result(self) -> None:
        """_validate_result coerces and validates fields."""
        result = _validate_result(
            {"match_score": "85", "eligible": "true", "matched_skills": ["Python"]}
        )
        self.assertEqual(result.match_score, 85)
        self.assertTrue(result.eligible)
        self.assertEqual(result.matched_skills, ["Python"])

    def test_validate_result_bounds_score(self) -> None:
        """_validate_result does not clamp (clamping is done by _clamp separately)."""
        result = _validate_result({"match_score": 150, "eligible": True})
        self.assertEqual(result.match_score, 150)


if __name__ == "__main__":
    unittest.main()
