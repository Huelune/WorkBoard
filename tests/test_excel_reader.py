"""Reads made-up workbooks through the Excel installed on this PC.

Skipped in the normal run: it starts Excel (a window flashes) and needs a real Windows Qt
session. Run it on its own:

    QT_QPA_PLATFORM=windows WORKBOARD_EXCEL_TEST=1 python -m unittest tests/test_excel_reader.py
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from support import write_xlsx

WANTED = os.environ.get("WORKBOARD_EXCEL_TEST") == "1"
if WANTED:
    from PySide6.QtWidgets import QApplication

    from workboard.import_export.excel import ExcelError, read_excel


@unittest.skipUnless(WANTED, "set WORKBOARD_EXCEL_TEST=1 to drive the installed Excel")
class ExcelReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_reads_text_and_numbers_from_a1(self) -> None:
        path = self.folder / "plain.xlsx"
        write_xlsx(path, {"A1": "제목", "B1": "담당", "C1": "순번", "A2": "예시 함수 1", "B2": "예시 담당", "C2": 3, "A3": "예시 함수 2"})
        before = path.read_bytes()
        sheets = read_excel(str(path))
        self.assertEqual(sheets, [("업무", [["제목", "담당", "순번"], ["예시 함수 1", "예시 담당", "3"], ["예시 함수 2", "", ""]])])
        self.assertEqual(path.read_bytes(), before)  # opened read-only, closed without saving

    def test_grid_rows_are_sheet_row_numbers_even_when_data_starts_lower(self) -> None:
        path = self.folder / "offset.xlsx"
        write_xlsx(path, {"B3": "제목", "C3": "담당", "B4": "예시 함수 1", "C4": "예시 담당"}, sheet="아래에서 시작")
        ((name, grid),) = read_excel(str(path))
        self.assertEqual(name, "아래에서 시작")
        self.assertEqual(len(grid), 4)
        self.assertEqual(grid[2], ["", "제목", "담당"])  # sheet row 3 is grid index 2
        self.assertEqual(grid[3], ["", "예시 함수 1", "예시 담당"])

    def test_macros_and_events_are_off_before_anything_is_opened(self) -> None:
        """Regression: an automated Excel opens workbooks at the lowest macro security by default."""
        from PySide6.QtAxContainer import QAxObject

        from workboard.import_export.excel import FORCE_DISABLE_MACROS, prepare

        excel = QAxObject("Excel.Application")
        try:
            # Asked of Excel itself (dynamicCall); property() on this object only echoes Qt-side values.
            self.assertEqual(excel.dynamicCall("AutomationSecurity"), 1)  # the unsafe default: macros run
            self.assertFalse(excel.dynamicCall("Visible"))
            self.assertTrue(prepare(excel))
            self.assertEqual(excel.dynamicCall("AutomationSecurity"), FORCE_DISABLE_MACROS)
            self.assertIs(excel.dynamicCall("EnableEvents"), False)
            self.assertIs(excel.dynamicCall("Visible"), True)
        finally:
            excel.dynamicCall("Quit()")

    def test_a_file_excel_cannot_open_is_reported_by_kind(self) -> None:
        with self.assertRaises(ExcelError) as caught:
            read_excel(str(self.folder / "없는 파일.xlsx"))
        self.assertEqual(caught.exception.kind, "cannot_open")


if __name__ == "__main__":
    unittest.main()
