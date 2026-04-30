"""Static asset acceptance tests."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_e2e_100_carbon_css_served_and_referenced(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    carbon_reference_bytes: int,
) -> None:
    """E2E-100: /agents references /static/css/carbon.css and the CSS is served."""
    login_response = await http_client.post(
        "/login",
        data={"email": "eve@example.com", "name": "Eve", "csrf_token": csrf_token},
    )
    assert login_response.status_code == 302
    cookies = {"session": login_response.headers["set-cookie"].split("session=", 1)[1].split(";", 1)[0]}

    agents_response = await http_client.get("/agents", cookies=cookies)
    assert agents_response.status_code == 200
    assert '<link rel="stylesheet" href="/static/css/carbon.css">' in agents_response.text

    css_response = await http_client.get("/static/css/carbon.css")
    assert css_response.status_code == 200
    assert css_response.headers["content-type"].startswith("text/css")

    served_len = len(css_response.content)
    delta_pct = abs(served_len - carbon_reference_bytes) / max(carbon_reference_bytes, 1)
    assert delta_pct <= 0.10, (
        f"served {served_len} bytes vs reference {carbon_reference_bytes}, delta {delta_pct:.2%} > 10%"
    )
