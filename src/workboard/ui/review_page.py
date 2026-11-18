from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workboard.ui.text import RESULT, local_time

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ROLE = Qt.ItemDataRole.UserRole


class ReviewPage(QWidget):
    """Completion reports waiting for an editor. The actor's own work never appears here."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.win = window
        self.note = QLabel()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["할 일", "담당자", "신고 시각", "근거 참조", "근거 설명", "결과"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(3, 220)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("반려 사유 (반려할 때 필수)")
        self.confirm_button = QPushButton("확인")
        self.reject_button = QPushButton("반려")

        actions = QHBoxLayout()
        actions.addWidget(self.reason, 1)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.confirm_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        layout.addWidget(self.tree, 1)
        layout.addLayout(actions)

        self.confirm_button.clicked.connect(lambda: self.decide("confirmed"))
        self.reject_button.clicked.connect(lambda: self.decide("rejected"))

    def count(self) -> int:
        return self.tree.topLevelItemCount()

    def reload(self) -> None:
        window = self.win
        self.tree.clear()
        project = window.project_id
        editor = bool(project) and window.service.is_editor(window.actor, project)
        for widget in (self.reason, self.confirm_button, self.reject_button):
            widget.setEnabled(editor)
        self.note.setText(
            "근거를 열어 본 뒤 확인하거나 반려하세요. 내가 담당한 업무는 여기에 나오지 않습니다."
            if editor
            else "완료 확인은 프로젝트 편집자만 할 수 있습니다."
        )
        if not editor:
            return
        for row in window.service.pending_reviews(window.actor, project):
            item = QTreeWidgetItem(
                [
                    row["title"],
                    row["assignee_id"],
                    local_time(row["reported_at"]),
                    row["evidence_ref"] or "",
                    row["evidence_note"] or "",
                    RESULT.get(row["result"], ""),
                ]
            )
            item.setData(0, ROLE, row["assignment_id"])
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def decide(self, decision: str) -> None:
        item = self.tree.currentItem()
        if item and self.win.review(item.data(0, ROLE), decision, self.reason.text()):
            self.reason.clear()
