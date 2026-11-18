"""Input forms. Each one keeps what was typed until the save actually succeeds."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from workboard.ui.text import DEFAULT_LABELS, RESULT, STATUS

# A submit callback returns None when the change was saved, or the message to show.
Submit = Callable[[dict], "str | None"]


def message_label() -> QLabel:
    """Inline problem text. Plain text only: it can quote what a person typed."""
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet("color: #e5534b;")
    label.hide()
    return label


def show_message(label: QLabel, text: str | None) -> None:
    label.setText(text or "")
    label.setVisible(bool(text))


class DueDate(QWidget):
    """A date picker with an explicit 'no due date' choice, so nobody types a date by hand."""

    def __init__(self, current: str | None = None) -> None:
        super().__init__()
        self.enabled = QCheckBox("기한 있음")
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        parsed = QDate.fromString(current or "", "yyyy-MM-dd")
        self.date.setDate(parsed if parsed.isValid() else QDate.currentDate())
        self.enabled.setChecked(parsed.isValid())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.enabled)
        layout.addWidget(self.date, 1)
        self.enabled.toggled.connect(self.date.setEnabled)
        self.date.setEnabled(self.enabled.isChecked())

    def value(self) -> str | None:
        return self.date.date().toString("yyyy-MM-dd") if self.enabled.isChecked() else None


class ProgressForm(QWidget):
    """The assignee's progress entry, shown beside the list. Only the fields the status needs appear."""

    def __init__(self, on_save: Callable[[str, dict], "str | None"]) -> None:
        super().__init__()
        self.on_save = on_save
        self.assignment_id: str | None = None
        self.status = QComboBox()
        for value, label in STATUS.items():
            self.status.addItem(label, value)
        self.progress = QSpinBox()
        self.progress.setRange(0, 100)
        self.progress.setSuffix(" %")
        self.hold_reason = QLineEdit()
        self.hold_reason.setPlaceholderText("무엇을 기다리는지")
        self.evidence_ref = QLineEdit()
        self.evidence_ref.setPlaceholderText("결과 문서 링크 또는 항목 ID")
        self.evidence_note = QLineEdit()
        self.evidence_note.setPlaceholderText("무엇을 확인했는지 한 줄")
        self.result = QComboBox()
        self.result.addItem("선택 안 함", None)
        for value, label in RESULT.items():
            self.result.addItem(label, value)
        self.message = message_label()
        self.save_button = QPushButton("저장")
        self.fields = {
            "progress": self.progress,
            "hold_reason": self.hold_reason,
            "evidence_ref": self.evidence_ref,
        }
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.addRow("상태", self.status)
        self.form.addRow("진행도", self.progress)
        self.form.addRow("보류 사유", self.hold_reason)
        self.form.addRow("완료 근거", self.evidence_ref)
        self.form.addRow("근거 설명", self.evidence_note)
        self.form.addRow("결과", self.result)
        self.form.addRow(self.message)
        self.form.addRow(self.save_button)
        self.status.currentIndexChanged.connect(self._sync)
        self.save_button.clicked.connect(self.save)
        for field in (self.hold_reason, self.evidence_ref, self.evidence_note):
            field.returnPressed.connect(self.save)

    def load(self, row: dict) -> None:
        """Show the saved values of one assignment, discarding anything typed for another."""
        self.assignment_id = row["assignment_id"]
        self.status.setCurrentIndex(self.status.findData(row["status"]))
        self.progress.setValue(row["progress"])
        self.hold_reason.setText(row["hold_reason"] or "")
        self.evidence_ref.setText(row["evidence_ref"] or "")
        self.evidence_note.setText(row["evidence_note"] or "")
        self.result.setCurrentIndex(max(0, self.result.findData(row["result"])))
        show_message(self.message, None)
        self._sync()

    def _sync(self) -> None:
        status = self.status.currentData()
        fixed = {"not_started": 0, "done": 100}.get(status)
        if fixed is not None:
            self.progress.setValue(fixed)
        elif self.progress.value() == 100:
            self.progress.setValue(99)
        self.progress.setEnabled(fixed is None)
        self.form.setRowVisible(self.hold_reason, status == "on_hold")
        for field in (self.evidence_ref, self.evidence_note, self.result):
            self.form.setRowVisible(field, status == "done")

    def values(self) -> dict:
        return {
            "status": self.status.currentData(),
            "progress": self.progress.value(),
            "result": self.result.currentData(),
            "hold_reason": self.hold_reason.text(),
            "evidence_ref": self.evidence_ref.text(),
            "evidence_note": self.evidence_note.text(),
        }

    def save(self) -> bool:
        if not self.assignment_id:
            return False
        error = self.on_save(self.assignment_id, self.values())
        show_message(self.message, error)
        return error is None

    def point_at(self, field: str | None) -> None:
        """Put the cursor in the field a refusal was about."""
        widget = self.fields.get(field or "")
        if widget and not widget.isHidden():
            widget.setFocus()


class _FormDialog(QDialog):
    """A dialog that closes only after its submit callback reports success."""

    def __init__(self, title: str, on_submit: Submit | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_submit = on_submit
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.form = QFormLayout(self)
        self.message = message_label()

    def add_buttons(self, accept_label: str) -> None:
        self.form.addRow(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(accept_label)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)

    def values(self) -> dict:
        raise NotImplementedError

    def accept(self) -> None:
        error = self.on_submit(self.values()) if self.on_submit else None
        show_message(self.message, error)
        if error is None:
            super().accept()


class DueDateDialog(_FormDialog):
    def __init__(self, current: str | None, on_submit: Submit | None = None, parent: QWidget | None = None) -> None:
        super().__init__("기한 변경", on_submit, parent)
        self.due_date = DueDate(current)
        self.form.addRow("기한", self.due_date)
        self.add_buttons("저장")

    def values(self) -> dict:
        return {"due_date": self.due_date.value()}


def parse_work_lines(text: str) -> list[dict]:
    """One work item per line. Tab-separated extras (pasted from a spreadsheet): reference ID, link."""
    items = []
    for line in text.splitlines():
        if line.strip():
            parts = [part.strip() for part in line.split("\t")] + ["", ""]
            items.append({"title": parts[0], "external_ref": parts[1], "external_url": parts[2]})
    return items


class WorkItemDialog(_FormDialog):
    """Adds one or many work items, each with its first assignment.

    Two ways to enter them: one row per item with a + button, or a box to paste many lines.
    An editor picks any member (or types a new ID); a member who is not an editor
    can only add work for themselves, so the assignee is fixed.
    """

    def __init__(
        self,
        scopes: list[dict],
        categories: list[dict],
        members: list[dict],
        actor: str,
        editor: bool,
        labels: tuple[str, str] = DEFAULT_LABELS,
        parent: QWidget | None = None,
        on_submit: Submit | None = None,
    ) -> None:
        super().__init__("업무 추가", on_submit, parent)
        self.setMinimumWidth(600)
        self.rows: list[tuple[QWidget, QLineEdit, QLineEdit, QLineEdit]] = []
        self.counter = QLabel()

        self.rows_box = QVBoxLayout()
        self.rows_box.setContentsMargins(0, 0, 0, 0)
        add = QPushButton("+ 줄 추가")
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.addLayout(self.rows_box)
        holder_layout.addWidget(add)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(190)

        self.titles = QPlainTextEdit()
        self.titles.setPlaceholderText(
            "한 줄에 업무 하나. 여러 줄을 붙여 넣으면 한꺼번에 등록됩니다.\n"
            "엑셀에서 두세 칸을 복사해 붙이면 둘째 칸은 외부 참조 ID, 셋째 칸은 링크로 들어갑니다."
        )
        self.modes = QTabWidget()
        self.modes.addTab(scroll, "하나씩 입력")
        self.modes.addTab(self.titles, "여러 줄 붙여 넣기")

        self.scope = QComboBox()
        self.category = QComboBox()
        for combo, records in ((self.scope, scopes), (self.category, categories)):
            for record in records:  # both are required, so there is no empty choice
                combo.addItem(record["name"], record["id"])
        self.assignee = QComboBox()
        for member in members:
            if editor or member["user_id"] == actor:
                name = member["display_name"]
                label = f"{name} ({member['user_id']})" if name else member["user_id"]
                self.assignee.addItem(label, member["user_id"])
        self.assignee.setCurrentIndex(max(0, self.assignee.findData(actor)))
        self.assignee.setEditable(editor)
        self.assignee.setEnabled(editor)
        self.due_date = DueDate()
        self.form.addRow(self.modes)
        self.form.addRow("", self.counter)
        for label, field in ((labels[0], self.scope), (labels[1], self.category)):
            caption = QLabel(label)
            caption.setTextFormat(Qt.TextFormat.PlainText)  # typed by an editor; never markup
            self.form.addRow(caption, field)
        self.form.addRow("담당자" if editor else "담당자 (본인)", self.assignee)
        self.form.addRow("기한", self.due_date)
        self.add_buttons("추가")
        # No default button: Enter in a row moves to a new row instead of submitting half a list.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

        add.clicked.connect(lambda: self.add_row())
        self.titles.textChanged.connect(self._count)
        self.modes.currentChanged.connect(self._count)
        self.add_row()

    def add_row(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        title, reference, link = QLineEdit(), QLineEdit(), QLineEdit()
        title.setPlaceholderText("업무 제목")
        reference.setPlaceholderText("외부 참조 ID")
        link.setPlaceholderText("링크 (https://…)")
        remove = QPushButton("−")
        remove.setFixedWidth(30)
        remove.setAutoDefault(False)
        remove.setToolTip("이 줄 지우기")
        remove.setAccessibleName("이 줄 지우기")
        layout.addWidget(title, 3)
        layout.addWidget(reference, 1)
        layout.addWidget(link, 2)
        layout.addWidget(remove)
        entry = (row, title, reference, link)
        self.rows.append(entry)
        self.rows_box.addWidget(row)
        for field in (title, reference, link):
            field.textChanged.connect(self._count)
            field.returnPressed.connect(lambda: self.add_row())
        remove.clicked.connect(lambda: self.remove_row(entry))
        title.setFocus()
        self._count()

    def remove_row(self, entry: tuple[QWidget, QLineEdit, QLineEdit, QLineEdit]) -> None:
        if len(self.rows) == 1:  # keep one row to type into
            for field in entry[1:]:
                field.clear()
            return
        self.rows.remove(entry)
        self.rows_box.removeWidget(entry[0])
        entry[0].deleteLater()
        self._count()

    def items(self) -> list[dict]:
        """Items from whichever way of entering is showing. A row counts once anything is typed in it."""
        if self.modes.currentIndex() == 1:
            return parse_work_lines(self.titles.toPlainText())
        return [
            {"title": title.text().strip(), "external_ref": reference.text().strip(), "external_url": link.text().strip()}
            for _, title, reference, link in self.rows
            if title.text().strip() or reference.text().strip() or link.text().strip()
        ]

    def _count(self, *_: object) -> None:
        self.counter.setText(f"{len(self.items())}건 등록")

    def assignee_id(self) -> str:
        """The picked member, or the text an editor typed for someone not in the list yet."""
        combo = self.assignee
        if combo.currentIndex() >= 0 and combo.currentText() == combo.itemText(combo.currentIndex()):
            return combo.currentData()
        return combo.currentText()

    def values(self) -> dict:
        return {
            "items": self.items(),
            "scope_id": self.scope.currentData(),
            "category_id": self.category.currentData(),
            "assignee_id": self.assignee_id(),
            "due_date": self.due_date.value(),
        }
