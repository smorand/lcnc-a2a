"""Authentication routes: /login and /logout."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.provider import AuthProvider
from lcnc_a2a.auth.session import SESSION_COOKIE_NAME, SessionManager
from lcnc_a2a.deps import (
    get_auth_provider,
    get_csrf_manager,
    get_db,
    get_session_manager,
    get_templates,
)
from lcnc_a2a.models.session import Session as SessionModel

EMAIL_MAX_LEN = 255
NAME_MAX_LEN = 255
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    csrf: CSRFManager = Depends(get_csrf_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    """Render the login form with a fresh CSRF token."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": csrf.generate(), "error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(""),
    name: str = Form(""),
    csrf_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
    csrf: CSRFManager = Depends(get_csrf_manager),
    auth_provider: AuthProvider = Depends(get_auth_provider),
    sessions: SessionManager = Depends(get_session_manager),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Handle login form submission."""
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    is_htmx = request.headers.get("HX-Request", "").lower() == "true"

    error = _validate_inputs(email=email, name=name)
    if error is not None:
        return _render_error(templates, request, csrf, error, is_htmx=is_htmx)

    user = await auth_provider.authenticate(db, email=email, name=name)
    sess = await sessions.create(db, user_id=user.id)
    await db.commit()

    cookie_value = sessions.sign(sess.id)
    if is_htmx:
        response: Response = Response(status_code=200, headers={"HX-Redirect": "/agents"})
    else:
        response = RedirectResponse(url="/agents", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sessions: SessionManager = Depends(get_session_manager),
    csrf: CSRFManager = Depends(get_csrf_manager),
    csrf_token: str = Form(""),
) -> Response:
    """Sign out: delete server session row and clear cookie."""
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)

    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_cookie is not None:
        session_id = sessions.verify(raw_cookie)
        if session_id is not None:
            await db.execute(delete(SessionModel).where(SessionModel.id == session_id))
            await db.commit()

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def _validate_inputs(*, email: str, name: str) -> str | None:
    """Return an error code or None for valid input."""
    if not email:
        return "email_required"
    if len(email) > EMAIL_MAX_LEN:
        return "email_too_long"
    if not EMAIL_REGEX.match(email):
        return "email_invalid"
    if not name:
        return "name_required"
    if len(name) > NAME_MAX_LEN:
        return "name_too_long"
    return None


def _render_error(
    templates: Jinja2Templates,
    request: Request,
    csrf: CSRFManager,
    error: str,
    *,
    is_htmx: bool,
) -> HTMLResponse:
    template = "partials/login_form.html" if is_htmx else "login.html"
    return templates.TemplateResponse(
        request,
        template,
        {"csrf_token": csrf.generate(), "error": error},
        status_code=400,
    )
