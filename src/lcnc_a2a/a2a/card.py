"""Agent Card builder (FR-011)."""

from __future__ import annotations

from typing import Any

from lcnc_a2a.models.agent import Agent


def build_agent_card(*, agent: Agent, base_url: str) -> dict[str, Any]:
    """Return the canonical Agent Card JSON for ``agent``."""
    description = agent.description or ""
    return {
        "name": agent.name,
        "description": description,
        "version": "1.0",
        "url": f"{base_url}/agents/{agent.id}",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "securitySchemes": {"bearer_api_key": {"type": "http", "scheme": "bearer"}},
        "skills": [
            {
                "name": agent.name,
                "description": description,
                "tags": [agent.mode],
            }
        ],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }
