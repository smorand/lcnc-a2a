"""Settings / startup acceptance tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_e2e_096_missing_encryption_key_blocks_startup() -> None:
    """E2E-096: app refuses to start if LCNC_A2A_ENCRYPTION_KEY is unset."""
    env = {k: v for k, v in os.environ.items() if k != "LCNC_A2A_ENCRYPTION_KEY"}
    env.setdefault("LCNC_A2A_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5432/lcnc_a2a")
    env.setdefault("LCNC_A2A_SESSION_SECRET", "test-session-secret")

    result = subprocess.run(
        [sys.executable, "-c", "import lcnc_a2a.main"],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode != 0, (
        f"expected non-zero exit, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "LCNC_A2A_ENCRYPTION_KEY is required" in result.stderr
