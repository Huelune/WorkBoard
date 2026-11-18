from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from support import Clock, temp_db_path
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.ui.text import spark

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication

    from workboard.ui.dialogs import ProgressDialog, WorkItemDialog, parse_work_lines
    from workboard.ui.main_window import MainWindow
    from workboard.ui.work_page import NOTE_COLUMN, PROGRESS_COLUMN, ROLE
except ImportError:  # the UI is optional for the stdlib-only test run
    QApplication = None

TODAY = "2026-01-20"
NOW = datetime(2026, 1, 19, tzinfo=timezone.utc)


class SparkTests(unittest.TestCase):
    def test_spark(self) -> None:
        self.assertEqual(spark([0, 1, 2, 4]), "·▂▄█")
        self.assertEqual(spark([0, 0]), "··")
        self.assertEqual(spark([]), "")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class UiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.clock = Clock()
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, self.clock)
        self.errors: list[str] = []

    def window(self, actor: str) -> MainWindow:
        window = MainWindow(self.svc, actor)
        self.addCleanup(window.deleteLater)
        window.today = lambda: TODAY  # type: ignore[method-assign]
        window.now = lambda: NOW  # type: ignore[method-assign]
        window.show_error = self.errors.append  # type: ignore[method-assign]
        window.refresh()
        return window

    def seed(self) -> None:
        svc = self.svc
        self.project = svc.create_project("ed1", "P1")
        svc.add_editor("ed1", self.project, "ed2")
        self.unit = svc.create_category("ed1", self.project, "UNIT")
        self.scope = svc.create_scope("ed1", self.project, "v1")
        self.ids = {}
        for name, assignee, due in (
            ("a1", "u1", "2026-01-10"),
            ("a2", "u1", "2026-01-25"),
            ("<b>굵게</b>", "u1", "2026-01-07"),
            ("a4", "u2", "2026-01-30"),
            ("a5", "u2", None),
        ):
            item = svc.create_work_item("ed1", self.project, name, category_id=self.unit)
            self.ids[name] = svc.assign("ed1", item, assignee, due)
        self.clock.now = datetime(2026, 1, 6, 9, tzinfo=timezone.utc)
        svc.update_progress("u1", self.ids["a1"], "done", 100, evidence_ref="https://example.test/r1")
        svc.update_progress("u1", self.ids["a2"], "done", 100, evidence_ref="REF-2", evidence_note="확인함")
        svc.update_progress("u1", self.ids["<b>굵게</b>"], "in_progress", 50)
        svc.update_progress("u2", self.ids["a4"], "done", 100, evidence_ref="REF-4")
        svc.update_progress("u2", self.ids["a5"], "on_hold", 20, hold_reason="자료 대기")
        svc.review_completion("ed1", self.ids["a1"], "confirmed")
        svc.review_completion("ed1", self.ids["a4"], "rejected", "근거 부족")

    @staticmethod
    def tops(tree) -> list:  # noqa: ANN001
        return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

    @staticmethod
    def kids(item) -> list:  # noqa: ANN001
        return [item.child(i) for i in range(item.childCount())]


class EmptyWindowTests(UiCase):
    def test_window_opens_on_an_empty_database(self) -> None:
        window = self.window("u1")
        self.assertEqual(window.work_page.tree.topLevelItemCount(), 0)
        self.assertFalse(window.manage_page.rename_button.isEnabled())
        self.assertIn("프로젝트를 먼저", window.pace_page.status.text())
        self.assertTrue(window.warning.isHidden())
        self.assertTrue(window.create_project("새 프로젝트"))
        self.assertEqual(window.project.currentText(), "새 프로젝트")
        self.assertTrue(window.manage_page.rename_button.isEnabled())
        self.assertFalse(window.create_project("  "))
        self.assertEqual(len(self.errors), 1)


class WorkListTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.win = self.window("ed2")
        self.page = self.win.work_page

    def test_grouped_by_assignee_and_collapsed_by_default(self) -> None:
        u1, u2 = self.tops(self.page.tree)
        self.assertEqual(u1.text(0), "u1 · 3건")
        self.assertEqual(u1.text(NOTE_COLUMN), "확인 대기 1 · 기한 초과 1")
        self.assertEqual(u1.data(PROGRESS_COLUMN, ROLE), 83)
        self.assertEqual(u2.text(NOTE_COLUMN), "반려 1")
        self.assertFalse(u1.isExpanded())
        self.assertEqual([k.text(0) for k in self.kids(u1)], ["<b>굵게</b>", "a2"])
        self.assertEqual([k.text(NOTE_COLUMN) for k in self.kids(u1)], ["진행 중 · 기한 초과", "확인 대기"])
        self.assertEqual([k.text(0) for k in self.kids(u2)], ["a4", "a5"])

    def test_confirmed_work_can_be_shown(self) -> None:
        self.page.hide_confirmed.setChecked(False)
        self.assertEqual(len(self.kids(self.tops(self.page.tree)[0])), 3)

    def test_other_groupings_and_search(self) -> None:
        self.page.group.setCurrentIndex(1)
        (unit,) = self.tops(self.page.tree)
        self.assertEqual(unit.text(0), "UNIT · 5건")
        self.assertEqual(self.page.group.itemText(1), "업무 분야별")
        self.page.group.setCurrentIndex(2)
        self.assertEqual(self.page.tree.topLevelItemCount(), 4)
        self.page.search.setText("U2")
        self.assertEqual([i.text(0) for i in self.tops(self.page.tree)], ["a4", "a5"])

    def test_expanded_group_stays_open_after_reload(self) -> None:
        self.tops(self.page.tree)[1].setExpanded(True)
        self.win.refresh()
        u1, u2 = self.tops(self.page.tree)
        self.assertEqual((u1.isExpanded(), u2.isExpanded()), (False, True))

    def test_detail_escapes_typed_text_and_links_only_urls(self) -> None:
        self.page.group.setCurrentIndex(2)
        self.page.hide_confirmed.setChecked(False)
        items = {i.text(0): i for i in self.tops(self.page.tree)}
        self.page.tree.setCurrentItem(items["<b>굵게</b>"])
        self.assertIn("<b>굵게</b>", self.page.detail.toPlainText())
        self.page.tree.setCurrentItem(items["a1"])
        self.assertIn('href="https://example.test/r1"', self.page.detail.toHtml())
        self.assertIn("확인 (ed1)", self.page.detail.toPlainText())
        self.page.tree.setCurrentItem(items["a2"])
        self.assertNotIn("href", self.page.detail.toHtml())
        self.assertFalse(self.page.progress_button.isEnabled())

    def test_editor_adds_many_and_bad_input_saves_nothing(self) -> None:
        values = {
            "items": parse_work_lines("새 업무 1\n\n새 업무 2\tREF-9\thttps://example.test/9\n"),
            "scope_id": self.scope,
            "category_id": self.unit,
            "assignee_id": "u2",
            "due_date": "2026-13-01",
        }
        self.assertEqual(values["items"][1], {"title": "새 업무 2", "external_ref": "REF-9", "external_url": "https://example.test/9"})
        before = self.conn.execute("SELECT COUNT(*) FROM work_item").fetchone()[0]
        self.assertFalse(self.win.add_work_items(values))
        self.assertFalse(self.win.add_work_items({**values, "due_date": None, "assignee_id": " "}))
        self.assertFalse(self.win.add_work_items({**values, "due_date": None, "items": []}))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_item").fetchone()[0], before)
        self.assertTrue(self.win.add_work_items({**values, "due_date": "2026-02-01"}))
        self.assertEqual(self.tops(self.page.tree)[1].text(0), "u2 · 4건")
        self.assertTrue(self.win.add_category("INTEG"))
        self.assertEqual(self.win.category.count(), 3)

    def test_member_names_are_shown_and_members_add_their_own_work(self) -> None:
        self.assertTrue(self.win.add_member("u1", "김 담당"))
        self.assertEqual(self.tops(self.page.tree)[0].text(0), "김 담당 · 3건")
        self.page.search.setText("김 담")
        self.assertEqual(len(self.kids(self.tops(self.page.tree)[0])), 2)
        member = self.window("u1")
        self.assertTrue(member.work_page.add_button.isEnabled())
        mine = {"items": parse_work_lines("내가 만든 일"), "scope_id": self.scope, "category_id": self.unit, "due_date": None}
        self.assertFalse(member.add_work_items({**mine, "assignee_id": "u2"}))
        self.assertTrue(member.add_work_items({**mine, "assignee_id": "u1"}))
        self.assertEqual(member.work_page.tree.topLevelItem(0).text(0), "김 담당 · 4건")
        self.assertFalse(self.window("stranger").work_page.add_button.isEnabled())

    def test_work_dialog_offers_members_to_editors_and_fixes_it_for_others(self) -> None:
        self.svc.add_member("ed1", self.project, "u1", "김 담당")
        members = self.svc.list_members(self.project)
        for_editor = WorkItemDialog([], self.svc.list_categories(self.project), members, "ed2", True)
        self.assertEqual(for_editor.windowTitle(), "업무 추가")
        labels = [for_editor.assignee.itemText(i) for i in range(for_editor.assignee.count())]
        self.assertEqual(labels, ["ed1", "ed2", "김 담당 (u1)", "u2"])
        self.assertEqual(for_editor.assignee_id(), "ed2")
        for_editor.assignee.setCurrentIndex(2)
        self.assertEqual(for_editor.assignee_id(), "u1")
        for_editor.assignee.setEditText("새사람")
        self.assertEqual(for_editor.assignee_id(), "새사람")
        self.assertEqual((for_editor.modes.currentIndex(), len(for_editor.rows)), (0, 1))
        self.assertEqual(for_editor.counter.text(), "0건 등록")
        for_editor.rows[0][1].setText(" 함수 1 ")
        for_editor.add_row()
        for_editor.rows[1][1].setText("함수 2")
        for_editor.rows[1][2].setText("REF-2")
        for_editor.rows[1][1].returnPressed.emit()
        self.assertEqual(len(for_editor.rows), 3)
        for_editor.rows[2][2].setText("제목 없는 줄")
        self.assertEqual(for_editor.counter.text(), "3건 등록")
        for_editor.remove_row(for_editor.rows[2])
        self.assertEqual(
            for_editor.values()["items"],
            [
                {"title": "함수 1", "external_ref": "", "external_url": ""},
                {"title": "함수 2", "external_ref": "REF-2", "external_url": ""},
            ],
        )
        for_editor.remove_row(for_editor.rows[1])
        for_editor.remove_row(for_editor.rows[0])
        self.assertEqual((len(for_editor.rows), for_editor.counter.text()), (1, "0건 등록"))
        for_editor.modes.setCurrentIndex(1)
        for_editor.titles.setPlainText("가\n나\n\n다")
        self.assertEqual(for_editor.counter.text(), "3건 등록")
        self.assertEqual([i["title"] for i in for_editor.values()["items"]], ["가", "나", "다"])
        for_editor.modes.setCurrentIndex(0)
        self.assertEqual(for_editor.values()["items"], [])
        for_member = WorkItemDialog([], [], members, "u1", False)
        self.assertEqual(for_member.assignee.count(), 1)
        self.assertEqual(for_member.assignee_id(), "u1")
        self.assertFalse(for_member.assignee.isEnabled())

    def test_non_editor_cannot_manage(self) -> None:
        window = self.window("u1")
        self.assertFalse(window.manage_page.rename_button.isEnabled())
        self.assertFalse(window.add_member("u9"))
        self.assertFalse(window.cancel_assignment(self.ids["a5"]))
        self.assertIn("권한이 없습니다", self.errors[-1])

    def test_dialogs_collect_values(self) -> None:
        row = self.page.rows[self.ids["a5"]]
        dialog = ProgressDialog(row)
        self.assertTrue(dialog.hold_reason.isEnabled())
        self.assertFalse(dialog.evidence_ref.isEnabled())
        dialog.status.setCurrentIndex(dialog.status.findData("done"))
        self.assertEqual(dialog.progress.value(), 100)
        self.assertFalse(dialog.progress.isEnabled())
        self.assertTrue(dialog.evidence_ref.isEnabled())
        dialog.evidence_ref.setText("REF-5")
        self.assertEqual(
            dialog.values(),
            {"status": "done", "progress": 100, "result": None, "hold_reason": "자료 대기", "evidence_ref": "REF-5", "evidence_note": ""},
        )
        dialog.status.setCurrentIndex(dialog.status.findData("in_progress"))
        self.assertEqual(dialog.progress.value(), 99)


class ProgressAndReviewTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()

    def test_assignee_reports_completion_with_evidence(self) -> None:
        window = self.window("u1")
        target = self.ids["<b>굵게</b>"]
        self.assertEqual(window.mine_page.tree.topLevelItemCount(), 2)
        self.assertFalse(window.save_progress(target, {"status": "done", "progress": 100}))
        self.assertIn("입력을 확인하세요", self.errors[-1])
        self.assertTrue(window.save_progress(target, {"status": "done", "progress": 100, "evidence_ref": "REF-3"}))
        self.assertEqual(window.mine_page.rows[target]["review_state"], "pending")
        self.assertEqual(window.sync.text(), "공유 대기 4")

    def test_editor_cannot_write_progress_for_others(self) -> None:
        window = self.window("ed2")
        self.assertFalse(window.save_progress(self.ids["a5"], {"status": "in_progress", "progress": 90}))
        self.assertIn("권한이 없습니다", self.errors[-1])
        self.assertEqual(window.work_page.rows[self.ids["a5"]]["progress"], 20)

    def test_editor_reviews_pending_completions(self) -> None:
        window = self.window("ed2")
        page = window.review_page
        self.assertEqual(page.count(), 1)
        self.assertEqual(window.nav.item(2).text(), "확인 대기 (1)")
        page.decide("rejected")
        self.assertEqual((page.count(), len(self.errors)), (1, 1))
        page.reason.setText("근거가 열리지 않음")
        page.decide("rejected")
        self.assertEqual((page.count(), page.reason.text()), (0, ""))
        self.assertEqual(window.nav.item(2).text(), "확인 대기")
        self.assertEqual(window.work_page.rows[self.ids["a2"]]["review_state"], "rejected")

    def test_own_work_and_non_editors_are_kept_out_of_review(self) -> None:
        item = self.svc.create_work_item("ed1", self.project, "편집자 업무")
        own = self.svc.assign("ed1", item, "ed2")
        self.svc.update_progress("ed2", own, "done", 100, evidence_ref="REF-9")
        self.assertEqual(self.window("ed2").review_page.count(), 1)
        self.assertEqual(self.window("ed1").review_page.count(), 2)
        outsider = self.window("u1").review_page
        self.assertEqual(outsider.count(), 0)
        self.assertFalse(outsider.confirm_button.isEnabled())


class PaceAndIntegrityTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.win = self.window("u2")

    def test_status_and_pace_tables(self) -> None:
        page = self.win.pace_page
        self.assertIn("전체 <b>5</b> · 확인된 완료 <b>1</b> · 확인 대기 <b>1</b> · 반려 <b>1</b>", page.status.text())
        self.assertIn("기한 초과 <b>1</b>", page.status.text())
        cells = lambda table, row: [table.item(row, c).text() for c in range(table.columnCount())]  # noqa: E731
        self.assertEqual(cells(page.assignees, 0), ["u1", "3", "0", "2", "1", "0", "··█·", "0.25"])
        self.assertEqual(cells(page.assignees, 1), ["u2", "2", "0", "1", "0", "1", "····", "0.0"])
        self.assertEqual(cells(page.projects, 0), ["P1", "5", "1", "4", "··█·", "0.25", "약 16주"])
        other = self.svc.create_project("ed1", "P2")
        item = self.svc.create_work_item("ed1", other, "b1")
        self.svc.assign("ed1", item, "u9")
        self.win.refresh()
        self.assertEqual(page.assignees.rowCount(), 2)
        page.reach.setCurrentIndex(1)
        self.assertEqual(page.assignees.rowCount(), 3)
        self.assertEqual(cells(page.projects, 1)[-1], "-")

    def test_tampering_shows_the_warning(self) -> None:
        self.assertTrue(self.win.warning.isHidden())
        self.conn.execute("UPDATE assignment_progress SET progress = 100 WHERE progress = 20")
        self.conn.commit()
        self.win.refresh()
        self.assertFalse(self.win.warning.isHidden())


if __name__ == "__main__":
    unittest.main()
