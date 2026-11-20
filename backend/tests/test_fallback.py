"""Tests for ai/fallback.py: deterministic fallback matcher."""

from __future__ import annotations

import unittest

from ai.fallback import FallbackMatcher, MatchResult
from ai.profile import CandidateProfile


class FallbackMatcherTests(unittest.TestCase):
    """Tests for the fallback (keyword heuristic) matcher."""

    def setUp(self) -> None:
        self.profile = CandidateProfile()
        self.matcher = FallbackMatcher(profile=self.profile)

    def test_score_returns_match_result(self) -> None:
        result = self.matcher.score(
            title="AI Engineer",
            description="We are looking for a Python Developer with Machine Learning experience.",
            location="Bengaluru",
            job_type="tech",
        )
        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.score_type, "fallback")
        self.assertGreaterEqual(result.match_score, 0)
        self.assertLessEqual(result.match_score, 100)
        self.assertIsInstance(result.matched_skills, list)
        self.assertIsInstance(result.missing_skills, list)
        self.assertIsInstance(result.explanation, str)
        self.assertIsInstance(result.eligible, bool)

    def test_high_match_for_relevant_job(self) -> None:
        result = self.matcher.score(
            title="Machine Learning Engineer",
            description="Python, PyTorch, LLM, RAG, LangChain, AI Engineer role.",
            location="Remote",
            job_type="tech,remote",
        )
        # Relevant AI/ML job should score meaningfully above zero
        self.assertGreater(result.match_score, 30)
        self.assertEqual(result.score_type, "fallback")

    def test_low_match_for_irrelevant_job(self) -> None:
        result = self.matcher.score(
            title="Accountant",
            description="We need someone to manage books and tax filings.",
            location="New York",
            job_type="non-tech",
        )
        self.assertLess(result.match_score, 60)
        self.assertFalse(result.eligible)

    def test_empty_description(self) -> None:
        result = self.matcher.score(
            title="Developer",
            description="",
            location="",
            job_type="",
        )
        self.assertGreaterEqual(result.match_score, 0)
        self.assertLessEqual(result.match_score, 100)


if __name__ == "__main__":
    unittest.main()
