from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import Clock, temp_db_path
from workboard.config import load_settings
from workboard.domain import is_overdue
from workboard.services import WorkBoardService
from workboard.storage import open_database


class Stage2ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())
        self.project = self.svc.create_project("ed1", "P")
        self.unit = self.svc.create_category("ed1", self.project, "UNIT")
        self.integ = self.svc.create_category("ed1", self.project, "INTEG")
        for category, assignee in ((self.unit, "u1"), (self.unit, "u2"), (self.integ, "u1")):
            item = self.svc.create_work_item("ed1", self.project, "T", category_id=category)
            self.svc.assign("ed1", item, assignee, "2026-01-10")

    def test_is_editor(self) -> None:
        self.assertTrue(self.svc.is_editor("ed1", self.project))
        self.assertFalse(self.svc.is_editor("u1", self.project))
        self.assertFalse(self.svc.is_editor(None, self.project))  # type: ignore[arg-type]
        self.assertFalse(self.svc.is_editor("ed1", "missing"))

    def test_group_summary_follows_the_list_filters(self) -> None:
        summary = self.svc.group_summary(self.project, "assignee", "2026-01-20", category_id=self.unit)
        self.assertEqual([(s["key"], s["count"], s["overdue"]) for s in summary], [("u1", 1, 1), ("u2", 1, 1)])
        self.assertEqual(self.svc.group_summary(self.project, "assignee", "2026-01-20")[0]["count"], 2)

    def test_is_overdue(self) -> None:
        self.assertTrue(is_overdue("2026-01-10", "in_progress", "2026-01-11"))
        self.assertFalse(is_overdue("2026-01-10", "in_progress", "2026-01-10"))
        self.assertFalse(is_overdue("2026-01-10", "done", "2026-01-11"))
        self.assertFalse(is_overdue(None, "in_progress", "2026-01-11"))


class SettingsTests(unittest.TestCase):
    def test_defaults_without_a_file(self) -> None:
        settings = load_settings(Path("does-not-exist.toml"), home=Path("H"))
        self.assertEqual((settings.db_path, settings.user_id), (Path("H") / ".workboard" / "workboard.db", ""))

    def test_file_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('[storage]\nlocal_db_path = "D:/data/wb.db"\n[user]\nid = " kim "\n', encoding="utf-8")
            settings = load_settings(path, home=Path("H"))
        self.assertEqual((settings.db_path, settings.user_id), (Path("D:/data/wb.db"), "kim"))

    def test_file_saved_with_a_byte_order_mark_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text("[user]\nid = 'kim'\n", encoding="utf-8-sig")
            self.assertEqual(load_settings(path, home=Path("H")).user_id, "kim")

    def test_blank_values_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('[storage]\nlocal_db_path = ""\n[user]\nid = ""\n', encoding="utf-8")
            settings = load_settings(path, home=Path("H"))
        self.assertEqual(settings.user_id, "")
        self.assertEqual(settings.db_path.name, "workboard.db")


if __name__ == "__main__":
    unittest.main()
