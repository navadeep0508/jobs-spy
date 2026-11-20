"""AI job matching service.

For each new job, evaluates it against the candidate profile using NVIDIA Nemotron
(via the OpenAI-compatible NIM endpoint) and returns a structured match result.
Scores are cached in SQLite so a job is never scored twice. When NVIDIA is
unavailable, a deterministic fallback matcher is used and the result is clearly
labeled score_type="fallback".
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Optional

from ai.client import NVIDIAClient
from ai.fallback import FallbackMatcher, MatchResult
from ai.profile import CandidateProfile
from db.db import get_job_score, upsert_job_score

log = logging.getLogger("ai.matcher")

# Hard eligibility rules that the AI must never override.
MAX_RETRIES = 2
JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.S)


SYSTEM_PROMPT = """You are an expert technical recruiter. Evaluate whether a job posting is a good match for a candidate.

Candidate profile:
{profile}

Job to evaluate:
Title: {title}
Company: {company}
Location: {location}
Description: {description}

Score each dimension 0-100:
- skill_match: overlap between the job's required skills and the candidate's technical skills
- experience_match: whether the role fits fresher/internship/entry-level (0-2 years). Penalize senior roles.
- location_match: whether the location matches target locations (India cities, Remote, Work from Home)
- role_match: whether the title matches target roles (AI/ML/LLM/Python/Data Engineer/Analyst/Intern)
- match_score: overall weighted score 0-100
- matched_skills: candidate skills actually mentioned in the job
- missing_skills: important candidate skills NOT mentioned
- eligible: true if match_score >= 80 AND the role is not clearly senior/mismatched
- explanation: one or two sentences justifying the score

Hard rules: mark eligible=false for senior/lead/principal roles, non-tech roles, or locations clearly outside India/remote even if the skill match is high.

Think through your evaluation step by step, then provide your final answer as a JSON object on the last line with keys: match_score, skill_match, experience_match, location_match, role_match, matched_skills, missing_skills, explanation, eligible. No markdown fences."""


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from model output."""
    if not text:
        return None
    text = text.strip()
    # Try direct parse first.
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    # Fall back to locating the first {...} block.
    m = JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    return None


def _validate_result(data: Optional[dict]) -> Optional[MatchResult]:
    if not isinstance(data, dict):
        return None
    try:
        return MatchResult(
            match_score=int(data.get("match_score", 0)),
            skill_match=int(data.get("skill_match", 0)),
            experience_match=int(data.get("experience_match", 0)),
            location_match=int(data.get("location_match", 0)),
            role_match=int(data.get("role_match", 0)),
            matched_skills=list(data.get("matched_skills") or []),
            missing_skills=list(data.get("missing_skills") or []),
            explanation=str(data.get("explanation", "") or ""),
            eligible=bool(data.get("eligible", False)),
            score_type="ai",
        )
    except (TypeError, ValueError):
        return None


def _clamp(result: MatchResult) -> MatchResult:
    for field in ("match_score", "skill_match", "experience_match", "location_match", "role_match"):
        value = getattr(result, field)
        setattr(result, field, max(0, min(100, int(value))))
    return result


class AIMatchingService:
    def __init__(
        self,
        profile: CandidateProfile,
        client: Optional[NVIDIAClient] = None,
        fallback: Optional[FallbackMatcher] = None,
    ):
        self.profile = profile
        self.client = client or NVIDIAClient()
        self.fallback = fallback or FallbackMatcher(profile)

    def score_job(
        self,
        conn,
        job_id: str,
        title: str,
        company: str,
        location: str,
        description: str,
        job_type: str = "",
    ) -> MatchResult:
        """Return a cached or freshly computed MatchResult for one job."""
        # Never re-score an already-scored job.
        cached = get_job_score(conn, job_id)
        if cached:
            return MatchResult(
                match_score=int(cached["match_score"]),
                skill_match=int(cached["skill_match"]),
                experience_match=int(cached["experience_match"]),
                location_match=int(cached["location_match"]),
                role_match=int(cached["role_match"]),
                matched_skills=_as_list(cached.get("matched_skills")),
                missing_skills=_as_list(cached.get("missing_skills")),
                explanation=str(cached.get("explanation", "") or ""),
                eligible=bool(int(cached.get("eligible", 0))),
                score_type=str(cached.get("score_type", "ai")),
            )

        result: Optional[MatchResult] = None
        if self.client.configured:
            result = self._score_with_ai(title, company, location, description, job_type)
        if result is None:
            log.info("Using fallback matcher for job %s (%s)", job_id, title)
            result = self.fallback.score(title, description, location, job_type)

        result = _clamp(result)
        upsert_job_score(
            conn,
            job_id=job_id,
            match_score=result.match_score,
            skill_match=result.skill_match,
            experience_match=result.experience_match,
            location_match=result.location_match,
            role_match=result.role_match,
            matched_skills=",".join(result.matched_skills),
            missing_skills=",".join(result.missing_skills),
            explanation=result.explanation,
            eligible=result.eligible,
            score_type=result.score_type,
            model=(self.client.model if result.score_type == "ai" else None),
        )
        return result

    def _score_with_ai(self, title: str, company: str, location: str,
                       description: str, job_type: str) -> Optional[MatchResult]:
        prompt = SYSTEM_PROMPT.format(
            profile=self.profile.as_context_text(),
            title=title,
            company=company,
            location=location or "Not specified",
            description=description[:2500],
        )
        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                content = self.client.chat_completion(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Evaluate this job now."},
                    ],
                    temperature=0.2,
                    max_tokens=1500,
                )
                data = _extract_json(content)
                result = _validate_result(data)
                if result is not None:
                    combined = f"{title} {description}".lower()
                    senior = bool(re.search(
                        r"\b(senior|lead|principal|staff|architect|manager|director|vp|head)\b",
                        combined,
                    ))
                    unrelated = bool(re.search(
                        r"\b(sales|marketing|customer support|driver|nurse|teacher|cashier)\b",
                        combined,
                    ))
                    if senior or unrelated:
                        result.eligible = False
                        result.explanation = (
                            ("Senior-only role." if senior else "Unrelated role category.")
                            + " "
                            + result.explanation
                        ).strip()
                    log.debug("NVIDIA AI score for '%s': %d (eligible=%s)", title,
                              result.match_score, result.eligible)
                    return result
                log.warning("NVIDIA returned unparseable output (attempt %d): %r", attempt, content[:200])
            except Exception as exc:
                last_error = str(exc)
                log.warning("NVIDIA scoring attempt %d failed: %s", attempt, last_error)
        log.error("NVIDIA AI scoring failed after %d attempts (%s); using fallback", MAX_RETRIES, last_error)
        return None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
