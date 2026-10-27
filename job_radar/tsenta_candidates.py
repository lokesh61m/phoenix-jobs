"""Conservative selection for a separate Telegram shortlist for Tsenta.

The user's full resume stays in Tsenta. A short private skills summary is read
from an environment secret, never persisted in the public radar repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .filtering import normalized, passes
from .models import Job
from .storage import make_fingerprint


TSENTA_MAX_REQUIRED_YEARS = 3.0
DEFAULT_COMPANIES_PATH = ROOT / "config" / "tsenta_companies.json"

# A negative statement wins over a positive one if both appear in a posting.
# Ambiguous or silent postings remain "unverified", never "confirmed".
NO_SPONSORSHIP = (
    r"\b(?:no|not offering|does not offer) (?:visa |immigration |employment )?sponsorship\b",
    r"\b(?:visa |immigration |employment )?sponsorship (?:is )?(?:not available|unavailable|not provided|not offered)\b",
    r"\b(?:will not|cannot|can't|unable to|do not|does not) sponsor\b",
    r"\bwithout (?:(?:the need for|requiring|future) )?(?:visa |employment )?sponsorship\b",
    r"\b(?:u\.?s\.?|united states) citizenship (?:is )?required\b",
    r"\bmust be (?:a )?(?:u\.?s\.?|united states) citizen\b",
)
YES_SPONSORSHIP = (
    r"\b(?:visa |immigration |employment )?sponsorship (?:is |will be )?(?:available|provided|offered)\b",
    r"\b(?:we |company )?will sponsor\b",
    r"\b(?:h-?1b|employment visa) sponsorship\b",
)

# Technical phrases are intentionally narrow. We use them to rank jobs, not to
# claim a hiring probability or to silently discard one of the user's role types.
SKILL_TERMS = (
    "distributed systems", "backend", "spring boot", "java", "golang", "go", "aws",
    "dynamodb", "sqs", "step functions", "kubernetes", "microservices", "kafka",
    "spark", "airflow", "python", "scala", "c++", "grpc", "rest api", "react",
    "redis", "postgresql", "observability", "sre", "ci/cd", "llm", "rag",
    "bedrock", "machine learning", "mlops", "data platform", "cloud infrastructure",
    "payment", "inference", "open telemetry", "opentelemetry",
)


@dataclass(frozen=True)
class Candidate:
    job: Job
    sponsorship: str
    matched_skills: tuple[str, ...]
    score: int


def load_selected_companies(path: Path = DEFAULT_COMPANIES_PATH) -> set[str]:
    return {normalized(name) for name in json.loads(path.read_text())}


def sponsorship_status(description: str) -> str:
    text = normalized(description)
    if any(re.search(pattern, text) for pattern in NO_SPONSORSHIP):
        return "not_available"
    if any(re.search(pattern, text) for pattern in YES_SPONSORSHIP):
        return "confirmed"
    return "unverified"


def _has_term(text: str, term: str) -> bool:
    phrase = re.escape(normalized(term))
    return bool(re.search(r"(?<!\w)" + phrase + r"(?!\w)", text))


def matched_skills(job: Job, profile_summary: str) -> tuple[str, ...]:
    summary = normalized(profile_summary)
    posting = normalized(f"{job.title} {job.description}")
    return tuple(term for term in SKILL_TERMS if _has_term(summary, term) and _has_term(posting, term))


def select_candidates(
    jobs: list[Job], profile_summary: str, selected_companies: set[str], cutoff: datetime
) -> list[Candidate]:
    selected: dict[str, Candidate] = {}
    for job in jobs:
        if normalized(job.company) not in selected_companies:
            continue
        # Undated boards are eligible only if we first saw the posting in this
        # window; a continuously visible old listing is not a fresh job.
        if (job.effective_date or job.first_seen_at) < cutoff:
            continue
        if not passes(job, max_required_years=TSENTA_MAX_REQUIRED_YEARS):
            continue
        sponsorship = sponsorship_status(job.description)
        if sponsorship == "not_available":
            continue
        skills = matched_skills(job, profile_summary)
        # Title relevance, US location, and experience are hard gates above.
        # This score is a transparent skill-overlap rank, not a fit percentage.
        score = len(skills)
        candidate = Candidate(job=job, sponsorship=sponsorship, matched_skills=skills, score=score)
        key = job.fingerprint or make_fingerprint(job)
        if key not in selected or score > selected[key].score:
            selected[key] = candidate
    return sorted(
        selected.values(),
        key=lambda item: (-item.score, item.sponsorship != "confirmed", item.job.company.lower(), item.job.title.lower()),
    )
