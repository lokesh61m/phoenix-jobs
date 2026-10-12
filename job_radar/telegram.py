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


def resolve_chat_id(token: str) -> str:
    configured = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if configured:
        return configured
    payload = call(token, "getUpdates", {"limit": "100", "timeout": "0"})
    updates = payload.get("result", [])
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private":
            return str(chat["id"])
    raise TelegramError("No private chat found. Open your bot in Telegram and send /start, then run again.")


def format_job(job: Job) -> str:
    published = job.published_at.strftime("%Y-%m-%d") if job.published_at else "date not provided"
    location = html.escape(job.location or "US")
    categories = ", ".join(job.categories)
    return (
        f"<b>{html.escape(job.title)}</b> — {html.escape(job.company)}\n"
        f"{location} · {html.escape(categories)} · posted {published}\n"
        f"<a href=\"{html.escape(job.apply_url or job.url, quote=True)}\">Apply directly</a>"
    )


def send_jobs(token: str, chat_id: str, jobs: list[Job], scanned_at: datetime) -> None:
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
    for chunk in chunks:
        call(token, "sendMessage", {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": "true"})
