from __future__ import annotations

import unittest
from datetime import date, datetime

from workboard.import_export.table import (
    build_rows,
    cell_text,
    clean_grid,
    guess_header_row,
    guess_mapping,
    mapping_from_headers,
    normalize_due,
    parse_pasted,
)

LABELS = ("코드 버전", "검증 분야")


class CellTests(unittest.TestCase):
    def test_cell_text(self) -> None:
        cases = (
            (None, ""),
            ("  글자 ", "글자"),
            (12.0, "12"),
            (0.5, "0.5"),
            (7, "7"),
            (True, "True"),
            (datetime(2026, 1, 15, 9, 30), "2026-01-15"),
            (date(2026, 2, 3), "2026-02-03"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(cell_text(value), expected)

    def test_clean_grid_trims_the_empty_edge(self) -> None:
        grid = clean_grid([["a", None, None], ["b", 1.0], [None, None, None], []])
        self.assertEqual(grid, [["a", ""], ["b", "1"]])
        self.assertEqual(clean_grid([]), [])
        self.assertEqual(clean_grid([[None], [None]]), [])

    def test_pasted_cells(self) -> None:
        self.assertEqual(parse_pasted("제목\t담당\r\n함수 1\t김\r\n\r\n"), [["제목", "담당"], ["함수 1", "김"]])
        self.assertEqual(parse_pasted(""), [])


class GuessTests(unittest.TestCase):
    GRID = [
        ["2026년 검증 계획", "", "", "", ""],
        ["", "", "", "", ""],
        ["코드 버전", "검증 분야", "대상 함수", "담당자", "완료 예정일"],
        ["v1.0", "단위", "함수 1", "김 담당", "2026-01-15"],
    ]

    def test_header_row_is_found_below_a_title_line(self) -> None:
        self.assertEqual(guess_header_row(self.GRID, LABELS), 2)
        self.assertEqual(guess_header_row([["", ""], ["값 1", "값 2"]], LABELS), 1)
        self.assertEqual(guess_header_row([], LABELS), 0)

    def test_mapping_prefers_the_projects_own_names(self) -> None:
        mapping = guess_mapping(self.GRID[2], LABELS)
        self.assertEqual(
            mapping,
            {"title": 2, "scope": 0, "category": 1, "assignee": 3, "due_date": 4, "external_ref": None, "external_url": None},
        )
        general = guess_mapping(["Task", "Version", "Type", "Owner", "Due", "Item ID", "Link"])
        self.assertEqual(list(general.values()), [0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(set(guess_mapping(["가", "나"]).values()), {None})

    def test_each_column_is_used_once(self) -> None:
        mapping = guess_mapping(["업무 ID", "업무 제목"])
        self.assertEqual((mapping["title"], mapping["external_ref"]), (0, None))

    def test_remembered_mapping_follows_the_heading_text(self) -> None:
        remembered = {"title": "대상 함수", "assignee": "담당자", "scope": "없어진 열"}
        mapping = mapping_from_headers(["담당자", "대상 함수", "비고"], remembered)
        self.assertEqual((mapping["title"], mapping["assignee"], mapping["scope"], mapping["due_date"]), (1, 0, None, None))


class RowTests(unittest.TestCase):
    def test_due_dates(self) -> None:
        for text, expected in (
            ("", None),
            ("2026-01-15", "2026-01-15"),
            ("2026.1.5", "2026-01-05"),
            ("2026/01/15", "2026-01-15"),
            ("2026. 1. 15.", "2026-01-15"),
            ("2026-01-15 00:00:00", "2026-01-15"),
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_due(text), expected)
        for text in ("내일", "01-15", "2026-13-01", "2026-02-30", "15/01/2026"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                normalize_due(text)

    def test_rows_carry_the_sheet_line_number(self) -> None:
        mapping = guess_mapping(GuessTests.GRID[2], LABELS)
        rows = build_rows(GuessTests.GRID, 2, mapping)
        self.assertEqual(
            rows,
            [
                {
                    "line": 4,
                    "title": "함수 1",
                    "scope": "v1.0",
                    "category": "단위",
                    "assignee": "김 담당",
                    "due_date": "2026-01-15",
                    "external_ref": "",
                    "external_url": "",
                }
            ],
        )
        self.assertEqual(build_rows(GuessTests.GRID, 3, mapping), [])


if __name__ == "__main__":
    unittest.main()
