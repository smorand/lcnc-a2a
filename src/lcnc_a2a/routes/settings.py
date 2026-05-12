"""User-facing UI settings (Appearance: dark mode + theme identity)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.middleware import fetch_current_user
from lcnc_a2a.deps import get_csrf_manager, get_templates
from lcnc_a2a.models.user import User
from lcnc_a2a.themes import is_valid_theme

router = APIRouter()

THEME_COOKIE_NAME = "lcnc_theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

IDENTITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("carbon", "IBM Carbon"),
    ("ei", "EI"),
)


def _theme_to_form(theme: str) -> tuple[str, bool]:
    """Split a resolved theme into ``(identity, dark)``."""
    if theme == "v2":
        return ("ei", False)
    return ("carbon", theme == "g100")


def _form_to_theme(identity: str, dark: bool) -> str:
    """Combine identity + dark mode into a single theme name."""
    if identity == "ei":
        return "v2"
    return "g100" if dark else "g10"


@router.get("/settings")
async def settings_page(
    request: Request,
    user: User | None = Depends(fetch_current_user),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    """Render the settings page (Appearance + future panels)."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    current_theme: str = getattr(request.state, "theme", "g100")
    identity, dark = _theme_to_form(current_theme)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "identity": identity,
            "dark": dark,
            "identity_choices": IDENTITY_CHOICES,
        },
    )


@router.post("/settings/appearance")
async def update_appearance(
    request: Request,
    identity: str = Form(""),
    dark: str = Form(""),
    csrf_token: str = Form(""),
    next: str = Form("/settings"),
    csrf: CSRFManager = Depends(get_csrf_manager),
) -> Response:
    """Update the user's appearance cookies (identity + dark mode)."""
    if not csrf.validate(csrf_token):
        return Response(content="csrf_invalid", status_code=403)
    if identity not in {value for value, _ in IDENTITY_CHOICES}:
        return Response(content="identity_invalid", status_code=400)

    theme = _form_to_theme(identity, dark in {"on", "1", "true"})
    if not is_valid_theme(theme):
        return Response(content="theme_invalid", status_code=400)

    target = next if next.startswith("/") else "/settings"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=THEME_COOKIE_NAME,
        value=theme,
        httponly=False,
        samesite="lax",
        max_age=THEME_COOKIE_MAX_AGE,
        path="/",
    )
    return response
