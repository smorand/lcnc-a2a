"""Dashboard route: GET /agents listing the user's agents with 30-day metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.middleware import fetch_current_user
from lcnc_a2a.deps import get_db, get_settings, get_templates
from lcnc_a2a.models.user import User
from lcnc_a2a.services.agents import list_agents_with_metrics
from lcnc_a2a.settings import Settings

router = APIRouter()


@router.get("/agents")
async def agents_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(fetch_current_user),
    templates: Jinja2Templates = Depends(get_templates),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Render the agent dashboard for the current user."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    rows = await list_agents_with_metrics(
        db,
        user_id=user.id,
        window_days=settings.metrics_window_days,
    )
    response: Response = templates.TemplateResponse(
        request,
        "agents/list.html",
        {"user": user, "rows": rows},
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def root() -> Response:
    """Send unauthenticated users straight to /login."""
    return RedirectResponse(url="/login", status_code=302)
