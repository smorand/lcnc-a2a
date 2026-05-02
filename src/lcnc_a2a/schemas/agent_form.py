"""Validation for the create-agent form."""

from __future__ import annotations

from dataclasses import dataclass

NAME_MAX = 120
DESCRIPTION_MAX = 2000
MODEL_ID_MAX = 200
PROMPT_MAX = 40000
MAX_LOOPS_MIN = 1
MAX_LOOPS_MAX = 50
MAX_TOKENS_MIN = 100
MAX_TOKENS_MAX = 200000
SIMILARITY_MIN = 0.50
SIMILARITY_MAX = 0.99
MAX_STEPS_MIN = 1
MAX_STEPS_MAX = 50

ALLOWED_MODES = frozenset({"simple", "react", "plan_execute"})
ALLOWED_PROVIDERS = frozenset({"openrouter", "openai_compatible"})


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
    system_prompt: str | None
    planner_prompt: str | None
    executor_prompt: str | None
    max_loops: int
    max_tokens: int
    similarity_threshold: float | None
    max_steps: int | None


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


def _default_max_loops(mode: str) -> int:
    return 1 if mode == "plan_execute" else 10


def _default_max_tokens(mode: str) -> int:
    return 16000 if mode == "plan_execute" else 8000


def validate_create_agent_form(
    *,
    name: str,
    description: str,
    mode: str,
    model_provider: str,
    model_endpoint: str,
    model_id: str,
    provider_api_key: str,
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

    if not model_endpoint.strip():
        raise AgentFormError("model_endpoint_required")

    model_id = model_id.strip()
    if not model_id:
        raise AgentFormError("model_id_required")
    if len(model_id) > MODEL_ID_MAX:
        raise AgentFormError("model_id_too_long")

    if require_provider_api_key and not provider_api_key:
        raise AgentFormError("provider_api_key_required")

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

    parsed_max_loops = _coerce_int(max_loops, code="max_loops_invalid") if max_loops else _default_max_loops(mode)
    if not (MAX_LOOPS_MIN <= parsed_max_loops <= MAX_LOOPS_MAX):
        raise AgentFormError("max_loops_out_of_range")

    parsed_max_tokens = _coerce_int(max_tokens, code="max_tokens_invalid") if max_tokens else _default_max_tokens(mode)
    if not (MAX_TOKENS_MIN <= parsed_max_tokens <= MAX_TOKENS_MAX):
        raise AgentFormError("max_tokens_out_of_range")

    parsed_similarity: float | None = None
    if mode == "react":
        parsed_similarity = (
            _coerce_float(similarity_threshold, code="similarity_threshold_invalid") if similarity_threshold else 0.95
        )
        if not (SIMILARITY_MIN <= parsed_similarity <= SIMILARITY_MAX):
            raise AgentFormError("similarity_threshold_out_of_range")

    parsed_max_steps: int | None = None
    if mode == "plan_execute":
        parsed_max_steps = _coerce_int(max_steps, code="max_steps_invalid") if max_steps else 20
        if not (MAX_STEPS_MIN <= parsed_max_steps <= MAX_STEPS_MAX):
            raise AgentFormError("max_steps_out_of_range")

    return AgentFormData(
        name=name,
        description=description.strip() or None,
        mode=mode,
        model_provider=model_provider,
        model_endpoint=model_endpoint.strip(),
        model_id=model_id,
        provider_api_key=provider_api_key,
        system_prompt=parsed_system,
        planner_prompt=parsed_planner,
        executor_prompt=parsed_executor,
        max_loops=parsed_max_loops,
        max_tokens=parsed_max_tokens,
        similarity_threshold=parsed_similarity,
        max_steps=parsed_max_steps,
    )
