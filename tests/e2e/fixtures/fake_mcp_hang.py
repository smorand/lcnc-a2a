"""An MCP stdio fixture that connects then sleeps forever. Used to test the 10s timeout."""

from __future__ import annotations

import sys
import time


def main() -> None:
    # Drain stdin a tiny bit so the process visibly engages, then hang indefinitely.
    sys.stdin.readable()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
