"""Main window. It talks to the Service only; no SQL and no NAS access here."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import WorkBoardError
from workboard.services import WorkBoardService
from workboard.ui.dialogs import ProgressDialog, WorkItemDialog
from workboard.ui.pace_page import PacePage
from workboard.ui.review_page import ReviewPage
from workboard.ui.text import error_text
from workboard.ui.work_page import WorkPage

PAGES = ["업무 목록", "내 업무", "확인 대기", "현황·속도"]
REVIEW_INDEX = 2


class MainWindow(QMainWindow):
    def __init__(self, service: WorkBoardService, actor: str) -> None:
        super().__init__()
        self.service, self.actor = service, actor
        self.names: dict[str | None, str] = {}
        self.people: dict[str, str] = {}
        self.setWindowTitle(f"WorkBoard — {actor}")
        self.resize(1180, 720)

        self.project = QComboBox()
        self.project.setMinimumWidth(180)
        self.scope = QComboBox()
        self.category = QComboBox()
        new_project = QPushButton("새 프로젝트")
        self.manage = QPushButton("관리")
        menu = QMenu(self.manage)
        menu.addAction("구성원 추가", self.ask_member)
        menu.addAction("편집자 추가", lambda: self._ask("편집자 추가", "사용자 ID", self.add_editor))
        menu.addAction("범위 추가", lambda: self._ask("범위 추가", "범위 이름 (예: 코드 버전)", self.add_scope))
        menu.addAction("분류 추가", lambda: self._ask("분류 추가", "분류 이름", self.add_category))
        self.manage.setMenu(menu)
        self.warning = QLabel("기록이 변경됨: 앱 밖에서 고친 흔적이 있습니다")
        self.warning.setStyleSheet("color: #e5534b; font-weight: bold;")
        self.sync = QLabel()

        top = QHBoxLayout()
        for widget in (QLabel("프로젝트"), self.project, QLabel("범위"), self.scope, QLabel("분류"), self.category):
            top.addWidget(widget)
        top.addWidget(new_project)
        top.addWidget(self.manage)
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
        self.stack = QStackedWidget()
        for page in (self.work_page, self.mine_page, self.review_page, self.pace_page):
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
        self._fill(self.project, self.service.list_projects(), None)
        self._project_changed()

    def _project_changed(self) -> None:
        project = self.project_id
        scopes = self.service.list_scopes(project) if project else []
        categories = self.service.list_categories(project) if project else []
        self.names = {record["id"]: record["name"] for record in scopes + categories}
        members = self.service.list_members(project) if project else []
        self.people = {m["user_id"]: m["display_name"] or m["user_id"] for m in members}
        self._fill(self.scope, scopes, "전체")
        self._fill(self.category, categories, "전체")
        self.manage.setEnabled(bool(project) and self.service.is_editor(self.actor, project))
        self._reload_pages()

    def _reload_pages(self) -> None:
        for page in (self.work_page, self.mine_page, self.review_page, self.pace_page):
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
        dialog = WorkItemDialog(
            self.service.list_scopes(project),
            self.service.list_categories(project),
            self.service.list_members(project),
            self.actor,
            self.service.is_editor(self.actor, project),
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_work_items(dialog.values())

    def ask_member(self) -> None:
        user_id, accepted = QInputDialog.getText(self, "구성원 추가", "사용자 ID")
        if not accepted:
            return
        name, accepted = QInputDialog.getText(self, "구성원 추가", "표시 이름 (비워도 됩니다)")
        if accepted:
            self.add_member(user_id, name)

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
