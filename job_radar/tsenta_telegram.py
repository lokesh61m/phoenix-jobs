"""Separate, opt-in Telegram shortlist for manually adding jobs to Tsenta.

No Tsenta API calls and no application starts occur in this module.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

from .config import db_path, setup_environment
from .storage import Store
from .telegram import TelegramError, resolve_chat_ids, send_plain_message
from .tsenta_candidates import Candidate, load_selected_companies, select_candidates


MAX_PER_RUN = 10
MESSAGE_CHUNK_SIZE = 3600


def message_text_for(candidate: Candidate) -> str:
    job = candidate.job
    skills = ", ".join(candidate.matched_skills) or "not specified"
    posted = job.effective_date.strftime("%Y-%m-%d %H:%M UTC") if job.effective_date else "first seen recently; posted date unknown"
    return (
        f"Company: {job.company}\nRole: {job.title}\nLocation: {job.location}\n"
        f"Posted: {posted}\nSponsorship: {candidate.sponsorship}\n"
        f"Matched profile skills: {skills}\nJob link: {job.url}\n\n"
        f"JOB DESCRIPTION\n\n{job.description.strip()}\n"
    )


def messages_for(candidate: Candidate) -> list[str]:
    """Keep each plain-text message below Telegram's 4096-character limit."""
    remaining = message_text_for(candidate)
    chunks: list[str] = []
    while len(remaining) > MESSAGE_CHUNK_SIZE:
        boundary = next(
            (index + 1 for index in range(MESSAGE_CHUNK_SIZE - 1, MESSAGE_CHUNK_SIZE // 2, -1)
             if remaining[index].isspace()),
            MESSAGE_CHUNK_SIZE,
        )
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    chunks.append(remaining)
    if len(chunks) == 1:
        return chunks
    label = f"{candidate.job.company} — {candidate.job.title}"[:120]
    return [f"{label} · part {index}/{len(chunks)}\n\n{chunk}" for index, chunk in enumerate(chunks, 1)]


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
        try:
            for chat_id in chat_ids:
                for message in messages_for(candidate):
                    send_plain_message(token, chat_id, message)
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
