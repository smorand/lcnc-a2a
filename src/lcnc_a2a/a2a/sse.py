"""SSE streaming helpers."""

from __future__ import annotations

import json
from typing import Any


def encode_sse_event(payload: dict[str, Any]) -> bytes:
    """Encode a JSON payload as a single SSE ``data:`` event."""
    return f"data: {json.dumps(payload)}\n\n".encode()
