from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from support import Clock, temp_db_path
from workboard.services import WorkBoardService
from workboard.storage import open_database

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


class ReviewFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.svc.add_editor("ed1", self.project, "ed2")
        item = self.svc.create_work_item("ed1", self.project, "T")
        self.done = self.svc.assign("ed1", item, "u1")
        self.open = self.svc.assign("ed1", item, "u2")
        self.svc.update_progress("u1", self.done, "done", 100, evidence_ref="REF")
        self.svc.review_completion("ed2", self.done, "confirmed")

    def test_replace_cannot_rewrite_append_only_rows(self) -> None:
        for table in ("completion_review", "progress_history"):
            row = dict(self.conn.execute(f"SELECT * FROM {table}").fetchone())
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))
            self.conn.rollback()

    def test_weeks_left_uses_the_unrounded_pace(self) -> None:
        year = self.svc.project_pace(START, START + timedelta(days=365))[0]
        self.assertEqual((year["remaining"], year["weeks_left"]), (1, 52.1))
        long = self.svc.project_pace(START, START + timedelta(days=1500))[0]
        self.assertEqual((long["per_week"], long["weeks_left"]), (0.0, 214.3))

    def test_checks_run_inside_the_write_transaction(self) -> None:
        seen = []
        original = self.svc._repo.get_assignment

        def spy(assignment_id: str) -> dict | None:
            seen.append(self.conn.in_transaction)
            return original(assignment_id)

        self.svc._repo.get_assignment = spy  # type: ignore[method-assign]
        self.svc.update_progress("u2", self.open, "in_progress", 10)
        self.svc.review_completion("ed1", self.done, "confirmed")
        self.svc.cancel_assignment("ed1", self.open)
        self.assertEqual(seen, [True, True, True])
        self.assertFalse(self.conn.in_transaction)


if __name__ == "__main__":
    unittest.main()
