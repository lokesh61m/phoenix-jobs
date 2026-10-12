from __future__ import annotations

import re
import unicodedata

from .models import Job


ROLE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Software Engineer", ("software engineer", "software development engineer", "software developer")),
    ("Backend / distributed systems", ("backend", "back-end", "distributed systems", "distributed computing")),
    ("Full-stack", ("full stack", "full-stack", "frontend", "front-end", "web engineer")),
    ("ML Engineer", ("machine learning engineer", "ml engineer", "machine learning")),
    ("AI Engineer", ("ai engineer", "artificial intelligence", "applied ai")),
    ("Data/ML Platform", ("data platform", "ml platform", "machine learning platform", "data engineer")),
    ("MLOps / infrastructure", ("mlops", "mle platform", "platform engineer", "infrastructure engineer")),
    ("Computer vision", ("computer vision", "perception engineer", "image processing")),
    ("NLP / LLMs", ("natural language", "nlp", "large language model", "llm", "generative ai")),
]

US_MARKERS = (
    "united states", "u.s.", "usa", "us -", "us remote", "remote - us", "remote, us",
    "new york", "san francisco", "seattle", "boston", "austin", "chicago", "atlanta",
    "los angeles", "denver", "dallas", "washington, dc", "washington dc", "palo alto",
    "mountain view", "san jose", "portland", "philadelphia", "miami", "salt lake city",
    "raleigh", "bellevue", "redmond", "minneapolis", "detroit", "houston", "phoenix",
)
US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
}
US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
)
NON_US_MARKERS = (
    "worldwide", "global remote", "europe", "emea", "canada", "united kingdom", "uk",
    "india", "australia", "singapore", "germany", "ireland", "israel", "japan",
    "latin america", "latam", "mexico", "brazil", "france", "netherlands",
)


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", value).strip()


def categories_for(job: Job) -> list[str]:
    title = normalized(f"{job.title} {job.department}")
    direct = [label for label, patterns in ROLE_PATTERNS if any(pattern in title for pattern in patterns)]
    if direct:
        return direct

    # Generic engineering titles are allowed only when their body clearly
    # describes software/ML work. This prevents titles like Account Director
    # or Product Manager from matching because the description mentions AI.
    non_building = ("sales", "solutions", "pre sales", "customer", "success", "support", "account", "recruit", "product manager", "program manager", "designer")
    if any(word in title for word in non_building) or not any(word in title for word in ("engineer", "developer")):
        return []
    body = normalized(job.description[:5000])
    technical_terms = (
        "software development", "backend", "distributed systems", "machine learning", "deep learning",
        "computer vision", "natural language", "large language model", "llm", "mlops", "data platform",
        "python", "java", "c++", "golang", "kubernetes", "aws", "api design",
    )
    if any(term in body for term in technical_terms):
        return ["Software Engineer"]
    return []


def is_us_role(job: Job) -> bool:
    location = normalized(job.location)
    if any(marker in location for marker in NON_US_MARKERS):
        return any(marker in location for marker in US_MARKERS)
    if any(marker in location for marker in US_MARKERS):
        return True
    if re.search(r"(?:^|[ ,/])(?:" + "|".join(sorted(US_STATE_CODES, key=len, reverse=True)) + r")(?:$|[ ,/])", location):
        return True
    if any(state in location for state in US_STATE_NAMES):
        return True
    if "remote" in location:
        body = normalized(job.description[:5000])
        return any(phrase in body for phrase in ("remote in the united states", "remote within the united states", "us remote", "remote - us", "remote, us"))
    return False


def minimum_years_required(job: Job) -> int | None:
    text = normalized(f"{job.title} {job.description}")
    candidates: list[int] = []
    patterns = (
        r"(?:at least|minimum of|min(?:imum)?|requires?|required)\s+(\d+)\+?\s*(?:years?|yrs?)",
        r"(\d+)\+\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)",
        r"(\d+)\s*[-–]\s*(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidates.append(int(match.group(1)))
    return min(candidates) if candidates else None


def passes_experience(job: Job) -> bool:
    text = normalized(job.description)
    high_requirement = re.search(r"\b([5-9]|[1-9][0-9])\s*\+?\s*(?:years?|yrs?)\b", text)
    if high_requirement:
        start = max(0, high_requirement.start() - 100)
        context = text[start:high_requirement.end() + 100]
        if not any(word in context for word in ("preferred", "nice to have", "bonus", "plus")):
            return False
    required = minimum_years_required(job)
    return required is None or required <= 4


def passes(job: Job) -> bool:
    job.categories = categories_for(job)
    title = normalized(job.title)
    obvious_mismatch = ("intern", "internship", "apprentice", "co op", "co-op", "staff ", "principal ", "director", "architect")
    return bool(
        job.title
        and job.url
        and job.categories
        and not any(term in title for term in obvious_mismatch)
        and is_us_role(job)
        and passes_experience(job)
    )
