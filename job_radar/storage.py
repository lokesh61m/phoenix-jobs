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


def tsenta_source_key(job: Job) -> str:
    if job.source_type and job.source_id:
        return f"{compact(job.company)}|{job.source_type}|{job.source_id}"
    return f"fingerprint|{job.fingerprint}"


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
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS tsenta_notifications (
                fingerprint TEXT PRIMARY KEY,
                source_key TEXT NOT NULL UNIQUE,
                sent_at TEXT NOT NULL
            )"""
        )
        self.connection.commit()

    def recent_jobs(self, cutoff: datetime) -> list[Job]:
        rows = self.connection.execute(
            "SELECT * FROM jobs WHERE last_seen_at >= ? ORDER BY last_seen_at DESC", (iso(cutoff),)
        ).fetchall()
        return [
            Job(
                company=row["company"], source_type=row["source_type"], source_id=row["source_id"],
                title=row["title"], location=row["location"], url=row["url"],
                apply_url=row["apply_url"], description=row["description"],
                published_at=parse_iso(row["published_at"]), updated_at=parse_iso(row["updated_at"]),
                first_seen_at=parse_iso(row["first_seen_at"]) or datetime.now(timezone.utc),
                last_seen_at=parse_iso(row["last_seen_at"]) or datetime.now(timezone.utc),
                last_sent_at=parse_iso(row["last_sent_at"]), fingerprint=row["fingerprint"],
                categories=row["categories"].split(",") if row["categories"] else [],
            )
            for row in rows
        ]

    def tsenta_notified(self, job: Job) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM tsenta_notifications WHERE fingerprint = ? OR source_key = ?",
            (job.fingerprint, tsenta_source_key(job)),
        ).fetchone() is not None

    def mark_tsenta_notified(self, job: Job, at: datetime) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO tsenta_notifications(fingerprint, source_key, sent_at) VALUES (?, ?, ?)",
                (job.fingerprint, tsenta_source_key(job), iso(at)),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

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
        # match from the same company/title/location as the same posting. Some
        # boards also publish duplicate requisition IDs with slightly different
        # descriptions on the same day, so the posting date is another strong
        # duplicate signal.
        candidates = self.connection.execute(
            "SELECT * FROM jobs WHERE company = ? AND title = ? ORDER BY last_seen_at DESC LIMIT 50",
            (job.company, job.title),
        ).fetchall()
        new_body = compact(job.description)[:2500]
        published_day = iso(job.published_at)[:10] if job.published_at else None
        for candidate in candidates:
            if compact(candidate["location"]) != compact(job.location):
                continue
            old_body = compact(candidate["description"])[:2500]
            candidate_day = (candidate["published_at"] or "")[:10] or None
            same_posting_day = published_day and candidate_day == published_day
            similar_body = new_body and old_body and SequenceMatcher(None, new_body, old_body).ratio() >= 0.92
            if same_posting_day or similar_body:
                job.fingerprint = candidate["fingerprint"]
                job.first_seen_at = parse_iso(candidate["first_seen_at"]) or job.first_seen_at
                job.last_sent_at = parse_iso(candidate["last_sent_at"])
                self.connection.execute(
                    """
                    UPDATE jobs SET source_type = ?, source_id = ?, url = ?, apply_url = ?,
                        published_at = ?, last_seen_at = ?, updated_at = ?, description = ?, categories = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        job.source_type, job.source_id, job.url, job.apply_url, iso(job.published_at),
                        iso(job.last_seen_at), iso(job.updated_at), job.description, ",".join(job.categories),
                        job.fingerprint,
                    ),
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
