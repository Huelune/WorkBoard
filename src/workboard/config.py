"""Local settings. Real paths and user IDs stay on the PC and are never committed."""

from __future__ import annotations

import getpass
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    user_id: str


def default_config_path() -> Path:
    override = os.environ.get("WORKBOARD_CONFIG")
    return Path(override) if override else Path.home() / ".workboard" / "config.toml"


def load_settings(path: Path | None = None, *, home: Path | None = None, login: str | None = None) -> Settings:
    """Read the settings file; anything missing or blank falls back to a per-user default.

    The user ID defaults to the OS login name (stage 0 assumption about user identity).
    """
    # utf-8-sig: Notepad and Windows PowerShell save UTF-8 with a byte order mark.
    path = path or default_config_path()
    data = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    db_path = str(data.get("storage", {}).get("local_db_path") or "").strip()
    user_id = str(data.get("user", {}).get("id") or "").strip()
    return Settings(
        db_path=Path(db_path) if db_path else (home or Path.home()) / ".workboard" / "workboard.db",
        user_id=user_id or login or getpass.getuser(),
    )
