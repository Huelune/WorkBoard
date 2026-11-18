from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from workboard.ui.text import RESULT, STATUS


class _FormDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.form = QFormLayout(self)

    def add_buttons(self, accept_label: str) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(accept_label)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)


class ProgressDialog(_FormDialog):
    """Assignee's progress entry. Choosing 완료 asks for the evidence."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__("진행 입력", parent)
        self.status = QComboBox()
        for value, label in STATUS.items():
            self.status.addItem(label, value)
        self.progress = QSpinBox()
        self.progress.setRange(0, 100)
        self.progress.setSuffix(" %")
        self.hold_reason = QLineEdit(row["hold_reason"] or "")
        self.evidence_ref = QLineEdit(row["evidence_ref"] or "")
        self.evidence_ref.setPlaceholderText("결과 문서 링크 또는 항목 ID")
        self.evidence_note = QLineEdit(row["evidence_note"] or "")
        self.evidence_note.setPlaceholderText("무엇을 확인했는지 한 줄")
        self.result = QComboBox()
        self.result.addItem("선택 안 함", None)
        for value, label in RESULT.items():
            self.result.addItem(label, value)
        self.form.addRow("상태", self.status)
        self.form.addRow("진행도", self.progress)
        self.form.addRow("보류 사유 (보류일 때 필수)", self.hold_reason)
        self.form.addRow("근거 참조 (완료일 때 필수)", self.evidence_ref)
        self.form.addRow("근거 설명", self.evidence_note)
        self.form.addRow("결과", self.result)
        self.add_buttons("저장")

        self.status.setCurrentIndex(self.status.findData(row["status"]))
        self.progress.setValue(row["progress"])
        self.result.setCurrentIndex(max(0, self.result.findData(row["result"])))
        self.status.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        status = self.status.currentData()
        fixed = {"not_started": 0, "done": 100}.get(status)
        if fixed is not None:
            self.progress.setValue(fixed)
        elif self.progress.value() == 100:
            self.progress.setValue(99)
        self.progress.setEnabled(fixed is None)
        self.hold_reason.setEnabled(status == "on_hold")
        for field in (self.evidence_ref, self.evidence_note, self.result):
            field.setEnabled(status == "done")

    def values(self) -> dict:
        return {
            "status": self.status.currentData(),
            "progress": self.progress.value(),
            "result": self.result.currentData(),
            "hold_reason": self.hold_reason.text(),
            "evidence_ref": self.evidence_ref.text(),
            "evidence_note": self.evidence_note.text(),
        }


class WorkItemDialog(_FormDialog):
    """A work item together with its first assignment; unassigned items are not listed anywhere."""

    def __init__(self, scopes: list[dict], categories: list[dict], parent: QWidget | None = None) -> None:
        super().__init__("업무 추가", parent)
        self.title = QLineEdit()
        self.scope = QComboBox()
        self.category = QComboBox()
        for combo, records in ((self.scope, scopes), (self.category, categories)):
            combo.addItem("없음", None)
            for record in records:
                combo.addItem(record["name"], record["id"])
        self.external_ref = QLineEdit()
        self.external_url = QLineEdit()
        self.assignee = QLineEdit()
        self.due_date = QLineEdit()
        self.due_date.setPlaceholderText("YYYY-MM-DD (비워 두면 기한 없음)")
        self.form.addRow("제목 (필수)", self.title)
        self.form.addRow("범위", self.scope)
        self.form.addRow("분류", self.category)
        self.form.addRow("외부 참조 ID", self.external_ref)
        self.form.addRow("외부 링크", self.external_url)
        self.form.addRow("담당자 ID (필수)", self.assignee)
        self.form.addRow("기한", self.due_date)
        self.add_buttons("추가")

    def values(self) -> dict:
        return {
            "title": self.title.text(),
            "scope_id": self.scope.currentData(),
            "category_id": self.category.currentData(),
            "external_ref": self.external_ref.text(),
            "external_url": self.external_url.text(),
            "assignee_id": self.assignee.text(),
            "due_date": self.due_date.text().strip() or None,
        }
