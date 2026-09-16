"""CORS and the cookie domain — the pair that decides whether login works in production.

Locally the Vite proxy makes the frontend same-origin, so neither is exercised by anything
else in the suite. In production the frontend is app.* on Vercel and the API is api.* on
Railway, and getting either of these wrong means every authenticated request fails with a
blank page and nothing in the server log.
"""

import app as app_module
import pytest

from routes import auth


ORIGIN = "https://app.example.com"


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(app_module, "CORS_ORIGINS", {ORIGIN})


def test_no_cors_headers_without_configuration(client):
    """Same-origin local development sends none, and an unconfigured deploy grants nobody."""
    response = client.get("/api/health", headers={"Origin": ORIGIN})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_a_known_origin_is_granted_with_credentials(client, allowed):
    response = client.get("/api/health", headers={"Origin": ORIGIN})

    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    # a wildcard is invalid with credentials, and would hand the API to any site
    assert response.headers["Access-Control-Allow-Origin"] != "*"
    assert "Origin" in response.headers["Vary"]


def test_an_unknown_origin_is_not_granted(client, allowed):
    response = client.get("/api/health", headers={"Origin": "https://attacker.example"})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_preflight_is_answered_before_auth_rejects_it(client, allowed):
    """A preflight carries no cookies. Letting it reach a @require_auth route would 401 the
    check that decides whether the real request may happen."""
    response = client.options("/api/jobs", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })

    assert response.status_code < 400
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Idempotency-Key" in response.headers["Access-Control-Allow-Headers"]


def test_cookies_are_scoped_to_the_parent_domain_when_configured(client, monkeypatch):
    """Scoped to api.yoursite.com alone, the cookie is never sent from app.yoursite.com."""
    monkeypatch.setattr(auth, "COOKIE_DOMAIN", ".example.com")
    client.post("/api/auth/signup", json={"username": "domainuser", "password": "pw123456"})

    response = client.post("/api/auth/login",
                           json={"username": "domainuser", "password": "pw123456"})

    # RFC 6265 drops the leading dot, so ".example.com" is sent as "Domain=example.com" —
    # which already covers every subdomain. Same scope, different spelling.
    cookies = response.headers.getlist("Set-Cookie")
    assert any("access_token" in c and "Domain=example.com" in c for c in cookies)
    assert any("refresh_token" in c and "Domain=example.com" in c for c in cookies)


def test_logout_clears_the_cookie_it_actually_set(client, monkeypatch):
    """A delete whose domain does not match the one it was set with leaves the cookie in
    place, and the user stays logged in."""
    monkeypatch.setattr(auth, "COOKIE_DOMAIN", ".example.com")
    client.post("/api/auth/signup", json={"username": "logoutuser", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "logoutuser", "password": "pw123456"})

    response = client.post("/api/auth/logout")

    assert all("Domain=example.com" in c for c in response.headers.getlist("Set-Cookie"))
