from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from workboard.config import load_settings
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.ui.login import LoginDialog
from workboard.ui.main_window import MainWindow


def _ask_who(service: WorkBoardService, default_id: str) -> str | None:
    """Run the login dialog and let it go before anything else is built."""
    login = LoginDialog(service, default_id)
    accepted = login.exec() == LoginDialog.DialogCode.Accepted
    user_id = login.user_id if accepted else None
    login.deleteLater()
    return user_id


def check_excel() -> int:
    """Ask for a workbook and say only whether this PC's Excel could read it.

    For checking document-security (DRM) behaviour on a company PC. The output is safe to
    capture: counts and a kind of failure, never a file name, sheet name or cell.
    """
    from PySide6.QtWidgets import QFileDialog

    from workboard.import_export.excel import ExcelError, read_excel
    from workboard.ui.text import EXCEL_ERRORS

    app = QApplication(sys.argv[:1])
    path, _ = QFileDialog.getOpenFileName(None, "확인할 엑셀 파일", "", "Excel (*.xlsx *.xlsm *.xls)")
    try:
        if not path:
            print("파일을 고르지 않았습니다.")
            return 1
        try:
            sheets = read_excel(path)
        except ExcelError as error:
            print(f"엑셀 읽기 실패 (종류: {error.kind})")
            print(EXCEL_ERRORS[error.kind])
            return 1
        print(f"엑셀 읽기 성공: 시트 {len(sheets)}개")
        for number, (_, grid) in enumerate(sheets, start=1):
            if grid is None:
                print(f"  시트 {number}: 너무 커서 읽지 않음")
            else:
                filled = sum(bool(cell) for row in grid for cell in row)
                print(f"  시트 {number}: {len(grid)}줄 x {max((len(r) for r in grid), default=0)}열, 글자가 든 칸 {filled}개")
        return 0
    finally:
        app.shutdown()


def run() -> int:
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    conn = open_database(settings.db_path)
    try:
        service = WorkBoardService(conn)
        user_id = _ask_who(service, settings.user_id)
        if not user_id:
            return 0
        window = MainWindow(service, user_id)
        window.show()
        code = app.exec()
        # Tear the widgets down while the application object still exists. Left to the
        # interpreter's exit, the order is arbitrary and the process can crash on the way out.
        window.close()
        window.deleteLater()
        del window
        app.processEvents()
        return code
    finally:
        conn.close()
        app.shutdown()
