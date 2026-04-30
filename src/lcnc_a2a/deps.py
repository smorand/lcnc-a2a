"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.csrf import CSRFManager
from lcnc_a2a.auth.provider import AuthProvider
from lcnc_a2a.auth.session import SessionManager
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.db import Database


def get_db_singleton(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to the per-request lifecycle."""
    db: Database = request.app.state.db
    async for session in db.session():
        yield session


def get_csrf_manager(request: Request) -> CSRFManager:
    return request.app.state.csrf  # type: ignore[no-any-return]


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.sessions  # type: ignore[no-any-return]


def get_auth_provider(request: Request) -> AuthProvider:
    return request.app.state.auth_provider  # type: ignore[no-any-return]


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def get_crypto(request: Request) -> CryptoService:
    return request.app.state.crypto  # type: ignore[no-any-return]
