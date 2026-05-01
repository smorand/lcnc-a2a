"""Agent CRUD routes: GET /agents/new, POST /agents, GET /agents/<id>, POST /agents/<id>/keys."""

from __future__ import annotations

import uuid

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
from lcnc_a2a.services.agents import AgentNameTakenError, create_agent, get_agent_for_user
from lcnc_a2a.services.api_keys import create_agent_api_key

ONE_TIME_KEY_COOKIE_PREFIX = "agent_key_once::"

router = APIRouter()


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
        {"user": user, "csrf_token": csrf.generate(), "error": None, "form": {}},
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
        "system_prompt": system_prompt,
        "planner_prompt": planner_prompt,
        "executor_prompt": executor_prompt,
        "max_loops": max_loops,
        "max_tokens": max_tokens,
        "similarity_threshold": similarity_threshold,
        "max_steps": max_steps,
    }

    try:
        data = validate_create_agent_form(
            name=name,
            description=description,
            mode=mode,
            model_provider=model_provider,
            model_endpoint=model_endpoint,
            model_id=model_id,
            provider_api_key=provider_api_key,
            system_prompt=system_prompt,
            planner_prompt=planner_prompt,
            executor_prompt=executor_prompt,
            max_loops=max_loops,
            max_tokens=max_tokens,
            similarity_threshold=similarity_threshold,
            max_steps=max_steps,
        )
    except AgentFormError as exc:
        return _render_form_error(templates, request, csrf, raw_form, exc.code)

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
        return _render_form_error(templates, request, csrf, raw_form, "name_taken")

    _row, plain_key = await create_agent_api_key(db, agent_id=agent.id, label="default")
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

    response: Response = templates.TemplateResponse(
        request,
        "agents/detail.html",
        {
            "user": user,
            "agent": agent,
            "keys": keys,
            "one_time_key": one_time_key,
            "csrf_token": csrf.generate(),
        },
    )
    if one_time_key is not None:
        response.delete_cookie(cookie_name)
    return response


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
) -> Response:
    return templates.TemplateResponse(
        request,
        "agents/new.html",
        {"csrf_token": csrf.generate(), "error": error, "form": form},
        status_code=400,
    )
