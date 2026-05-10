"""Agent CRUD routes: GET /agents/new, POST /agents, GET /agents/<id>, POST /agents/<id>/keys."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.middleware import fetch_current_user
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.deps import get_crypto, get_csrf_manager, get_db, get_templates
from lcnc_a2a.models.agent_api_key import AgentApiKey
from lcnc_a2a.models.user import User
from lcnc_a2a.schemas.agent_form import AgentFormError, validate_create_agent_form
from lcnc_a2a.services.agents import (
    AgentNameTakenError,
    create_agent,
    delete_agent_cascade,
    get_agent_for_user,
    set_status,
    update_agent,
)
from lcnc_a2a.services.api_keys import create_agent_api_key
from lcnc_a2a.services.mcp_catalog import CATALOG, get_entry
from lcnc_a2a.services.mcp_discovery import create_server, list_servers_for_agent
from lcnc_a2a.services.runs import list_running_run_ids_for_agent

ONE_TIME_KEY_COOKIE_PREFIX = "agent_key_once::"
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_EXTRA_HEADERS_SLOTS = 5


async def _extract_extra_header_pairs(request: Request) -> list[tuple[str, str]]:
    """Read up to 5 ``(header_name_X, header_value_X)`` pairs off the form."""
    form = await request.form()
    pairs: list[tuple[str, str]] = []
    for i in range(_EXTRA_HEADERS_SLOTS):
        name = form.get(f"header_name_{i}", "")
        value = form.get(f"header_value_{i}", "")
        pairs.append(
            (
                name if isinstance(name, str) else "",
                value if isinstance(value, str) else "",
            )
        )
    return pairs


async def _extract_mcp_preset_ids(request: Request) -> list[str]:
    """Read all ``mcp_preset_ids`` form values, keep only ids known to the catalog."""
    form = await request.form()
    raw = form.getlist("mcp_preset_ids") if hasattr(form, "getlist") else []
    seen: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            continue
        if value and value not in seen and get_entry(value) is not None:
            seen.append(value)
    return seen


def _attached_catalog_ids(mcp_rows: Sequence[object]) -> list[str]:
    """Return catalog ids whose ``command``/``url`` matches an attached server.

    Used by the edit Tools step to mark catalog cards as already-attached
    instead of available, which prevents the user from creating duplicates.
    """
    attached: list[str] = []
    for entry in CATALOG:
        for row in mcp_rows:
            if getattr(row, "transport", None) != entry.transport:
                continue
            if entry.transport == "stdio":
                if entry.command and getattr(row, "command", None) == entry.command:
                    attached.append(entry.id)
                    break
            else:
                if entry.url and getattr(row, "url", None) == entry.url:
                    attached.append(entry.id)
                    break
    return attached


_PRESET_LABELS = {
    "openrouter": "OpenRouter",
    "localhost": "Localhost (mlx_lm)",
    "other": "Other (OpenAI-compatible)",
}

router = APIRouter()


def _infer_model_preset(provider: str, endpoint: str) -> str:
    """Map ``(model_provider, model_endpoint)`` back to a UI preset name."""
    if provider == "openrouter":
        return "openrouter"
    try:
        host = urlparse(endpoint).hostname
    except ValueError:
        host = None
    if host is not None and host.lower() in _LOCALHOST_HOSTS:
        return "localhost"
    return "other"


def _infer_api_key_source(agent_provider_api_key_env_var: str | None) -> str:
    return "env_dynamic" if agent_provider_api_key_env_var else "input"


@router.get("/agents/new")
async def new_agent_form(
    request: Request,
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the create-agent form."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "agents/new.html",
        {
            "user": user,
            "csrf_token": csrf.generate(),
            "error": None,
            "form": {},
            "model_preset": "openrouter",
            "mcp_catalog": CATALOG,
            "mcp_preset_ids": [],
        },
    )


@router.post("/agents")
async def create_agent_submit(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    mode: str = Form(""),
    model_provider: str = Form(""),
    model_endpoint: str = Form(""),
    model_id: str = Form(""),
    provider_api_key: str = Form(""),
    api_key_source: str = Form("input"),
    provider_api_key_env_var_name: str = Form(""),
    system_prompt: str = Form(""),
    planner_prompt: str = Form(""),
    executor_prompt: str = Form(""),
    max_loops: str = Form(""),
    max_tokens: str = Form(""),
    similarity_threshold: str = Form(""),
    max_steps: str = Form(""),
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Validate, create the agent, mint its first API key, redirect to detail."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    raw_form = {
        "name": name,
        "description": description,
        "mode": mode,
        "model_provider": model_provider,
        "model_endpoint": model_endpoint,
        "model_id": model_id,
        "api_key_source": api_key_source,
        "provider_api_key_env_var_name": provider_api_key_env_var_name,
        "system_prompt": system_prompt,
        "planner_prompt": planner_prompt,
        "executor_prompt": executor_prompt,
        "max_loops": max_loops,
        "max_tokens": max_tokens,
        "similarity_threshold": similarity_threshold,
        "max_steps": max_steps,
    }

    extra_header_pairs = await _extract_extra_header_pairs(request)
    selected_preset_ids = await _extract_mcp_preset_ids(request)

    try:
        data = validate_create_agent_form(
            name=name,
            description=description,
            mode=mode,
            model_provider=model_provider,
            model_endpoint=model_endpoint,
            model_id=model_id,
            provider_api_key=provider_api_key,
            api_key_source=api_key_source,
            provider_api_key_env_var_name=provider_api_key_env_var_name,
            extra_header_pairs=extra_header_pairs,
            system_prompt=system_prompt,
            planner_prompt=planner_prompt,
            executor_prompt=executor_prompt,
            max_loops=max_loops,
            max_tokens=max_tokens,
            similarity_threshold=similarity_threshold,
            max_steps=max_steps,
        )
    except AgentFormError as exc:
        return _render_form_error(templates, request, csrf, raw_form, exc.code, selected_preset_ids)

    try:
        agent = await create_agent(
            db,
            user_id=user.id,
            name=data.name,
            description=data.description,
            mode=data.mode,
            model_provider=data.model_provider,
            model_endpoint=data.model_endpoint,
            model_id=data.model_id,
            provider_api_key=data.provider_api_key,
            provider_api_key_env_var=data.provider_api_key_env_var,
            extra_headers=data.extra_headers,
            crypto=crypto,
            system_prompt=data.system_prompt,
            planner_prompt=data.planner_prompt,
            executor_prompt=data.executor_prompt,
            max_loops=data.max_loops,
            max_tokens=data.max_tokens,
            similarity_threshold=data.similarity_threshold,
            max_steps=data.max_steps,
        )
    except AgentNameTakenError:
        return _render_form_error(templates, request, csrf, raw_form, "name_taken", selected_preset_ids)

    _row, plain_key = await create_agent_api_key(db, agent_id=agent.id, label="default")

    for preset_id in selected_preset_ids:
        entry = get_entry(preset_id)
        if entry is None:
            continue
        await create_server(
            db,
            agent_id=agent.id,
            transport=entry.transport,
            command=entry.command,
            env=dict(entry.env) if entry.env else None,
            cwd=None,
            url=entry.url,
            headers=dict(entry.headers) if entry.headers else None,
            crypto=crypto,
        )

    await db.commit()

    response: Response = RedirectResponse(url=f"/agents/{agent.id}", status_code=302)
    response.set_cookie(
        key=f"{ONE_TIME_KEY_COOKIE_PREFIX}{agent.id}",
        value=plain_key,
        httponly=True,
        samesite="lax",
        max_age=300,
    )
    return response


@router.get("/agents/{agent_id}")
async def agent_detail(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render an agent detail page; show the one-time plain key if just created."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    keys_result = await db.execute(
        select(AgentApiKey).where(AgentApiKey.agent_id == agent.id).order_by(AgentApiKey.created_at)
    )
    keys = list(keys_result.scalars().all())

    cookie_name = f"{ONE_TIME_KEY_COOKIE_PREFIX}{agent.id}"
    one_time_key = request.cookies.get(cookie_name)

    mcp_rows = await list_servers_for_agent(db, agent_id=agent.id)
    mcp_servers: list[dict[str, object]] = []
    for row in mcp_rows:
        cache = row.tools_cache or {}
        tools = cache.get("tools") if isinstance(cache, dict) else None
        mcp_servers.append({"row": row, "tool_count": len(tools) if isinstance(tools, list) else 0})

    preset = _infer_model_preset(agent.model_provider, agent.model_endpoint)
    response: Response = templates.TemplateResponse(
        request,
        "agents/detail.html",
        {
            "user": user,
            "agent": agent,
            "keys": keys,
            "one_time_key": one_time_key,
            "csrf_token": csrf.generate(),
            "model_preset_label": _PRESET_LABELS.get(preset, preset),
            "mcp_servers": mcp_servers,
        },
    )
    if one_time_key is not None:
        response.delete_cookie(cookie_name)
    return response


@router.get("/agents/{agent_id}/edit")
async def edit_agent_form(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the prefilled edit-agent form."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    extra_headers = _decode_extra_headers(agent, crypto)
    form = {
        "name": agent.name,
        "description": agent.description or "",
        "mode": agent.mode,
        "model_provider": agent.model_provider,
        "model_endpoint": agent.model_endpoint,
        "model_id": agent.model_id,
        "api_key_source": _infer_api_key_source(agent.provider_api_key_env_var),
        "provider_api_key_env_var_name": agent.provider_api_key_env_var or "",
        "system_prompt": agent.system_prompt or "",
        "planner_prompt": agent.planner_prompt or "",
        "executor_prompt": agent.executor_prompt or "",
        "max_loops": str(agent.max_loops),
        "max_tokens": str(agent.max_tokens),
        "similarity_threshold": "" if agent.similarity_threshold is None else str(agent.similarity_threshold),
        "max_steps": "" if agent.max_steps is None else str(agent.max_steps),
        "extra_header_slots": _padded_header_slots(extra_headers),
    }

    mcp_rows = await list_servers_for_agent(db, agent_id=agent.id)
    mcp_servers: list[dict[str, object]] = []
    for row in mcp_rows:
        cache = row.tools_cache or {}
        tools = cache.get("tools") if isinstance(cache, dict) else None
        mcp_servers.append({"row": row, "tool_count": len(tools) if isinstance(tools, list) else 0})
    attached_preset_ids = _attached_catalog_ids(mcp_rows)

    return templates.TemplateResponse(
        request,
        "agents/edit.html",
        {
            "user": user,
            "agent": agent,
            "form": form,
            "model_preset": _infer_model_preset(agent.model_provider, agent.model_endpoint),
            "csrf_token": csrf.generate(),
            "error": None,
            "mcp_catalog": CATALOG,
            "mcp_servers": mcp_servers,
            "mcp_attached_preset_ids": attached_preset_ids,
        },
    )


def _decode_extra_headers(agent: AgentApiKey | object, crypto: CryptoService) -> dict[str, str]:
    """Decrypt the agent's extra HTTP headers JSON, or return ``{}``."""
    enc = getattr(agent, "provider_extra_headers_enc", None)
    if not enc:
        return {}
    import json

    try:
        decoded = crypto.decrypt(enc).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}


def _padded_header_slots(headers: dict[str, str]) -> list[tuple[str, str]]:
    """Pad the headers dict to exactly ``_EXTRA_HEADERS_SLOTS`` (name, value) tuples."""
    items = list(headers.items())[:_EXTRA_HEADERS_SLOTS]
    while len(items) < _EXTRA_HEADERS_SLOTS:
        items.append(("", ""))
    return items


@router.post("/agents/{agent_id}")
async def update_or_delete_agent(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Update or (when ``_method=DELETE``) delete an agent (HTML form)."""
    form = await request.form()

    def _f(name: str) -> str:
        value = form.get(name, "")
        return value if isinstance(value, str) else ""

    name = _f("name")
    description = _f("description")
    mode = _f("mode")
    model_provider = _f("model_provider")
    model_endpoint = _f("model_endpoint")
    model_id = _f("model_id")
    provider_api_key = _f("provider_api_key")
    api_key_source = _f("api_key_source") or "input"
    provider_api_key_env_var_name = _f("provider_api_key_env_var_name")
    system_prompt = _f("system_prompt")
    planner_prompt = _f("planner_prompt")
    executor_prompt = _f("executor_prompt")
    max_loops = _f("max_loops")
    max_tokens = _f("max_tokens")
    similarity_threshold = _f("similarity_threshold")
    max_steps = _f("max_steps")
    csrf_token = _f("csrf_token")
    method_override = _f("_method")

    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    if method_override.upper() == "DELETE":
        registry = getattr(request.app.state, "cancellation_registry", None)
        if registry is not None:
            running = await list_running_run_ids_for_agent(db, agent_id=agent.id)
            registry.cancel_all_for_agent(running)
        await delete_agent_cascade(db, agent=agent)
        await db.commit()
        return RedirectResponse(url="/agents", status_code=302)

    raw_form = {
        "name": name,
        "description": description,
        "mode": mode,
        "model_provider": model_provider,
        "model_endpoint": model_endpoint,
        "model_id": model_id,
        "api_key_source": api_key_source,
        "provider_api_key_env_var_name": provider_api_key_env_var_name,
        "system_prompt": system_prompt,
        "planner_prompt": planner_prompt,
        "executor_prompt": executor_prompt,
        "max_loops": max_loops,
        "max_tokens": max_tokens,
        "similarity_threshold": similarity_threshold,
        "max_steps": max_steps,
    }

    extra_header_pairs = await _extract_extra_header_pairs(request)
    selected_preset_ids = await _extract_mcp_preset_ids(request)

    try:
        data = validate_create_agent_form(
            name=name,
            description=description,
            mode=mode,
            model_provider=model_provider,
            model_endpoint=model_endpoint,
            model_id=model_id,
            provider_api_key=provider_api_key,
            api_key_source=api_key_source,
            provider_api_key_env_var_name=provider_api_key_env_var_name,
            extra_header_pairs=extra_header_pairs,
            system_prompt=system_prompt,
            planner_prompt=planner_prompt,
            executor_prompt=executor_prompt,
            max_loops=max_loops,
            max_tokens=max_tokens,
            similarity_threshold=similarity_threshold,
            max_steps=max_steps,
            require_provider_api_key=False,
        )
    except AgentFormError as exc:
        return _render_edit_form_error(templates, request, csrf, agent, raw_form, exc.code, selected_preset_ids)

    try:
        await update_agent(
            db,
            agent=agent,
            name=data.name,
            description=data.description,
            mode=data.mode,
            model_provider=data.model_provider,
            model_endpoint=data.model_endpoint,
            model_id=data.model_id,
            provider_api_key=data.provider_api_key,
            provider_api_key_env_var=data.provider_api_key_env_var,
            extra_headers=data.extra_headers,
            crypto=crypto,
            system_prompt=data.system_prompt,
            planner_prompt=data.planner_prompt,
            executor_prompt=data.executor_prompt,
            max_loops=data.max_loops,
            max_tokens=data.max_tokens,
            similarity_threshold=data.similarity_threshold,
            max_steps=data.max_steps,
        )
    except AgentNameTakenError:
        return _render_edit_form_error(templates, request, csrf, agent, raw_form, "name_taken", selected_preset_ids)

    if selected_preset_ids:
        existing_rows = await list_servers_for_agent(db, agent_id=agent.id)
        already_attached = set(_attached_catalog_ids(existing_rows))
        for preset_id in selected_preset_ids:
            if preset_id in already_attached:
                continue
            entry = get_entry(preset_id)
            if entry is None:
                continue
            await create_server(
                db,
                agent_id=agent.id,
                transport=entry.transport,
                command=entry.command,
                env=dict(entry.env) if entry.env else None,
                cwd=None,
                url=entry.url,
                headers=dict(entry.headers) if entry.headers else None,
                crypto=crypto,
            )

    await db.commit()
    return RedirectResponse(url=f"/agents/{agent.id}", status_code=302)


@router.post("/agents/{agent_id}/start")
async def start_agent(
    request: Request,
    agent_id: uuid.UUID,
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
) -> Response:
    """Flip ``agents.status`` to ``started``."""
    return await _toggle_status(request, agent_id, csrf_token, "started", db, user, csrf)


@router.post("/agents/{agent_id}/stop")
async def stop_agent(
    request: Request,
    agent_id: uuid.UUID,
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
) -> Response:
    """Flip ``agents.status`` to ``stopped``."""
    return await _toggle_status(request, agent_id, csrf_token, "stopped", db, user, csrf)


async def _toggle_status(
    request: Request,
    agent_id: uuid.UUID,
    csrf_token: str,
    target_status: str,
    db: AsyncSession,
    user: User | None,
    csrf: CSRFManager,
) -> Response:
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    await set_status(db, agent=agent, status=target_status)
    await db.commit()

    referer = request.headers.get("referer") or "/agents"
    return RedirectResponse(url=referer, status_code=302)


@router.post("/agents/{agent_id}/keys")
async def create_additional_key(
    request: Request,
    agent_id: uuid.UUID,
    label: str = Form("default"),
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Mint another API key for an existing agent. Returns an HTML partial."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    label = label.strip() or "default"
    if len(label) > 60:
        return Response(content="label_too_long", status_code=400)

    _row, plain_key = await create_agent_api_key(db, agent_id=agent.id, label=label)
    await db.commit()

    return templates.TemplateResponse(
        request,
        "agents/partials/api_key_once.html",
        {"plain_key": plain_key, "label": label},
    )


def _render_form_error(
    templates: Jinja2Templates,
    request: Request,
    csrf: CSRFManager,
    form: dict[str, str],
    error: str,
    mcp_preset_ids: list[str] | None = None,
) -> Response:
    preset = _infer_model_preset(
        form.get("model_provider", "openrouter"),
        form.get("model_endpoint", ""),
    )
    return templates.TemplateResponse(
        request,
        "agents/new.html",
        {
            "csrf_token": csrf.generate(),
            "error": error,
            "form": form,
            "model_preset": preset,
            "mcp_catalog": CATALOG,
            "mcp_preset_ids": mcp_preset_ids or [],
        },
        status_code=400,
    )


def _render_edit_form_error(
    templates: Jinja2Templates,
    request: Request,
    csrf: CSRFManager,
    agent: object,
    form: dict[str, str],
    error: str,
    mcp_preset_ids: list[str] | None = None,
) -> Response:
    preset = _infer_model_preset(form.get("model_provider", "openrouter"), form.get("model_endpoint", ""))
    return templates.TemplateResponse(
        request,
        "agents/edit.html",
        {
            "csrf_token": csrf.generate(),
            "error": error,
            "form": form,
            "agent": agent,
            "model_preset": preset,
            "mcp_catalog": CATALOG,
            "mcp_servers": [],
            "mcp_attached_preset_ids": [],
            "mcp_preset_ids": mcp_preset_ids or [],
        },
        status_code=400,
    )
