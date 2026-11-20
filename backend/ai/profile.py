"""Candidate profile configuration.

The profile (target roles, skills, locations, experience) is stored in the
``candidate_preferences`` SQLite table as JSON strings and can be edited at any
time. Sensible defaults match the target candidate described in the project plan
(AI/ML-focused fresher/internship seeker in India / remote).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import sqlite3


DEFAULTS = {
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
    "min_match_score": "80",
}

# Preference keys that are stored as JSON lists.
_LIST_KEYS = {
    "target_roles", "technical_skills", "target_locations",
    "prioritize_locations", "experience_levels",
}


@dataclass
class CandidateProfile:
    target_roles: list[str] = field(default_factory=lambda: list(DEFAULTS["target_roles"]))
    technical_skills: list[str] = field(default_factory=lambda: list(DEFAULTS["technical_skills"]))
    target_locations: list[str] = field(default_factory=lambda: list(DEFAULTS["target_locations"]))
    prioritize_locations: list[str] = field(default_factory=lambda: list(DEFAULTS["prioritize_locations"]))
    experience_levels: list[str] = field(default_factory=lambda: list(DEFAULTS["experience_levels"]))
    min_match_score: int = 80

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> "CandidateProfile":
        prefs = _load_prefs(conn)
        return cls(
            target_roles=_as_list(prefs.get("target_roles")) or list(DEFAULTS["target_roles"]),
            technical_skills=_as_list(prefs.get("technical_skills")) or list(DEFAULTS["technical_skills"]),
            target_locations=_as_list(prefs.get("target_locations")) or list(DEFAULTS["target_locations"]),
            prioritize_locations=_as_list(prefs.get("prioritize_locations")) or list(DEFAULTS["prioritize_locations"]),
            experience_levels=_as_list(prefs.get("experience_levels")) or list(DEFAULTS["experience_levels"]),
            min_match_score=_as_int(prefs.get("min_match_score"), int(DEFAULTS["min_match_score"])),
        )

    def to_db(self, conn: sqlite3.Connection) -> None:
        from db.db import set_candidate_preference

        set_candidate_preference(conn, "target_roles", json.dumps(self.target_roles))
        set_candidate_preference(conn, "technical_skills", json.dumps(self.technical_skills))
        set_candidate_preference(conn, "target_locations", json.dumps(self.target_locations))
        set_candidate_preference(conn, "prioritize_locations", json.dumps(self.prioritize_locations))
        set_candidate_preference(conn, "experience_levels", json.dumps(self.experience_levels))
        set_candidate_preference(conn, "min_match_score", str(self.min_match_score))

    def as_context_text(self) -> str:
        """Human-readable summary injected into the AI matching prompt."""
        return (
            "Target roles: " + ", ".join(self.target_roles) + ".\n"
            "Technical skills: " + ", ".join(self.technical_skills) + ".\n"
            "Target locations: " + ", ".join(self.target_locations) + ".\n"
            "Prioritized locations: " + ", ".join(self.prioritize_locations) + ".\n"
            "Experience levels: " + ", ".join(self.experience_levels) + ".\n"
            "Minimum match score for notification: " + str(self.min_match_score) + ".\n"
        )


def _load_prefs(conn: sqlite3.Connection) -> dict[str, str]:
    from db.db import get_all_candidate_preferences

    return get_all_candidate_preferences(conn)


def _as_list(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _as_int(value: Optional[str], fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
