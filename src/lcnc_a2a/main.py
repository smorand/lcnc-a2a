"""FastAPI application factory and entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.dev_provider import DevModeAuthProvider
from lcnc_a2a.auth.session import SessionManager
from lcnc_a2a.db import Database
from lcnc_a2a.observability.otel import configure_tracing
from lcnc_a2a.routes import a2a as a2a_routes
from lcnc_a2a.routes import agents as agents_routes
from lcnc_a2a.routes import auth as auth_routes
from lcnc_a2a.routes import dashboard as dashboard_routes
from lcnc_a2a.routes import mcp as mcp_routes
from lcnc_a2a.routes import runs as runs_routes
from lcnc_a2a.routes import settings as settings_routes
from lcnc_a2a.routes.settings import THEME_COOKIE_NAME
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.app_secrets import bootstrap_secrets
from lcnc_a2a.services.cancellation import CancellationRegistry
from lcnc_a2a.settings import Settings
from lcnc_a2a.themes import ALLOWED_THEMES, THEME_LABELS, is_valid_theme

logger = logging.getLogger(__name__)
ABANDONED_RUN_THRESHOLD = timedelta(hours=1)

PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_ROOT / "static"
TEMPLATES_DIR = PACKAGE_ROOT / "templates"


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    settings = Settings()

    app_secrets = bootstrap_secrets(
        database_url=settings.database_url,
        env_encryption_key=settings.encryption_key,
    )
    crypto = app_secrets.crypto

    db = Database(settings.database_url)
    csrf = CSRFManager(app_secrets.session_secret, max_age_seconds=settings.csrf_max_age_seconds)
    sessions = SessionManager(app_secrets.session_secret, expiry_hours=settings.session_expiry_hours)
    auth_provider = DevModeAuthProvider()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["theme"] = settings.theme  # default; per-request override via request.state.theme
    templates.env.globals["theme_choices"] = [(t, THEME_LABELS[t]) for t in ALLOWED_THEMES]
    templates.env.globals["new_csrf_token"] = csrf.generate
    cancellation_registry = CancellationRegistry()

    configure_tracing(settings.trace_file)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Reap runs left ``running`` / ``paused`` from a previous process
        # that crashed or was killed mid-stream. Without this the dashboard
        # and ``GET /tasks/{id}`` would lie about the run state.
        try:
            async for session in db.session():
                reaped = await runs_service.reap_abandoned_runs(session, older_than=ABANDONED_RUN_THRESHOLD)
                break
            if reaped:
                logger.warning("reaped %d abandoned run(s) at startup", reaped)
        except Exception:
            logger.exception("failed to reap abandoned runs at startup")
        yield
        await db.close()

    app = FastAPI(title="LCNC A2A Builder", lifespan=lifespan)

    @app.middleware("http")
    async def resolve_theme(request, call_next):  # type: ignore[no-untyped-def]
        cookie_theme = request.cookies.get(THEME_COOKIE_NAME)
        request.state.theme = cookie_theme if is_valid_theme(cookie_theme) else settings.theme
        return await call_next(request)

    app.state.settings = settings
    app.state.crypto = crypto
    app.state.app_secrets = app_secrets
    app.state.db = db
    app.state.csrf = csrf
    app.state.sessions = sessions
    app.state.auth_provider = auth_provider
    app.state.templates = templates
    app.state.cancellation_registry = cancellation_registry

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(agents_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(mcp_routes.router)
    app.include_router(runs_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(a2a_routes.router)

    return app


app = create_app()


def run() -> None:
    """uvicorn entry point used by the console script."""
    import uvicorn

    uvicorn.run("lcnc_a2a.main:app", host="0.0.0.0", port=8001, reload=False)


if __name__ == "__main__":
    run()
