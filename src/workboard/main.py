from __future__ import annotations

import argparse

from workboard import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBoard desktop app")
    parser.add_argument("--version", action="version", version=f"WorkBoard {__version__}")
    parser.parse_args()
    try:
        from workboard.ui.app import run
    except ImportError as error:
        if not (error.name or "").startswith("PySide6"):
            raise
        print("PySide6 is not installed. Install it with: python -m pip install PySide6")
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
