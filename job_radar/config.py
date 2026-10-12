from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def setup_environment() -> None:
    load_dotenv(ROOT / ".env")


def companies_path() -> Path:
    value = os.environ.get("COMPANIES_PATH", "config/companies.json")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def db_path() -> Path:
    value = os.environ.get("DB_PATH", "data/jobs.sqlite3")
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
