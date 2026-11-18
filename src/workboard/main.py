from __future__ import annotations

import argparse

from workboard import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBoard desktop app")
    parser.add_argument("--version", action="version", version=f"WorkBoard {__version__}")
    parser.add_argument(
        "--check-excel",
        action="store_true",
        help="pick a workbook and report only whether this PC's Excel could read it (counts, no content)",
    )
    options = parser.parse_args()
    try:
        from workboard.ui.app import check_excel, run
    except ImportError as error:
        if not (error.name or "").startswith("PySide6"):
            raise
        print("PySide6 is not installed. Install it with: python -m pip install PySide6")
        return 1
    return check_excel() if options.check_excel else run()


if __name__ == "__main__":
    raise SystemExit(main())
