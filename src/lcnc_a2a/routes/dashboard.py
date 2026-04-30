"""Dashboard route (placeholder for US-002)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.session import SESSION_COOKIE_NAME, SessionManager
from lcnc_a2a.deps import get_db, get_session_manager, get_templates

router = APIRouter()


@router.get("/agents")
async def agents_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sessions: SessionManager = Depends(get_session_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Empty dashboard. Redirects to /login if no valid session."""
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_cookie is None:
        return RedirectResponse(url="/login", status_code=302)
    session_id = sessions.verify(raw_cookie)
    if session_id is None:
        return RedirectResponse(url="/login", status_code=302)
    user = await sessions.lookup(db, session_id)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    response: Response = templates.TemplateResponse(
        request,
        "agents.html",
        {"user": user, "agents": []},
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def root() -> Response:
    """Send unauthenticated users straight to /login."""
    return RedirectResponse(url="/login", status_code=302)
