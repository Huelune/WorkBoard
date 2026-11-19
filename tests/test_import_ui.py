from __future__ import annotations

import os
import unittest

from support import Clock, temp_db_path
from workboard.services import WorkBoardService
from workboard.storage import open_database

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QDate, QDateTime, QTime
    from PySide6.QtWidgets import QApplication, QDialog

    from workboard.import_export.excel import column_letters, plain
    from workboard.import_export.table import cell_text
    from workboard.ui.import_dialog import ImportDialog
    from workboard.ui.main_window import MainWindow
except ImportError:
    QApplication = None

GRID = [
    ["2026년 검증 계획", "", "", "", "", ""],
    ["코드 버전", "검증 분야", "대상 함수", "담당자", "완료 예정일", "비고"],
    ["v1.0", "단위", "함수 1", "김 담당", "2026-01-15", "메모"],
    ["v1.0", "단위", "함수 2", "이 담당", "2026.1.20", ""],
    ["v1.1", "통합", "함수 3", "없는 사람", "", ""],
    ["v1.0", "단위", "", "", "", ""],
    ["v1.0", "단위", "함수 1", "김 담당", "", ""],
]
PASTED = "\n".join("\t".join(row) for row in GRID)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ImportUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.svc.set_labels("ed1", self.project, "코드 버전", "검증 분야")
        self.svc.add_member("ed1", self.project, "kim", "김 담당")
        self.svc.add_member("ed1", self.project, "lee", "이 담당")
        self.errors: list[str] = []

    def window(self, actor: str = "ed1") -> MainWindow:
        window = MainWindow(self.svc, actor)
        self.addCleanup(window.deleteLater)
        window.show_error = self.errors.append  # type: ignore[method-assign]
        return window

    def dialog(self, window: MainWindow) -> ImportDialog:
        dialog = ImportDialog(window)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_paste_match_preview_and_register(self) -> None:
        window = self.window()
        dialog = self.dialog(window)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("엑셀 파일을 열거나", dialog.summary.text())
        self.assertTrue(dialog.paste_text(PASTED))
        self.assertEqual(dialog.header_row.value(), 2)  # found below the title line
        self.assertEqual(
            dialog.mapping(),
            {"title": 2, "scope": 0, "category": 1, "assignee": 3, "due_date": 4, "external_ref": None, "external_url": None},
        )
        self.assertEqual(dialog.columns["title"].currentText(), "C · 대상 함수")
        self.assertEqual(
            dialog.summary.text(),
            "읽은 줄 5 = 새로 등록 2 + 이미 있음 1 + 오류 1 + 빈 줄 1\n새로 만들어질 코드 버전: v1.0\n새로 만들어질 검증 분야: 단위",
        )
        listed = [[dialog.results.topLevelItem(i).text(c) for c in range(4)] for i in range(dialog.results.topLevelItemCount())]
        self.assertEqual([row[:3] for row in listed], [["5", "오류", "함수 3"], ["7", "이미 있음", "함수 1"]])
        self.assertIn("구성원 목록에 없는 담당자", listed[0][3])
        self.assertIn("이미 등록돼 있거나", listed[1][3])
        self.assertEqual(dialog.apply_button.text(), "새 업무 2건 등록 (오류 1건 제외)")

        self.assertTrue(dialog.apply())
        self.assertEqual((dialog.result(), dialog.imported), (QDialog.DialogCode.Accepted, 2))
        rows = self.svc.list_assignments(self.project)
        self.assertEqual([(r["title"], r["assignee_id"], r["due_date"]) for r in rows], [("함수 1", "kim", "2026-01-15"), ("함수 2", "lee", "2026-01-20")])
        self.assertEqual(len(window.work_page.rows), 2)  # the list behind the dialog is already up to date
        self.assertIn("2건", window.statusBar().currentMessage())

        again = self.dialog(window)
        again.paste_text(PASTED)
        self.assertIn("새로 등록 0 + 이미 있음 3", again.summary.text())
        self.assertFalse(again.apply_button.isEnabled())
        self.assertFalse(again.apply())

    def test_required_columns_must_be_matched_and_choices_are_remembered(self) -> None:
        window = self.window()
        dialog = self.dialog(window)
        odd = [["버전", "분야", "무엇", "누가"], ["v1.0", "단위", "함수 9", "kim"]]
        dialog.load_grid(odd)
        self.assertIsNone(dialog.mapping()["title"])  # "무엇" is not a heading it can guess
        self.assertIn("필수 항목", dialog.summary.text())
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.set_column("title", 2)
        dialog.set_column("assignee", 3)
        self.assertEqual(dialog.apply_button.text(), "새 업무 1건 등록")
        self.assertTrue(dialog.apply())
        self.assertEqual(self.svc.get_import_mapping(self.project)["title"], "무엇")

        later = self.dialog(window)
        later.load_grid([["누가", "무엇", "분야", "버전"], ["lee", "함수 10", "단위", "v1.0"]])
        self.assertEqual(later.mapping()["title"], 1)  # remembered by heading text, wherever the column moved
        self.assertEqual(later.mapping()["assignee"], 0)
        self.assertEqual(later.apply_button.text(), "새 업무 1건 등록")

    def test_changing_the_header_row_or_sheet_replans(self) -> None:
        dialog = self.dialog(self.window())
        dialog.load_sheets([("첫 시트", GRID), ("큰 시트", None), ("빈 시트", [])])
        self.assertFalse(dialog.sheet.isHidden())
        self.assertIn("읽은 줄 5", dialog.summary.text())
        dialog.header_row.setValue(1)
        self.assertIn("필수 항목", dialog.summary.text())
        dialog.header_row.setValue(2)
        self.assertIn("읽은 줄 5", dialog.summary.text())
        dialog.sheet.setCurrentIndex(1)
        self.assertIn("너무 커서", dialog.message.text())
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.sheet.setCurrentIndex(2)
        self.assertTrue(dialog.message.isHidden())
        self.assertFalse(dialog.paste_text("  \n"))
        self.assertIn("복사한 칸이 없습니다", dialog.message.text())

    def test_only_editors_get_the_import_button(self) -> None:
        self.assertTrue(self.window("ed1").work_page.import_button.isEnabled())
        member = self.window("kim")
        self.assertFalse(member.work_page.import_button.isEnabled())
        member.ask_import()  # does nothing for a non-editor
        dialog = self.dialog(member)
        dialog.paste_text(PASTED)
        self.assertIn("권한이 없습니다", dialog.summary.text())
        self.assertFalse(dialog.apply_button.isEnabled())

    def test_excel_values_become_plain_text(self) -> None:
        moment = QDateTime(QDate(2026, 2, 3), QTime(0, 0))
        self.assertEqual(cell_text(plain(moment)), "2026-02-03")
        self.assertEqual(cell_text(plain(QDate(2026, 2, 3))), "2026-02-03")
        self.assertEqual([cell_text(plain(v)) for v in (None, 3.0, "글자")], ["", "3", "글자"])
        self.assertEqual([column_letters(n) for n in (1, 26, 27, 52, 53, 702, 703)], ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"])


if __name__ == "__main__":
    unittest.main()
