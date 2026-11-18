"""Main window. It talks to the Service only; no SQL and no NAS access here."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import WorkBoardError, is_web_link
from workboard.services import WorkBoardService
from workboard.ui.dialogs import ProgressDialog, WorkItemDialog
from workboard.ui.manage_page import ManagePage
from workboard.ui.pace_page import PacePage
from workboard.ui.review_page import ReviewPage
from workboard.ui.text import DEFAULT_LABELS, error_text
from workboard.ui.work_page import WorkPage

PAGES = ["업무 목록", "내 업무", "확인 대기", "현황·속도", "관리"]
REVIEW_INDEX = 2
MANAGE_INDEX = 4


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
        new_project = QPushButton("새 프로젝트")
        self.warning = QLabel("기록이 변경됨: 앱 밖에서 고친 흔적이 있습니다")
        self.warning.setStyleSheet("color: #e5534b; font-weight: bold;")
        self.sync = QLabel()

        top = QHBoxLayout()
        for widget in (QLabel("프로젝트"), self.project, self.scope_caption, self.scope, self.category_caption, self.category):
            top.addWidget(widget)
        top.addWidget(new_project)
        top.addStretch(1)
        top.addWidget(self.warning)
        top.addWidget(self.sync)

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

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.project.currentIndexChanged.connect(self._project_changed)
        self.scope.currentIndexChanged.connect(self._reload_pages)
        self.category.currentIndexChanged.connect(self._reload_pages)
        new_project.clicked.connect(lambda: self._ask("새 프로젝트", "프로젝트 이름", self.create_project))
        QShortcut(QKeySequence.StandardKey.Refresh, self, self.refresh)

        self.nav.setCurrentRow(0)
        self.refresh()

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
        self._fill(self.project, projects, None)
        self._project_changed()

    def _project_changed(self) -> None:
        project = self.project_id
        scopes = self.service.list_scopes(project) if project else []
        categories = self.service.list_categories(project) if project else []
        # Names include retired items so old work still shows what it was filed under.
        named = (
            self.service.list_scopes(project, include_deleted=True)
            + self.service.list_categories(project, include_deleted=True)
            if project
            else []
        )
        self.names = {record["id"]: record["name"] for record in named}
        record = self.records.get(project)
        self.labels = (record["scope_label"], record["category_label"]) if record else DEFAULT_LABELS
        self.scope_caption.setText(self.labels[0])
        self.category_caption.setText(self.labels[1])
        members = self.service.list_members(project) if project else []
        self.people = {m["user_id"]: m["display_name"] or m["user_id"] for m in members}
        self._fill(self.scope, scopes, "전체")
        self._fill(self.category, categories, "전체")
        self._reload_pages()

    def _reload_pages(self) -> None:
        for page in self.pages:
            page.reload()
        waiting = self.review_page.count()
        self.nav.item(REVIEW_INDEX).setText(f"확인 대기 ({waiting})" if waiting else "확인 대기")
        pending = self.service.pending_sync_count()
        self.sync.setText(f"공유 대기 {pending}" if pending else "")
        self.warning.setVisible(not self.service.check_integrity()["ok"])

    # ----- actions (each takes plain values so tests can call them without a dialog)

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "WorkBoard", message)

    def act(self, call: Callable[[], object]) -> bool:
        """Run one Service change, show a refusal as kind plus next step, then redraw."""
        try:
            call()
        except (WorkBoardError, sqlite3.Error) as error:
            self.show_error(error_text(error))
            return False
        finally:
            self.refresh()
        return True

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

    def add_work_items(self, values: dict) -> bool:
        return self.act(
            lambda: self.service.add_work_items(
                self.actor,
                self.project_id,
                values["items"],
                values["assignee_id"],
                due_date=values["due_date"],
                scope_id=values["scope_id"],
                category_id=values["category_id"],
            )
        )

    def save_progress(self, assignment_id: str, values: dict) -> bool:
        return self.act(lambda: self.service.update_progress(self.actor, assignment_id, **values))

    def set_due_date(self, assignment_id: str, due_date: str | None) -> bool:
        return self.act(lambda: self.service.set_due_date(self.actor, assignment_id, due_date))

    def cancel_assignment(self, assignment_id: str) -> bool:
        return self.act(lambda: self.service.cancel_assignment(self.actor, assignment_id))

    def delete_work_item(self, work_item_id: str) -> bool:
        return self.act(lambda: self.service.delete_work_item(self.actor, work_item_id))

    def open_link(self, url: str | None) -> bool:
        """Open a link in the browser. Only http(s): a link can come from someone else's data."""
        return is_web_link(url) and self._open_url(url)

    def _open_url(self, url: str) -> bool:
        return QDesktopServices.openUrl(QUrl(url))

    def review(self, assignment_id: str, decision: str, reason: str | None = None) -> bool:
        return self.act(lambda: self.service.review_completion(self.actor, assignment_id, decision, reason))

    # ----- dialogs (thin: collect values, then call an action above)

    def _ask(self, title: str, label: str, action: Callable[[str], bool]) -> None:
        text, accepted = QInputDialog.getText(self, title, label)
        if accepted:
            action(text)

    def ask_work_item(self) -> None:
        if not self.project_id:
            return
        project = self.project_id
        scopes, categories = self.service.list_scopes(project), self.service.list_categories(project)
        if not scopes or not categories:
            self.nav.setCurrentRow(MANAGE_INDEX)
            self.show_error("업무를 추가하려면 먼저 관리 화면에서 두 칸의 항목을 각각 하나 이상 만드세요.")
            return
        dialog = WorkItemDialog(
            scopes,
            categories,
            self.service.list_members(project),
            self.actor,
            self.service.is_editor(self.actor, project),
            self.labels,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_work_items(dialog.values())

    def ask_make_editor(self, user_id: str | None) -> None:
        if not user_id:
            return
        answer = QMessageBox.question(
            self, "편집자 지정", "이 구성원을 편집자로 지정할까요? 편집자는 업무 정의와 완료 확인을 할 수 있습니다."
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.add_editor(user_id)

    def _confirm(self, title: str, question: str) -> bool:
        return QMessageBox.question(self, title, question) == QMessageBox.StandardButton.Yes

    def ask_rename_item(self, kind: str, item_id: str | None) -> None:
        if not item_id:
            return
        name, accepted = QInputDialog.getText(self, "이름 바꾸기", "새 이름", text=self.names.get(item_id, ""))
        if accepted:
            self.rename_item(kind, item_id, name)

    def ask_delete_item(self, kind: str, item_id: str | None) -> None:
        if item_id and self._confirm(
            "항목 삭제", "이 항목을 삭제할까요? 새 업무에서는 고를 수 없게 되고, 이미 등록된 업무에는 이름이 그대로 보입니다."
        ):
            self.delete_item(kind, item_id)

    def ask_remove_member(self, user_id: str | None) -> None:
        if user_id and self._confirm(
            "구성원 삭제", "이 구성원을 목록에서 뺄까요? 맡은 업무와 진행 기록은 그대로 남습니다."
        ):
            self.remove_member(user_id)

    def ask_remove_editor(self, user_id: str | None) -> None:
        if user_id and self._confirm("편집자 해제", "이 구성원의 편집자 역할을 해제할까요? 구성원으로는 남습니다."):
            self.remove_editor(user_id)

    def ask_delete(self, row: dict | None) -> None:
        if not row:
            return
        answer = QMessageBox.question(
            self, "업무 삭제", "이 업무를 삭제할까요? 목록과 집계에서 빠지고 화면에서 되돌릴 수 없습니다. 기록은 남습니다."
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_work_item(row["work_item_id"])

    def edit_progress(self, row: dict | None) -> None:
        if not row or row["assignee_id"] != self.actor:
            return
        dialog = ProgressDialog(row, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_progress(row["assignment_id"], dialog.values())

    def ask_due_date(self, row: dict | None) -> None:
        if not row:
            return
        text, accepted = QInputDialog.getText(
            self, "기한 변경", "기한 (YYYY-MM-DD, 비우면 기한 없음)", text=row["due_date"] or ""
        )
        if accepted:
            self.set_due_date(row["assignment_id"], text.strip() or None)

    def ask_cancel(self, row: dict | None) -> None:
        if not row:
            return
        answer = QMessageBox.question(
            self, "배정 취소", "이 배정을 취소할까요? 진행 기록은 남지만 목록과 집계에서 빠집니다."
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.cancel_assignment(row["assignment_id"])
