"""Translate MCP ``tools_cache`` entries to OpenAI ``tools[]`` format."""

from __future__ import annotations

from typing import Any


def to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert MCP-style tool descriptors to the OpenAI ``tools`` format."""
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name", "")
        if not name:
            continue
        schema = tool.get("input_schema") or {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": schema,
                },
            }
        )
    return out
