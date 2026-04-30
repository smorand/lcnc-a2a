"""Session and auth gate acceptance tests."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lcnc_a2a.models.session import Session as SessionModel


@pytest.mark.asyncio
async def test_e2e_005_tampered_session_redirects_to_login(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-005: tampered session cookie redirects to /login."""
    login_response = await http_client.post(
        "/login",
        data={"email": "carol@example.com", "name": "Carol", "csrf_token": csrf_token},
    )
    assert login_response.status_code == 302

    set_cookie = login_response.headers["set-cookie"]
    cookie_value = set_cookie.split("session=", 1)[1].split(";", 1)[0]
    tampered = cookie_value[:-1] + ("A" if cookie_value[-1] != "A" else "B")

    factory = async_sessionmaker(db_engine)
    async with factory() as sess:
        before = list((await sess.execute(select(SessionModel))).scalars().all())

    response = await http_client.get("/agents", cookies={"session": tampered})

    assert response.status_code == 302
    assert response.headers["location"] == "/login"

    async with factory() as sess:
        after = list((await sess.execute(select(SessionModel))).scalars().all())
    assert {s.id for s in before} == {s.id for s in after}


@pytest.mark.asyncio
async def test_e2e_011_anonymous_agents_redirects_to_login(
    http_client: httpx.AsyncClient,
) -> None:
    """E2E-011: anonymous GET /agents redirects to /login."""
    response = await http_client.get("/agents")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_e2e_099_get_agents_requires_session(
    http_client: httpx.AsyncClient,
) -> None:
    """E2E-099: traceability duplicate of E2E-011."""
    response = await http_client.get("/agents")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_e2e_101_htmx_login_returns_fragment(
    http_client: httpx.AsyncClient,
    csrf_token: str,
) -> None:
    """E2E-101: HTMX login returns a fragment with HX-Redirect or 200 + fragment."""
    response = await http_client.post(
        "/login",
        data={"email": "dora@example.com", "name": "Dora", "csrf_token": csrf_token},
        headers={"HX-Request": "true"},
    )

    body_lower = response.text.lower()
    assert "<html" not in body_lower
    assert "<head" not in body_lower

    has_hx_redirect = response.headers.get("hx-redirect") == "/agents"
    has_redirect_fragment = response.status_code == 200 and "/agents" in response.text
    assert has_hx_redirect or has_redirect_fragment
