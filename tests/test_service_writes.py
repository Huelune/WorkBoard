from __future__ import annotations

import unittest

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import Repository, open_database

TABLES = (
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
)


class ServiceWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = temp_db_path(self)
        self.clock = Clock()
        self.open()
        self.addCleanup(lambda: self.conn.close())
        self.project = self.svc.create_project("ed1", "프로젝트")
        self.svc.add_editor("ed1", self.project, "ed2")
        self.item = self.svc.create_work_item("ed1", self.project, "업무")
        self.assignment = self.svc.assign("ed1", self.item, "u1", "2026-01-15")

    def open(self) -> None:
        self.conn = open_database(self.path)
        self.svc = WorkBoardService(self.conn, self.clock)
        self.repo = Repository(self.conn)

    def counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}

    def outbox(self) -> set[tuple[str, str]]:
        return {(r["kind"], r["author_id"]) for r in self.conn.execute("SELECT * FROM sync_outbox")}

    def done(self, actor: str = "u1", ref: str = "REF-1") -> bool:
        return self.svc.update_progress(actor, self.assignment, "done", 100, evidence_ref=ref)

    def test_work_survives_restart(self) -> None:
        self.svc.update_progress("u1", self.assignment, "in_progress", 40)
        self.conn.close()
        self.open()
        row = self.repo.assignment_rows(project_id=self.project)[0]
        self.assertEqual((row["title"], row["assignee_id"], row["due_date"]), ("업무", "u1", "2026-01-15"))
        self.assertEqual((row["status"], row["progress"]), ("in_progress", 40))
        self.assertEqual(len(self.repo.history(self.assignment)), 1)

    def test_only_editors_change_definitions(self) -> None:
        before = self.counts()
        attempts = (
            lambda: self.svc.add_editor("u1", self.project, "u1"),
            lambda: self.svc.create_scope("u1", self.project, "v1"),
            lambda: self.svc.create_category("u1", self.project, "UNIT"),
            lambda: self.svc.create_work_item("u1", self.project, "다른 업무"),
            lambda: self.svc.delete_work_item("u1", self.item),
            lambda: self.svc.assign("u1", self.item, "u1"),
            lambda: self.svc.set_due_date("u1", self.assignment, "2026-02-01"),
            lambda: self.svc.cancel_assignment("u1", self.assignment),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(PermissionDenied):
                attempt()
        self.assertEqual(self.counts(), before)
        row = self.repo.assignment_rows(project_id=self.project)[0]
        self.assertEqual(row["due_date"], "2026-01-15")

    def test_only_assignee_changes_progress(self) -> None:
        before = self.counts()
        for actor in ("ed1", "u2"):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                self.svc.update_progress(actor, self.assignment, "in_progress", 10)
        self.assertEqual(self.counts(), before)

    def test_review_needs_another_editor(self) -> None:
        self.done()
        own = self.svc.assign("ed1", self.item, "ed1")
        self.svc.update_progress("ed1", own, "done", 100, evidence_ref="REF-2")
        before = self.counts()
        with self.assertRaises(PermissionDenied):
            self.svc.review_completion("u2", self.assignment, "confirmed")
        with self.assertRaises(PermissionDenied):
            self.svc.review_completion("u1", self.assignment, "confirmed")
        with self.assertRaises(PermissionDenied):
            self.svc.review_completion("ed1", own, "confirmed")
        self.assertEqual(self.counts(), before)
        self.svc.review_completion("ed2", own, "confirmed")
        self.assertEqual(self.counts()["completion_review"], 1)

    def test_invalid_input_is_not_saved(self) -> None:
        other = self.svc.create_project("ed1", "다른 프로젝트")
        foreign_scope = self.svc.create_scope("ed1", other, "v9")
        foreign_category = self.svc.create_category("ed1", other, "X")
        cancelled = self.svc.assign("ed1", self.item, "u2")
        self.svc.cancel_assignment("ed1", cancelled)
        before = self.counts()
        attempts = (
            lambda: self.svc.create_project("ed1", "  "),
            lambda: self.svc.create_project(" ", "이름"),
            lambda: self.svc.create_work_item("ed1", self.project, ""),
            lambda: self.svc.create_work_item("ed1", self.project, "T", scope_id=foreign_scope),
            lambda: self.svc.create_work_item("ed1", self.project, "T", category_id=foreign_category),
            lambda: self.svc.create_scope("ed1", self.project, "v2", parent_id=foreign_scope),
            lambda: self.svc.assign("ed1", self.item, " "),
            lambda: self.svc.assign("ed1", self.item, "u1", "2026-13-01"),
            lambda: self.svc.set_due_date("ed1", self.assignment, "내일"),
            lambda: self.svc.update_progress("u1", self.assignment, "done", 100),
            lambda: self.svc.update_progress("u2", cancelled, "in_progress", 10),
            lambda: self.svc.review_completion("ed2", self.assignment, "confirmed"),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                attempt()
        self.assertEqual(self.counts(), before)

    def test_review_input_rules(self) -> None:
        self.done()
        for decision, reason in (("rejected", None), ("rejected", "  "), ("maybe", "x")):
            with self.subTest(decision=decision), self.assertRaises(ValidationError):
                self.svc.review_completion("ed2", self.assignment, decision, reason)
        self.assertEqual(self.counts()["completion_review"], 0)

    def test_deleted_work_item_blocks_progress(self) -> None:
        self.svc.delete_work_item("ed1", self.item)
        with self.assertRaises(ValidationError):
            self.svc.update_progress("u1", self.assignment, "in_progress", 10)
        with self.assertRaises(ValidationError):
            self.svc.assign("ed1", self.item, "u2")

    def test_unknown_ids_are_not_found(self) -> None:
        attempts = (
            lambda: self.svc.add_editor("ed1", "missing", "u1"),
            lambda: self.svc.create_work_item("ed1", "missing", "T"),
            lambda: self.svc.create_work_item("ed1", self.project, "T", scope_id="missing"),
            lambda: self.svc.assign("ed1", "missing", "u1"),
            lambda: self.svc.update_progress("u1", "missing", "in_progress", 10),
            lambda: self.svc.review_completion("ed2", "missing", "confirmed"),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(NotFound):
                attempt()

    def test_progress_change_is_atomic(self) -> None:
        before = self.counts()
        self.assertTrue(self.svc.update_progress("u1", self.assignment, "in_progress", 30))
        after = self.counts()
        self.assertEqual(after["assignment_progress"], 1)
        self.assertEqual(after["progress_history"], 1)
        self.assertIn(("progress", "u1"), self.outbox())

        def fail(*args: object) -> None:
            raise RuntimeError("disk full")

        self.svc._repo.mark_dirty = fail  # type: ignore[method-assign]
        self.conn.execute("DELETE FROM sync_outbox")
        self.conn.commit()
        with self.assertRaises(RuntimeError):
            self.svc.update_progress("u1", self.assignment, "in_progress", 60)
        row = self.repo.assignment_rows(project_id=self.project)[0]
        self.assertEqual(row["progress"], 30)
        self.assertEqual(self.counts()["progress_history"], 1)
        self.assertEqual(self.counts()["sync_outbox"], 0)
        self.assertEqual(before["progress_history"], 0)

    def test_unchanged_save_adds_no_history(self) -> None:
        self.assertFalse(self.svc.update_progress("u1", self.assignment, "not_started", 0))
        self.assertTrue(self.done())
        self.assertFalse(self.done(ref="  REF-1 "))
        self.assertEqual(self.counts()["progress_history"], 1)

    def test_history_chain_and_clock(self) -> None:
        self.svc.update_progress("u1", self.assignment, "in_progress", 30)
        self.clock.advance(days=1)
        self.done()
        first, second = self.repo.history(self.assignment)
        self.assertEqual(first["prev_hash"], "")
        self.assertEqual(second["prev_hash"], first["hash"])
        self.assertEqual(first["recorded_at"], "2026-01-05T00:00:00.000000+00:00")
        self.assertEqual(second["recorded_at"], "2026-01-06T00:00:00.000000+00:00")
        self.assertTrue(self.svc.verify_history(self.project, "u1"))
        self.assertTrue(self.svc.verify_history(self.project, "nobody"))

    def test_tampered_history_fails_verification(self) -> None:
        self.svc.update_progress("u1", self.assignment, "in_progress", 30)
        self.done()
        self.conn.executescript(
            "DROP TRIGGER progress_history_no_update;"
            "UPDATE progress_history SET recorded_at = '2025-12-01T00:00:00.000000+00:00' WHERE progress = 30;"
        )
        self.assertFalse(self.svc.verify_history(self.project, "u1"))

    def test_outbox_marks_each_snapshot_once(self) -> None:
        self.assertEqual(self.outbox(), {("definition", "")})
        self.done()
        self.svc.review_completion("ed2", self.assignment, "rejected", "근거 부족")
        self.svc.review_completion("ed2", self.assignment, "confirmed")
        self.assertEqual(self.outbox(), {("definition", ""), ("progress", "u1"), ("review", "ed2")})
        self.assertEqual(self.svc.pending_sync_count(), 3)


if __name__ == "__main__":
    unittest.main()
