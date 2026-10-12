from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .models import Job


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def compact(value: str) -> str:
    return re.sub(r"\W+", " ", value.lower()).strip()


def make_fingerprint(job: Job) -> str:
    stable = "|".join((compact(job.company), compact(job.title), compact(job.location), compact(job.description)[:2500]))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                fingerprint TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                url TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                published_at TEXT,
                updated_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_sent_at TEXT,
                description TEXT NOT NULL,
                categories TEXT NOT NULL
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS jobs_lookup ON jobs(company, title, location)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS jobs_source_lookup ON jobs(company, source_type, source_id)")
        self.connection.commit()

    def upsert(self, job: Job) -> tuple[Job, bool]:
        job.fingerprint = make_fingerprint(job)
        current = self.connection.execute("SELECT * FROM jobs WHERE fingerprint = ?", (job.fingerprint,)).fetchone()
        if not current and job.source_type and job.source_id:
            # A posting's description or URL can change while its source ID
            # remains stable. Prefer that stable career-page identity before
            # treating the changed payload as a new posting.
            current = self.connection.execute(
                "SELECT * FROM jobs WHERE company = ? AND source_type = ? AND source_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                (job.company, job.source_type, job.source_id),
            ).fetchone()
        if current:
            job.fingerprint = current["fingerprint"]
            self.connection.execute(
                """
                UPDATE jobs SET source_type = ?, source_id = ?, company = ?, title = ?, location = ?,
                    url = ?, apply_url = ?, published_at = ?, last_seen_at = ?, updated_at = ?,
                    description = ?, categories = ? WHERE fingerprint = ?
                """,
                (
                    job.source_type, job.source_id, job.company, job.title, job.location,
                    job.url, job.apply_url, iso(job.published_at), iso(job.last_seen_at),
                    iso(job.updated_at), job.description, ",".join(job.categories), job.fingerprint,
                ),
            )
            job.first_seen_at = parse_iso(current["first_seen_at"]) or job.first_seen_at
            job.last_sent_at = parse_iso(current["last_sent_at"])
            self.connection.commit()
            return job, False

        # URL changes and small text edits are common during reposts. Treat a close
        # match from the same company/title/location as the same posting.
        candidates = self.connection.execute(
            "SELECT * FROM jobs WHERE company = ? AND title = ? AND location = ? ORDER BY last_seen_at DESC LIMIT 20",
            (job.company, job.title, job.location),
        ).fetchall()
        new_body = compact(job.description)[:2500]
        for candidate in candidates:
            old_body = compact(candidate["description"])[:2500]
            if new_body and old_body and SequenceMatcher(None, new_body, old_body).ratio() >= 0.92:
                job.fingerprint = candidate["fingerprint"]
                job.first_seen_at = parse_iso(candidate["first_seen_at"]) or job.first_seen_at
                job.last_sent_at = parse_iso(candidate["last_sent_at"])
                self.connection.execute(
                    "UPDATE jobs SET last_seen_at = ?, updated_at = ?, url = ?, apply_url = ?, description = ?, categories = ? WHERE fingerprint = ?",
                    (iso(job.last_seen_at), iso(job.updated_at), job.url, job.apply_url, job.description, ",".join(job.categories), job.fingerprint),
                )
                self.connection.commit()
                return job, False

        self.connection.execute(
            """
            INSERT INTO jobs (
                fingerprint, source_type, source_id, company, title, location, url, apply_url,
                published_at, updated_at, first_seen_at, last_seen_at, description, categories
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.fingerprint, job.source_type, job.source_id, job.company, job.title,
                job.location, job.url, job.apply_url, iso(job.published_at), iso(job.updated_at),
                iso(job.first_seen_at), iso(job.last_seen_at), job.description, ",".join(job.categories),
            ),
        )
        self.connection.commit()
        return job, True

    def mark_sent(self, jobs: list[Job], sent_at: datetime) -> None:
        for job in jobs:
            self.connection.execute("UPDATE jobs SET last_sent_at = ? WHERE fingerprint = ?", (iso(sent_at), job.fingerprint))
        self.connection.commit()
