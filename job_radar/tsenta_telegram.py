"""Separate, opt-in Telegram shortlist for manually adding jobs to Tsenta.

No Tsenta API calls and no application starts occur in this module.
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone

from .config import db_path, setup_environment
from .storage import Store
from .telegram import TelegramError, resolve_chat_ids, send_text_document
from .tsenta_candidates import Candidate, load_selected_companies, select_candidates


MAX_PER_RUN = 10


def document_for(candidate: Candidate) -> tuple[str, str, str]:
    job = candidate.job
    identifier = re.sub(r"[^a-zA-Z0-9_-]+", "-", job.source_id or job.fingerprint[:12])[:30]
    company = re.sub(r"[^a-zA-Z0-9_-]+", "-", job.company).strip("-")[:35]
    filename = f"{company}-{identifier}.txt"
    skills = ", ".join(candidate.matched_skills) or "not specified"
    posted = job.effective_date.strftime("%Y-%m-%d %H:%M UTC") if job.effective_date else "first seen recently; posted date unknown"
    caption = (
        f"{job.company} — {job.title}\n{job.location}\n"
        f"Sponsorship: {candidate.sponsorship} · Matched skills: {skills}\n{job.url}"
    )
    content = (
        f"Company: {job.company}\nRole: {job.title}\nLocation: {job.location}\n"
        f"Posted: {posted}\nSponsorship: {candidate.sponsorship}\n"
        f"Matched profile skills: {skills}\nJob link: {job.url}\n\n"
        f"JOB DESCRIPTION\n\n{job.description.strip()}\n"
    )
    return filename, caption, content


def run_once(store: Store, token: str, profile_skills: str, now: datetime) -> int:
    cutoff = now - timedelta(hours=24)
    candidates = select_candidates(store.recent_jobs(cutoff), profile_skills, load_selected_companies(), cutoff)
    pending = [candidate for candidate in candidates if candidate.score >= 2 and not store.tsenta_notified(candidate.job)]
    if not pending:
        print("[tsenta telegram] eligible_new=0")
        return 0
    chat_ids = resolve_chat_ids(token)
    sent = 0
    for candidate in pending[:MAX_PER_RUN]:
        filename, caption, content = document_for(candidate)
        try:
            for chat_id in chat_ids:
                send_text_document(token, chat_id, filename, content, caption)
        except (TelegramError, OSError) as exc:
            # Network exceptions may include request URLs; never risk printing
            # the BotFather token embedded in Telegram's API path.
            print(f"[tsenta telegram error] {candidate.job.company} | {candidate.job.title}: {type(exc).__name__}")
            continue
        store.mark_tsenta_notified(candidate.job, now)
        sent += 1
    print(f"[tsenta telegram] eligible_new={len(pending)} sent={sent} recipients={len(chat_ids)}")
    return sent


def main() -> None:
    setup_environment()
    parser = argparse.ArgumentParser(description="Send Tsenta shortlist to a separate Telegram bot")
    parser.add_argument("--dry-run", action="store_true", help="list candidates without sending messages")
    args = parser.parse_args()
    token = os.environ.get("TSENTA_TELEGRAM_BOT_TOKEN", "").strip()
    profile_skills = os.environ.get("TSENTA_PROFILE_SKILLS", "").strip()
    if not token or not profile_skills:
        print("[tsenta telegram] not configured: TSENTA_TELEGRAM_BOT_TOKEN and TSENTA_PROFILE_SKILLS are required")
        return
    store = Store(str(db_path()))
    now = datetime.now(timezone.utc)
    if args.dry_run:
        cutoff = now - timedelta(hours=24)
        candidates = select_candidates(store.recent_jobs(cutoff), profile_skills, load_selected_companies(), cutoff)
        pending = [item for item in candidates if item.score >= 2 and not store.tsenta_notified(item.job)]
        print(f"[tsenta telegram dry-run] eligible_new={len(pending)}")
        for item in pending[:MAX_PER_RUN]:
            print(f"- {item.job.company} | {item.job.title} | {item.sponsorship} | skills={item.score} | {item.job.url}")
        return
    run_once(store, token, profile_skills, now)


if __name__ == "__main__":
    main()
