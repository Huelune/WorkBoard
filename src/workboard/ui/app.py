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
