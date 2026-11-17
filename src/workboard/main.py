from __future__ import annotations

import argparse

from workboard import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBoard project foundation")
    parser.add_argument("--version", action="version", version=f"WorkBoard {__version__}")
    parser.parse_args()
    print("WorkBoard foundation is ready. UI and storage are not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
