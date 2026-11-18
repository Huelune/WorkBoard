from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

STATS = (
    ("total", "전체"),
    ("confirmed", "확인된 완료"),
    ("pending", "확인 대기"),
    ("rejected", "반려"),
    ("on_hold", "보류"),
    ("overdue", "기한 초과"),
)
ASSIGNEE_COLUMNS = ["담당자", "배정", "본인 등록", "완료 신고", "확인된 완료", "반려", "주별 확인된 완료", "주당 평균(건)"]
PROJECT_COLUMNS = ["프로젝트", "전체", "확인된 완료", "남음", "주별 확인된 완료", "주당 평균(건)", "남은 주(추정)"]
WEEKLY_TIP = "기간을 7일씩 나눈 구간별 건수입니다. 왼쪽이 오래된 주, 오른쪽이 최근 주입니다."


def weekly_text(counts: list[int]) -> str:
    """Per-week counts, oldest first, as plain numbers a person can read."""
    return "  ".join(str(count) for count in counts)


def _fill(table: QTableWidget, rows: list[list[object]]) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if c:  # every column after the name is a number: line the digits up
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, c, item)


def _table(columns: list[str], weekly_column: int) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeaderItem(weekly_column).setToolTip(WEEKLY_TIP)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setAlternatingRowColors(True)
    return table


class PacePage(QWidget):
    """Status and pace. Every number is computed from history; nothing here is typed in."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.win = window
        self.status = QLabel()
        self.stats: dict[str, QLabel] = {}
        stats_row = QHBoxLayout()
        for key, caption in STATS:
            number = QLabel("0")
            font = number.font()
            font.setPointSize(font.pointSize() + 6)
            font.setBold(True)
            number.setFont(font)
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(QLabel(caption))
            cell.addWidget(number)
            stats_row.addLayout(cell)
            stats_row.addSpacing(24)
            self.stats[key] = number
        stats_row.addStretch(1)
        self.stats_box = QWidget()
        self.stats_box.setLayout(stats_row)

        self.weeks = QComboBox()
        for weeks in (4, 8, 12):
            self.weeks.addItem(f"최근 {weeks}주", weeks)
        self.reach = QComboBox()
        self.reach.addItem("이 프로젝트", False)
        self.reach.addItem("모든 프로젝트", True)
        self.assignees = _table(ASSIGNEE_COLUMNS, 6)
        self.projects = _table(PROJECT_COLUMNS, 4)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.assignees, "담당자별")
        self.tabs.addTab(self.projects, "프로젝트별")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("속도"))
        controls.addWidget(self.weeks)
        controls.addWidget(self.reach)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.stats_box)
        layout.addSpacing(8)
        layout.addLayout(controls)
        layout.addWidget(self.tabs, 1)
        note = QLabel(
            "속도는 확인된 완료 건수 기준입니다. 확인이 밀리면 실제보다 낮게 보이고, 업무 크기 차이는 반영하지 못합니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.weeks.currentIndexChanged.connect(self.reload)
        self.reach.currentIndexChanged.connect(self.reload)
        self.tabs.currentChanged.connect(lambda index: self.reach.setVisible(index == 0))

    def reload(self) -> None:
        window = self.win
        service, project = window.service, window.project_id
        end = window.now()
        start = end - timedelta(weeks=self.weeks.currentData())
        self.stats_box.setVisible(bool(project))
        self.status.setVisible(not project)
        self.status.setText("" if project else "프로젝트를 먼저 만드세요.")
        if project:
            status = service.project_status(project, window.today())
            for key, label in self.stats.items():
                label.setText(str(status[key]))
        everywhere = self.reach.currentData()
        pace = service.assignee_pace(start, end, None if everywhere else project) if everywhere or project else []
        _fill(
            self.assignees,
            [
                [
                    window.person(p["assignee_id"]),
                    p["assigned"],
                    p["self_added"],
                    p["reported"],
                    p["confirmed"],
                    p["rejected"],
                    weekly_text(p["weekly"]),
                    p["per_week"],
                ]
                for p in pace
            ],
        )
        _fill(
            self.projects,
            [
                [
                    p["name"],
                    p["total"],
                    p["confirmed_total"],
                    p["remaining"],
                    weekly_text(p["weekly"]),
                    p["per_week"],
                    "-" if p["weeks_left"] is None else f"약 {p['weeks_left']:g}주",
                ]
                for p in service.project_pace(start, end)
            ],
        )
