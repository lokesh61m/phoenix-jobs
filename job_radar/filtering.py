from __future__ import annotations

import re
import unicodedata

from .models import Job


ROLE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Software Engineer", ("software engineer", "software development engineer", "software developer", "sde")),
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


MAX_REQUIRED_YEARS = 3.5
OPTIONAL_EXPERIENCE_MARKERS = ("preferred", "nice to have", "bonus", "plus")
OPTIONAL_SECTION_MARKERS = re.compile(
    r"\b(?:preferred|desired|nice to have|bonus)\s+(?:qualifications?|requirements?|experience|skills?)?\b"
)
REQUIRED_SECTION_MARKERS = re.compile(
    r"\b(?:required|basic|minimum)\s+(?:qualifications?|requirements?|experience|skills?)\b"
)


def normalized(value: str) -> str:
    value = (value or "").replace("–", "-").replace("—", "-")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
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


def passes_experience(job: Job) -> bool:
    text = normalized(job.description)
    number = r"\d+(?:\.\d+)?"

    def is_optional(match: re.Match[str]) -> bool:
        # A preferred-qualifications section can be separated from its years
        # phrase by a long degree/skills bullet. Looking only a few characters
        # around the number incorrectly rejected valid jobs such as a role
        # requiring 2+ years but preferring 5+ years.
        before = text[:match.start()]
        last_optional = list(OPTIONAL_SECTION_MARKERS.finditer(before))
        last_required = list(REQUIRED_SECTION_MARKERS.finditer(before))
        if last_optional and (not last_required or last_optional[-1].start() > last_required[-1].start()):
            return True

        start = max(0, match.start() - 240)
        end = min(len(text), match.end() + 240)
        context = text[start:end]
        return any(marker in context for marker in OPTIONAL_EXPERIENCE_MARKERS)

    # For ranges, use the upper bound: a posting asking for 3-5 years can
    # require more than the user's 3.5-year ceiling.
    range_pattern = rf"\b({number})\s*(?:-|to)\s*({number})\s*(?:years?|yrs?)\b"
    for match in re.finditer(range_pattern, text):
        if not is_optional(match) and float(match.group(2)) > MAX_REQUIRED_YEARS:
            return False

    # Catch 4 years, 4+ years, and similar wording.
    single_pattern = rf"\b({number})\s*\+?\s*(?:years?|yrs?)\b"
    for match in re.finditer(single_pattern, text):
        if not is_optional(match) and float(match.group(1)) > MAX_REQUIRED_YEARS:
            return False

    # "More than 3.5 years" exceeds the ceiling even though the number itself
    # is not greater than 3.5.
    more_than_pattern = rf"\b(?:more than|over)\s+({number})\s*(?:years?|yrs?)\b"
    for match in re.finditer(more_than_pattern, text):
        if not is_optional(match) and float(match.group(1)) >= MAX_REQUIRED_YEARS:
            return False

    return True


def passes(job: Job) -> bool:
    job.categories = categories_for(job)
    title = normalized(job.title)
    obvious_mismatch = ("intern", "internship", "apprentice", "co op", "co-op", "staff ", "principal ", "director", "architect")
    return bool(
        job.title
        and job.url
        and job.categories
        and not any(term in title for term in obvious_mismatch)
        and not re.search(r"\b(?:senior|manager)\b", title)
        and is_us_role(job)
        and passes_experience(job)
    )
