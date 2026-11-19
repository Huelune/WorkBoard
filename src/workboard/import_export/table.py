"""Turning a spreadsheet grid into work-item candidates. No Qt, no Excel, no database."""

from __future__ import annotations

import re
from datetime import date, datetime

FIELDS = ("title", "scope", "category", "assignee", "due_date", "external_ref", "external_url")
REQUIRED = ("title", "scope", "category", "assignee")
MAX_ROWS, MAX_COLUMNS = 5000, 60

# Words that usually head each column. The project's own two field names are tried first.
_HINTS = {
    "title": ("제목", "업무", "대상", "함수", "항목", "내용", "title", "task", "name"),
    "scope": ("버전", "범위", "릴리스", "version", "scope", "release"),
    "category": ("분야", "분류", "종류", "구분", "category", "type"),
    "assignee": ("담당", "작업자", "assignee", "owner"),
    "due_date": ("기한", "마감", "완료일", "예정", "due", "deadline"),
    "external_url": ("링크", "주소", "url", "link"),
    "external_ref": ("id", "번호", "참조", "ref"),
}
_DATE = re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\.?(?:[ T].*)?$")


def cell_text(value: object) -> str:
    """One cell as the text a person sees: whole numbers without `.0`, dates as YYYY-MM-DD."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def clean_grid(grid: list[list[object]]) -> list[list[str]]:
    """Text cells in a rectangle, without the empty rows and columns at the end."""
    rows = [[cell_text(cell) for cell in row] for row in grid]
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, cell in enumerate(row) if cell), default=0) for row in rows), default=0)
    return [(row + [""] * width)[:width] for row in rows]


def parse_pasted(text: str) -> list[list[str]]:
    """Cells copied from a spreadsheet arrive as lines of tab-separated text."""
    return clean_grid([line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")])


def _score(cell: str, field: str, labels: tuple[str, str]) -> int:
    text = cell.strip().casefold()
    if not text:
        return 0
    own = {"scope": labels[0], "category": labels[1]}.get(field, "").strip().casefold()
    if own and text == own:
        return 3
    return 1 if any(hint in text for hint in _HINTS[field]) else 0


def guess_header_row(grid: list[list[str]], labels: tuple[str, str] = ("", "")) -> int:
    """Index of the row that looks most like column headings among the first twenty."""
    best, best_score = 0, 0
    for index, row in enumerate(grid[:20]):
        score = sum(max(_score(cell, field, labels) for field in FIELDS) > 0 for cell in row)
        if score > best_score:
            best, best_score = index, score
    if best_score:
        return best
    return next((index for index, row in enumerate(grid) if sum(bool(cell) for cell in row) >= 2), 0)


def guess_mapping(headers: list[str], labels: tuple[str, str] = ("", "")) -> dict[str, int | None]:
    """Which column feeds each field, judged by the heading text. Each column is used once."""
    mapping: dict[str, int | None] = dict.fromkeys(FIELDS)
    taken: set[int] = set()
    for strength in (3, 1):  # exact project names first, then the general hints
        for field in FIELDS:
            if mapping[field] is not None:
                continue
            for column, header in enumerate(headers):
                if column not in taken and _score(header, field, labels) == strength:
                    mapping[field] = column
                    taken.add(column)
                    break
    return mapping


def mapping_from_headers(headers: list[str], remembered: dict[str, str]) -> dict[str, int | None]:
    """Reuse a remembered choice (field -> heading text) wherever that heading is still there."""
    positions = {header.strip().casefold(): column for column, header in reversed(list(enumerate(headers))) if header.strip()}
    return {field: positions.get(str(remembered.get(field, "")).strip().casefold()) for field in FIELDS}


def normalize_due(text: str) -> str | None:
    """A due date as YYYY-MM-DD, or None when the cell is empty. Raises ValueError otherwise."""
    text = text.strip()
    if not text:
        return None
    match = _DATE.match(text)
    if not match:
        raise ValueError("not a date")
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day).isoformat()


def build_rows(grid: list[list[str]], header_row: int, mapping: dict[str, int | None]) -> list[dict]:
    """One candidate per sheet row under the headings. `line` is the row number shown in Excel."""
    rows = []
    for index in range(header_row + 1, len(grid)):
        cells = grid[index]
        row: dict = {"line": index + 1}
        for field in FIELDS:
            column = mapping.get(field)
            row[field] = cells[column] if column is not None and column < len(cells) else ""
        rows.append(row)
    return rows
