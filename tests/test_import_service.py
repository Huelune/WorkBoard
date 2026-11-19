from __future__ import annotations

import sqlite3
import unittest

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.storage.database import MIGRATIONS


def row(line: int, title: str = "함수", **fields: str) -> dict:
    base = {"scope": "v1", "category": "단위", "assignee": "김 담당", "due_date": "", "external_ref": "", "external_url": ""}
    return {"line": line, "title": title, **base, **fields}


class ImportCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.v1 = self.svc.create_scope("ed1", self.project, "v1")
        self.unit = self.svc.create_category("ed1", self.project, "단위")
        self.svc.add_member("ed1", self.project, "kim", "김 담당")
        self.svc.add_member("ed1", self.project, "lee", "이 담당")

    def verdicts(self, rows: list[dict]) -> list[tuple[int, str, str | None]]:
        plan = self.svc.plan_import("ed1", self.project, rows)
        return [(r["line"], r["verdict"], r["reason"]) for r in plan["rows"]]

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class PlanTests(ImportCase):
    def test_plan_writes_nothing_and_counts_add_up(self) -> None:
        rows = [
            row(2, "함수 1"),
            row(3, ""),
            row(4, "함수 2", assignee="kim", due_date="2026.1.15"),
            row(5, "함수 3", assignee="모르는 사람"),
            row(6, "함수 1"),  # same as line 2
            row(7, "함수 4", scope="v2", category="통합", assignee=" 이 담당 "),
        ]
        before = (self.count("work_item"), self.count("scope"), self.count("category"), self.count("sync_outbox"))
        plan = self.svc.plan_import("ed1", self.project, rows)
        self.assertEqual(
            [(r["line"], r["verdict"], r["reason"]) for r in plan["rows"]],
            [
                (2, "new", None),
                (3, "blank", None),
                (4, "new", None),
                (5, "error", "unknown_assignee"),
                (6, "duplicate", None),
                (7, "new", None),
            ],
        )
        self.assertEqual(plan["counts"], {"read": 6, "new": 3, "duplicate": 1, "error": 1, "blank": 1})
        self.assertEqual((plan["new_scopes"], plan["new_categories"]), (["v2"], ["통합"]))
        self.assertEqual(sum(plan["counts"][k] for k in ("new", "duplicate", "error", "blank")), plan["counts"]["read"])
        after = (self.count("work_item"), self.count("scope"), self.count("category"), self.count("sync_outbox"))
        self.assertEqual(after, before)

    def test_error_reasons(self) -> None:
        self.svc.add_member("ed1", self.project, "kim2", "김 담당")  # two members share the name
        self.svc.add_member("ed1", self.project, "park", "박 담당")
        old = self.svc.create_scope("ed1", self.project, "v0")
        self.svc.delete_item("ed1", "scope", old)
        gone = self.svc.create_category("ed1", self.project, "폐지")
        self.svc.delete_item("ed1", "category", gone)
        rows = [
            row(2, scope=""),
            row(3, category=" "),
            row(4, assignee=""),
            row(5, assignee="없는 사람"),
            row(6),  # 김 담당 is ambiguous now
            row(7, assignee="park", scope="V0"),
            row(8, assignee="park", category="폐지"),
            row(9, assignee="park", due_date="내일"),
            row(10, assignee="park", external_url="ftp://example.test/x"),
            row(11, assignee="PARK", due_date="2026-02-03", external_url="https://example.test/x"),
        ]
        self.assertEqual(
            [reason for _, _, reason in self.verdicts(rows)],
            [
                "no_scope",
                "no_category",
                "no_assignee",
                "unknown_assignee",
                "ambiguous_assignee",
                "deleted_scope",
                "deleted_category",
                "bad_due_date",
                "bad_link",
                None,
            ],
        )

    def test_already_registered_work_is_a_duplicate(self) -> None:
        self.svc.add_work_items(
            "ed1",
            self.project,
            [{"title": "함수 1", "external_ref": "REF-1"}, {"title": "함수 2"}],
            "kim",
            scope_id=self.v1,
            category_id=self.unit,
        )
        rows = [
            row(2, "이름이 바뀐 함수", external_ref="ref-1"),  # same reference: the same work
            row(3, "함수 1", external_ref="REF-9"),  # another reference: different work
            row(4, "함수 2"),  # no reference: matched by title
            row(5, "함수 2", assignee="lee"),  # another assignee: a separate assignment
            row(6, "함수 2", scope="v2"),  # another version: separate work
            row(7, " 함수 2 ", scope="V1"),  # same after trimming and case folding
        ]
        self.assertEqual(
            [verdict for _, verdict, _ in self.verdicts(rows)],
            ["duplicate", "new", "duplicate", "new", "new", "duplicate"],
        )

    def test_cancelled_and_deleted_work_does_not_block_a_reimport(self) -> None:
        first, second = self.svc.add_work_items(
            "ed1", self.project, [{"title": "취소될 일"}, {"title": "삭제될 일"}], "kim", scope_id=self.v1, category_id=self.unit
        )
        self.svc.cancel_assignment("ed1", first)
        self.svc.delete_work_item("ed1", self.svc.assignment_detail(second)["row"]["work_item_id"])
        self.assertEqual([v for _, v, _ in self.verdicts([row(2, "취소될 일"), row(3, "삭제될 일")])], ["new", "new"])

    def test_only_editors_and_only_well_formed_input(self) -> None:
        for call in (self.svc.plan_import, self.svc.apply_import):
            with self.subTest(call=call.__name__):
                with self.assertRaises(PermissionDenied):
                    call("kim", self.project, [row(2)])
                with self.assertRaises(NotFound):
                    call("ed1", "missing", [row(2)])
                for bad in ("표", [["함수"]], None):
                    with self.assertRaises(ValidationError):
                        call("ed1", self.project, bad)
        self.assertEqual(self.verdicts([{"line": 2, "title": 5, "scope": None}]), [(2, "blank", None)])
        self.assertEqual(self.count("work_item"), 0)


class ApplyTests(ImportCase):
    def test_apply_registers_the_new_rows_and_creates_missing_items(self) -> None:
        rows = [
            row(2, "함수 1", due_date="2026/01/15", external_ref="REF-1", external_url="https://example.test/1"),
            row(3, "함수 2", scope="v2", category="통합", assignee="lee"),
            row(4, "함수 3", scope="V2", category="통합"),  # reuses the items the row above creates
            row(5, "함수 4", assignee="없는 사람"),
            row(6, ""),
        ]
        result = self.svc.apply_import("ed1", self.project, rows)
        self.assertEqual(result["counts"], {"read": 5, "new": 3, "duplicate": 0, "error": 1, "blank": 1})
        listed = self.svc.list_assignments(self.project)
        self.assertEqual([r["title"] for r in listed], ["함수 1", "함수 2", "함수 3"])
        self.assertEqual([r["assignee_id"] for r in listed], ["kim", "lee", "kim"])
        self.assertEqual((listed[0]["due_date"], listed[0]["external_ref"], listed[0]["external_url"]), ("2026-01-15", "REF-1", "https://example.test/1"))
        self.assertEqual({r["created_by"] for r in listed}, {"ed1"})
        self.assertEqual([s["name"] for s in self.svc.list_scopes(self.project)], ["v1", "v2"])
        self.assertEqual([c["name"] for c in self.svc.list_categories(self.project)], ["단위", "통합"])
        self.assertEqual(listed[1]["scope_id"], listed[2]["scope_id"])
        self.assertTrue(self.svc.check_integrity()["ok"])

    def test_importing_the_same_sheet_again_adds_nothing(self) -> None:
        rows = [row(2, "함수 1", external_ref="REF-1"), row(3, "함수 2"), row(4, "함수 3", scope="v2")]
        self.assertEqual(self.svc.apply_import("ed1", self.project, rows)["counts"]["new"], 3)
        again = self.svc.apply_import("ed1", self.project, rows)
        self.assertEqual(again["counts"], {"read": 3, "new": 0, "duplicate": 3, "error": 0, "blank": 0})
        self.assertEqual((self.count("work_item"), self.count("scope")), (3, 2))

    def test_failure_midway_saves_nothing(self) -> None:
        original = self.svc._repo.insert
        seen: list[str] = []

        def flaky(table: str, **values: object) -> None:
            seen.append(table)
            if seen.count("assignment") == 2:
                raise sqlite3.OperationalError("disk full")
            original(table, **values)

        self.svc._repo.insert = flaky  # type: ignore[method-assign]
        with self.assertRaises(sqlite3.OperationalError):
            self.svc.apply_import("ed1", self.project, [row(2, "가", scope="v2"), row(3, "나"), row(4, "다")])
        self.assertEqual((self.count("work_item"), self.count("assignment"), self.count("scope")), (0, 0, 1))


class MappingMemoryTests(ImportCase):
    def test_mapping_is_remembered_per_project(self) -> None:
        self.assertEqual(self.svc.get_import_mapping(self.project), {})
        self.svc.save_import_mapping("ed1", self.project, {"title": "대상 함수", "assignee": "담당자", "unknown": "x", "scope": 5})
        self.assertEqual(self.svc.get_import_mapping(self.project), {"title": "대상 함수", "assignee": "담당자"})
        self.svc.save_import_mapping("ed1", self.project, {"title": "제목"})
        self.assertEqual(self.svc.get_import_mapping(self.project), {"title": "제목"})
        other = self.svc.create_project("ed1", "다른")
        self.assertEqual(self.svc.get_import_mapping(other), {})
        with self.assertRaises(PermissionDenied):
            self.svc.save_import_mapping("kim", self.project, {"title": "x"})
        with self.assertRaises(ValidationError):
            self.svc.save_import_mapping("ed1", self.project, "제목")
        with self.assertRaises(NotFound):
            self.svc.get_import_mapping("missing")

    def test_version_five_database_is_upgraded(self) -> None:
        path = temp_db_path(self)
        old = sqlite3.connect(path)
        old.executescript("".join(MIGRATIONS[:5]) + "PRAGMA user_version = 5;")
        old.execute("INSERT INTO project (id, name, created_at) VALUES ('p1', 'P', 't')")
        old.commit()
        old.close()
        conn = open_database(path)
        self.addCleanup(conn.close)
        self.assertEqual(WorkBoardService(conn, Clock()).get_import_mapping("p1"), {})


if __name__ == "__main__":
    unittest.main()
