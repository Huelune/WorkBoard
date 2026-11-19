"""Local SQLite persistence."""

from workboard.storage.database import open_database
from workboard.storage.repository import Repository

__all__ = ["Repository", "open_database"]
