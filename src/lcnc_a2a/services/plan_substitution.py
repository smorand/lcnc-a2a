"""Resolve ``${step_N.output}`` placeholders in PE plan step args (FR-016)."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{step_(\d+)\.output\}")


def substitute_args(args: dict[str, Any], step_outputs: dict[int, str]) -> dict[str, Any]:
    """Return ``args`` with all ``${step_N.output}`` references resolved.

    A whole-string placeholder (``"${step_1.output}"``) is replaced by the
    literal output value (preserving the value's type when it is a string).
    Embedded placeholders inside larger strings are interpolated.
    Unknown step references are left untouched (the validator already
    enforced ``depends_on`` correctness).
    """
    walked = _walk(args, step_outputs)
    if isinstance(walked, dict):
        return walked
    return {}


def _walk(value: Any, outputs: dict[int, str]) -> Any:
    if isinstance(value, str):
        return _replace_in_str(value, outputs)
    if isinstance(value, dict):
        return {k: _walk(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, outputs) for v in value]
    return value


def _replace_in_str(s: str, outputs: dict[int, str]) -> str:
    full = _PLACEHOLDER.fullmatch(s)
    if full is not None:
        sid = int(full.group(1))
        if sid in outputs:
            return outputs[sid]
        return s

    def _repl(match: re.Match[str]) -> str:
        sid = int(match.group(1))
        return outputs.get(sid, match.group(0))

    return _PLACEHOLDER.sub(_repl, s)


__all__ = ["substitute_args"]
