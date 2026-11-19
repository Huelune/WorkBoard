from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
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
        self.item = item = self.svc.create_work_item("ed1", self.project, "T")
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

    def rows(self) -> int:
        tables = ("project", "project_editor", "scope", "category", "completion_review")
        return sum(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)

    def test_blank_or_unknown_filters_do_not_list_everything(self) -> None:
        svc, week = self.svc, START + timedelta(days=7)
        for actor in (None, "", "  "):
            with self.subTest(actor=actor), self.assertRaises(ValidationError):
                svc.my_assignments(actor)  # type: ignore[arg-type]
        calls = (
            svc.list_assignments,
            svc.list_scopes,
            svc.list_categories,
            lambda p: svc.pending_reviews("ed1", p),
            lambda p: svc.group_summary(p, "assignee", "2026-01-20"),
            lambda p: svc.project_status(p, "2026-01-20"),
        )
        for index, call in enumerate(calls):
            for project in (None, "missing"):
                with self.subTest(index=index, project=project), self.assertRaises(NotFound):
                    call(project)
        with self.assertRaises(NotFound):
            svc.assignee_pace(START, week, "missing")
        self.assertEqual(len(svc.assignee_pace(START, week)), 2)
        self.assertEqual(len(svc.my_assignments("u1")), 1)

    def test_definition_input_types_are_checked(self) -> None:
        svc, before = self.svc, self.rows()
        attempts = (
            lambda: svc.create_category("ed1", self.project, "C", position="abc"),
            lambda: svc.create_category("ed1", self.project, "C", position=1.5),
            lambda: svc.create_category("ed1", self.project, "C", position=None),
            lambda: svc.create_category("ed1", self.project, "C", position=True),
            lambda: svc.create_scope("ed1", self.project, "S", kind=5),
            lambda: svc.create_project("ed1", "N", description=5),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                attempt()
        for actor in ([], {}, None, 7):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                svc.add_editor(actor, self.project, "x")  # type: ignore[arg-type]
            with self.subTest(actor=actor, kind="review"), self.assertRaises(PermissionDenied):
                svc.review_completion(actor, self.done, "confirmed")  # type: ignore[arg-type]
        self.assertEqual(self.rows(), before)

    def test_due_date_of_dead_assignment_cannot_change(self) -> None:
        self.svc.cancel_assignment("ed1", self.open)
        with self.assertRaises(ValidationError):
            self.svc.set_due_date("ed1", self.open, "2026-02-01")
        self.svc.cancel_assignment("ed1", self.open)
        self.svc.delete_work_item("ed1", self.item)
        with self.assertRaises(ValidationError):
            self.svc.set_due_date("ed1", self.done, "2026-02-01")
        due = self.conn.execute("SELECT COUNT(*) FROM assignment WHERE due_date IS NOT NULL").fetchone()[0]
        self.assertEqual(due, 0)


if __name__ == "__main__":
    unittest.main()
