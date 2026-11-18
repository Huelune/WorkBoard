"""Main window. It talks to the Service only; no SQL and no NAS access here."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import WorkBoardError, is_web_link
from workboard.services import WorkBoardService
from workboard.ui.dialogs import DueDateDialog, WorkItemDialog
from workboard.ui.login import ChangePasswordDialog
from workboard.ui.manage_page import ManagePage
from workboard.ui.pace_page import PacePage
from workboard.ui.review_page import ReviewPage
from workboard.ui.text import DEFAULT_LABELS, error_field, error_text
from workboard.ui.theme import tone
from workboard.ui.work_page import WorkPage

PAGES = ["업무 목록", "내 업무", "확인 대기", "현황·속도", "관리"]
WORK_INDEX, MINE_INDEX, REVIEW_INDEX, MANAGE_INDEX = 0, 1, 2, 4
SAVED = "저장됨"


class MainWindow(QMainWindow):
    def __init__(self, service: WorkBoardService, actor: str) -> None:
        super().__init__()
        self.service, self.actor = service, actor
        self.names: dict[str | None, str] = {}
        self.people: dict[str, str] = {}
        self.records: dict[str, dict] = {}
        self.labels: tuple[str, str] = DEFAULT_LABELS
        self.scope_caption, self.category_caption = QLabel(), QLabel()
        for caption in (self.scope_caption, self.category_caption):
            caption.setTextFormat(Qt.TextFormat.PlainText)  # typed by an editor; never markup
        self.setWindowTitle(f"WorkBoard — {actor}")
        self.resize(1180, 720)

        self.project = QComboBox()
        self.project.setMinimumWidth(180)
        self.scope = QComboBox()
        self.category = QComboBox()
        # The two filters only narrow the shared list, so they are only shown there.
        self.filters = QWidget()
        filters = QHBoxLayout(self.filters)
        filters.setContentsMargins(0, 0, 0, 0)
        for widget in (self.scope_caption, self.scope, self.category_caption, self.category):
            filters.addWidget(widget)
        self.warning = QLabel("기록이 변경됨: 앱 밖에서 고친 흔적이 있습니다")
        self.warning.setStyleSheet(f"color: {tone('red', self.palette()).name()}; font-weight: bold;")
        self.project_caption = QLabel("프로젝트")
        self.account = QToolButton()
        self.account.setText(actor)
        self.account.setToolTip("로그인한 사용자")
        self.account.setStyleSheet(  # room for the menu arrow, and the arrow level with the name
            "QToolButton { padding: 3px 22px 3px 10px; }"
            "QToolButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: right center; right: 6px; }"
        )
        self.account.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        account_menu = QMenu(self.account)
        account_menu.addAction("비밀번호 변경", lambda: ChangePasswordDialog(self.service, self.actor, self).exec())
        self.account.setMenu(account_menu)

        top = QHBoxLayout()
        top.addWidget(self.project_caption)
        top.addWidget(self.project)
        top.addWidget(self.filters)
        top.addStretch(1)
        top.addWidget(self.warning)
        top.addWidget(self.account)

        self.nav = QListWidget()
        self.nav.addItems(PAGES)
        self.nav.setFixedWidth(130)
        self.work_page = WorkPage(self)
        self.mine_page = WorkPage(self, mine=True)
        self.review_page = ReviewPage(self)
        self.pace_page = PacePage(self)
        self.manage_page = ManagePage(self)
        self.pages = (self.work_page, self.mine_page, self.review_page, self.pace_page, self.manage_page)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)

        body = QHBoxLayout()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addLayout(body, 1)
        self.setCentralWidget(central)
        self.statusBar()  # where "저장됨" appears for a moment after each change

        self.nav.currentRowChanged.connect(self._page_changed)
        self.project.currentIndexChanged.connect(self._project_changed)
        self.scope.currentIndexChanged.connect(self._reload_pages)
        self.category.currentIndexChanged.connect(self._reload_pages)
        QShortcut(QKeySequence.StandardKey.Refresh, self, self.refresh)

        self.refresh()
        # Most visits are an assignee updating their own work, so start there when there is any.
        self.nav.setCurrentRow(MINE_INDEX if self.mine_page.rows else WORK_INDEX)

    # ----- state the pages read

    @property
    def project_id(self) -> str | None:
        return self.project.currentData()

    @property
    def scope_id(self) -> str | None:
        return self.scope.currentData()

    @property
    def category_id(self) -> str | None:
        return self.category.currentData()

    def person(self, user_id: str) -> str:
        """Display name in the current project, falling back to the ID."""
        return self.people.get(user_id, user_id)

    def today(self) -> str:
        return date.today().isoformat()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ----- navigation

    def _page_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._show_filters()

    def _show_filters(self) -> None:
        """Show each top control only where it does something: "my work" spans every project."""
        page = self.nav.currentRow()
        self.filters.setVisible(page == WORK_INDEX and bool(self.project_id))
        self.project.setVisible(page != MINE_INDEX)
        self.project_caption.setVisible(page != MINE_INDEX)

    def show_manage(self) -> None:
        self.nav.setCurrentRow(MANAGE_INDEX)

    def clear_filters(self) -> None:
        self.scope.setCurrentIndex(0)
        self.category.setCurrentIndex(0)

    # ----- refresh

    @staticmethod
    def _fill(combo: QComboBox, records: list[dict], all_label: str | None) -> None:
        """Refill a combo, keeping the current choice when it still exists."""
        keep = combo.currentData()
        with QSignalBlocker(combo):
            combo.clear()
            if all_label:
                combo.addItem(all_label, None)
            for record in records:
                combo.addItem(record["name"], record["id"])
            combo.setCurrentIndex(max(0, combo.findData(keep)))

    def refresh(self) -> None:
        projects = self.service.list_projects()
        self.records = {record["id"]: record for record in projects}
        # Item names for every project (retired ones too): "my work" lists work from all of them,
        # and old work still shows what it was filed under.
        self.names = {
            item["id"]: item["name"]
            for record in projects
            for item in (
                self.service.list_scopes(record["id"], include_deleted=True)
                + self.service.list_categories(record["id"], include_deleted=True)
            )
        }
        self._fill(self.project, projects, None)
        self._project_changed()

    def _project_changed(self) -> None:
        project = self.project_id
        scopes = self.service.list_scopes(project) if project else []
        categories = self.service.list_categories(project) if project else []
        record = self.records.get(project)
        self.labels = (record["scope_label"], record["category_label"]) if record else DEFAULT_LABELS
        self.scope_caption.setText(self.labels[0])
        self.category_caption.setText(self.labels[1])
        members = self.service.list_members(project) if project else []
        self.people = {m["user_id"]: m["display_name"] or m["user_id"] for m in members}
        self._fill(self.scope, scopes, "전체")
        self._fill(self.category, categories, "전체")
        self._show_filters()
        self._reload_pages()

    def _reload_pages(self) -> None:
        for page in self.pages:
            page.reload()
        waiting = self.review_page.count()
        self.nav.item(REVIEW_INDEX).setText(f"확인 대기 ({waiting})" if waiting else "확인 대기")
        self.warning.setVisible(not self.service.check_integrity()["ok"])

    # ----- running a change

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "WorkBoard", message)

    def _run(self, call: Callable[[], object]) -> Exception | None:
        """Run one Service change. A refusal changes nothing, so the screen is left exactly
        as it was (whatever the person typed stays); a success redraws and says so."""
        try:
            call()
        except (WorkBoardError, sqlite3.Error) as error:
            return error
        self.refresh()
        self.statusBar().showMessage(SAVED, 3000)
        return None

    def attempt(self, call: Callable[[], object]) -> str | None:
        """For forms that show the problem next to the input: the message, or None when saved."""
        error = self._run(call)
        return error_text(error) if error else None

    def act(self, call: Callable[[], object]) -> bool:
        """For one-step actions with nothing typed to lose: a refusal is shown in a message box."""
        error = self.attempt(call)
        if error:
            self.show_error(error)
        return error is None

    def attempt_progress(self, assignment_id: str, values: dict) -> tuple[str | None, str | None]:
        """Save progress from the side form: (message, field to point at), both None when saved."""
        error = self._run(lambda: self.service.update_progress(self.actor, assignment_id, **values))
        return (error_text(error), error_field(error)) if error else (None, None)

    # ----- actions (each takes plain values so tests can call them without a dialog)

    def create_project(self, name: str) -> bool:
        created: list[str] = []
        ok = self.act(lambda: created.append(self.service.create_project(self.actor, name)))
        if ok:
            self.project.setCurrentIndex(self.project.findData(created[0]))
        return ok

    def add_editor(self, user_id: str) -> bool:
        return self.act(lambda: self.service.add_editor(self.actor, self.project_id, user_id))

    def add_scope(self, name: str) -> bool:
        return self.act(lambda: self.service.create_scope(self.actor, self.project_id, name))

    def add_category(self, name: str) -> bool:
        position = self.category.count()
        return self.act(lambda: self.service.create_category(self.actor, self.project_id, name, position))

    def add_member(self, user_id: str, display_name: str = "") -> bool:
        return self.act(lambda: self.service.add_member(self.actor, self.project_id, user_id, display_name))

    def rename_item(self, kind: str, item_id: str, name: str) -> bool:
        return self.act(lambda: self.service.rename_item(self.actor, kind, item_id, name))

    def delete_item(self, kind: str, item_id: str) -> bool:
        return self.act(lambda: self.service.delete_item(self.actor, kind, item_id))

    def remove_member(self, user_id: str) -> bool:
        return self.act(lambda: self.service.remove_member(self.actor, self.project_id, user_id))

    def remove_editor(self, user_id: str) -> bool:
        return self.act(lambda: self.service.remove_editor(self.actor, self.project_id, user_id))

    def set_labels(self, scope_label: str, category_label: str) -> bool:
        return self.act(lambda: self.service.set_labels(self.actor, self.project_id, scope_label, category_label))

    def _add_work_items(self, values: dict) -> Callable[[], object]:
        return lambda: self.service.add_work_items(
            self.actor,
            self.project_id,
            values["items"],
            values["assignee_id"],
            due_date=values["due_date"],
            scope_id=values["scope_id"],
            category_id=values["category_id"],
        )

    def add_work_items(self, values: dict) -> bool:
        return self.act(self._add_work_items(values))

    def save_progress(self, assignment_id: str, values: dict) -> bool:
        return self.act(lambda: self.service.update_progress(self.actor, assignment_id, **values))

    def set_due_date(self, assignment_id: str, due_date: str | None) -> bool:
        return self.act(lambda: self.service.set_due_date(self.actor, assignment_id, due_date))

    def cancel_assignment(self, assignment_id: str) -> bool:
        return self.act(lambda: self.service.cancel_assignment(self.actor, assignment_id))

    def delete_work_item(self, work_item_id: str) -> bool:
        return self.act(lambda: self.service.delete_work_item(self.actor, work_item_id))

    def review(self, assignment_id: str, decision: str, reason: str | None = None) -> bool:
        return self.act(lambda: self.service.review_completion(self.actor, assignment_id, decision, reason))

    def open_link(self, url: str | None) -> bool:
        """Open a link in the browser. Only http(s): a link can come from someone else's data."""
        return is_web_link(url) and self._open_url(url)

    def _open_url(self, url: str) -> bool:
        return QDesktopServices.openUrl(QUrl(url))

    def edit_progress(self, row: dict | None) -> None:
        """Take the person to the progress entry of one of their own assignments."""
        if not row or row["assignee_id"] != self.actor:
            return
        page = self.stack.currentWidget()
        if not isinstance(page, WorkPage) or not page.select(row["assignment_id"]):
            self.nav.setCurrentRow(MINE_INDEX)
            page = self.mine_page
            page.select(row["assignment_id"])
        page.focus_form()

    # ----- dialogs (thin: collect values, then call an action above)

    def _confirm(self, title: str, question: str, action: str) -> bool:
        """Ask before something that is hard to undo. The buttons name the action, in Korean,
        and the safe choice is the default."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)  # the question can quote a title someone typed
        box.setText(question)
        go = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(box.addButton("그만두기", QMessageBox.ButtonRole.RejectRole))
        box.exec()
        return box.clickedButton() is go

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        pages = (self.work_page, self.mine_page)
        if any(page.has_unsaved() for page in pages) and not self._confirm(
            "닫기", "저장하지 않은 진행 입력이 있습니다. 저장하지 않고 닫을까요?", "저장하지 않고 닫기"
        ):
            event.ignore()
            return
        for page in pages:  # the person chose to drop them; a second close must not ask again
            page.drafts.clear()
            if page.form.assignment_id:
                page.form.revert()
        event.accept()

    def ask_new_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "새 프로젝트", "프로젝트 이름")
        if accepted:
            self.create_project(name)

    def ask_work_item(self) -> None:
        if not self.project_id:
            return
        project = self.project_id
        scopes, categories = self.service.list_scopes(project), self.service.list_categories(project)
        if not scopes or not categories:
            self.show_manage()
            self.show_error("업무를 추가하려면 먼저 관리 화면에서 두 칸의 항목을 각각 하나 이상 만드세요.")
            return
        # The dialog stays open, with everything typed, until the save goes through.
        WorkItemDialog(
            scopes,
            categories,
            self.service.list_members(project),
            self.actor,
            self.service.is_editor(self.actor, project),
            self.labels,
            self,
            on_submit=lambda values: self.attempt(self._add_work_items(values)),
        ).exec()

    def ask_import(self) -> None:
        if self.project_id and self.service.is_editor(self.actor, self.project_id):
            from workboard.ui.import_dialog import ImportDialog

            ImportDialog(self).exec()

    def ask_due_date(self, row: dict | None) -> None:
        if not row:
            return
        DueDateDialog(
            row["due_date"],
            lambda values: self.attempt(
                lambda: self.service.set_due_date(self.actor, row["assignment_id"], values["due_date"])
            ),
            self,
        ).exec()

    def ask_make_editor(self, user_id: str | None) -> None:
        if user_id and self._confirm(
            "편집자 지정",
            f"'{self.person(user_id)}' 구성원을 편집자로 지정할까요? 편집자는 업무 정의와 완료 확인을 할 수 있습니다.",
            "편집자로 지정",
        ):
            self.add_editor(user_id)

    def ask_rename_item(self, kind: str, item_id: str | None) -> None:
        if not item_id:
            return
        name, accepted = QInputDialog.getText(self, "이름 바꾸기", "새 이름", text=self.names.get(item_id, ""))
        if accepted:
            self.rename_item(kind, item_id, name)

    def ask_delete_item(self, kind: str, item_id: str | None) -> None:
        if item_id and self._confirm(
            "항목 삭제",
            f"'{self.names.get(item_id, '')}' 항목을 삭제할까요? 새 업무에서는 고를 수 없게 되고, 이미 등록된 업무에는 이름이 그대로 보입니다.",
            "삭제",
        ):
            self.delete_item(kind, item_id)

    def ask_remove_member(self, user_id: str | None) -> None:
        if user_id and self._confirm(
            "구성원 삭제",
            f"'{self.person(user_id)}' 구성원을 목록에서 뺄까요? 맡은 업무와 진행 기록은 그대로 남습니다.",
            "목록에서 빼기",
        ):
            self.remove_member(user_id)

    def ask_remove_editor(self, user_id: str | None) -> None:
        if user_id and self._confirm(
            "편집자 해제", f"'{self.person(user_id)}' 구성원의 편집자 역할을 해제할까요? 구성원으로는 남습니다.", "역할 해제"
        ):
            self.remove_editor(user_id)

    def ask_delete(self, row: dict | None) -> None:
        if row and self._confirm(
            "업무 삭제",
            f"'{row['title']}' 업무를 삭제할까요? 목록과 집계에서 빠지고 화면에서 되돌릴 수 없습니다. 기록은 남습니다.",
            "삭제",
        ):
            self.delete_work_item(row["work_item_id"])

    def ask_cancel(self, row: dict | None) -> None:
        if row and self._confirm(
            "배정 취소",
            f"'{row['title']}' 업무의 '{self.person(row['assignee_id'])}' 배정을 취소할까요? 진행 기록은 남지만 목록과 집계에서 빠집니다.",
            "배정 취소",
        ):
            self.cancel_assignment(row["assignment_id"])
