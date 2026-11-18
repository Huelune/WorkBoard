from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from workboard.config import load_settings
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.ui.main_window import MainWindow


def run() -> int:
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    conn = open_database(settings.db_path)
    try:
        window = MainWindow(WorkBoardService(conn), settings.user_id)
        window.show()
        return app.exec()
    finally:
        conn.close()
