from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow


class ManagePage(QWidget):
    """Project settings in one place: the two field names, their items, and the members.

    Everyone can read it; only project editors can change anything.
    """

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.win = window
        self.note = QLabel()
        self.note.setWordWrap(True)

        self.scope_name, self.category_name = QLineEdit(), QLineEdit()
        self.rename_button = QPushButton("칸 이름 저장")
        names = QHBoxLayout()
        names.addWidget(QLabel("첫째 칸 이름"))
        names.addWidget(self.scope_name, 1)
        names.addWidget(QLabel("둘째 칸 이름"))
        names.addWidget(self.category_name, 1)
        names.addWidget(self.rename_button)
        names_box = QGroupBox("칸 이름 (예: 코드 버전 / 검증 분야)")
        names_box.setLayout(names)

        self.scope_box, self.scope_list, self.scope_new, self.scope_add = self._item_box()
        self.category_box, self.category_list, self.category_new, self.category_add = self._item_box()

        self.members = QTableWidget(0, 3)
        self.members.setHorizontalHeaderLabels(["사용자 ID", "표시 이름", "역할"])
        self.members.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.members.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.members.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.members.verticalHeader().setVisible(False)
        self.members.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.member_id, self.member_name = QLineEdit(), QLineEdit()
        self.member_id.setPlaceholderText("사용자 ID")
        self.member_name.setPlaceholderText("표시 이름 (비워도 됩니다)")
        self.member_add = QPushButton("추가 / 이름 저장")
        self.editor_button = QPushButton("선택한 구성원을 편집자로")
        member_inputs = QHBoxLayout()
        for widget in (self.member_id, self.member_name, self.member_add, self.editor_button):
            member_inputs.addWidget(widget)
        member_layout = QVBoxLayout()
        member_layout.addWidget(self.members, 1)
        member_layout.addLayout(member_inputs)
        self.member_box = QGroupBox("구성원")
        self.member_box.setLayout(member_layout)

        items = QHBoxLayout()
        items.addWidget(self.scope_box, 1)
        items.addWidget(self.category_box, 1)
        items.addWidget(self.member_box, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        layout.addWidget(names_box)
        layout.addLayout(items, 1)

        self.rename_button.clicked.connect(self._rename)
        for field, button, add in (
            (self.scope_new, self.scope_add, self.win.add_scope),
            (self.category_new, self.category_add, self.win.add_category),
        ):
            handler = self._adder(field, add)
            button.clicked.connect(handler)
            field.returnPressed.connect(handler)
        self.member_add.clicked.connect(self._add_member)
        self.member_name.returnPressed.connect(self._add_member)
        self.editor_button.clicked.connect(lambda: self.win.ask_make_editor(self.selected_member()))
        self.members.itemSelectionChanged.connect(self._member_selected)

    @staticmethod
    def _item_box() -> tuple[QGroupBox, QListWidget, QLineEdit, QPushButton]:
        box, items, field, button = QGroupBox(), QListWidget(), QLineEdit(), QPushButton("추가")
        field.setPlaceholderText("새 항목 이름")
        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(button)
        layout = QVBoxLayout(box)
        layout.addWidget(items, 1)
        layout.addLayout(row)
        return box, items, field, button

    @staticmethod
    def _adder(field: QLineEdit, add) -> object:  # noqa: ANN001
        def handler() -> None:
            if add(field.text()):
                field.clear()

        return handler

    def _rename(self) -> None:
        self.win.set_labels(self.scope_name.text(), self.category_name.text())

    def _add_member(self) -> None:
        if self.win.add_member(self.member_id.text(), self.member_name.text()):
            self.member_id.clear()
            self.member_name.clear()

    def selected_member(self) -> str | None:
        row = self.members.currentRow()
        return self.members.item(row, 0).text() if row >= 0 else None

    def _member_selected(self) -> None:
        row = self.members.currentRow()
        if row >= 0:  # picked for renaming
            self.member_id.setText(self.members.item(row, 0).text())
            self.member_name.setText(self.members.item(row, 1).text())

    def reload(self) -> None:
        window = self.win
        project = window.project_id
        editor = bool(project) and window.service.is_editor(window.actor, project)
        self.note.setText(
            "프로젝트의 칸 이름, 항목, 구성원을 관리합니다. 업무를 추가하려면 두 칸 모두 항목이 하나 이상 있어야 합니다."
            if editor
            else "이 화면은 프로젝트 편집자만 바꿀 수 있습니다. 내용은 누구나 볼 수 있습니다."
        )
        for widget in (
            self.scope_name, self.category_name, self.rename_button, self.scope_new, self.scope_add,
            self.category_new, self.category_add, self.member_id, self.member_name, self.member_add,
            self.editor_button,
        ):  # fmt: skip
            widget.setEnabled(editor)
        self.scope_name.setText(window.labels[0])
        self.category_name.setText(window.labels[1])
        self.scope_box.setTitle(window.labels[0])
        self.category_box.setTitle(window.labels[1])
        for items, records in (
            (self.scope_list, window.service.list_scopes(project) if project else []),
            (self.category_list, window.service.list_categories(project) if project else []),
        ):
            items.clear()
            items.addItems([record["name"] for record in records])
        members = window.service.list_members(project) if project else []
        self.members.setRowCount(len(members))
        for row, member in enumerate(members):
            cells = (member["user_id"], member["display_name"], "편집자" if member["is_editor"] else "구성원")
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.members.setItem(row, column, item)
