from __future__ import annotations

import unittest

from support import Clock, temp_db_path
from workboard.services import WorkBoardService
from workboard.storage import open_database

CLEAN = {
    "ok": True,
    "broken_history_chains": [],
    "broken_review_chains": [],
    "progress_mismatches": [],
    "protection_removed": False,
}


class IntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.svc.add_editor("ed1", self.project, "ed2")
        item = self.svc.create_work_item("ed1", self.project, "T")
        self.a = self.svc.assign("ed1", item, "u1")
        self.b = self.svc.assign("ed1", item, "u2")
        self.svc.update_progress("u1", self.a, "in_progress", 40)
        self.svc.update_progress("u1", self.a, "done", 100, evidence_ref="REF")
        self.svc.update_progress("u2", self.b, "done", 100, evidence_ref="REF")
        self.svc.review_completion("ed2", self.a, "rejected", "근거 부족")
        self.svc.review_completion("ed2", self.b, "confirmed")

    def tamper(self, sql: str) -> None:
        """Edit the file the way someone with a database tool would."""
        self.conn.executescript(
            "DROP TRIGGER IF EXISTS progress_history_no_update;"
            "DROP TRIGGER IF EXISTS progress_history_no_delete;"
            "DROP TRIGGER IF EXISTS completion_review_no_update;"
            "DROP TRIGGER IF EXISTS completion_review_no_delete;" + sql
        )

    def problems(self) -> dict:
        report = self.svc.check_integrity()
        return {key: value for key, value in report.items() if value != CLEAN[key]}

    def test_untouched_database_is_clean(self) -> None:
        self.assertEqual(self.svc.check_integrity(), CLEAN)

    def test_reviews_are_chained_per_project_and_reviewer(self) -> None:
        first, second = self.conn.execute("SELECT * FROM completion_review ORDER BY seq").fetchall()
        self.assertEqual(first["prev_hash"], "")
        self.assertEqual(second["prev_hash"], first["hash"])
        self.assertTrue(self.svc.verify_reviews(self.project, "ed2"))
        self.svc.review_completion("ed1", self.a, "confirmed")
        other = self.conn.execute("SELECT * FROM completion_review WHERE reviewer_id = 'ed1'").fetchone()
        self.assertEqual(other["prev_hash"], "")
        self.assertEqual(self.svc.check_integrity(), CLEAN)

    def test_edited_review_is_detected(self) -> None:
        self.tamper("UPDATE completion_review SET decision = 'confirmed', reason = NULL WHERE decision = 'rejected';")
        self.assertFalse(self.svc.verify_reviews(self.project, "ed2"))
        problems = self.problems()
        self.assertEqual(problems["broken_review_chains"], [{"project_id": self.project, "owner_id": "ed2"}])
        self.assertFalse(self.svc.check_integrity()["ok"])

    def test_removed_review_is_detected(self) -> None:
        self.tamper("DELETE FROM completion_review WHERE decision = 'rejected';")
        self.assertEqual(
            self.problems()["broken_review_chains"], [{"project_id": self.project, "owner_id": "ed2"}]
        )

    def test_edited_history_is_detected(self) -> None:
        self.tamper("UPDATE progress_history SET recorded_at = '2025-12-01T00:00:00.000000+00:00' WHERE progress = 40;")
        self.assertEqual(
            self.problems()["broken_history_chains"], [{"project_id": self.project, "owner_id": "u1"}]
        )

    def test_edited_current_progress_is_detected(self) -> None:
        self.conn.execute("UPDATE assignment_progress SET evidence_ref = 'FAKE' WHERE assignment_id = ?", (self.a,))
        self.conn.commit()
        self.assertEqual(self.problems(), {"ok": False, "progress_mismatches": [self.a]})

    def test_progress_without_history_and_history_without_progress_are_detected(self) -> None:
        self.conn.execute("DELETE FROM assignment_progress WHERE assignment_id = ?", (self.a,))
        self.conn.commit()
        self.tamper(f"DELETE FROM completion_review; DELETE FROM progress_history WHERE assignment_id = '{self.b}';")
        self.assertEqual(sorted(self.svc.check_integrity()["progress_mismatches"]), sorted([self.a, self.b]))

    def test_removed_protection_is_detected(self) -> None:
        self.tamper("")
        self.assertEqual(self.problems(), {"ok": False, "protection_removed": True})


if __name__ == "__main__":
    unittest.main()
