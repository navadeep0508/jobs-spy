"""Deterministic fallback matcher.

Used when NVIDIA is unavailable (no key, network failure, rate limit, timeout).
Scores a job purely from keyword heuristics against the candidate profile. Results
are clearly labeled score_type="fallback" so they are never mistaken for AI scores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ai.profile import CandidateProfile


@dataclass
class MatchResult:
    match_score: int
    skill_match: int
    experience_match: int
    location_match: int
    role_match: int
    matched_skills: list[str]
    missing_skills: list[str]
    explanation: str
    eligible: bool
    score_type: str = "fallback"


def _whole_word_present(text: str, term: str) -> bool:
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", re.I)
    return bool(pattern.search(text))


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(_whole_word_present(text, t) for t in terms if t)


def _intersection(text: str, terms: list[str]) -> list[str]:
    found = []
    for t in terms:
        if t and _whole_word_present(text, t) and t not in found:
            found.append(t)
    return found


class FallbackMatcher:
    """Keyword-based matcher that mirrors the AI scoring structure."""

    def __init__(self, profile: CandidateProfile):
        self.profile = profile

    def score(self, title: str, description: str, location: str, job_type: str) -> MatchResult:
        text = " ".join(filter(None, [title, description]))
        loc_text = " ".join(filter(None, [title, description, location or "", job_type or ""]))
        title_lower = title.lower()
        senior_title = bool(re.search(
            r"\b(senior|lead|principal|staff|architect|manager|director|vp|head)\b",
            title_lower,
        ))
        unrelated_title = bool(re.search(
            r"\b(sales|marketing|customer support|driver|nurse|teacher|cashier|accountant|finance)\b",
            title_lower,
        ))

        # --- Skill match -----------------------------------------------------
        matched_skills = _intersection(text, self.profile.technical_skills)
        missing_skills = [s for s in self.profile.technical_skills if s not in matched_skills]
        skill_match = _pct(len(matched_skills), max(1, len(self.profile.technical_skills)))

        # --- Role match ------------------------------------------------------
        role_keywords = self.profile.target_roles + self.profile.technical_skills
        role_hits = _intersection(text, role_keywords)
        role_match = min(100, len(role_hits) * 25)

        # --- Experience match (fresher/internship friendly) ------------------
        exp_text = text.lower()
        is_intern = bool(re.search(r"\b(intern|internship|apprentice|fresher|entry.?level|graduate|0.?[-–]1|0.?[-–]2)\b", exp_text))
        is_senior = bool(re.search(r"\b(senior|lead|principal|staff|architect|manager|5\+|6\+|7\+|8\+|10\+)\s*(year|yr)s?\b", exp_text))
        if is_intern and not is_senior:
            experience_match = 100
        elif is_senior:
            experience_match = 20
        elif _contains_any(exp_text, self.profile.experience_levels):
            experience_match = 80
        else:
            experience_match = 60

        # --- Location match --------------------------------------------------
        location_match = 100 if _contains_any(loc_text, self.profile.target_locations) else (
            60 if _contains_any(loc_text, self.profile.prioritize_locations) else 30
        )

        # --- Overall score (weighted) ----------------------------------------
        weights = {
            "skill": 0.35,
            "role": 0.25,
            "experience": 0.20,
            "location": 0.20,
        }
        match_score = int(
            skill_match * weights["skill"]
            + role_match * weights["role"]
            + experience_match * weights["experience"]
            + location_match * weights["location"]
        )
        match_score = max(0, min(100, match_score))

        min_score = self.profile.min_match_score
        eligible = (
            match_score >= min_score
            and not is_senior
            and not senior_title
            and not unrelated_title
        )

        parts = []
        if matched_skills:
            parts.append("skills: " + ", ".join(matched_skills[:6]))
        if is_intern:
            parts.append("entry-level/internship friendly")
        if is_senior:
            parts.append("senior role (de-prioritized)")
        if location_match >= 100:
            parts.append("target location")
        elif location_match >= 60:
            parts.append("prioritized location")
        explanation = "Fallback keyword score. " + "; ".join(parts) if parts else (
            "Fallback keyword score. No strong profile signals detected."
        )

        return MatchResult(
            match_score=match_score,
            skill_match=skill_match,
            experience_match=experience_match,
            location_match=location_match,
            role_match=role_match,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            explanation=explanation,
            eligible=eligible,
            score_type="fallback",
        )


def _pct(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return max(0, min(100, int(round(part / whole * 100))))
