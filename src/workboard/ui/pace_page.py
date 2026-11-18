from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

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

from workboard.ui.text import spark

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

ASSIGNEE_COLUMNS = ["담당자", "배정", "완료 신고", "확인된 완료", "반려", "주별 확인된 완료", "주당 평균"]
PROJECT_COLUMNS = ["프로젝트", "전체", "확인된 완료", "남음", "주별 확인된 완료", "주당 평균", "남은 주(추정)"]


def _fill(table: QTableWidget, rows: list[list[object]]) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(value)))


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
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
        self.status.setWordWrap(True)
        self.weeks = QComboBox()
        for weeks in (4, 8, 12):
            self.weeks.addItem(f"최근 {weeks}주", weeks)
        self.reach = QComboBox()
        self.reach.addItem("이 프로젝트", False)
        self.reach.addItem("모든 프로젝트", True)
        self.assignees = _table(ASSIGNEE_COLUMNS)
        self.projects = _table(PROJECT_COLUMNS)

        assignee_tab = QWidget()
        assignee_layout = QVBoxLayout(assignee_tab)
        assignee_layout.addWidget(self.reach)
        assignee_layout.addWidget(self.assignees, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(assignee_tab, "담당자별")
        self.tabs.addTab(self.projects, "프로젝트별")

        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.weeks)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs, 1)
        note = QLabel(
            "속도는 확인된 완료 건수 기준입니다. 확인이 밀리면 실제보다 낮게 보이고, 업무 크기 차이는 반영하지 못합니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.weeks.currentIndexChanged.connect(self.reload)
        self.reach.currentIndexChanged.connect(self.reload)

    def reload(self) -> None:
        window = self.win
        service, project = window.service, window.project_id
        end = window.now()
        start = end - timedelta(weeks=self.weeks.currentData())
        if project:
            s = service.project_status(project, window.today())
            self.status.setText(
                f"전체 <b>{s['total']}</b> · 확인된 완료 <b>{s['confirmed']}</b> · 확인 대기 <b>{s['pending']}</b>"
                f" · 반려 <b>{s['rejected']}</b> · 보류 <b>{s['on_hold']}</b> · 기한 초과 <b>{s['overdue']}</b>"
            )
        else:
            self.status.setText("프로젝트를 먼저 만드세요.")
        everywhere = self.reach.currentData()
        pace = service.assignee_pace(start, end, None if everywhere else project) if everywhere or project else []
        _fill(
            self.assignees,
            [
                [p["assignee_id"], p["assigned"], p["reported"], p["confirmed"], p["rejected"], spark(p["weekly"]), p["per_week"]]
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
                    spark(p["weekly"]),
                    p["per_week"],
                    "-" if p["weeks_left"] is None else f"약 {p['weeks_left']:g}주",
                ]
                for p in service.project_pace(start, end)
            ],
        )
