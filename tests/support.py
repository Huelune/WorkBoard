from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Clock:
    """Fixed clock the tests move by hand."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def temp_db_path(case: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(folder.cleanup)
    return Path(folder.name) / "workboard.db"
