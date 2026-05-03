"""An MCP stdio fixture that exits 1 immediately while writing ``boom`` to stderr."""

from __future__ import annotations

import sys


def main() -> None:
    sys.stderr.write("boom\n")
    sys.stderr.flush()
    sys.exit(1)


if __name__ == "__main__":
    main()
