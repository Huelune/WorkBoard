from __future__ import annotations

import os
import sqlite3
import unittest

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.storage.database import MIGRATIONS

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication

    from workboard.ui.dialogs import WorkItemDialog
    from workboard.ui.main_window import MainWindow
except ImportError:
    QApplication = None

DEFAULT = ("대상 버전", "업무 분야")


def labels(svc: WorkBoardService, project: str) -> tuple[str, str]:
    record = next(p for p in svc.list_projects() if p["id"] == project)
    return record["scope_label"], record["category_label"]


class LabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")

    def test_projects_start_with_default_labels_and_keep_their_own(self) -> None:
        other = self.svc.create_project("ed1", "다른")
        self.assertEqual(labels(self.svc, self.project), DEFAULT)
        self.svc.set_labels("ed1", self.project, " 코드 버전 ", "검증 분야")
        self.assertEqual(labels(self.svc, self.project), ("코드 버전", "검증 분야"))
        self.assertEqual(labels(self.svc, other), DEFAULT)

    def test_only_editors_rename_and_blank_names_are_refused(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.svc.set_labels("u1", self.project, "A", "B")
        for pair in (("", "B"), ("A", "  "), (None, "B"), ("A", 5)):
            with self.subTest(pair=pair), self.assertRaises(ValidationError):
                self.svc.set_labels("ed1", self.project, *pair)
        with self.assertRaises(NotFound):
            self.svc.set_labels("ed1", "missing", "A", "B")
        self.assertEqual(labels(self.svc, self.project), DEFAULT)

    def test_version_two_database_gets_default_labels(self) -> None:
        path = temp_db_path(self)
        old = sqlite3.connect(path)
        old.executescript("".join(MIGRATIONS[:2]) + "PRAGMA user_version = 2;")
        old.execute("INSERT INTO project (id, name, created_at) VALUES ('p1', 'P', 't')")
        old.commit()
        old.close()
        conn = open_database(path)
        self.addCleanup(conn.close)
        self.assertEqual(labels(WorkBoardService(conn, Clock()), "p1"), DEFAULT)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LabelUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        category = self.svc.create_category("ed1", self.project, "UNIT")
        scope = self.svc.create_scope("ed1", self.project, "v1")
        self.assignment = self.svc.add_work_items(
            "ed1", self.project, [{"title": "T"}], "u1", scope_id=scope, category_id=category
        )[0]
        self.window = MainWindow(self.svc, "ed1")
        self.addCleanup(self.window.deleteLater)
        self.errors: list[str] = []
        self.window.show_error = self.errors.append  # type: ignore[method-assign]

    def shown(self) -> tuple[str, ...]:
        window, page = self.window, self.window.work_page
        return (
            window.scope_caption.text(),
            window.category_caption.text(),
            window.scope_action.text(),
            window.category_action.text(),
            page.group.itemText(1),
            page.tree.headerItem().text(5),
        )

    def test_screen_uses_the_project_labels(self) -> None:
        self.assertEqual(
            self.shown(), ("대상 버전", "업무 분야", "대상 버전 추가", "업무 분야 추가", "업무 분야별", "업무 분야")
        )
        self.assertTrue(self.window.set_labels("코드 버전", "검증 분야"))
        self.assertEqual(
            self.shown(), ("코드 버전", "검증 분야", "코드 버전 추가", "검증 분야 추가", "검증 분야별", "검증 분야")
        )
        page = self.window.work_page
        page.group.setCurrentIndex(2)
        page.tree.setCurrentItem(page.tree.topLevelItem(0))
        detail = page.detail.toPlainText()
        self.assertIn("코드 버전 v1", detail)
        self.assertIn("검증 분야 UNIT", detail)
        dialog = WorkItemDialog([], [], self.svc.list_members(self.project), "ed1", True, self.window.labels)
        captions = [dialog.form.itemAt(row, dialog.form.ItemRole.LabelRole) for row in range(dialog.form.rowCount())]
        self.assertLessEqual({"코드 버전", "검증 분야"}, {c.widget().text() for c in captions if c})
        self.assertFalse(self.window.set_labels("", "검증 분야"))
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.window.scope_caption.text(), "코드 버전")

    def test_markup_in_a_label_is_shown_as_typed(self) -> None:
        from PySide6.QtCore import Qt

        markup = "<b>굵게</b><img src=x>"
        self.assertTrue(self.window.set_labels(markup, markup))
        for caption in (self.window.scope_caption, self.window.category_caption):
            self.assertEqual((caption.text(), caption.textFormat()), (markup, Qt.TextFormat.PlainText))
        dialog = WorkItemDialog([], [], self.svc.list_members(self.project), "ed1", True, self.window.labels)
        captions = [dialog.form.itemAt(row, dialog.form.ItemRole.LabelRole) for row in range(dialog.form.rowCount())]
        typed = [c.widget() for c in captions if c and c.widget().text() == markup]
        self.assertEqual([c.textFormat() for c in typed], [Qt.TextFormat.PlainText] * 2)
        page = self.window.work_page
        page.group.setCurrentIndex(2)
        page.tree.setCurrentItem(page.tree.topLevelItem(0))
        self.assertIn(markup, page.detail.toPlainText())
        self.assertNotIn("<img", page.detail.toHtml())

    def test_labels_follow_the_selected_project(self) -> None:
        self.window.set_labels("코드 버전", "검증 분야")
        self.window.create_project("다른 프로젝트")
        self.assertEqual(self.shown()[:2], ("대상 버전", "업무 분야"))
        self.window.project.setCurrentIndex(self.window.project.findData(self.project))
        self.assertEqual(self.shown()[:2], ("코드 버전", "검증 분야"))


if __name__ == "__main__":
    unittest.main()
