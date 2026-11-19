from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workboard.import_export.excel import ExcelError, column_letters, read_excel
from workboard.import_export.table import (
    FIELDS,
    REQUIRED,
    build_rows,
    guess_header_row,
    guess_mapping,
    mapping_from_headers,
    parse_pasted,
)
from workboard.ui.dialogs import message_label, show_message
from workboard.ui.text import EXCEL_ERRORS, IMPORT_REASONS, IMPORT_VERDICTS

if TYPE_CHECKING:
    from workboard.ui.main_window import MainWindow

PREVIEW_ROWS = 30
LISTED_ROWS = 300


class ImportDialog(QDialog):
    """Bring work in from a spreadsheet: read it, match the columns, preview, then register.

    Nothing is saved until the last button, and that button says exactly how many rows it will add.
    """

    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self.win = window
        self.project_id = window.project_id
        self.sheets: list[tuple[str, list[list[str]] | None]] = []
        self.grid: list[list[str]] = []
        self.plan: dict | None = None
        self.imported = 0
        self.setWindowTitle("엑셀에서 가져오기")
        self.resize(900, 680)

        self.open_button = QPushButton("엑셀 파일 열기…")
        self.paste_button = QPushButton("복사한 칸 붙여 넣기")
        self.sheet = QComboBox()
        self.sheet_caption = QLabel("시트")
        self.source_note = QLabel(
            "엑셀 파일을 열면 엑셀이 잠깐 떠서 읽기 전용으로 읽고 닫습니다. 파일은 바뀌지 않습니다. "
            "열리지 않으면 엑셀에서 칸을 복사한 뒤 '복사한 칸 붙여 넣기'를 누르세요."
        )
        self.source_note.setWordWrap(True)
        source = QHBoxLayout()
        source.addWidget(self.open_button)
        source.addWidget(self.paste_button)
        source.addWidget(self.sheet_caption)
        source.addWidget(self.sheet)
        source.addStretch(1)

        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.setMinimumHeight(170)
        self.header_row = QSpinBox()
        self.header_row.setMinimum(1)
        self.header_row.setToolTip("열 제목이 적힌 줄의 번호. 그 아래 줄부터 업무로 읽습니다.")

        labels = window.labels
        captions = {
            "title": "제목 (필수)",
            "scope": f"{labels[0]} (필수)",
            "category": f"{labels[1]} (필수)",
            "assignee": "담당자 (필수)",
            "due_date": "기한",
            "external_ref": "외부 참조 ID",
            "external_url": "외부 링크",
        }
        self.columns: dict[str, QComboBox] = {}
        mapping_form = QFormLayout()
        mapping_form.addRow("제목 줄", self.header_row)
        for field in FIELDS:
            combo = QComboBox()
            caption = QLabel(captions[field])
            caption.setTextFormat(Qt.TextFormat.PlainText)  # two of these are typed by an editor
            mapping_form.addRow(caption, combo)
            self.columns[field] = combo
            combo.currentIndexChanged.connect(self._replan)

        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.message = message_label()
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["줄", "판정", "제목", "사유"])
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        self.results.setWordWrap(True)
        self.results.setColumnWidth(0, 50)
        self.results.setColumnWidth(1, 80)
        self.results.setColumnWidth(2, 150)  # the reason comes last and takes the rest of the width
        self.apply_button = QPushButton("등록")
        self.close_button = QPushButton("닫기")
        self.close_button.setAutoDefault(False)
        self.apply_button.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.apply_button)

        middle = QHBoxLayout()
        middle.addLayout(mapping_form, 1)
        side = QVBoxLayout()
        side.addWidget(self.summary)
        side.addWidget(self.results, 1)
        middle.addLayout(side, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.source_note)
        layout.addLayout(source)
        layout.addWidget(self.preview, 1)
        layout.addLayout(middle, 2)
        layout.addWidget(self.message)
        layout.addLayout(buttons)

        self.open_button.clicked.connect(self.ask_file)
        self.paste_button.clicked.connect(lambda: self.paste_text(QApplication.clipboard().text()))
        self.sheet.currentIndexChanged.connect(self._sheet_changed)
        self.header_row.valueChanged.connect(self._header_changed)
        self.apply_button.clicked.connect(self.apply)
        self.close_button.clicked.connect(self.reject)
        self._show_sheets()
        self._replan()

    # ----- getting a grid

    def ask_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "가져올 엑셀 파일", "", "Excel (*.xlsx *.xlsm *.xls)")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            sheets = read_excel(path)
        except ExcelError as error:
            show_message(self.message, EXCEL_ERRORS[error.kind])
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self.load_sheets(sheets)
        return True

    def paste_text(self, text: str) -> bool:
        grid = parse_pasted(text or "")
        if not grid:
            show_message(self.message, "복사한 칸이 없습니다. 엑셀에서 칸을 고르고 복사한 뒤 다시 누르세요.")
            return False
        self.load_sheets([("붙여 넣은 칸", grid)])
        return True

    def load_sheets(self, sheets: list[tuple[str, list[list[str]] | None]]) -> None:
        self.sheets = sheets
        self.sheet.blockSignals(True)
        self.sheet.clear()
        self.sheet.addItems([name for name, _ in sheets])
        self.sheet.blockSignals(False)
        self._show_sheets()
        self._sheet_changed()

    def _show_sheets(self) -> None:
        several = len(self.sheets) > 1
        self.sheet.setVisible(several)
        self.sheet_caption.setVisible(several)

    def _sheet_changed(self) -> None:
        index = self.sheet.currentIndex()
        grid = self.sheets[index][1] if 0 <= index < len(self.sheets) else []
        if grid is None:
            show_message(self.message, "이 시트는 너무 커서 읽지 않았습니다(5,000줄 또는 60열 초과). 필요한 부분만 복사해 붙여 넣으세요.")
            grid = []
        else:
            show_message(self.message, None)
        self.load_grid(grid)

    def load_grid(self, grid: list[list[str]]) -> None:
        self.grid = grid
        width = max((len(row) for row in grid), default=0)
        self.preview.clear()
        self.preview.setColumnCount(width)
        self.preview.setRowCount(min(len(grid), PREVIEW_ROWS))
        self.preview.setHorizontalHeaderLabels([column_letters(c + 1) for c in range(width)])
        for r, row in enumerate(grid[:PREVIEW_ROWS]):
            for c, cell in enumerate(row):
                self.preview.setItem(r, c, QTableWidgetItem(cell))
        self.header_row.blockSignals(True)
        self.header_row.setMaximum(max(1, len(grid)))
        self.header_row.setValue(guess_header_row(grid, self.win.labels) + 1)
        self.header_row.blockSignals(False)
        self._header_changed()

    # ----- matching columns

    def headers(self) -> list[str]:
        index = self.header_row.value() - 1
        return self.grid[index] if 0 <= index < len(self.grid) else []

    def _header_changed(self) -> None:
        headers = self.headers()
        remembered = self.win.service.get_import_mapping(self.project_id) if self.project_id else {}
        chosen = mapping_from_headers(headers, remembered)
        guessed = guess_mapping(headers, self.win.labels)
        taken = {column for column in chosen.values() if column is not None}
        for field in FIELDS:  # a remembered choice wins; a guess only fills what is still free
            if chosen[field] is None and guessed[field] is not None and guessed[field] not in taken:
                chosen[field] = guessed[field]
                taken.add(guessed[field])
        for field, combo in self.columns.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(없음)", None)
            for column, header in enumerate(headers):
                letter = column_letters(column + 1)
                combo.addItem(f"{letter} · {header}" if header else letter, column)
            combo.setCurrentIndex(max(0, combo.findData(chosen[field])))
            combo.blockSignals(False)
        self._replan()

    def mapping(self) -> dict[str, int | None]:
        return {field: combo.currentData() for field, combo in self.columns.items()}

    def set_column(self, field: str, column: int | None) -> None:
        self.columns[field].setCurrentIndex(max(0, self.columns[field].findData(column)))

    # ----- preview and apply

    def rows(self) -> list[dict]:
        return build_rows(self.grid, self.header_row.value() - 1, self.mapping())

    def _replan(self, *_: object) -> None:
        self.plan = None
        self.results.clear()
        self.apply_button.setText("등록")
        self.apply_button.setEnabled(False)
        if not self.grid:
            self.summary.setText("엑셀 파일을 열거나, 엑셀에서 복사한 칸을 붙여 넣으세요.")
            return
        mapping = self.mapping()
        missing = [self.columns[f] for f in REQUIRED if mapping[f] is None]
        if missing:
            self.summary.setText("필수 항목(제목, 두 분류 칸, 담당자)에 맞는 열을 왼쪽에서 골라 주세요.")
            return
        try:
            self.plan = self.win.service.plan_import(self.win.actor, self.project_id, self.rows())
        except Exception as error:  # noqa: BLE001  (shown as a kind of problem, never as raw text)
            from workboard.ui.text import error_text

            self.summary.setText(error_text(error))
            return
        counts = self.plan["counts"]
        lines = [
            f"읽은 줄 {counts['read']} = 새로 등록 {counts['new']} + 이미 있음 {counts['duplicate']}"
            f" + 오류 {counts['error']} + 빈 줄 {counts['blank']}"
        ]
        for label, names in ((self.win.labels[0], self.plan["new_scopes"]), (self.win.labels[1], self.plan["new_categories"])):
            if names:
                lines.append(f"새로 만들어질 {label}: {', '.join(names)}")
        self.summary.setText("\n".join(lines))
        listed = [row for row in self.plan["rows"] if row["verdict"] in ("error", "duplicate")]
        for row in listed[:LISTED_ROWS]:
            reason = IMPORT_REASONS.get(row["reason"], "같은 업무가 이미 등록돼 있거나 표 안에서 겹침")
            item = QTreeWidgetItem([str(row["line"]), IMPORT_VERDICTS[row["verdict"]], row["title"], reason])
            item.setToolTip(3, reason)
            self.results.addTopLevelItem(item)
        if len(listed) > LISTED_ROWS:
            self.results.addTopLevelItem(QTreeWidgetItem(["…", "", "", f"그 밖에 {len(listed) - LISTED_ROWS}줄"]))
        label = f"새 업무 {counts['new']}건 등록"
        if counts["error"]:
            label += f" (오류 {counts['error']}건 제외)"
        self.apply_button.setText(label)
        self.apply_button.setEnabled(counts["new"] > 0)

    def apply(self) -> bool:
        if not self.plan or not self.plan["counts"]["new"]:
            return False
        applied: list[dict] = []
        rows = self.rows()
        error = self.win.attempt(
            lambda: applied.append(self.win.service.apply_import(self.win.actor, self.project_id, rows))
        )
        if error:
            show_message(self.message, error)
            return False
        self.imported = applied[0]["counts"]["new"]
        headers = self.headers()
        remembered = {f: headers[c] for f, c in self.mapping().items() if c is not None and headers[c]}
        self.win.attempt(lambda: self.win.service.save_import_mapping(self.win.actor, self.project_id, remembered))
        self.win.statusBar().showMessage(f"새 업무 {self.imported}건을 등록했습니다.", 5000)
        self.accept()
        return True
