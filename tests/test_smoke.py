"""Smoke tests: every public route renders or redirects sanely."""
from __future__ import annotations


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_home_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_security_headers_present(client):
    r = client.get("/healthz")
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
