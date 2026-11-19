from __future__ import annotations

import os
import unittest

from support import Clock, temp_db_path
from workboard.domain import ValidationError, is_web_link
from workboard.services import WorkBoardService
from workboard.storage import open_database

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication

    from workboard.ui.dialogs import WorkItemDialog
    from workboard.ui.main_window import MANAGE_INDEX, MainWindow
    from workboard.ui.work_page import LINK_COLUMN
except ImportError:
    QApplication = None

LINK = "https://example.test/item/1"


class LinkRuleTests(unittest.TestCase):
    def test_only_web_links_count(self) -> None:
        for value in ("http://a", "https://a"):
            self.assertTrue(is_web_link(value))
        for value in ("javascript:alert(1)", "file:///c:/x", "ftp://a", "REF-1", "", None, 5, " https://a"):
            self.assertFalse(is_web_link(value))

    def test_older_single_item_call_checks_the_link_too(self) -> None:
        conn = open_database(temp_db_path(self))
        self.addCleanup(conn.close)
        svc = WorkBoardService(conn, Clock())
        project = svc.create_project("ed1", "P")
        with self.assertRaises(ValidationError):
            svc.create_work_item("ed1", project, "T", external_url="javascript:alert(1)")
        self.assertEqual(svc.create_work_item("ed1", project, "T", external_url=f" {LINK} ")[:0], "")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ManageAndMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.scope = self.svc.create_scope("ed1", self.project, "v1")
        self.category = self.svc.create_category("ed1", self.project, "UNIT")
        self.linked, self.plain = self.svc.add_work_items(
            "ed1",
            self.project,
            [{"title": "링크 있음", "external_ref": "REF-1", "external_url": LINK}, {"title": "링크 없음"}],
            "u1",
            scope_id=self.scope,
            category_id=self.category,
        )
        self.errors: list[str] = []
        self.opened: list[str] = []

    def window(self, actor: str) -> MainWindow:
        window = MainWindow(self.svc, actor)
        self.addCleanup(window.deleteLater)
        window.show_error = self.errors.append  # type: ignore[method-assign]
        window._open_url = lambda url: self.opened.append(url) or True  # type: ignore[method-assign]
        window.work_page.group.setCurrentIndex(2)
        return window

    @staticmethod
    def texts(widget) -> list[str]:  # noqa: ANN001
        return [widget.item(i).text() for i in range(widget.count())]

    def test_manage_page_edits_names_items_and_members(self) -> None:
        window = self.window("ed1")
        page = window.manage_page
        self.assertEqual(window.nav.item(MANAGE_INDEX).text(), "관리")
        self.assertEqual((page.scope_box.title(), page.category_box.title()), ("대상 버전", "업무 분야"))
        self.assertEqual((self.texts(page.scope_list), self.texts(page.category_list)), (["v1"], ["UNIT"]))

        page.scope_new.setText("v2")
        page.scope_add.click()
        page.category_new.setText("INTEG")
        page.category_new.returnPressed.emit()
        self.assertEqual((self.texts(page.scope_list), self.texts(page.category_list)), (["v1", "v2"], ["UNIT", "INTEG"]))
        self.assertEqual((page.scope_new.text(), window.scope.count(), window.category.count()), ("", 3, 3))

        page.scope_new.setText("  ")
        page.scope_add.click()
        self.assertEqual((len(self.errors), len(self.texts(page.scope_list))), (1, 2))

        page.scope_name.setText("코드 버전")
        page.category_name.setText("검증 분야")
        page.rename_button.click()
        self.assertEqual((page.scope_box.title(), window.scope_caption.text()), ("코드 버전", "코드 버전"))

        page.member_id.setText("u2")
        page.member_name.setText("이 담당")
        page.member_add.click()
        rows = [[page.members.item(r, c).text() for c in range(3)] for r in range(page.members.rowCount())]
        self.assertEqual(rows, [["ed1", "", "편집자"], ["u1", "", "구성원"], ["u2", "이 담당", "구성원"]])
        page.members.selectRow(1)
        self.assertEqual((page.selected_member(), page.member_id.text()), ("u1", "u1"))
        page.member_name.setText("김 담당")
        page.member_add.click()
        self.assertEqual(page.members.item(1, 1).text(), "김 담당")
        self.assertTrue(window.add_editor("u2"))
        self.assertEqual(page.members.item(2, 2).text(), "편집자")

    def test_manage_page_is_read_only_for_others(self) -> None:
        page = self.window("u1").manage_page
        self.assertEqual(self.texts(page.scope_list), ["v1"])
        for widget in (page.rename_button, page.scope_add, page.category_new, page.member_add, page.editor_button):
            self.assertFalse(widget.isEnabled())

    def test_adding_work_needs_both_fields(self) -> None:
        window = self.window("ed1")
        dialog = WorkItemDialog(
            self.svc.list_scopes(self.project), self.svc.list_categories(self.project), [], "ed1", True
        )
        self.assertEqual((dialog.scope.count(), dialog.scope.currentData()), (1, self.scope))
        self.assertEqual((dialog.category.count(), dialog.category.currentData()), (1, self.category))
        values = {"items": [{"title": "T"}], "assignee_id": "u1", "due_date": None}
        self.assertFalse(window.add_work_items({**values, "scope_id": None, "category_id": self.category}))
        self.assertFalse(window.add_work_items({**values, "scope_id": self.scope, "category_id": None}))
        self.assertEqual(len(window.work_page.rows), 2)

        window.create_project("빈 프로젝트")
        self.errors.clear()
        window.ask_work_item()  # must not open a dialog: there is nothing to pick yet
        self.assertEqual(len(self.errors), 1)
        self.assertIn("관리 화면", self.errors[0])
        self.assertEqual(window.nav.currentRow(), MANAGE_INDEX)

    def test_right_click_choices_depend_on_who_is_looking(self) -> None:
        def choices(actor: str, assignment: str) -> dict[str, bool]:
            page = self.window(actor).work_page
            return {label: enabled for label, enabled, _ in page.menu_actions(page.rows[assignment])}

        self.assertEqual(
            choices("ed1", self.linked),
            {"진행 입력": False, "기한 변경": True, "배정 취소": True, "업무 삭제": True, "외부 링크 열기": True, "근거 링크 열기": False},
        )
        self.assertEqual(
            choices("u1", self.plain),
            {"진행 입력": True, "기한 변경": False, "배정 취소": False, "업무 삭제": False, "외부 링크 열기": False, "근거 링크 열기": False},
        )
        self.svc.update_progress("u1", self.plain, "done", 100, evidence_ref="https://example.test/proof")
        self.assertTrue(choices("ed1", self.plain)["근거 링크 열기"])

    def test_menu_actions_run_and_delete_removes_the_row(self) -> None:
        window = self.window("ed1")
        page = window.work_page
        actions = {label: call for label, _, call in page.menu_actions(page.rows[self.linked])}
        actions["외부 링크 열기"]()
        self.assertEqual(self.opened, [LINK])
        self.assertTrue(window.delete_work_item(page.rows[self.linked]["work_item_id"]))
        self.assertEqual(list(page.rows), [self.plain])
        outsider = self.window("u1")
        self.assertFalse(outsider.delete_work_item(outsider.work_page.rows[self.plain]["work_item_id"]))
        self.assertEqual(list(outsider.work_page.rows), [self.plain])

    def test_reference_column_opens_only_web_links(self) -> None:
        window = self.window("ed1")
        page = window.work_page
        items = {page.tree.topLevelItem(i).text(0): page.tree.topLevelItem(i) for i in range(2)}
        linked, plain = items["링크 있음"], items["링크 없음"]
        self.assertEqual((linked.text(LINK_COLUMN), plain.text(LINK_COLUMN)), ("REF-1", ""))
        self.assertTrue(linked.font(LINK_COLUMN).underline())
        self.assertFalse(plain.font(LINK_COLUMN).underline())
        self.assertEqual(linked.toolTip(LINK_COLUMN), LINK)
        page._clicked(linked, 0)
        page._clicked(plain, LINK_COLUMN)
        self.assertEqual(self.opened, [])
        page._clicked(linked, LINK_COLUMN)
        self.assertEqual(self.opened, [LINK])
        for unsafe in ("javascript:alert(1)", "file:///c:/x", "REF-1", None):
            self.assertFalse(window.open_link(unsafe))
        self.assertEqual(self.opened, [LINK])
        page.tree.setCurrentItem(linked)
        self.assertIn(f'href="{LINK}"', page.detail.toHtml())


if __name__ == "__main__":
    unittest.main()
