from __future__ import annotations

import html
import json
import os
import ssl
import urllib.parse
import urllib.request
from datetime import datetime

from .models import Job


class TelegramError(RuntimeError):
    pass


try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


def call(token: str, method: str, values: dict[str, str] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(values or {}).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "SoftwareJobRadar/0.1"})
    with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", f"Telegram {method} failed"))
    return payload


def send_plain_message(token: str, chat_id: str, text: str) -> None:
    call(token, "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})


def configured_chat_ids() -> set[str]:
    """Return explicitly configured recipients, accepting one or many IDs."""
    values = [
        os.environ.get("TELEGRAM_CHAT_ID", ""),
        os.environ.get("TELEGRAM_CHAT_IDS", ""),
    ]
    chat_ids: set[str] = set()
    for value in values:
        chat_ids.update(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())
    return chat_ids


def resolve_chat_ids(token: str) -> list[str]:
    """Resolve explicitly configured recipients plus recent private /start chats.

    Telegram expires updates after 24 hours. Use a chat-ID secret for reliable
    delivery beyond that window instead of relying on old /start updates.
    """
    chat_ids = configured_chat_ids()
    payload = call(token, "getUpdates", {"limit": "100", "timeout": "0"})
    updates = payload.get("result", [])
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        if chat.get("type") == "private" and text.startswith("/start"):
            chat_ids.add(str(chat["id"]))
    if not chat_ids:
        raise TelegramError("No Telegram subscribers found. Open your bot and send /start, then run again.")
    return sorted(chat_ids)


def format_job(job: Job) -> str:
    published = job.published_at.strftime("%Y-%m-%d") if job.published_at else "date not provided"
    location = html.escape(job.location or "US")
    categories = ", ".join(job.categories)
    # AMD's application endpoint opens an iCIMS login/application flow. Show
    # the public AMD job description first so the role can be reviewed before
    # applying.
    target_url = job.url if job.source_type == "amd" else (job.apply_url or job.url)
    link_label = "View job" if job.source_type == "amd" else "Apply directly"
    return (
        f"<b>{html.escape(job.title)}</b> — {html.escape(job.company)}\n"
        f"{location} · {html.escape(categories)} · posted {published}\n"
        f"<a href=\"{html.escape(target_url, quote=True)}\">{link_label}</a>"
    )


def send_jobs(token: str, chat_ids: list[str], jobs: list[Job], scanned_at: datetime) -> None:
    if not jobs:
        return
    header = f"<b>Fresh US software/AI jobs</b> · {scanned_at.strftime('%Y-%m-%d %H:%M UTC')} · {len(jobs)} new\n\n"
    chunks: list[str] = []
    current = header
    for job in jobs:
        block = format_job(job) + "\n\n"
        if len(current) + len(block) > 3800:
            chunks.append(current)
            current = block
        else:
            current += block
    if current.strip():
        chunks.append(current)
    for chat_id in chat_ids:
        for chunk in chunks:
            call(token, "sendMessage", {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": "true"})
