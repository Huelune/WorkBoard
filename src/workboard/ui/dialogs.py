from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("업무 추가", parent)
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
            combo.addItem("없음", None)
            for record in records:
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
        self.due_date = QLineEdit()
        self.due_date.setPlaceholderText("YYYY-MM-DD (비워 두면 기한 없음)")
        self.form.addRow(self.modes)
        self.form.addRow("", self.counter)
        self.form.addRow("범위", self.scope)
        self.form.addRow("분류", self.category)
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
        link.setPlaceholderText("링크")
        remove = QPushButton("−")
        remove.setFixedWidth(30)
        remove.setAutoDefault(False)
        remove.setToolTip("이 줄 지우기")
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
            "due_date": self.due_date.text().strip() or None,
        }
