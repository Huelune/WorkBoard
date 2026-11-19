from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import is_web_link
from workboard.ui.dialogs import message_label, show_message
from workboard.ui.text import RESULT, detail_html, local_time

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ROLE = Qt.ItemDataRole.UserRole
EVIDENCE_COLUMN = 3


class ReviewPage(QWidget):
    """Completion reports waiting for an editor, with the evidence one click away.

    The actor's own work never appears here.
    """

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.win = window
        self.rows: dict[str, dict] = {}
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["할 일", "담당자", "신고 시각", "근거", "결과"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(EVIDENCE_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(EVIDENCE_COLUMN, 180)  # long links are cut short here; the side shows them whole
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.open_button = QPushButton("근거 열기")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("반려 사유 (반려할 때 필수)")
        self.message = message_label()
        self.confirm_button = QPushButton("확인")
        self.reject_button = QPushButton("반려")

        self.side = QWidget()
        side_layout = QVBoxLayout(self.side)
        side_layout.setContentsMargins(8, 0, 0, 0)
        side_layout.addWidget(self.open_button)
        side_layout.addWidget(self.detail, 1)
        side_layout.addWidget(self.reason)
        side_layout.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.confirm_button)
        side_layout.addLayout(actions)
        self.side.setMinimumWidth(320)
        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([640, 400])
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        layout.addWidget(splitter, 1)

        self.tree.currentItemChanged.connect(self._show_detail)
        self.tree.itemClicked.connect(self._clicked)
        self.open_button.clicked.connect(self.open_evidence)
        self.confirm_button.clicked.connect(lambda: self.decide("confirmed"))
        self.reject_button.clicked.connect(lambda: self.decide("rejected"))
        self.reason.returnPressed.connect(lambda: self.decide("rejected"))

    def count(self) -> int:
        return self.tree.topLevelItemCount()

    def current_row(self) -> dict | None:
        item = self.tree.currentItem()
        return self.rows.get(item.data(0, ROLE)) if item else None

    def open_evidence(self) -> bool:
        row = self.current_row()
        return bool(row) and self.win.open_link(row["evidence_ref"])

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == EVIDENCE_COLUMN:
            self.open_evidence()

    def _show_detail(self, item: QTreeWidgetItem | None, _previous: object = None) -> None:
        row = self.rows.get(item.data(0, ROLE)) if item else None
        self.side.setVisible(bool(row))
        show_message(self.message, None)
        if not row:
            return
        # A reference that is not a web link (a document or item ID) has nothing to open.
        self.open_button.setEnabled(is_web_link(row["evidence_ref"]))
        self.open_button.setToolTip("" if is_web_link(row["evidence_ref"]) else "근거가 링크가 아니라 열 수 없습니다")
        detail = self.win.service.assignment_detail(row["assignment_id"])
        self.detail.setHtml(detail_html(detail, self.win.names, self.win.person, self.win.labels))

    def reload(self) -> None:
        window = self.win
        selected = self.tree.currentItem().data(0, ROLE) if self.tree.currentItem() else None
        self.tree.clear()
        self.rows = {}
        project = window.project_id
        editor = bool(project) and window.service.is_editor(window.actor, project)
        for widget in (self.reason, self.confirm_button, self.reject_button):
            widget.setEnabled(editor)
        rows = window.service.pending_reviews(window.actor, project) if editor else []
        if not editor:
            self.note.setText("완료 확인은 프로젝트 편집자만 할 수 있습니다.")
        elif not rows:
            self.note.setText("확인을 기다리는 완료 신고가 없습니다. 내가 담당한 업무는 여기에 나오지 않습니다.")
        else:
            self.note.setText("근거를 열어 본 뒤 확인하거나 반려하세요. 내가 담당한 업무는 여기에 나오지 않습니다.")
        self.rows = {row["assignment_id"]: row for row in rows}
        for row in rows:
            item = QTreeWidgetItem(
                [
                    row["title"],
                    window.person(row["assignee_id"]),
                    local_time(row["reported_at"]),
                    row["evidence_ref"] or "",
                    RESULT.get(row["result"], ""),
                ]
            )
            if is_web_link(row["evidence_ref"]):
                font = item.font(EVIDENCE_COLUMN)
                font.setUnderline(True)
                item.setFont(EVIDENCE_COLUMN, font)
                item.setForeground(EVIDENCE_COLUMN, self.palette().link())
            item.setData(0, ROLE, row["assignment_id"])
            self.tree.addTopLevelItem(item)
            if row["assignment_id"] == selected:
                self.tree.setCurrentItem(item)
        if not self.tree.currentItem() and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if not self.tree.currentItem():
            self.side.hide()

    def decide(self, decision: str) -> bool:
        row = self.current_row()
        if not row:
            return False
        error = self.win.attempt(
            lambda: self.win.service.review_completion(
                self.win.actor, row["assignment_id"], decision, self.reason.text()
            )
        )
        if error:
            show_message(self.message, error)
            self.reason.setFocus()
            return False
        self.reason.clear()
        return True
