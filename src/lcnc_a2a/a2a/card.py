"""Agent Card builder (spec section 4.4.1)."""

from __future__ import annotations

from typing import Any

from lcnc_a2a.models.agent import Agent

A2A_VERSION = "1.0"
PROVIDER_ORGANIZATION = "lcnc-a2a"
PROVIDER_URL = "https://github.com/smorand/lcnc-a2a"


def build_agent_card(*, agent: Agent, base_url: str) -> dict[str, Any]:
    """Return the canonical A2A Agent Card JSON for ``agent``.

    ``base_url`` is the URL prefix for the per-agent endpoint mount, e.g.
    ``https://host/agents/<id>``. The HTTP+JSON/REST binding routes
    (``/message:send``, ``/message:stream``, ``/tasks/...``) are mounted
    directly under it.
    """
    description = agent.description or ""
    return {
        # Core identity (spec 4.4.1).
        "id": str(agent.id),
        "name": agent.name,
        "description": description,
        "version": A2A_VERSION,
        # Publisher information.
        "provider": {
            "organization": PROVIDER_ORGANIZATION,
            "url": PROVIDER_URL,
        },
        # Endpoint declarations: REST is the primary binding we serve.
        "interfaces": [
            {
                "transport": "HTTP+JSON",
                "url": base_url,
            },
        ],
        # Feature flags.
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        # Authentication: bearer API key per agent.
        "securitySchemes": {
            "bearer_api_key": {"type": "http", "scheme": "bearer"},
        },
        "security": [{"bearer_api_key": []}],
        # Default I/O modes.
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        # Skills surface the agent's mode as a tag.
        "skills": [
            {
                "id": f"{agent.id}-default",
                "name": agent.name,
                "description": description,
                "tags": [agent.mode],
                "inputModes": ["text"],
                "outputModes": ["text"],
            }
        ],
    }
