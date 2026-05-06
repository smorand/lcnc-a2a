"""Validation for the create-agent form."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

NAME_MAX = 120
DESCRIPTION_MAX = 2000
MODEL_ID_MAX = 200
PROMPT_MAX = 40000
MAX_LOOPS_MIN = 1
MAX_LOOPS_MAX = 50
MAX_TOKENS_MIN = 100
MAX_TOKENS_MAX = 1_000_000
SIMILARITY_MIN = 0.50
SIMILARITY_MAX = 0.99
MAX_STEPS_MIN = 1
MAX_STEPS_MAX = 50

DEFAULT_MAX_LOOPS = 30
DEFAULT_MAX_TOKENS = 1_000_000
DEFAULT_SIMILARITY = 0.85
DEFAULT_MAX_STEPS = 20

OPENROUTER_ENV_VAR = "OPENROUTER_API_KEY"
ENV_VAR_NAME_MAX = 120
LOCALHOST_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

ALLOWED_MODES = frozenset({"simple", "react", "plan_execute"})
ALLOWED_PROVIDERS = frozenset({"openrouter", "openai_compatible"})
ALLOWED_API_KEY_SOURCES = frozenset({"input", "env_snapshot", "env_dynamic"})

EXTRA_HEADERS_MAX = 5
HEADER_NAME_MAX = 80
HEADER_VALUE_MAX = 500
# Per RFC 7230 ``token`` chars: alphanumerics + a small set of punctuation.
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")


class AgentFormError(ValueError):
    """Validation error carrying a contract error code (e.g., name_required)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AgentFormData:
    """Validated create-agent form payload."""

    name: str
    description: str | None
    mode: str
    model_provider: str
    model_endpoint: str
    model_id: str
    provider_api_key: str
    provider_api_key_env_var: str | None
    system_prompt: str | None
    planner_prompt: str | None
    executor_prompt: str | None
    max_loops: int
    max_tokens: int
    similarity_threshold: float | None
    max_steps: int | None
    extra_headers: dict[str, str] = field(default_factory=dict)


def _coerce_int(raw: str, *, code: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise AgentFormError(code) from exc


def _coerce_float(raw: str, *, code: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise AgentFormError(code) from exc


def _is_localhost_endpoint(endpoint: str) -> bool:
    try:
        host = urlparse(endpoint).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in LOCALHOST_HOSTNAMES


def _validate_extra_headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Coerce up to ``EXTRA_HEADERS_MAX`` (name, value) pairs into a dict.

    Empty pairs (both name and value blank) are silently ignored. Trailing
    whitespace is stripped. Names are validated as RFC 7230 tokens; values
    are stored verbatim (modulo length cap). Duplicate names raise.
    """
    out: dict[str, str] = {}
    for name, value in pairs[:EXTRA_HEADERS_MAX]:
        name = name.strip()
        value = value.strip()
        if not name and not value:
            continue
        if not name:
            raise AgentFormError("extra_header_name_required")
        if len(name) > HEADER_NAME_MAX:
            raise AgentFormError("extra_header_name_too_long")
        if not HEADER_NAME_RE.match(name):
            raise AgentFormError("extra_header_name_invalid")
        if len(value) > HEADER_VALUE_MAX:
            raise AgentFormError("extra_header_value_too_long")
        if name.lower() in (existing.lower() for existing in out):
            raise AgentFormError("extra_header_duplicate")
        out[name] = value
    return out


def _validate_env_var_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise AgentFormError("api_key_env_var_name_required")
    if len(name) > ENV_VAR_NAME_MAX:
        raise AgentFormError("api_key_env_var_name_too_long")
    if not all(c.isalnum() or c == "_" for c in name) or name[0].isdigit():
        raise AgentFormError("api_key_env_var_name_invalid")
    return name


def _resolve_api_key(
    *,
    api_key_source: str,
    provider_api_key: str,
    provider_api_key_env_var_name: str,
    model_provider: str,
    is_localhost: bool,
    require_provider_api_key: bool,
) -> tuple[str, str | None]:
    """Apply the API-key source rules and return ``(plain_key, env_var_name)``."""
    if api_key_source not in ALLOWED_API_KEY_SOURCES:
        raise AgentFormError("api_key_source_invalid")

    if is_localhost:
        return "", None

    if api_key_source == "input":
        if require_provider_api_key and not provider_api_key:
            raise AgentFormError("provider_api_key_required")
        return provider_api_key, None

    # env_snapshot or env_dynamic: pick the env var name.
    if model_provider == "openrouter":
        env_var = OPENROUTER_ENV_VAR
    else:
        env_var = _validate_env_var_name(provider_api_key_env_var_name)

    if api_key_source == "env_snapshot":
        value = os.environ.get(env_var, "")
        if not value:
            raise AgentFormError("api_key_env_not_found")
        return value, None

    return "", env_var


def validate_create_agent_form(
    *,
    name: str,
    description: str,
    mode: str,
    model_provider: str,
    model_endpoint: str,
    model_id: str,
    provider_api_key: str,
    api_key_source: str = "input",
    provider_api_key_env_var_name: str = "",
    extra_header_pairs: list[tuple[str, str]] | None = None,
    system_prompt: str,
    planner_prompt: str,
    executor_prompt: str,
    max_loops: str,
    max_tokens: str,
    similarity_threshold: str,
    max_steps: str,
    require_provider_api_key: bool = True,
) -> AgentFormData:
    """Validate form fields, raising ``AgentFormError`` with a contract error code."""
    name = name.strip()
    if not name:
        raise AgentFormError("name_required")
    if len(name) > NAME_MAX:
        raise AgentFormError("name_too_long")

    if len(description) > DESCRIPTION_MAX:
        raise AgentFormError("description_too_long")

    if mode not in ALLOWED_MODES:
        raise AgentFormError("mode_invalid")

    if model_provider not in ALLOWED_PROVIDERS:
        raise AgentFormError("model_provider_invalid")

    endpoint_stripped = model_endpoint.strip()
    if not endpoint_stripped:
        raise AgentFormError("model_endpoint_required")
    is_localhost = _is_localhost_endpoint(endpoint_stripped)

    model_id = model_id.strip()
    if not model_id:
        raise AgentFormError("model_id_required")
    if len(model_id) > MODEL_ID_MAX:
        raise AgentFormError("model_id_too_long")

    resolved_key, env_var_name = _resolve_api_key(
        api_key_source=api_key_source,
        provider_api_key=provider_api_key,
        provider_api_key_env_var_name=provider_api_key_env_var_name,
        model_provider=model_provider,
        is_localhost=is_localhost,
        require_provider_api_key=require_provider_api_key,
    )

    parsed_extra_headers = _validate_extra_headers(extra_header_pairs or [])

    parsed_system: str | None = None
    parsed_planner: str | None = None
    parsed_executor: str | None = None

    if mode in {"simple", "react"}:
        if not system_prompt.strip():
            raise AgentFormError("prompts_required")
        if len(system_prompt) > PROMPT_MAX:
            raise AgentFormError("prompt_too_long")
        parsed_system = system_prompt
    else:  # plan_execute
        if not planner_prompt.strip() or not executor_prompt.strip():
            raise AgentFormError("prompts_required")
        if len(planner_prompt) > PROMPT_MAX or len(executor_prompt) > PROMPT_MAX:
            raise AgentFormError("prompt_too_long")
        parsed_planner = planner_prompt
        parsed_executor = executor_prompt

    parsed_max_loops = _coerce_int(max_loops, code="max_loops_invalid") if max_loops else DEFAULT_MAX_LOOPS
    if not (MAX_LOOPS_MIN <= parsed_max_loops <= MAX_LOOPS_MAX):
        raise AgentFormError("max_loops_out_of_range")

    parsed_max_tokens = _coerce_int(max_tokens, code="max_tokens_invalid") if max_tokens else DEFAULT_MAX_TOKENS
    if not (MAX_TOKENS_MIN <= parsed_max_tokens <= MAX_TOKENS_MAX):
        raise AgentFormError("max_tokens_out_of_range")

    parsed_similarity: float | None = None
    if mode == "react":
        parsed_similarity = (
            _coerce_float(similarity_threshold, code="similarity_threshold_invalid")
            if similarity_threshold
            else DEFAULT_SIMILARITY
        )
        if not (SIMILARITY_MIN <= parsed_similarity <= SIMILARITY_MAX):
            raise AgentFormError("similarity_threshold_out_of_range")

    parsed_max_steps: int | None = None
    if mode == "plan_execute":
        parsed_max_steps = _coerce_int(max_steps, code="max_steps_invalid") if max_steps else DEFAULT_MAX_STEPS
        if not (MAX_STEPS_MIN <= parsed_max_steps <= MAX_STEPS_MAX):
            raise AgentFormError("max_steps_out_of_range")

    return AgentFormData(
        name=name,
        description=description.strip() or None,
        mode=mode,
        model_provider=model_provider,
        model_endpoint=endpoint_stripped,
        model_id=model_id,
        provider_api_key=resolved_key,
        provider_api_key_env_var=env_var_name,
        extra_headers=parsed_extra_headers,
        system_prompt=parsed_system,
        planner_prompt=parsed_planner,
        executor_prompt=parsed_executor,
        max_loops=parsed_max_loops,
        max_tokens=parsed_max_tokens,
        similarity_threshold=parsed_similarity,
        max_steps=parsed_max_steps,
    )
