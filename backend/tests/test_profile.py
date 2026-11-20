"""Tests for ai/profile.py: CandidateProfile defaults and DB persistence."""

from __future__ import annotations

import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from ai.profile import CandidateProfile, DEFAULTS, _as_list, _as_int


class CandidateProfileDefaultsTests(unittest.TestCase):
    """Tests for CandidateProfile default values."""

    def test_defaults_match_de_constants(self) -> None:
        profile = CandidateProfile()
        self.assertEqual(profile.target_roles, DEFAULTS["target_roles"])
        self.assertEqual(profile.technical_skills, DEFAULTS["technical_skills"])
        self.assertEqual(profile.target_locations, DEFAULTS["target_locations"])
        self.assertEqual(profile.experience_levels, DEFAULTS["experience_levels"])
        self.assertEqual(profile.min_match_score, 80)
        self.assertEqual(profile.prioritize_locations, DEFAULTS["prioritize_locations"])

    def test_default_lists_are_independent_copies(self) -> None:
        """Each instance gets its own list copies (no shared mutable default)."""
        p1 = CandidateProfile()
        p2 = CandidateProfile()
        p1.target_roles.append("Foo")
        self.assertNotIn("Foo", p2.target_roles)

    def test_as_context_text_includes_profile(self) -> None:
        profile = CandidateProfile()
        text = profile.as_context_text()
        self.assertIn("AI Engineer", text)
        self.assertIn("Python", text)
        self.assertIn("Remote", text)
        self.assertIn("80", text)


class CandidateProfileDbTests(unittest.TestCase):
    """Tests for from_db / to_db persistence."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "jobs.db"))
        self.conn.row_factory = sqlite3.Row
        from db.db import init_db

        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_from_db_returns_defaults_on_empty_db(self) -> None:
        profile = CandidateProfile.from_db(self.conn)
        self.assertEqual(profile.target_roles, DEFAULTS["target_roles"])
        self.assertEqual(profile.min_match_score, 80)

    def test_from_db_returns_stored_values(self) -> None:
        from db.db import set_candidate_preference

        set_candidate_preference(
            self.conn, "target_roles", '["AI Engineer", "ML Engineer"]'
        )
        set_candidate_preference(self.conn, "min_match_score", "90")
        profile = CandidateProfile.from_db(self.conn)
        self.assertEqual(profile.target_roles, ["AI Engineer", "ML Engineer"])
        self.assertEqual(profile.min_match_score, 90)

    def test_to_db_persists_profile(self) -> None:
        from db.db import get_candidate_preference

        profile = CandidateProfile(
            target_roles=["Data Scientist"],
            technical_skills=["Python"],
            target_locations=["Remote"],
            prioritize_locations=["Remote"],
            experience_levels=["Entry Level"],
            min_match_score=75,
        )
        profile.to_db(self.conn)
        self.assertEqual(
            get_candidate_preference(self.conn, "target_roles"), '["Data Scientist"]'
        )
        self.assertEqual(get_candidate_preference(self.conn, "min_match_score"), "75")


class HelperTests(unittest.TestCase):
    """Tests for internal helper functions."""

    def test_as_list(self) -> None:
        self.assertEqual(_as_list('["a", "b"]'), ["a", "b"])
        self.assertEqual(_as_list(""), [])
        self.assertEqual(_as_list(None), [])
        self.assertEqual(_as_list("not json"), [])

    def test_as_int(self) -> None:
        self.assertEqual(_as_int("80", 0), 80)
        self.assertEqual(_as_int(None, 5), 5)
        self.assertEqual(_as_int("notint", 5), 5)


if __name__ == "__main__":
    unittest.main()
