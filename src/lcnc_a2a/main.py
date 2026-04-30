"""FastAPI application factory and entry point."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.dev_provider import DevModeAuthProvider
from lcnc_a2a.auth.session import SessionManager
from lcnc_a2a.crypto import ENCRYPTION_KEY_REQUIRED_MESSAGE, CryptoService, InvalidEncryptionKeyError
from lcnc_a2a.db import Database
from lcnc_a2a.observability.otel import configure_tracing
from lcnc_a2a.routes import auth as auth_routes
from lcnc_a2a.routes import dashboard as dashboard_routes
from lcnc_a2a.settings import Settings

PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_ROOT / "static"
TEMPLATES_DIR = PACKAGE_ROOT / "templates"


def _exit_missing_encryption_key() -> None:
    sys.stderr.write(ENCRYPTION_KEY_REQUIRED_MESSAGE + "\n")
    sys.stderr.flush()
    raise SystemExit(1)


def _load_settings() -> Settings:
    """Load settings; abort with the canonical message if the key is missing."""
    if not os.environ.get("LCNC_A2A_ENCRYPTION_KEY"):
        _exit_missing_encryption_key()
    try:
        return Settings()
    except ValidationError as exc:
        for err in exc.errors():
            if "encryption_key" in err.get("loc", ()):
                _exit_missing_encryption_key()
        raise


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    settings = _load_settings()

    try:
        crypto = CryptoService(settings.encryption_key)
    except InvalidEncryptionKeyError:
        _exit_missing_encryption_key()
        raise  # for type-checkers; unreachable

    db = Database(settings.database_url)
    csrf = CSRFManager(settings.session_secret, max_age_seconds=settings.csrf_max_age_seconds)
    sessions = SessionManager(settings.session_secret, expiry_hours=settings.session_expiry_hours)
    auth_provider = DevModeAuthProvider()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    configure_tracing(settings.trace_file)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.close()

    app = FastAPI(title="LCNC A2A Builder", lifespan=lifespan)
    app.state.settings = settings
    app.state.crypto = crypto
    app.state.db = db
    app.state.csrf = csrf
    app.state.sessions = sessions
    app.state.auth_provider = auth_provider
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)

    return app


app = create_app()


def run() -> None:
    """uvicorn entry point used by the console script."""
    import uvicorn

    uvicorn.run("lcnc_a2a.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
