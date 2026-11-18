from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Clock:
    """Fixed clock the tests move by hand."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def temp_db_path(case: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(folder.cleanup)
    return Path(folder.name) / "workboard.db"


def write_xlsx(path: Path, cells: dict[str, object], sheet: str = "업무") -> None:
    """A minimal real .xlsx made without Excel. `cells` maps addresses like "B3" to text or numbers."""
    import re
    import zipfile
    from xml.sax.saxutils import escape

    rows: dict[int, list[str]] = {}
    for address, value in sorted(cells.items(), key=lambda item: (int(re.sub(r"\D", "", item[0])), item[0])):
        number = int(re.sub(r"\D", "", address))
        if isinstance(value, (int, float)):
            xml = f'<c r="{address}"><v>{value}</v></c>'
        else:
            xml = f'<c r="{address}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        rows.setdefault(number, []).append(xml)
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(f'<row r="{number}">{"".join(parts)}</row>' for number, parts in sorted(rows.items()))
        + "</sheetData></worksheet>"
    )
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{package}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{package}/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main}" xmlns:r="{rel}">'
            f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{package}/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
