from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import is_overdue, is_web_link
from workboard.ui.text import attention, detail_html, group_notes, state_label

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["담당자 / 할 일", "담당자", "진행", "상태 · 챙길 것", "기한", "외부 참조", "분류"]
LINK_COLUMN = 5
PROGRESS_COLUMN = 2
NOTE_COLUMN = 3
# (light theme, dark theme) pairs; the app follows the system theme.
_TONES = {
    "red": ("#b3261e", "#ff8a80"),
    "amber": ("#9a6700", "#ffc857"),
    "green": ("#1a7f37", "#7ee787"),
    "track": ("#d8dde3", "#4a4f57"),
    "bar": ("#3b82c4", "#5aa2e6"),
}


def tone(name: str, palette: QPalette) -> QColor:
    dark = palette.window().color().lightness() < 128
    return QColor(_TONES[name][dark])


class ProgressDelegate(QStyledItemDelegate):
    """Draws the progress column as a bar so a row reads at a glance."""

    def paint(self, painter, option, index) -> None:  # noqa: ANN001
        value = index.data(ROLE)
        if value is None:
            super().paint(painter, option, index)
            return
        # Painted by hand: the native progress-bar style differs too much between platforms.
        super().paint(painter, option, index)  # selection background; the cell has no text
        track = option.rect.adjusted(6, 5, -44, -5)
        painter.fillRect(track, tone("track", option.palette))
        filled = track.adjusted(0, 0, -round(track.width() * (100 - int(value)) / 100), 0)
        painter.fillRect(filled, tone("bar", option.palette))
        painter.setPen(
            option.palette.highlightedText().color()
            if option.state & QStyle.StateFlag.State_Selected
            else option.palette.text().color()
        )
        label = option.rect.adjusted(option.rect.width() - 40, 0, -4, 0)
        painter.drawText(label, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{int(value)}%")


class WorkPage(QWidget):
    """Assignment list with a detail panel. `mine` shows the actor's work across projects."""

    def __init__(self, window: MainWindow, mine: bool = False) -> None:
        super().__init__()
        self.win, self.mine = window, mine
        self.rows: dict[str, dict] = {}
        self.folded: set[object] = set()  # groups the user closed; everything else stays open

        self.group = QComboBox()
        for label, value in (("담당자별", "assignee"), ("분류별", "category"), ("묶지 않음", None)):
            self.group.addItem(label, value)
        self.hide_confirmed = QCheckBox("확인된 완료 숨기기")
        self.hide_confirmed.setChecked(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("검색 (할 일, 담당자)")
        self.search.setClearButtonEnabled(True)
        self.progress_button = QPushButton("진행 입력")
        self.add_button = QPushButton("업무 추가")

        bar = QHBoxLayout()
        if not mine:
            bar.addWidget(QLabel("묶기"))
            bar.addWidget(self.group)
        bar.addWidget(self.hide_confirmed)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.progress_button)
        if not mine:
            bar.addWidget(self.add_button)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setItemDelegateForColumn(PROGRESS_COLUMN, ProgressDelegate(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(PROGRESS_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(PROGRESS_COLUMN, 150)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)

        self.group.currentIndexChanged.connect(self.reload)
        self.hide_confirmed.toggled.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.tree.currentItemChanged.connect(self._show_detail)
        self.tree.itemExpanded.connect(lambda item: self.folded.discard(item.data(1, ROLE)))
        self.tree.itemCollapsed.connect(lambda item: self.folded.add(item.data(1, ROLE)))
        self.tree.itemDoubleClicked.connect(lambda *_: self.win.edit_progress(self.current_row()))
        self.progress_button.clicked.connect(lambda: self.win.edit_progress(self.current_row()))
        self.add_button.clicked.connect(self.win.ask_work_item)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._clicked)

    def current_row(self) -> dict | None:
        item = self.tree.currentItem()
        return self.rows.get(item.data(0, ROLE)) if item else None

    def menu_actions(self, row: dict) -> list[tuple[str, bool, object]]:
        """Right-click choices for one row: label, whether the actor may use it, what it does."""
        win = self.win
        editor = win.service.is_editor(win.actor, row["project_id"])
        return [
            ("진행 입력", row["assignee_id"] == win.actor, lambda: win.edit_progress(row)),
            ("기한 변경", editor, lambda: win.ask_due_date(row)),
            ("배정 취소", editor, lambda: win.ask_cancel(row)),
            ("업무 삭제", editor, lambda: win.ask_delete(row)),
            ("외부 링크 열기", is_web_link(row["external_url"]), lambda: win.open_link(row["external_url"])),
            ("근거 링크 열기", is_web_link(row["evidence_ref"]), lambda: win.open_link(row["evidence_ref"])),
        ]

    def _menu(self, position) -> None:  # noqa: ANN001
        item = self.tree.itemAt(position)
        row = self.rows.get(item.data(0, ROLE)) if item else None
        if not row:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        for index, (label, enabled, call) in enumerate(self.menu_actions(row)):
            if index == 4:
                menu.addSeparator()
            menu.addAction(label, call).setEnabled(enabled)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        row = self.rows.get(item.data(0, ROLE))
        if row and column == LINK_COLUMN:
            self.win.open_link(row["external_url"])

    def _visible(self, row: dict) -> bool:
        if self.hide_confirmed.isChecked() and row["review_state"] == "confirmed":
            return False
        needle = self.search.text().strip().casefold()
        return not needle or needle in f"{row['title']} {row['assignee_id']} {self.win.person(row['assignee_id'])}".casefold()

    def reload(self) -> None:
        window = self.win
        selected = self.tree.currentItem().data(0, ROLE) if self.tree.currentItem() else None
        self.tree.clear()
        self.detail.clear()
        self.rows = {}
        today, project = window.today(), window.project_id
        self.group.setItemText(1, f"{window.labels[1]}별")
        self.tree.headerItem().setText(len(COLUMNS) - 1, window.labels[1])
        red, amber, green = (tone(name, self.palette()) for name in ("red", "amber", "green"))
        self.add_button.setEnabled(bool(project) and window.service.is_member(window.actor, project))
        if self.mine:
            rows = window.service.my_assignments(window.actor)
        elif project:
            rows = window.service.list_assignments(
                project, scope_id=window.scope_id, category_id=window.category_id
            )
        else:
            return
        self.rows = {row["assignment_id"]: row for row in rows}
        shown = sorted((r for r in rows if self._visible(r)), key=lambda r: attention(r, today))

        by = None if self.mine else self.group.currentData()
        parents: dict[object, QTreeWidgetItem] = {}
        if by:
            summaries = window.service.group_summary(
                project, by, today, scope_id=window.scope_id, category_id=window.category_id
            )
            for summary in summaries:
                key = summary["key"]
                name = window.person(key) if by == "assignee" else window.names.get(key, f"{window.labels[1]} 없음")
                parent = QTreeWidgetItem([f"{name} · {summary['count']}건", "", "", group_notes(summary)])
                parent.setData(1, ROLE, key)
                parent.setData(PROGRESS_COLUMN, ROLE, round(summary["average_progress"]))
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                if summary["rejected"] or summary["overdue"]:
                    parent.setForeground(NOTE_COLUMN, QBrush(red))
                elif summary["pending"]:
                    parent.setForeground(NOTE_COLUMN, QBrush(amber))
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(key not in self.folded)
                parents[key] = parent

        for row in shown:
            overdue = is_overdue(row["due_date"], row["status"], today)
            note = state_label(row) + (" · 기한 초과" if overdue else "")
            item = QTreeWidgetItem(
                [
                    row["title"],
                    window.person(row["assignee_id"]),
                    "",
                    note,
                    row["due_date"] or "",
                    row["external_ref"] or ("링크" if row["external_url"] else ""),
                    window.names.get(row["category_id"], ""),
                ]
            )
            if is_web_link(row["external_url"]):  # clickable: looks like a link, opens on click
                font = item.font(LINK_COLUMN)
                font.setUnderline(True)
                item.setFont(LINK_COLUMN, font)
                item.setForeground(LINK_COLUMN, self.palette().link())
                item.setToolTip(LINK_COLUMN, row["external_url"])
            item.setData(0, ROLE, row["assignment_id"])
            item.setData(PROGRESS_COLUMN, ROLE, row["progress"])
            color = {"rejected": red, "pending": amber, "confirmed": green}.get(row["review_state"])
            if overdue:
                color = red
            if color:
                item.setForeground(NOTE_COLUMN, QBrush(color))
            parent = parents.get(row[f"{by}_id"]) if by else None
            (parent.addChild if parent else self.tree.addTopLevelItem)(item)
            if row["assignment_id"] == selected:
                self.tree.setCurrentItem(item)

    def _show_detail(self, item: QTreeWidgetItem | None, _previous: object = None) -> None:
        row = self.rows.get(item.data(0, ROLE)) if item else None
        self.progress_button.setEnabled(bool(row) and row["assignee_id"] == self.win.actor)
        if not row:
            self.detail.clear()
            return
        self.detail.setHtml(detail_html(self.win.service.assignment_detail(row["assignment_id"]), self.win.names, self.win.person, self.win.labels))
