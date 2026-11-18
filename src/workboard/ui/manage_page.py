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
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ROLE = Qt.ItemDataRole.UserRole


class ItemBox(QGroupBox):
    """One field's items: the list, a line to add one, and rename/delete for the picked one."""

    def __init__(self) -> None:
        super().__init__()
        self.items = QListWidget()
        self.new = QLineEdit()
        self.new.setPlaceholderText("새 항목 이름")
        self.add = QPushButton("추가")
        self.rename = QPushButton("이름 바꾸기")
        self.delete = QPushButton("삭제")
        adding = QHBoxLayout()
        adding.addWidget(self.new, 1)
        adding.addWidget(self.add)
        editing = QHBoxLayout()
        editing.addWidget(self.rename)
        editing.addWidget(self.delete)
        editing.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.items, 1)
        layout.addLayout(editing)
        layout.addLayout(adding)


class ManagePage(QWidget):
    """Project settings in one place: the two field names, their items, and the members.

    Everyone can read it; only project editors can change anything.
    """

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.win = window
        self.editor = False
        self.editors: set[str] = set()
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

        self.boxes = {"scope": ItemBox(), "category": ItemBox()}
        self.scope_box, self.category_box = self.boxes["scope"], self.boxes["category"]
        # Short names the rest of the app and the tests use.
        self.scope_list, self.scope_new, self.scope_add = self.scope_box.items, self.scope_box.new, self.scope_box.add
        self.scope_rename, self.scope_delete = self.scope_box.rename, self.scope_box.delete
        self.category_list, self.category_new = self.category_box.items, self.category_box.new
        self.category_add = self.category_box.add
        self.category_rename, self.category_delete = self.category_box.rename, self.category_box.delete

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
        self.member_remove = QPushButton("구성원 삭제")
        self.editor_button = QPushButton("편집자로 지정")
        self.editor_remove = QPushButton("편집자 해제")
        member_inputs = QHBoxLayout()
        for widget in (self.member_id, self.member_name, self.member_add):
            member_inputs.addWidget(widget)
        member_actions = QHBoxLayout()
        for widget in (self.editor_button, self.editor_remove, self.member_remove):
            member_actions.addWidget(widget)
        member_actions.addStretch(1)
        member_layout = QVBoxLayout()
        member_layout.addWidget(self.members, 1)
        member_layout.addLayout(member_actions)
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

        self.rename_button.clicked.connect(
            lambda: self.win.set_labels(self.scope_name.text(), self.category_name.text())
        )
        for kind, add in (("scope", self.win.add_scope), ("category", self.win.add_category)):
            box = self.boxes[kind]
            handler = self._adder(box.new, add)
            box.add.clicked.connect(handler)
            box.new.returnPressed.connect(handler)
            box.rename.clicked.connect(lambda _=False, k=kind: self.win.ask_rename_item(k, self.selected_item(k)))
            box.delete.clicked.connect(lambda _=False, k=kind: self.win.ask_delete_item(k, self.selected_item(k)))
            box.items.currentRowChanged.connect(self._sync_buttons)
        self.member_add.clicked.connect(self._add_member)
        self.member_name.returnPressed.connect(self._add_member)
        self.editor_button.clicked.connect(lambda: self.win.ask_make_editor(self.selected_member()))
        self.editor_remove.clicked.connect(lambda: self.win.ask_remove_editor(self.selected_member()))
        self.member_remove.clicked.connect(lambda: self.win.ask_remove_member(self.selected_member()))
        self.members.itemSelectionChanged.connect(self._member_selected)

    @staticmethod
    def _adder(field: QLineEdit, add) -> object:  # noqa: ANN001
        def handler() -> None:
            if add(field.text()):
                field.clear()

        return handler

    def _add_member(self) -> None:
        if self.win.add_member(self.member_id.text(), self.member_name.text()):
            self.member_id.clear()
            self.member_name.clear()

    def selected_item(self, kind: str) -> str | None:
        item = self.boxes[kind].items.currentItem()
        return item.data(ROLE) if item else None

    def selected_member(self) -> str | None:
        row = self.members.currentRow()
        return self.members.item(row, 0).text() if row >= 0 else None

    def _member_selected(self) -> None:
        row = self.members.currentRow()
        if row >= 0:  # picked for renaming
            self.member_id.setText(self.members.item(row, 0).text())
            self.member_name.setText(self.members.item(row, 1).text())
        self._sync_buttons()

    def _sync_buttons(self, *_: object) -> None:
        """Buttons that act on a picked row are on only when a row is picked and the action applies."""
        for kind, box in self.boxes.items():
            picked = self.editor and self.selected_item(kind) is not None
            box.rename.setEnabled(picked)
            box.delete.setEnabled(picked)
        member = self.selected_member() if self.editor else None
        is_editor = member in self.editors
        self.editor_button.setEnabled(member is not None and not is_editor)
        self.editor_remove.setEnabled(member is not None and is_editor)
        self.member_remove.setEnabled(member is not None and not is_editor)

    def reload(self) -> None:
        window = self.win
        project = window.project_id
        self.editor = bool(project) and window.service.is_editor(window.actor, project)
        self.note.setText(
            "프로젝트의 칸 이름, 항목, 구성원을 관리합니다. 업무를 추가하려면 두 칸 모두 항목이 하나 이상 있어야 합니다."
            if self.editor
            else "이 화면은 프로젝트 편집자만 바꿀 수 있습니다. 내용은 누구나 볼 수 있습니다."
        )
        for widget in (
            self.scope_name, self.category_name, self.rename_button, self.scope_new, self.scope_add,
            self.category_new, self.category_add, self.member_id, self.member_name, self.member_add,
        ):  # fmt: skip
            widget.setEnabled(self.editor)
        self.scope_name.setText(window.labels[0])
        self.category_name.setText(window.labels[1])
        self.scope_box.setTitle(window.labels[0])
        self.category_box.setTitle(window.labels[1])
        for kind, records in (
            ("scope", window.service.list_scopes(project) if project else []),
            ("category", window.service.list_categories(project) if project else []),
        ):
            items = self.boxes[kind].items
            items.clear()
            for record in records:
                item = QListWidgetItem(record["name"])
                item.setData(ROLE, record["id"])
                items.addItem(item)
        members = window.service.list_members(project) if project else []
        self.editors = {member["user_id"] for member in members if member["is_editor"]}
        self.members.setRowCount(0)
        self.members.setRowCount(len(members))
        for row, member in enumerate(members):
            cells = (member["user_id"], member["display_name"], "편집자" if member["is_editor"] else "구성원")
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.members.setItem(row, column, item)
        self._sync_buttons()
