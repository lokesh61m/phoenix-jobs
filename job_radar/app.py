from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import companies_path, db_path, env_int, setup_environment
from .filtering import passes
from .models import Job
from .sources import SourceError, fetch_source
from .storage import Store
from .telegram import TelegramError, resolve_chat_id, send_jobs


def load_companies(path: Path) -> list[dict[str, str]]:
    return [entry for entry in json.loads(path.read_text()) if entry.get("enabled", True)]


def is_fresh(job: Job, cutoff: datetime) -> bool:
    source_date = job.effective_date
    return source_date is None or source_date >= cutoff


def scan(dry_run: bool = False) -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=env_int("LOOKBACK_HOURS", 72))
    store = Store(str(db_path()))
    new_jobs: list[Job] = []
    source_count = 0
    source_errors = 0

    entries = load_companies(companies_path())
    workers = max(1, min(16, env_int("FETCH_WORKERS", 12)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_source, entry): entry for entry in entries}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                fetched = future.result()
                source_count += 1
            except Exception as exc:
                source_errors += 1
                print(f"[source error] {entry['company']} ({entry['type']}): {exc}")
                continue
            for job in fetched:
                if not is_fresh(job, cutoff) or not passes(job):
                    continue
                saved, _is_new = store.upsert(job)
                # Keep failed notifications queued. A scan may discover the job
                # successfully, then Telegram may be unavailable or unconfigured.
                # It must be sent on the next successful scan instead of being lost.
                if saved.last_sent_at is None:
                    new_jobs.append(saved)

    new_jobs.sort(key=lambda job: (job.company.lower(), job.title.lower()))
    print(f"[scan] sources={source_count} errors={source_errors} new_jobs={len(new_jobs)}")
    if dry_run:
        for job in new_jobs:
            print(f"- {job.company} | {job.title} | {job.location} | {job.apply_url}")
        return len(new_jobs)
    if not new_jobs:
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[notify] TELEGRAM_BOT_TOKEN is not configured; jobs remain unsent and will retry next scan")
        return len(new_jobs)
    try:
        chat_id = resolve_chat_id(token)
        send_jobs(token, chat_id, new_jobs, now)
        store.mark_sent(new_jobs, now)
        print(f"[notify] sent {len(new_jobs)} jobs to Telegram chat {chat_id}")
    except (TelegramError, OSError) as exc:
        print(f"[notify error] {exc}")
    return len(new_jobs)


def main() -> None:
    setup_environment()
    parser = argparse.ArgumentParser(description="Monitor fresh US software and AI jobs")
    parser.add_argument("--once", action="store_true", help="run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="scan and print new jobs without Telegram")
    args = parser.parse_args()
    if args.once:
        scan(dry_run=args.dry_run)
        return

    interval = max(5, env_int("POLL_INTERVAL_MINUTES", 20)) * 60
    print(f"Software Job Radar running; refresh interval={interval // 60} minutes")
    while True:
        started = time.monotonic()
        scan(dry_run=args.dry_run)
        remaining = interval - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
