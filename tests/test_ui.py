from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from support import Clock, temp_db_path
from workboard.services import WorkBoardService
from workboard.storage import open_database

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog

    from workboard.ui.dialogs import DueDate, DueDateDialog, WorkItemDialog, parse_work_lines
    from workboard.ui.main_window import MANAGE_INDEX, MINE_INDEX, SAVED, WORK_INDEX, MainWindow
    from workboard.ui.pace_page import weekly_text
    from workboard.ui.review_page import EVIDENCE_COLUMN
    from workboard.ui.work_page import NOTE_COLUMN, OWNER_COLUMN, PROGRESS_COLUMN, ROLE
except ImportError:  # the UI is optional for the stdlib-only test run
    QApplication = None

TODAY = "2026-01-20"
NOW = datetime(2026, 1, 19, tzinfo=timezone.utc)
PROOF = "https://example.test/proof"


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
        self.opened: list[str] = []

    def window(self, actor: str) -> MainWindow:
        window = MainWindow(self.svc, actor)
        self.addCleanup(window.deleteLater)
        window.today = lambda: TODAY  # type: ignore[method-assign]
        window.now = lambda: NOW  # type: ignore[method-assign]
        window.show_error = self.errors.append  # type: ignore[method-assign]
        window._open_url = lambda url: self.opened.append(url) or True  # type: ignore[method-assign]
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


class EmptyStateTests(UiCase):
    """Every empty screen says what to do next and offers the button that does it."""

    def empty(self, page) -> tuple[bool, str, str]:  # noqa: ANN001
        button = "" if page.empty_button.isHidden() else page.empty_button.text()
        return page.body.currentIndex() == 1, page.empty_text.text(), button

    def test_first_run_walks_from_nothing_to_the_first_work_item(self) -> None:
        window = self.window("ed1")
        shown, text, button = self.empty(window.work_page)
        self.assertTrue(shown)
        self.assertIn("아직 프로젝트가 없습니다", text)
        self.assertEqual(button, "새 프로젝트")
        self.assertIn("맡은 업무가 없습니다", self.empty(window.mine_page)[1])
        self.assertFalse(window.manage_page.rename_button.isEnabled())
        self.assertFalse(window.pace_page.status.isHidden())
        self.assertTrue(window.warning.isHidden())

        self.assertTrue(window.create_project("새 프로젝트"))
        self.assertEqual(window.project.currentText(), "새 프로젝트")
        self.assertEqual(window.statusBar().currentMessage(), SAVED)
        shown, text, button = self.empty(window.work_page)
        self.assertIn("'대상 버전', '업무 분야' 두 칸에 항목이 하나씩", text)
        self.assertEqual(button, "관리 화면으로")
        window.work_page.empty_button.click()
        self.assertEqual(window.nav.currentRow(), MANAGE_INDEX)

        self.assertTrue(window.add_scope("v1"))
        self.assertTrue(window.add_category("UNIT"))
        self.assertEqual(self.empty(window.work_page)[1:], ("아직 업무가 없습니다.", "업무 추가"))
        scope, category = window.scope.itemData(1), window.category.itemData(1)
        values = {"items": [{"title": "T"}], "scope_id": scope, "category_id": category, "assignee_id": "ed1", "due_date": None}
        self.assertTrue(window.add_work_items(values))
        self.assertEqual(window.work_page.body.currentIndex(), 0)
        self.assertEqual(window.mine_page.body.currentIndex(), 0)

        self.assertFalse(window.create_project("  "))
        self.assertEqual(self.errors, ["이름을 적어 주세요."])

    def test_empty_states_for_people_who_cannot_fix_them(self) -> None:
        project = self.svc.create_project("ed1", "P")
        outsider = self.window("stranger")
        self.assertEqual(self.empty(outsider.work_page)[2], "")
        self.assertIn("편집자가 프로젝트를 준비하는 중", self.empty(outsider.work_page)[1])
        self.svc.create_scope("ed1", project, "v1")
        self.svc.create_category("ed1", project, "C")
        outsider.refresh()
        self.assertIn("구성원이 아니어서", self.empty(outsider.work_page)[1])
        outsider.mine_page.empty_button.click()
        self.assertEqual(outsider.nav.currentRow(), WORK_INDEX)

    def test_a_filter_that_matches_nothing_offers_to_clear_it(self) -> None:
        self.seed()
        other = self.svc.create_scope("ed1", self.project, "v2")
        window = self.window("ed2")
        window.scope.setCurrentIndex(window.scope.findData(other))
        self.assertEqual(self.empty(window.work_page)[1:], ("고른 조건에 맞는 업무가 없습니다.", "조건 지우기"))
        window.work_page.empty_button.click()
        self.assertEqual((window.scope.currentIndex(), window.work_page.body.currentIndex()), (0, 0))


class WorkListTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.win = self.window("ed2")
        self.page = self.win.work_page

    def test_grouped_by_assignee_and_open_by_default(self) -> None:
        u1, u2 = self.tops(self.page.tree)
        self.assertEqual(u1.text(0), "u1 · 3건")
        self.assertEqual(u1.text(NOTE_COLUMN), "확인 대기 1 · 기한 초과 1")
        self.assertEqual(u1.data(PROGRESS_COLUMN, ROLE), 83)
        self.assertEqual(u1.data(PROGRESS_COLUMN, Qt.ItemDataRole.AccessibleTextRole), "평균 진행 83%")
        self.assertEqual(u2.text(NOTE_COLUMN), "반려 1")
        self.assertTrue(u1.isExpanded())
        self.assertTrue(u2.isExpanded())
        self.assertEqual([k.text(0) for k in self.kids(u1)], ["<b>굵게</b>", "a2"])
        self.assertEqual([k.text(NOTE_COLUMN) for k in self.kids(u1)], ["진행 중 · 기한 초과", "확인 대기"])
        self.assertEqual(self.kids(u1)[0].data(PROGRESS_COLUMN, Qt.ItemDataRole.AccessibleTextRole), "진행 50%")
        self.assertEqual([k.text(0) for k in self.kids(u2)], ["a4", "a5"])
        self.assertTrue(self.page.tree.isColumnHidden(OWNER_COLUMN))  # the heading already names the assignee
        self.assertTrue(self.page.side.isHidden())  # nothing selected: the list has the full width

    def test_confirmed_work_can_be_shown(self) -> None:
        self.page.hide_confirmed.setChecked(False)
        self.assertEqual(len(self.kids(self.tops(self.page.tree)[0])), 3)

    def test_other_groupings_and_search(self) -> None:
        self.page.group.setCurrentIndex(1)
        (unit,) = self.tops(self.page.tree)
        self.assertEqual(unit.text(0), "UNIT · 5건")
        self.assertEqual(self.page.group.itemText(1), "업무 분야별")
        self.assertFalse(self.page.tree.isColumnHidden(OWNER_COLUMN))
        self.page.group.setCurrentIndex(2)
        self.assertEqual(self.page.tree.topLevelItemCount(), 4)
        self.page.search.setText("U2")
        self.assertEqual([i.text(0) for i in self.tops(self.page.tree)], ["a4", "a5"])

    def test_folded_group_stays_folded_after_reload(self) -> None:
        self.tops(self.page.tree)[1].setExpanded(False)
        self.win.refresh()
        u1, u2 = self.tops(self.page.tree)
        self.assertEqual((u1.isExpanded(), u2.isExpanded()), (True, False))
        u2.setExpanded(True)
        self.win.refresh()
        self.assertTrue(self.tops(self.page.tree)[1].isExpanded())

    def test_detail_escapes_typed_text_and_links_only_urls(self) -> None:
        self.page.group.setCurrentIndex(2)
        self.page.hide_confirmed.setChecked(False)
        items = {i.text(0): i for i in self.tops(self.page.tree)}
        self.page.tree.setCurrentItem(items["<b>굵게</b>"])
        self.assertFalse(self.page.side.isHidden())
        self.assertTrue(self.page.form.isHidden())  # someone else's work: details only, no progress entry
        self.assertIn("<b>굵게</b>", self.page.detail.toPlainText())
        self.page.tree.setCurrentItem(items["a1"])
        self.assertIn('href="https://example.test/r1"', self.page.detail.toHtml())
        self.assertIn("확인 (ed1)", self.page.detail.toPlainText())
        self.page.tree.setCurrentItem(items["a2"])
        self.assertNotIn("href", self.page.detail.toHtml())

    def test_filters_show_only_where_they_apply(self) -> None:
        self.assertFalse(self.win.filters.isHidden())
        self.win.nav.setCurrentRow(MINE_INDEX)
        self.assertTrue(self.win.filters.isHidden())
        self.win.nav.setCurrentRow(WORK_INDEX)
        self.assertFalse(self.win.filters.isHidden())
        self.assertEqual(self.win.account.text(), "ed2")

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
        self.assertEqual(
            self.errors,
            [
                "기한은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.",
                "담당자를 골라 주세요.",
                "등록할 업무를 한 줄 이상 적어 주세요.",
            ],
        )
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

    def test_non_editor_cannot_manage(self) -> None:
        window = self.window("u1")
        self.assertFalse(window.manage_page.rename_button.isEnabled())
        self.assertTrue(window.manage_page.new_project.isEnabled())
        self.assertFalse(window.add_member("u9"))
        self.assertFalse(window.cancel_assignment(self.ids["a5"]))
        self.assertEqual(self.errors[-1], "권한이 없습니다. 프로젝트 편집자만 바꿀 수 있습니다.")


class DialogTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()

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

    def test_due_date_is_picked_not_typed(self) -> None:
        empty = DueDate()
        self.assertEqual((empty.value(), empty.date.isEnabled()), (None, False))
        empty.enabled.setChecked(True)
        self.assertRegex(empty.value(), r"^\d{4}-\d{2}-\d{2}$")
        existing = DueDate("2026-02-03")
        self.assertEqual(existing.value(), "2026-02-03")
        existing.enabled.setChecked(False)
        self.assertIsNone(existing.value())
        self.assertIsNone(DueDate("2026-13-99").value())

    def test_a_refused_save_keeps_the_dialog_and_everything_typed(self) -> None:
        """Regression: the dialog used to close first, so a refusal threw the typed rows away."""
        window = self.window("ed2")
        before = len(self.svc.list_assignments(self.project))
        dialog = WorkItemDialog(
            self.svc.list_scopes(self.project),
            self.svc.list_categories(self.project),
            self.svc.list_members(self.project),
            "ed2",
            True,
            window.labels,
            window,
            on_submit=lambda values: window.attempt(window._add_work_items(values)),
        )
        self.addCleanup(dialog.deleteLater)
        dialog.rows[0][1].setText("링크가 잘못된 업무")
        dialog.rows[0][3].setText("ftp://example.test/x")
        dialog.add_row()
        dialog.rows[1][1].setText("둘째 업무")
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(dialog.message.isHidden())
        self.assertEqual(dialog.message.text(), "링크는 http:// 또는 https://로 시작해야 합니다.")
        self.assertEqual([row[1].text() for row in dialog.rows], ["링크가 잘못된 업무", "둘째 업무"])
        self.assertEqual((len(self.svc.list_assignments(self.project)), self.errors), (before, []))
        dialog.rows[0][3].setText("https://example.test/x")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(len(self.svc.list_assignments(self.project)), before + 2)

    def test_due_date_dialog_saves_through_its_callback(self) -> None:
        window = self.window("ed2")
        target = self.ids["a5"]
        dialog = DueDateDialog(
            None,
            lambda values: window.attempt(lambda: self.svc.set_due_date("ed2", target, values["due_date"])),
        )
        self.addCleanup(dialog.deleteLater)
        dialog.due_date.enabled.setChecked(True)
        picked = dialog.due_date.value()
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(window.work_page.rows[target]["due_date"], picked)
        refused = DueDateDialog(None, lambda values: "거부됨")
        self.addCleanup(refused.deleteLater)
        refused.accept()
        self.assertNotEqual(refused.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(refused.message.text(), "거부됨")


class ProgressTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()

    def test_app_opens_on_my_work_with_the_progress_entry_ready(self) -> None:
        window = self.window("u1")
        self.assertEqual((window.nav.currentRow(), window.stack.currentIndex()), (MINE_INDEX, MINE_INDEX))
        page = window.mine_page
        self.assertEqual([i.text(0) for i in self.tops(page.tree)], ["<b>굵게</b>", "a2"])
        self.assertEqual(page.tree.headerItem().text(OWNER_COLUMN), "프로젝트")
        self.assertEqual(page.tree.topLevelItem(0).text(OWNER_COLUMN), "P1")
        self.assertEqual(page.current_row()["assignment_id"], self.ids["<b>굵게</b>"])  # the one needing a hand
        self.assertFalse(page.form.isHidden())
        self.assertEqual((page.form.status.currentData(), page.form.progress.value()), ("in_progress", 50))
        self.assertEqual(self.window("ed2").nav.currentRow(), WORK_INDEX)  # nothing assigned: the whole list

    def test_form_shows_only_what_the_status_needs(self) -> None:
        form = self.window("u1").mine_page.form
        visible = lambda: [  # noqa: E731
            name
            for name in ("hold_reason", "evidence_ref", "evidence_note", "result")
            if form.form.isRowVisible(getattr(form, name))
        ]
        self.assertEqual(visible(), [])
        form.status.setCurrentIndex(form.status.findData("on_hold"))
        self.assertEqual(visible(), ["hold_reason"])
        form.status.setCurrentIndex(form.status.findData("done"))
        self.assertEqual(visible(), ["evidence_ref", "evidence_note", "result"])
        self.assertEqual((form.progress.value(), form.progress.isEnabled()), (100, False))
        form.status.setCurrentIndex(form.status.findData("in_progress"))
        self.assertEqual((form.progress.value(), form.progress.isEnabled()), (99, True))
        form.status.setCurrentIndex(form.status.findData("not_started"))
        self.assertEqual((form.progress.value(), visible()), (0, []))

    def test_a_refused_save_keeps_what_was_typed_and_says_which_field(self) -> None:
        """Regression: a refusal used to close the form, lose the input and list every rule."""
        window = self.window("u1")
        page, target = window.mine_page, self.ids["<b>굵게</b>"]
        form = page.form
        form.status.setCurrentIndex(form.status.findData("done"))
        form.evidence_note.setText("시험 12건 통과")
        self.assertFalse(form.save())
        self.assertEqual(form.message.text(), "완료 근거를 적어 주세요. 결과 문서의 링크나 항목 ID입니다.")
        self.assertFalse(form.message.isHidden())
        self.assertEqual((form.status.currentData(), form.evidence_note.text()), ("done", "시험 12건 통과"))
        self.assertEqual((self.errors, page.rows[target]["status"]), ([], "in_progress"))
        self.assertEqual(window.statusBar().currentMessage(), "")

        form.evidence_ref.setText(PROOF)
        self.assertTrue(form.save())
        self.assertTrue(form.message.isHidden())
        self.assertEqual(window.statusBar().currentMessage(), SAVED)
        self.assertEqual(page.rows[target]["review_state"], "pending")
        self.assertEqual(page.current_row()["assignment_id"], target)  # still on the same row
        self.assertEqual(form.evidence_ref.text(), PROOF)

    def test_one_step_actions_still_explain_a_refusal(self) -> None:
        window = self.window("u1")
        target = self.ids["<b>굵게</b>"]
        self.assertFalse(window.save_progress(target, {"status": "on_hold", "progress": 10}))
        self.assertEqual(self.errors[-1], "보류 사유를 적어 주세요.")
        self.assertFalse(window.save_progress(target, {"status": "in_progress", "progress": 100}))
        self.assertIn("진행도가 상태와 맞지 않습니다", self.errors[-1])
        self.assertTrue(window.save_progress(target, {"status": "done", "progress": 100, "evidence_ref": "REF-3"}))
        self.assertEqual(window.mine_page.rows[target]["review_state"], "pending")

    def test_editor_cannot_write_progress_for_others(self) -> None:
        window = self.window("ed2")
        self.assertFalse(window.save_progress(self.ids["a5"], {"status": "in_progress", "progress": 90}))
        self.assertEqual(self.errors[-1], "권한이 없습니다. 진행은 담당자 본인만 바꿀 수 있습니다.")
        self.assertEqual(window.work_page.rows[self.ids["a5"]]["progress"], 20)

    def test_progress_entry_is_reachable_from_the_shared_list(self) -> None:
        window = self.window("u2")
        window.nav.setCurrentRow(WORK_INDEX)
        page = window.work_page
        row = page.rows[self.ids["a5"]]
        self.assertTrue(dict((label, enabled) for label, enabled, _ in page.menu_actions(row))["진행 입력"])
        window.edit_progress(row)
        self.assertEqual(page.current_row()["assignment_id"], self.ids["a5"])
        self.assertFalse(page.form.isHidden())
        self.assertEqual(page.form.hold_reason.text(), "자료 대기")
        window.edit_progress(page.rows[self.ids["a2"]])  # not mine: nothing happens
        self.assertEqual(page.current_row()["assignment_id"], self.ids["a5"])


class ReviewTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()

    def test_editor_reviews_with_the_evidence_in_front_of_them(self) -> None:
        self.svc.update_progress("u2", self.ids["a5"], "done", 100, evidence_ref=PROOF, evidence_note="보고서")
        window = self.window("ed2")
        page = window.review_page
        self.assertEqual(page.count(), 2)
        self.assertEqual(window.nav.item(2).text(), "확인 대기 (2)")
        self.assertEqual(page.current_row()["assignment_id"], self.ids["a2"])
        self.assertFalse(page.side.isHidden())
        self.assertIn("확인함", page.detail.toPlainText())
        self.assertFalse(page.open_button.isEnabled())  # REF-2 is an ID, not a link
        self.assertFalse(page.open_evidence())

        linked = page.tree.topLevelItem(1)
        self.assertTrue(linked.font(EVIDENCE_COLUMN).underline())
        page.tree.setCurrentItem(linked)
        self.assertTrue(page.open_button.isEnabled())
        self.assertIn(f'href="{PROOF}"', page.detail.toHtml())
        page._clicked(linked, 0)
        self.assertEqual(self.opened, [])
        page._clicked(linked, EVIDENCE_COLUMN)
        page.open_button.click()
        self.assertEqual(self.opened, [PROOF, PROOF])

    def test_a_rejection_needs_a_reason_and_says_so_in_place(self) -> None:
        window = self.window("ed2")
        page = window.review_page
        self.assertFalse(page.decide("rejected"))
        self.assertEqual((page.message.text(), page.message.isHidden()), ("반려 사유를 적어 주세요.", False))
        self.assertEqual((page.count(), self.errors), (1, []))
        page.reason.setText("근거가 열리지 않음")
        self.assertTrue(page.decide("rejected"))
        self.assertEqual((page.count(), page.reason.text()), (0, ""))
        self.assertTrue(page.side.isHidden())
        self.assertIn("확인을 기다리는 완료 신고가 없습니다", page.note.text())
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
        self.assertIn("편집자만", outsider.note.text())


class PaceAndIntegrityTests(UiCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.win = self.window("u2")

    def test_weekly_counts_are_plain_numbers(self) -> None:
        self.assertEqual(weekly_text([0, 0, 1, 0]), "0  0  1  0")
        self.assertEqual(weekly_text([]), "")

    def test_status_and_pace_tables(self) -> None:
        page = self.win.pace_page
        self.assertEqual(
            {key: label.text() for key, label in page.stats.items()},
            {"total": "5", "confirmed": "1", "pending": "1", "rejected": "1", "on_hold": "1", "overdue": "1"},
        )
        self.assertTrue(page.status.isHidden())
        cells = lambda table, row: [table.item(row, c).text() for c in range(table.columnCount())]  # noqa: E731
        self.assertEqual(cells(page.assignees, 0), ["u1", "3", "0", "2", "1", "0", "0  0  1  0", "0.25"])
        self.assertEqual(cells(page.assignees, 1), ["u2", "2", "0", "1", "0", "1", "0  0  0  0", "0.0"])
        self.assertEqual(cells(page.projects, 0), ["P1", "5", "1", "4", "0  0  1  0", "0.25", "약 16주"])
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.assertEqual(page.assignees.item(0, 1).textAlignment(), right)
        self.assertNotEqual(page.assignees.item(0, 0).textAlignment(), right)
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
