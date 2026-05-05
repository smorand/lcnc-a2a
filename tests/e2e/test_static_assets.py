"""Static asset acceptance tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


@pytest.mark.asyncio
async def test_e2e_100_app_and_theme_css_served_and_referenced(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    app_css_path: Path,
    theme_css_dir: Path,
) -> None:
    """E2E-100 (amended 2026-05-03): /agents references the structural app.css plus
    the active theme's stylesheet, and both are served as text/css."""
    login_response = await http_client.post(
        "/login",
        data={"email": "eve@example.com", "name": "Eve", "csrf_token": csrf_token},
    )
    assert login_response.status_code == 302
    cookies = {"session": login_response.headers["set-cookie"].split("session=", 1)[1].split(";", 1)[0]}

    agents_response = await http_client.get("/agents", cookies=cookies)
    assert agents_response.status_code == 200
    body = agents_response.text
    assert '<link rel="stylesheet" href="/static/css/app.css">' in body
    # default theme g100
    assert '<link rel="stylesheet" href="/static/css/themes/g100.css">' in body

    for url, source_path in (
        ("/static/css/app.css", app_css_path),
        ("/static/css/themes/g100.css", theme_css_dir / "g100.css"),
    ):
        css_response = await http_client.get(url)
        assert css_response.status_code == 200, url
        assert css_response.headers["content-type"].startswith("text/css"), url
        assert len(css_response.content) > 0, url
        assert len(css_response.content) >= source_path.stat().st_size * 0.9, url


@pytest.mark.asyncio
async def test_theme_env_swaps_stylesheet(
    monkeypatch: pytest.MonkeyPatch,
    csrf_token: str,
) -> None:
    """LCNC_A2A_THEME=g10 -> /agents links themes/g10.css instead of g100.css."""
    monkeypatch.setenv("LCNC_A2A_THEME", "g10")

    import lcnc_a2a.main as main_module

    importlib.reload(main_module)
    app = main_module.app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        # New csrf manager after reload — fetch a fresh token.
        login_page = await client.get("/login")
        import re

        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', login_page.text)
        assert match is not None
        token = match.group(1)
        login = await client.post(
            "/login",
            data={"email": "theme@example.com", "name": "Theme", "csrf_token": token},
        )
        assert login.status_code == 302
        cookie_value = login.headers["set-cookie"].split("session=", 1)[1].split(";", 1)[0]
        agents = await client.get("/agents", cookies={"session": cookie_value})
        assert agents.status_code == 200
        assert '<link rel="stylesheet" href="/static/css/themes/g10.css">' in agents.text
        assert '<link rel="stylesheet" href="/static/css/themes/g100.css">' not in agents.text


def test_theme_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings() must reject an unknown theme via pydantic validation."""
    monkeypatch.setenv("LCNC_A2A_THEME", "midnight")
    from pydantic import ValidationError

    from lcnc_a2a.settings import Settings

    with pytest.raises(ValidationError):
        Settings()
