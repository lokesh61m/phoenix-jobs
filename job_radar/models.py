from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    company: str
    source_type: str
    source_id: str
    title: str
    location: str
    url: str
    apply_url: str
    description: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    workplace_type: str = ""
    department: str = ""
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    last_sent_at: datetime | None = None
    fingerprint: str = ""
    categories: list[str] = field(default_factory=list)

    @property
    def effective_date(self) -> datetime | None:
        return self.published_at or self.updated_at
