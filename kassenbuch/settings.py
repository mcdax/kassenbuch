"""Settings from environment variables, read on every call so tests and the CLI can change them."""

import os
from pathlib import Path


def data_dir() -> Path:
    """Folder for the database and login tokens (KASSENBUCH_DATA, default ./data)."""
    return Path(os.environ.get("KASSENBUCH_DATA", "data"))


def db_path() -> Path:
    """Database in its own folder (KASSENBUCH_DB), so the web app can be given the database without the tokens."""
    return Path(os.environ.get("KASSENBUCH_DB", data_dir() / "db" / "kassenbuch.db"))


def tokens_dir() -> Path:
    return data_dir() / "tokens"


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")
