"""Runs history & per-run trace UI (US-008).

Three endpoints, all owner-checked:

- GET ``/agents/<id>/runs`` -> 100 most recent runs for the agent.
- GET ``/agents/<id>/runs/<run_id>`` -> HTMX expand partial with the run's steps.
- GET ``/agents/<id>/runs/<run_id>/steps/<step_id>/full`` -> full tool I/O payload.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.middleware import fetch_current_user
from lcnc_a2a.deps import get_db, get_templates
from lcnc_a2a.models.user import User
from lcnc_a2a.services.agents import get_agent_for_user
from lcnc_a2a.services.runs_view import (
    PAYLOAD_TRUNCATE_THRESHOLD,
    SUMMARY_LIMIT,
    get_run_for_agent,
    get_step_for_run,
    list_recent_runs,
    list_steps,
    serialize_payload,
    summarize_final_answer,
    truncate_payload,
)

router = APIRouter()


@router.get("/agents/{agent_id}/runs")
async def runs_list(
    request: Request,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the 100 most recent runs for ``agent_id``."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    runs = await list_recent_runs(db, agent_id=agent.id)
    rows = []
    for run in runs:
        summary, summary_truncated = summarize_final_answer(run.final_answer)
        rows.append(
            {
                "run": run,
                "summary": summary,
                "summary_truncated": summary_truncated,
            },
        )

    return templates.TemplateResponse(
        request,
        "agents/runs_list.html",
        {
            "user": user,
            "agent": agent,
            "rows": rows,
            "summary_limit": SUMMARY_LIMIT,
        },
    )


@router.get("/agents/{agent_id}/runs/{run_id}")
async def run_expand(
    request: Request,
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render an HTMX partial with the steps of a single run."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    run = await get_run_for_agent(db, agent_id=agent.id, run_id=run_id)
    if run is None:
        return Response(content="not_found", status_code=404)

    steps = await list_steps(db, run_id=run.id)
    step_rows = []
    for step in steps:
        args_text = serialize_payload(step.tool_args_json)
        result_text = serialize_payload(step.tool_result_json)
        args_display, args_truncated = truncate_payload(args_text)
        result_display, result_truncated = truncate_payload(result_text)
        step_rows.append(
            {
                "step": step,
                "args_display": args_display,
                "args_truncated": args_truncated,
                "args_present": bool(args_text),
                "result_display": result_display,
                "result_truncated": result_truncated,
                "result_present": bool(result_text),
            },
        )

    return templates.TemplateResponse(
        request,
        "agents/partials/run_expand.html",
        {
            "agent": agent,
            "run": run,
            "step_rows": step_rows,
            "payload_threshold": PAYLOAD_TRUNCATE_THRESHOLD,
        },
    )


@router.get("/agents/{agent_id}/runs/{run_id}/steps/{step_id}/full")
async def step_full_payload(
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
) -> Response:
    """Stream the FULL JSON payload (tool_result_json, falling back to tool_args_json)."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    agent = await get_agent_for_user(db, agent_id=agent_id, user_id=user.id)
    if agent is None:
        return Response(content="not_found", status_code=404)

    run = await get_run_for_agent(db, agent_id=agent.id, run_id=run_id)
    if run is None:
        return Response(content="not_found", status_code=404)

    step = await get_step_for_run(db, run_id=run.id, step_id=step_id)
    if step is None:
        return Response(content="not_found", status_code=404)

    payload = step.tool_result_json if step.tool_result_json is not None else step.tool_args_json
    body = serialize_payload(payload)
    return PlainTextResponse(content=body, status_code=200)
