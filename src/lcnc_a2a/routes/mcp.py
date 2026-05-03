"""HTTP routes for attaching/configuring/discovering MCP servers per agent."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.middleware import fetch_current_user
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.deps import get_crypto, get_csrf_manager, get_db, get_templates
from lcnc_a2a.mcp_client.errors import McpDiscoveryError, McpDiscoveryTimeoutError
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_mcp_server import AgentMcpServer
from lcnc_a2a.models.user import User
from lcnc_a2a.services.agents import get_agent_for_user
from lcnc_a2a.services.mcp_discovery import (
    SUPPORTED_TRANSPORTS,
    InvalidMcpFormError,
    TransportRediscoveryRequiredError,
    create_server,
    delete_server,
    get_server_for_agent,
    list_servers_for_agent,
    masked_env_keys,
    masked_header_keys,
    parse_json_map,
    persist_discovery_result,
    run_discovery,
    update_server,
)

router = APIRouter()


def _list_entries(servers: list[AgentMcpServer], /) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for row in servers:
        cache = row.tools_cache or {}
        tools = cache.get("tools") if isinstance(cache, dict) else None
        entries.append({"row": row, "tool_count": len(tools) if isinstance(tools, list) else 0})
    return entries


@router.get("/agents/{agent_id}/mcp")
async def list_mcp_servers(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the list-of-servers HTML partial for an agent."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)
    rows = await list_servers_for_agent(db, agent_id=agent.id)
    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_list.html",
        {"agent": agent, "servers": _list_entries(rows)},
    )


@router.get("/agents/{agent_id}/mcp/new")
async def new_mcp_form(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the empty add-MCP-server form."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)
    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_form.html",
        {
            "agent": agent,
            "row": None,
            "form": {"transport": "stdio"},
            "error": None,
            "csrf_token": csrf.generate(),
            "env_keys": [],
            "header_keys": [],
        },
    )


@router.post("/agents/{agent_id}/mcp")
async def create_mcp_server(
    request: Request,
    agent_id: uuid.UUID,
    transport: str = Form(""),
    command: str = Form(""),
    env: str = Form(""),
    cwd: str = Form(""),
    url: str = Form(""),
    headers: str = Form(""),
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Persist a new MCP server. Discovery is NOT run here; user must call /discover."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    raw_form = {
        "transport": transport,
        "command": command,
        "env": env,
        "cwd": cwd,
        "url": url,
        "headers": headers,
    }
    try:
        env_map = parse_json_map(env, field="env") if transport == "stdio" else {}
        header_map = parse_json_map(headers, field="headers") if transport == "streamable_http" else {}
        if transport not in SUPPORTED_TRANSPORTS:
            raise InvalidMcpFormError("transport_invalid")
        row = await create_server(
            db,
            agent_id=agent.id,
            transport=transport,
            command=command if transport == "stdio" else None,
            env=env_map if transport == "stdio" else None,
            cwd=(cwd or None) if transport == "stdio" else None,
            url=url if transport == "streamable_http" else None,
            headers=header_map if transport == "streamable_http" else None,
            crypto=crypto,
        )
    except InvalidMcpFormError as exc:
        return _render_form_error(templates, request, csrf, agent, raw_form, exc.code)

    await db.commit()

    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_form.html",
        {
            "agent": agent,
            "row": row,
            "form": _form_from_row(row, crypto),
            "error": None,
            "csrf_token": csrf.generate(),
            "env_keys": masked_env_keys(row, crypto),
            "header_keys": masked_header_keys(row, crypto),
        },
    )


@router.get("/agents/{agent_id}/mcp/{server_id}")
async def view_mcp_server(
    request: Request,
    agent_id: uuid.UUID,
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the prefilled edit form for an existing MCP server (env/headers MASKED)."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)
    row = await get_server_for_agent(db, server_id=server_id, agent_id=agent.id)
    if row is None:
        return Response(content="not_found", status_code=404)
    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_form.html",
        {
            "agent": agent,
            "row": row,
            "form": _form_from_row(row, crypto, mask_secrets=True),
            "error": None,
            "csrf_token": csrf.generate(),
            "env_keys": masked_env_keys(row, crypto),
            "header_keys": masked_header_keys(row, crypto),
        },
    )


@router.post("/agents/{agent_id}/mcp/{server_id}")
async def update_or_delete_mcp_server(
    request: Request,
    agent_id: uuid.UUID,
    server_id: uuid.UUID,
    transport: str = Form(""),
    command: str = Form(""),
    env: str = Form(""),
    cwd: str = Form(""),
    url: str = Form(""),
    headers: str = Form(""),
    csrf_token: str = Form(""),
    method_override: str = Form("", alias="_method"),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Update an MCP server config (or delete it via ``_method=DELETE``)."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)
    row = await get_server_for_agent(db, server_id=server_id, agent_id=agent.id)
    if row is None:
        return Response(content="not_found", status_code=404)

    if method_override.upper() == "DELETE":
        await delete_server(db, row=row)
        await db.commit()
        return RedirectResponse(url=f"/agents/{agent.id}", status_code=302)

    raw_form = {
        "transport": transport,
        "command": command,
        "env": env,
        "cwd": cwd,
        "url": url,
        "headers": headers,
    }

    try:
        env_map = parse_json_map(env, field="env") if transport == "stdio" else None
        header_map = parse_json_map(headers, field="headers") if transport == "streamable_http" else None
        await update_server(
            db,
            row=row,
            transport=transport,
            command=command if transport == "stdio" else None,
            env=env_map,
            cwd=(cwd or None) if transport == "stdio" else None,
            url=url if transport == "streamable_http" else None,
            headers=header_map,
            crypto=crypto,
        )
    except TransportRediscoveryRequiredError:
        await db.rollback()
        return Response(content="rediscovery_required", status_code=409)
    except InvalidMcpFormError as exc:
        await db.rollback()
        return _render_form_error(templates, request, csrf, agent, raw_form, exc.code, row=row, crypto=crypto)

    await db.commit()
    return RedirectResponse(url=f"/agents/{agent.id}/mcp/{row.id}", status_code=302)


@router.post("/agents/{agent_id}/mcp/{server_id}/discover")
async def discover_mcp_server(
    request: Request,
    agent_id: uuid.UUID,
    server_id: uuid.UUID,
    transport: str = Form(""),
    command: str = Form(""),
    env: str = Form(""),
    cwd: str = Form(""),
    url: str = Form(""),
    headers: str = Form(""),
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    csrf: CSRFManager = Depends(get_csrf_manager),
    crypto: CryptoService = Depends(get_crypto),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Run discovery against the (optionally overridden) MCP-server config.

    On success the row's transport/config + tools_cache + discovered_at are persisted.
    """
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)
    row = await get_server_for_agent(db, server_id=server_id, agent_id=agent.id)
    if row is None:
        return Response(content="not_found", status_code=404)

    # Override the row in-memory with the new config if any field was supplied.
    has_override = any([transport, command, env, cwd, url, headers])
    if has_override:
        try:
            chosen = transport or row.transport
            if chosen not in SUPPORTED_TRANSPORTS:
                raise InvalidMcpFormError("transport_invalid")
            if chosen == "stdio":
                row.transport = "stdio"
                row.command = command or row.command
                if env:
                    row.env_enc = _encrypt_via_service(env, "env", crypto)
                if cwd:
                    row.cwd = cwd
                row.url = None
                row.headers_enc = None
            else:
                row.transport = "streamable_http"
                row.url = url or row.url
                if headers:
                    row.headers_enc = _encrypt_via_service(headers, "headers", crypto)
                row.command = None
                row.env_enc = None
                row.cwd = None
        except InvalidMcpFormError as exc:
            await db.rollback()
            return Response(content=exc.code, status_code=400)

    try:
        tools = await run_discovery(row=row, crypto=crypto)
    except McpDiscoveryTimeoutError as exc:
        await db.rollback()
        body = "mcp_discovery_timeout"
        if exc.detail:
            body = f"{body}\n{exc.detail}"
        return Response(content=body, status_code=422)
    except McpDiscoveryError as exc:
        await db.rollback()
        body = "mcp_discovery_failed"
        if exc.detail:
            body = f"{body}\n{exc.detail}"
        return Response(content=body, status_code=422)

    await persist_discovery_result(db, row=row, tools=tools)
    await db.commit()

    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_tools.html",
        {"row": row, "tools": tools, "discovered_at": row.discovered_at},
    )


def _form_from_row(row: AgentMcpServer, crypto: CryptoService, *, mask_secrets: bool = True) -> dict[str, str]:
    """Build a form-shape dict from a row. With ``mask_secrets=True`` env/headers are blank."""
    if row.transport == "stdio":
        return {
            "transport": "stdio",
            "command": row.command or "",
            "env": "",
            "cwd": row.cwd or "",
            "url": "",
            "headers": "",
        }
    return {
        "transport": "streamable_http",
        "command": "",
        "env": "",
        "cwd": "",
        "url": row.url or "",
        "headers": "",
    }


def _encrypt_via_service(raw_json: str, field: str, crypto: CryptoService) -> bytes:
    """Parse + encrypt a JSON map field; raises ``InvalidMcpFormError`` on bad JSON."""
    import json

    parsed = parse_json_map(raw_json, field=field)
    return crypto.encrypt(json.dumps(parsed, sort_keys=True).encode("utf-8"))


def _render_form_error(
    templates: Jinja2Templates,
    request: Request,
    csrf: CSRFManager,
    agent: Agent,
    form: dict[str, str],
    error: str,
    *,
    row: AgentMcpServer | None = None,
    crypto: CryptoService | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "agents/partials/mcp_form.html",
        {
            "agent": agent,
            "row": row,
            "form": form,
            "error": error,
            "csrf_token": csrf.generate(),
            "env_keys": masked_env_keys(row, crypto) if (row and crypto) else [],
            "header_keys": masked_header_keys(row, crypto) if (row and crypto) else [],
        },
        status_code=400,
    )
