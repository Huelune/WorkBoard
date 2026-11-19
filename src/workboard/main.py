from __future__ import annotations

import argparse

from workboard import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBoard project foundation")
    parser.add_argument("--version", action="version", version=f"WorkBoard {__version__}")
    parser.parse_args()
    print("WorkBoard domain, local storage and services are ready. The UI is not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
