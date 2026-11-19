from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.storage.database import MIGRATIONS

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


class MemberCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class MemberTests(MemberCase):
    def test_creator_editors_and_assignees_become_members(self) -> None:
        self.svc.add_editor("ed1", self.project, "ed2")
        self.svc.add_member("ed1", self.project, "u1", "  김 담당  ")
        item = self.svc.create_work_item("ed1", self.project, "T")
        self.svc.assign("ed1", item, "u2")
        self.assertEqual(
            self.svc.list_members(self.project),
            [
                {"user_id": "ed1", "display_name": "", "is_editor": True},
                {"user_id": "ed2", "display_name": "", "is_editor": True},
                {"user_id": "u1", "display_name": "김 담당", "is_editor": False},
                {"user_id": "u2", "display_name": "", "is_editor": False},
            ],
        )
        self.assertTrue(self.svc.is_member("u1", self.project))
        self.assertFalse(self.svc.is_member("x", self.project))
        self.assertFalse(self.svc.is_member(None, self.project))  # type: ignore[arg-type]

    def test_adding_a_member_again_only_updates_the_name(self) -> None:
        self.svc.add_member("ed1", self.project, "u1", "이름")
        self.svc.add_member("ed1", self.project, "u1")
        self.assertEqual(self.svc.list_members(self.project)[1]["display_name"], "이름")
        self.svc.add_member("ed1", self.project, "u1", "새 이름")
        self.assertEqual(self.svc.list_members(self.project)[1]["display_name"], "새 이름")
        self.assertEqual(self.count("project_member"), 2)

    def test_only_editors_manage_members(self) -> None:
        self.svc.add_member("ed1", self.project, "u1")
        with self.assertRaises(PermissionDenied):
            self.svc.add_member("u1", self.project, "u2")
        with self.assertRaises(ValidationError):
            self.svc.add_member("ed1", self.project, "  ")
        with self.assertRaises(NotFound):
            self.svc.add_member("ed1", "missing", "u2")
        with self.assertRaises(NotFound):
            self.svc.list_members("missing")
        self.assertEqual(self.count("project_member"), 2)


class AddWorkItemsTests(MemberCase):
    def setUp(self) -> None:
        super().setUp()
        self.svc.add_member("ed1", self.project, "u1")
        self.category = self.svc.create_category("ed1", self.project, "UNIT")
        self.scope = self.svc.create_scope("ed1", self.project, "v1")
        real = self.svc.add_work_items

        def add(actor, project, items, assignee, **extra):  # noqa: ANN001, ANN003, ANN202
            extra.setdefault("scope_id", self.scope)
            extra.setdefault("category_id", self.category)
            return real(actor, project, items, assignee, **extra)

        self.svc.add_work_items = add  # type: ignore[method-assign]
        self.items = [
            {"title": " 함수 A ", "external_ref": "REF-1", "external_url": "https://example.test/1"},
            {"title": "함수 B"},
            {"title": "함수 C", "external_ref": "  "},
        ]

    def test_editor_registers_many_for_anyone(self) -> None:
        ids = self.svc.add_work_items(
            "ed1", self.project, self.items, "u9", due_date="2026-02-01", category_id=self.category
        )
        rows = self.svc.list_assignments(self.project)
        self.assertEqual([r["assignment_id"] for r in rows], ids)
        self.assertEqual([r["title"] for r in rows], ["함수 A", "함수 B", "함수 C"])
        self.assertEqual({(r["assignee_id"], r["due_date"], r["category_id"]) for r in rows}, {("u9", "2026-02-01", self.category)})
        self.assertEqual([r["external_ref"] for r in rows], ["REF-1", None, None])
        self.assertEqual({r["created_by"] for r in rows}, {"ed1"})
        self.assertTrue(self.svc.is_member("u9", self.project))

    def test_member_registers_only_for_themselves(self) -> None:
        ids = self.svc.add_work_items("u1", self.project, self.items, "u1")
        self.assertEqual(len(ids), 3)
        self.assertEqual({r["created_by"] for r in self.svc.my_assignments("u1")}, {"u1"})
        before = self.count("work_item")
        with self.assertRaises(PermissionDenied):
            self.svc.add_work_items("u1", self.project, self.items, "u2")
        with self.assertRaises(PermissionDenied):
            self.svc.add_work_items("stranger", self.project, self.items, "stranger")
        self.assertEqual(self.count("work_item"), before)

    def test_nothing_is_saved_when_one_line_is_bad(self) -> None:
        other = self.svc.create_project("ed1", "다른")
        foreign = self.svc.create_category("ed1", other, "X")
        attempts = (
            lambda: self.svc.add_work_items("ed1", self.project, [*self.items, {"title": " "}], "u1"),
            lambda: self.svc.add_work_items("ed1", self.project, [], "u1"),
            lambda: self.svc.add_work_items("ed1", self.project, self.items, " "),
            lambda: self.svc.add_work_items("ed1", self.project, self.items, "u1", due_date="내일"),
            lambda: self.svc.add_work_items("ed1", self.project, self.items, "u1", category_id=foreign),
            lambda: self.svc.add_work_items("ed1", self.project, [{"title": 5}], "u1"),
            lambda: self.svc.add_work_items("ed1", self.project, "함수", "u1"),
            lambda: self.svc.add_work_items("ed1", self.project, self.items, "u1", scope_id=None),
            lambda: self.svc.add_work_items("ed1", self.project, self.items, "u1", category_id=None),
            lambda: self.svc.add_work_items("ed1", self.project, [{"title": "T", "external_url": "javascript:alert(1)"}], "u1"),
            lambda: self.svc.add_work_items("ed1", self.project, [{"title": "T", "external_url": "file:///c:/x"}], "u1"),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                attempt()
        with self.assertRaises(NotFound):
            self.svc.add_work_items("ed1", "missing", self.items, "u1")
        self.assertEqual((self.count("work_item"), self.count("assignment")), (0, 0))

    def test_failure_midway_rolls_everything_back(self) -> None:
        original = self.svc._repo.insert
        calls = []

        def flaky(table: str, **values: object) -> None:
            calls.append(table)
            if calls.count("assignment") == 2:
                raise sqlite3.OperationalError("disk full")
            original(table, **values)

        self.svc._repo.insert = flaky  # type: ignore[method-assign]
        with self.assertRaises(sqlite3.OperationalError):
            self.svc.add_work_items("ed1", self.project, self.items, "u1")
        self.assertEqual((self.count("work_item"), self.count("assignment")), (0, 0))

    def test_pace_counts_self_registered_work(self) -> None:
        self.svc.add_work_items("u1", self.project, self.items[:2], "u1")
        self.svc.add_work_items("ed1", self.project, self.items[2:], "u1")
        pace = self.svc.assignee_pace(START, START + timedelta(days=7), self.project)
        self.assertEqual((pace[0]["assigned"], pace[0]["self_added"]), (3, 2))


class MigrationTests(unittest.TestCase):
    def test_version_one_database_is_upgraded_in_place(self) -> None:
        path = temp_db_path(self)
        old = sqlite3.connect(path)
        old.executescript(MIGRATIONS[0] + "PRAGMA user_version = 1;")
        old.executescript(
            "INSERT INTO project (id, name, created_at) VALUES ('p1', 'P', 't');"
            "INSERT INTO project_editor VALUES ('p1', 'ed1');"
            "INSERT INTO work_item (id, project_id, title) VALUES ('w1', 'p1', 'T');"
            "INSERT INTO assignment (id, work_item_id, assignee_id, created_at) VALUES ('a1', 'w1', 'u1', 't');"
        )
        old.close()
        conn = open_database(path)
        self.addCleanup(conn.close)
        svc = WorkBoardService(conn, Clock())
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
        self.assertEqual([m["user_id"] for m in svc.list_members("p1")], ["ed1", "u1"])
        self.assertEqual(svc.list_assignments("p1")[0]["created_by"], None)
        self.assertTrue(svc.check_integrity()["ok"])


if __name__ == "__main__":
    unittest.main()
