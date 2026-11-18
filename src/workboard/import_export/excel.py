"""Read a workbook through the Excel installed on this PC.

The files are protected by document-security software, so only the signed-in user's own
Excel can open them; reading the file bytes directly would get ciphertext. Excel is shown
while it works so that any prompt from Excel or the security software can be answered.
The workbook is opened read-only and closed without saving.

Needs a normal Windows Qt session: Qt sets up COM there, and not on the offscreen platform.
"""

from __future__ import annotations

import os

from workboard.import_export.table import MAX_COLUMNS, MAX_ROWS, clean_grid

# One sheet: its name and its cells from A1 down to the last used cell, or None when it is too big.
Sheet = tuple[str, "list[list[str]] | None"]


class ExcelError(Exception):
    """Reading failed. `kind` is one of: not_installed, cannot_open, no_sheets."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def column_letters(number: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def plain(value: object) -> object:
    """Qt's date types as Python ones; everything else unchanged."""
    return value.toPython() if hasattr(value, "toPython") else value


FORCE_DISABLE_MACROS = 3  # msoAutomationSecurityForceDisable


def prepare(excel: object) -> bool:
    """Make this Excel safe to open a workbook in, and visible. Returns False if that failed.

    An Excel driven by another program opens files at the lowest macro security, so a
    workbook's own macros (Workbook_Open and the like) would run without asking. The import
    only reads cell values, so macros and events are switched off for this Excel instance.

    The settings go through dynamicCall on purpose. On the application object setProperty
    quietly stores a Qt-side value and never reaches Excel, and property() then reads that
    value back, so it looks set when it is not. The check below asks Excel itself.
    """
    excel.dynamicCall("SetAutomationSecurity(int)", FORCE_DISABLE_MACROS)
    excel.dynamicCall("SetEnableEvents(bool)", False)
    excel.dynamicCall("SetVisible(bool)", True)  # so a prompt from Excel or the security software can be answered
    return excel.dynamicCall("AutomationSecurity") == FORCE_DISABLE_MACROS and excel.dynamicCall("EnableEvents") is False


def read_excel(path: str) -> list[Sheet]:
    from PySide6.QtAxContainer import QAxObject  # imported here so the rest of the package needs no Qt

    excel = QAxObject("Excel.Application")
    if excel.isNull():
        raise ExcelError("not_installed")
    books = None
    try:
        if not prepare(excel):
            raise ExcelError("cannot_open")  # refuse to open anything if macros could not be disabled
        books = excel.querySubObject("Workbooks")
        book = (
            books.querySubObject(
                "Open(const QString&, const QVariant&, const QVariant&)",
                os.path.abspath(path).replace("/", "\\"),
                0,  # do not update links
                True,  # read-only
            )
            if books
            else None
        )
        if book is None or book.isNull():
            raise ExcelError("cannot_open")
        try:
            sheets: list[Sheet] = []
            for index in range(1, int(book.querySubObject("Worksheets").property("Count") or 0) + 1):
                sheet = book.querySubObject("Worksheets(int)", index)
                used = sheet.querySubObject("UsedRange")
                last_row = int(used.property("Row")) + int(used.querySubObject("Rows").property("Count")) - 1
                last_column = int(used.property("Column")) + int(used.querySubObject("Columns").property("Count")) - 1
                name = str(sheet.property("Name"))
                if last_row > MAX_ROWS or last_column > MAX_COLUMNS:
                    sheets.append((name, None))
                    continue
                # From A1, so a grid index is the sheet's own row and column number.
                area = sheet.querySubObject("Range(const QString&)", f"A1:{column_letters(last_column)}{last_row}")
                value = area.property("Value")
                rows = value if isinstance(value, list) else [[value]]
                sheets.append((name, clean_grid([[plain(cell) for cell in row] for row in rows])))
            if not sheets:
                raise ExcelError("no_sheets")
            return sheets
        finally:
            book.dynamicCall("Close(bool)", False)  # never save
    finally:
        # Quit only the Excel this call started, and only when nothing else is open in it.
        if books is not None and not int(books.property("Count") or 0):
            excel.dynamicCall("Quit()")
