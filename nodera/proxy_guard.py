"""Headless recovery entry point; intentionally independent of Qt."""

import sys
from pathlib import Path

from nodera.system_proxy import watch_parent


def main():
    if len(sys.argv) != 5 or sys.argv[1] != "--proxy-guard":
        return 1
    watch_parent(int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
