from __future__ import annotations

import sqlite3
import unittest

from support import temp_db_path
from workboard.storage import Repository, open_database
from workboard.storage.database import MIGRATIONS

TABLES = {
    "project",
    "project_editor",
    "scope",
    "category",
    "work_item",
    "assignment",
    "assignment_progress",
    "progress_history",
    "completion_review",
    "sync_outbox",
}


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = temp_db_path(self)
        self.conn = open_database(self.path)
        self.addCleanup(lambda: self.conn.close())
        self.repo = Repository(self.conn)

    def seed_history(self) -> None:
        with self.conn:
            self.repo.insert("project", id="p1", name="P", description="", created_at="t")
            self.repo.insert("work_item", id="w1", project_id="p1", title="T")
            self.repo.insert("assignment", id="a1", work_item_id="w1", assignee_id="u1", created_at="t")
            self.repo.insert(
                "progress_history",
                id="h1",
                assignment_id="a1",
                project_id="p1",
                author_id="u1",
                recorded_at="t",
                status="done",
                progress=100,
                evidence_ref="REF",
                prev_hash="",
                hash="x",
            )
            self.repo.insert(
                "completion_review",
                id="r1",
                history_id="h1",
                project_id="p1",
                reviewer_id="u2",
                decision="confirmed",
                reviewed_at="t",
                prev_hash="",
                hash="x",
            )

    def test_schema_is_created_and_versioned(self) -> None:
        names = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual(TABLES, names)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))

    def test_data_survives_reopen(self) -> None:
        self.seed_history()
        self.conn.close()
        self.conn = open_database(self.path)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
        rows = Repository(self.conn).assignment_rows(project_id="p1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "T")
        self.assertEqual(rows[0]["review_decision"], "confirmed")

    def test_history_and_reviews_are_append_only(self) -> None:
        self.seed_history()
        for sql in (
            "UPDATE progress_history SET progress = 1",
            "DELETE FROM progress_history",
            "UPDATE completion_review SET decision = 'rejected'",
            "DELETE FROM completion_review",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)
            self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM progress_history").fetchone()[0], 1)

    def test_foreign_keys_are_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.insert("work_item", id="w1", project_id="missing", title="T")

    def test_newer_database_is_refused(self) -> None:
        self.conn.execute("PRAGMA user_version = 99")
        self.conn.close()
        with self.assertRaises(RuntimeError):
            self.conn = open_database(self.path)


if __name__ == "__main__":
    unittest.main()
