from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPalette
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
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workboard.domain import is_overdue, is_web_link
from workboard.ui.dialogs import ProgressForm
from workboard.ui.text import DEFAULT_LABELS, attention, detail_html, group_notes, state_label
from workboard.ui.theme import tone

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ROLE = Qt.ItemDataRole.UserRole
OWNER_COLUMN = 1  # the assignee on the shared list, the project on "my work"
PROGRESS_COLUMN = 2
NOTE_COLUMN = 3
LINK_COLUMN = 5
FIELD_COLUMN = 6

class RowDelegate(QStyledItemDelegate):
    """On a selected row every cell uses the selection's own text colour, so a status colour
    never ends up unreadable against the system accent."""

    def initStyleOption(self, option, index) -> None:  # noqa: ANN001, N802
        super().initStyleOption(option, index)
        if option.state & QStyle.StateFlag.State_Selected:
            selected = option.palette.color(QPalette.ColorRole.HighlightedText)
            option.palette.setColor(QPalette.ColorRole.Text, selected)


class ProgressDelegate(RowDelegate):
    """Draws the progress column as a bar so a row reads at a glance."""

    def paint(self, painter, option, index) -> None:  # noqa: ANN001
        value = index.data(ROLE)
        super().paint(painter, option, index)  # selection background; the cell has no text
        if value is None:
            return
        # Painted by hand: the native progress-bar style differs too much between platforms.
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
    """Assignment list with the selected row's details beside it.

    `mine` shows the actor's work across projects, with the progress entry always open:
    updating one's own work is what most visits are for.
    """

    def __init__(self, window: MainWindow, mine: bool = False) -> None:
        super().__init__()
        self.win, self.mine = window, mine
        self.rows: dict[str, dict] = {}
        self.folded: set[object] = set()  # groups the user closed; everything else stays open
        self.drafts: dict[str, dict] = {}  # typed but not saved, kept per assignment while the app is open
        self._saving = False
        self._empty_action: Callable[[], object] | None = None

        self.group = QComboBox()
        for label, value in (("담당자별", "assignee"), ("분류별", "category"), ("묶지 않음", None)):
            self.group.addItem(label, value)
        self.hide_confirmed = QCheckBox("확인된 완료 숨기기")
        self.hide_confirmed.setChecked(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("검색 (할 일, 담당자)")
        self.search.setClearButtonEnabled(True)
        self.add_button = QPushButton("업무 추가")
        self.import_button = QPushButton("엑셀에서 가져오기")

        bar = QHBoxLayout()
        if not mine:
            bar.addWidget(QLabel("묶기"))
            bar.addWidget(self.group)
        bar.addWidget(self.hide_confirmed)
        bar.addWidget(self.search, 1)
        if not mine:
            bar.addWidget(self.import_button)
            bar.addWidget(self.add_button)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["할 일" if mine else "담당자 / 할 일", "프로젝트" if mine else "담당자", "진행", "상태 · 챙길 것", "기한", "외부 참조", ""]
        )
        self.tree.setItemDelegate(RowDelegate(self.tree))
        self.tree.setItemDelegateForColumn(PROGRESS_COLUMN, ProgressDelegate(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(PROGRESS_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(PROGRESS_COLUMN, 110)
        # On "my work" the reference is one click away in the details; the row keeps room for the title.
        self.tree.setColumnHidden(LINK_COLUMN, mine)

        self.form = ProgressForm(self._save)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.side = QWidget()
        side_layout = QVBoxLayout(self.side)
        side_layout.setContentsMargins(8, 0, 0, 0)
        side_layout.addWidget(self.form)
        side_layout.addWidget(self.detail, 1)
        # What an editor may do to the selected row, in plain sight as well as on right-click.
        self.due_button = QPushButton("기한 변경")
        self.cancel_button = QPushButton("배정 취소")
        self.delete_button = QPushButton("업무 삭제")
        self.editor_bar = QWidget()
        editor_layout = QHBoxLayout(self.editor_bar)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.due_button, self.cancel_button, self.delete_button):
            editor_layout.addWidget(button)
        editor_layout.addStretch(1)
        side_layout.addWidget(self.editor_bar)
        self.side.setMinimumWidth(320)
        self.side.hide()  # nothing selected yet: the list gets the whole width
        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.side)
        splitter.setStretchFactor(0, 1)  # extra width goes to the list, where titles need it
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([700, 340])

        self.empty_text = QLabel()
        self.empty_text.setTextFormat(Qt.TextFormat.PlainText)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_button = QPushButton()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.empty_text)
        empty_layout.addWidget(self.empty_button, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(2)
        self.body = QStackedWidget()
        self.body.addWidget(splitter)
        self.body.addWidget(empty)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.body, 1)

        self.group.currentIndexChanged.connect(self.reload)
        self.hide_confirmed.toggled.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.tree.currentItemChanged.connect(self._show_detail)
        self.tree.itemExpanded.connect(lambda item: self.folded.discard(item.data(1, ROLE)))
        self.tree.itemCollapsed.connect(lambda item: self.folded.add(item.data(1, ROLE)))
        self.tree.itemActivated.connect(lambda *_: self.focus_form())  # Enter or double-click
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._clicked)
        self.add_button.clicked.connect(self.win.ask_work_item)
        self.import_button.clicked.connect(self.win.ask_import)
        self.due_button.clicked.connect(lambda: self.win.ask_due_date(self.current_row()))
        self.cancel_button.clicked.connect(lambda: self.win.ask_cancel(self.current_row()))
        self.delete_button.clicked.connect(lambda: self.win.ask_delete(self.current_row()))
        self.empty_button.clicked.connect(lambda: self._empty_action and self._empty_action())

    # ----- the selected row

    def current_row(self) -> dict | None:
        item = self.tree.currentItem()
        return self.rows.get(item.data(0, ROLE)) if item else None

    def select(self, assignment_id: str) -> bool:
        """Select the row of one assignment if it is showing."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, ROLE) == assignment_id:
                self.tree.setCurrentItem(item)
                return True
            stack.extend(item.child(i) for i in range(item.childCount()))
        return False

    def focus_form(self) -> None:
        if not self.form.isHidden():
            self.form.status.setFocus()

    def _keep_draft(self) -> None:
        """Remember what is typed for the assignment in the form, so leaving the row loses nothing."""
        assignment_id = self.form.assignment_id
        if self._saving or not assignment_id:
            return
        if self.form.is_dirty():
            self.drafts[assignment_id] = self.form.values()
        else:
            self.drafts.pop(assignment_id, None)

    def has_unsaved(self) -> bool:
        self._keep_draft()
        return bool(self.drafts)

    def _save(self, assignment_id: str, values: dict) -> str | None:
        self.drafts.pop(assignment_id, None)
        self._saving = True  # the redraw after a successful save must show the saved values
        try:
            error, field = self.win.attempt_progress(assignment_id, values)
        finally:
            self._saving = False
        if error:
            self.form.point_at(field)
        else:
            self.tree.setFocus()  # back to the list, on the same row, ready for the next one
        return error

    def _show_detail(self, item: QTreeWidgetItem | None, _previous: object = None) -> None:
        self._keep_draft()
        row = self.rows.get(item.data(0, ROLE)) if item else None
        self.side.setVisible(bool(row))
        if not row:
            return
        own = row["assignee_id"] == self.win.actor
        self.form.setVisible(own)
        if own:
            self.form.load(row, self.drafts.get(row["assignment_id"]))
        else:
            self.form.assignment_id = None
        self.editor_bar.setVisible(self.win.service.is_editor(self.win.actor, row["project_id"]))
        detail = self.win.service.assignment_detail(row["assignment_id"])
        self.detail.setHtml(detail_html(detail, self.win.names, self.win.person, self._labels(row)))

    def _labels(self, row: dict) -> tuple[str, str]:
        """The two field names of the row's own project; "my work" can span several."""
        record = self.win.records.get(row["project_id"])
        return (record["scope_label"], record["category_label"]) if record else DEFAULT_LABELS

    # ----- right-click and links

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

    # ----- filling the list

    def _visible(self, row: dict) -> bool:
        if self.hide_confirmed.isChecked() and row["review_state"] == "confirmed":
            return False
        needle = self.search.text().strip().casefold()
        return not needle or needle in f"{row['title']} {row['assignee_id']} {self.win.person(row['assignee_id'])}".casefold()

    def _show_empty(self, text: str, button: str | None = None, action: Callable[[], object] | None = None) -> None:
        """Replace the list with one sentence about what to do next, and the button that does it."""
        self.empty_text.setText(text)
        self.empty_button.setText(button or "")
        self.empty_button.setVisible(bool(button))
        self._empty_action = action
        self.body.setCurrentIndex(1)

    def _explain_empty(self) -> None:
        win = self.win
        project = win.project_id
        if self.mine:
            self._show_empty(
                "맡은 업무가 없습니다.\n편집자가 배정하거나, 업무 목록에서 내 업무를 직접 등록할 수 있습니다.",
                "업무 목록으로",
                lambda: win.nav.setCurrentRow(0),
            )
        elif not project:
            self._show_empty(
                "아직 프로젝트가 없습니다.\n프로젝트를 만들면 만든 사람이 편집자가 됩니다.", "새 프로젝트", win.ask_new_project
            )
        elif win.scope_id or win.category_id:
            self._show_empty("고른 조건에 맞는 업무가 없습니다.", "조건 지우기", win.clear_filters)
        elif not (win.service.list_scopes(project) and win.service.list_categories(project)):
            if win.service.is_editor(win.actor, project):
                self._show_empty(
                    f"업무를 넣으려면 '{win.labels[0]}', '{win.labels[1]}' 두 칸에 항목이 하나씩 있어야 합니다.",
                    "관리 화면으로",
                    win.show_manage,
                )
            else:
                self._show_empty("아직 업무가 없습니다. 편집자가 프로젝트를 준비하는 중입니다.")
        elif win.service.is_member(win.actor, project):
            self._show_empty("아직 업무가 없습니다.", "업무 추가", win.ask_work_item)
        else:
            self._show_empty(
                "아직 업무가 없습니다.\n이 프로젝트의 구성원이 아니어서 업무를 등록할 수 없습니다. 편집자에게 추가를 요청하세요."
            )

    def reload(self) -> None:
        window = self.win
        self._keep_draft()
        selected = self.tree.currentItem().data(0, ROLE) if self.tree.currentItem() else None
        self.tree.clear()
        self.rows = {}
        today, project = window.today(), window.project_id
        self.group.setItemText(1, f"{window.labels[1]}별")
        red, amber, green = (tone(name, self.palette()) for name in ("red", "amber", "green"))
        self.add_button.setEnabled(bool(project) and window.service.is_member(window.actor, project))
        self.import_button.setEnabled(bool(project) and window.service.is_editor(window.actor, project))
        if self.mine:
            rows = window.service.my_assignments(window.actor)
        elif project:
            rows = window.service.list_assignments(
                project, scope_id=window.scope_id, category_id=window.category_id
            )
        else:
            rows = []
        if not rows:
            self.side.hide()
            self._explain_empty()
            return
        self.body.setCurrentIndex(0)
        self.rows = {row["assignment_id"]: row for row in rows}
        shown = sorted((r for r in rows if self._visible(r)), key=lambda r: attention(r, today))

        by = None if self.mine else self.group.currentData()
        # Hide the owner column when it would say the same thing on every row: under a
        # per-assignee heading, or on "my work" when all of it is in one project.
        one_project = len({row["project_id"] for row in rows}) == 1
        # The last column is named in the project's own word; across several projects no one word fits.
        self.tree.headerItem().setText(
            FIELD_COLUMN, self._labels(rows[0])[1] if self.mine and one_project else "분류" if self.mine else window.labels[1]
        )
        self.tree.setColumnHidden(OWNER_COLUMN, one_project if self.mine else by == "assignee")
        parents: dict[object, QTreeWidgetItem] = {}
        if by:
            summaries = window.service.group_summary(
                project, by, today, scope_id=window.scope_id, category_id=window.category_id
            )
            for summary in summaries:
                key = summary["key"]
                name = window.person(key) if by == "assignee" else window.names.get(key, f"{window.labels[1]} 없음")
                # Headings stay in the normal text colour; colour is kept for the rows that need a hand.
                parent = QTreeWidgetItem([f"{name} · {summary['count']}건", "", "", group_notes(summary)])
                parent.setData(1, ROLE, key)
                average = round(summary["average_progress"])
                parent.setData(PROGRESS_COLUMN, ROLE, average)
                parent.setData(PROGRESS_COLUMN, Qt.ItemDataRole.AccessibleTextRole, f"평균 진행 {average}%")
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(key not in self.folded)
                parents[key] = parent

        for row in shown:
            overdue = is_overdue(row["due_date"], row["status"], today)
            note = state_label(row) + (" · 기한 초과" if overdue else "")
            if row["assignment_id"] in self.drafts:
                note += " · 저장 안 됨"
            owner = (
                window.records.get(row["project_id"], {}).get("name", "")
                if self.mine
                else window.person(row["assignee_id"])
            )
            item = QTreeWidgetItem(
                [
                    row["title"],
                    owner,
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
            item.setData(PROGRESS_COLUMN, Qt.ItemDataRole.AccessibleTextRole, f"진행 {row['progress']}%")
            color = {"rejected": red, "pending": amber, "confirmed": green}.get(row["review_state"])
            if overdue:
                color = red
            if color:
                item.setForeground(NOTE_COLUMN, QBrush(color))
            parent = parents.get(row[f"{by}_id"]) if by else None
            (parent.addChild if parent else self.tree.addTopLevelItem)(item)
            if row["assignment_id"] == selected:
                self.tree.setCurrentItem(item)
        if self.mine and not self.tree.currentItem() and self.tree.topLevelItemCount():
            # Something is always selected on "my work", so the progress entry is ready to type into.
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if not self.tree.currentItem():
            self.side.hide()
