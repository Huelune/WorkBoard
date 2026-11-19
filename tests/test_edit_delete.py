from __future__ import annotations

import os
import unittest

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import open_database

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication

    from workboard.ui.main_window import MainWindow
except ImportError:
    QApplication = None


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.v1 = self.svc.create_scope("ed1", self.project, "v1")
        self.v2 = self.svc.create_scope("ed1", self.project, "v2")
        self.unit = self.svc.create_category("ed1", self.project, "UNIT")
        self.integ = self.svc.create_category("ed1", self.project, "INTEG", 1)
        self.svc.add_member("ed1", self.project, "u1", "김 담당")
        (self.assignment,) = self.svc.add_work_items(
            "ed1", self.project, [{"title": "T"}], "u1", scope_id=self.v1, category_id=self.unit
        )

    def names(self, records: list[dict]) -> list[str]:
        return [record["name"] for record in records]


class ItemTests(Case):
    def test_rename(self) -> None:
        self.svc.rename_item("ed1", "scope", self.v1, " v1.0 ")
        self.svc.rename_item("ed1", "category", self.unit, "단위")
        self.assertEqual(self.names(self.svc.list_scopes(self.project)), ["v1.0", "v2"])
        self.assertEqual(self.names(self.svc.list_categories(self.project)), ["단위", "INTEG"])

    def test_deleted_item_leaves_lists_but_old_work_keeps_its_name(self) -> None:
        self.svc.delete_item("ed1", "scope", self.v1)
        self.svc.delete_item("ed1", "category", self.unit)
        self.assertEqual(self.names(self.svc.list_scopes(self.project)), ["v2"])
        self.assertEqual(self.names(self.svc.list_categories(self.project)), ["INTEG"])
        self.assertEqual(self.names(self.svc.list_scopes(self.project, include_deleted=True)), ["v1", "v2"])
        row = self.svc.list_assignments(self.project)[0]
        self.assertEqual((row["scope_id"], row["category_id"]), (self.v1, self.unit))
        self.svc.delete_item("ed1", "scope", self.v1)  # deleting twice is harmless

    def test_deleted_item_cannot_be_used_for_new_work(self) -> None:
        self.svc.delete_item("ed1", "scope", self.v1)
        self.svc.delete_item("ed1", "category", self.integ)
        before = len(self.svc.list_assignments(self.project))
        with self.assertRaises(ValidationError):
            self.svc.add_work_items("ed1", self.project, [{"title": "N"}], "u1", scope_id=self.v1, category_id=self.unit)
        with self.assertRaises(ValidationError):
            self.svc.add_work_items("ed1", self.project, [{"title": "N"}], "u1", scope_id=self.v2, category_id=self.integ)
        with self.assertRaises(ValidationError):
            self.svc.create_scope("ed1", self.project, "v3", parent_id=self.v1)
        self.assertEqual(len(self.svc.list_assignments(self.project)), before)

    def test_refusals_change_nothing(self) -> None:
        attempts = (
            (PermissionDenied, lambda: self.svc.rename_item("u1", "scope", self.v1, "x")),
            (PermissionDenied, lambda: self.svc.delete_item("u1", "category", self.unit)),
            (ValidationError, lambda: self.svc.rename_item("ed1", "scope", self.v1, "  ")),
            (ValidationError, lambda: self.svc.rename_item("ed1", "project", self.project, "x")),
            (ValidationError, lambda: self.svc.delete_item("ed1", "work_item", self.v1)),
            (NotFound, lambda: self.svc.rename_item("ed1", "scope", "missing", "x")),
            (NotFound, lambda: self.svc.delete_item("ed1", "category", self.v1)),
        )
        for index, (error, attempt) in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(error):
                attempt()
        self.assertEqual(self.names(self.svc.list_scopes(self.project)), ["v1", "v2"])
        self.assertEqual(self.names(self.svc.list_categories(self.project)), ["UNIT", "INTEG"])


class MemberTests(Case):
    def ids(self) -> list[tuple[str, bool]]:
        return [(m["user_id"], m["is_editor"]) for m in self.svc.list_members(self.project)]

    def test_remove_member_keeps_their_work(self) -> None:
        self.svc.remove_member("ed1", self.project, "u1")
        self.assertEqual(self.ids(), [("ed1", True)])
        self.assertFalse(self.svc.is_member("u1", self.project))
        self.assertEqual(len(self.svc.my_assignments("u1")), 1)
        self.assertTrue(self.svc.update_progress("u1", self.assignment, "in_progress", 10))
        with self.assertRaises(PermissionDenied):
            self.svc.add_work_items("u1", self.project, [{"title": "N"}], "u1", scope_id=self.v1, category_id=self.unit)
        self.svc.remove_member("ed1", self.project, "u1")  # removing twice is harmless

    def test_editor_role_can_be_taken_away_but_never_from_the_last_one(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.remove_editor("ed1", self.project, "ed1")
        self.svc.add_editor("ed1", self.project, "u1")
        with self.assertRaises(ValidationError):
            self.svc.remove_member("ed1", self.project, "u1")  # still an editor
        self.svc.remove_editor("u1", self.project, "ed1")
        self.assertEqual(self.ids(), [("ed1", False), ("u1", True)])
        with self.assertRaises(PermissionDenied):
            self.svc.remove_editor("ed1", self.project, "u1")
        with self.assertRaises(ValidationError):
            self.svc.remove_editor("u1", self.project, "u1")
        self.svc.remove_member("u1", self.project, "ed1")
        self.assertEqual(self.ids(), [("u1", True)])

    def test_refusals_change_nothing(self) -> None:
        attempts = (
            (PermissionDenied, lambda: self.svc.remove_member("u1", self.project, "u1")),
            (PermissionDenied, lambda: self.svc.remove_editor("u1", self.project, "ed1")),
            (ValidationError, lambda: self.svc.remove_member("ed1", self.project, " ")),
            (ValidationError, lambda: self.svc.remove_editor("ed1", self.project, "u1")),  # not an editor
            (NotFound, lambda: self.svc.remove_member("ed1", "missing", "u1")),
        )
        for index, (error, attempt) in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(error):
                attempt()
        self.assertEqual(self.ids(), [("ed1", True), ("u1", False)])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ManageUiTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.app = QApplication.instance() or QApplication([])
        self.errors: list[str] = []
        self.window = MainWindow(self.svc, "ed1")
        self.addCleanup(self.window.deleteLater)
        self.window.show_error = self.errors.append  # type: ignore[method-assign]
        self.page = self.window.manage_page

    @staticmethod
    def texts(widget) -> list[str]:  # noqa: ANN001
        return [widget.item(i).text() for i in range(widget.count())]

    def test_items_are_renamed_and_deleted_from_the_page(self) -> None:
        page = self.page
        self.assertFalse(page.scope_rename.isEnabled())
        page.scope_list.setCurrentRow(0)
        self.assertTrue(page.scope_rename.isEnabled())
        self.assertEqual(page.selected_item("scope"), self.v1)
        self.assertTrue(self.window.rename_item("scope", self.v1, "v1.0"))
        self.assertEqual(self.texts(page.scope_list), ["v1.0", "v2"])
        self.assertTrue(self.window.delete_item("scope", self.v1))
        self.assertEqual(self.texts(page.scope_list), ["v2"])
        self.assertEqual(self.window.scope.count(), 2)  # 전체 + v2
        self.assertEqual(self.window.names[self.v1], "v1.0")  # old work still shows its name
        self.window.work_page.group.setCurrentIndex(2)
        tree = self.window.work_page.tree
        tree.setCurrentItem(tree.topLevelItem(0))
        self.assertIn("대상 버전 v1.0", self.window.work_page.detail.toPlainText())
        self.assertFalse(self.window.rename_item("category", self.unit, " "))
        self.assertEqual(len(self.errors), 1)

    def test_members_are_removed_and_roles_changed_from_the_page(self) -> None:
        page = self.page
        for button in (page.member_remove, page.editor_button, page.editor_remove):
            self.assertFalse(button.isEnabled())
        page.members.selectRow(1)
        self.assertEqual(page.selected_member(), "u1")
        self.assertEqual(
            (page.member_remove.isEnabled(), page.editor_button.isEnabled(), page.editor_remove.isEnabled()),
            (True, True, False),
        )
        self.assertTrue(self.window.add_editor("u1"))
        page.members.selectRow(1)
        self.assertEqual(
            (page.member_remove.isEnabled(), page.editor_button.isEnabled(), page.editor_remove.isEnabled()),
            (False, False, True),
        )
        self.assertTrue(self.window.remove_editor("u1"))
        self.assertTrue(self.window.remove_member("u1"))
        self.assertEqual(page.members.rowCount(), 1)
        self.assertFalse(self.window.remove_editor("ed1"))
        self.assertEqual(len(self.errors), 1)

    def test_page_is_read_only_for_non_editors(self) -> None:
        window = MainWindow(self.svc, "u1")
        self.addCleanup(window.deleteLater)
        page = window.manage_page
        page.scope_list.setCurrentRow(0)
        page.members.selectRow(0)
        for button in (page.scope_rename, page.scope_delete, page.category_delete, page.member_remove, page.editor_remove):
            self.assertFalse(button.isEnabled())


if __name__ == "__main__":
    unittest.main()
