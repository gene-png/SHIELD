"""Round-7 §18: every visible timestamp renders as a `<time
datetime="...Z" class="local-time">...</time>` element so the
client-side JS in static/shield/local-time.js can convert it to the
viewer's timezone.

Server-side responsibilities:
  - The `local_time` Jinja filter emits the right markup.
  - The audit log keeps UTC visible (canonical) and adds a local-time
    rendering above it.
  - The base layout pulls in the JS.

Client-side conversion isn't unit-tested here — that's browser
behavior. We pin the markup contract so any future template that
opts in produces consistent shape.
"""
from __future__ import annotations

from datetime import datetime

from shield import create_app
from shield.config import TestConfig


def test_local_time_filter_emits_time_element():
    app = create_app(TestConfig)
    with app.app_context():
        env = app.jinja_env
        rendered = env.from_string("{{ d | local_time }}").render(
            d=datetime(2026, 5, 18, 14, 30, 0),
        )
    assert '<time ' in rendered
    assert 'datetime="2026-05-18T14:30:00Z"' in rendered
    assert 'class="local-time"' in rendered
    # Fallback text the page shows before the JS runs.
    assert "2026-05-18 14:30 UTC" in rendered


def test_local_time_filter_date_variant():
    app = create_app(TestConfig)
    with app.app_context():
        env = app.jinja_env
        rendered = env.from_string("{{ d | local_time(fmt='date') }}").render(
            d=datetime(2026, 5, 18, 14, 30, 0),
        )
    assert 'data-fmt="date"' in rendered
    # The visible fallback (between the tags) is date-only.
    visible = rendered.split(">", 1)[1].rsplit("<", 1)[0]
    assert visible == "2026-05-18"
    assert "14:30" in rendered  # still in the ISO attribute (correct)


def test_local_time_filter_returns_dash_for_none():
    app = create_app(TestConfig)
    with app.app_context():
        env = app.jinja_env
        rendered = env.from_string("{{ d | local_time }}").render(d=None)
    # &mdash; is the HTML entity we emit; "<time" must not appear.
    assert rendered.strip() == "&mdash;"
    assert "<time" not in rendered


def test_base_template_loads_local_time_script(admin_client):
    """The base layout should pull in local-time.js so every page
    benefits from client-side conversion. Use /audit/ (admin-only,
    plain HTML response) to verify."""
    r = admin_client.get("/audit/")
    assert r.status_code == 200
    assert b"local-time.js" in r.data


def test_audit_log_uses_local_time_and_shows_utc(admin_client, app, admin):
    """Audit details column keeps UTC visible (canonical) AND adds a
    local-time rendering on top."""
    from shield.extensions import db
    from shield.models import AuditEntry
    db.session.add(AuditEntry(
        action="auth.login",
        actor_id=admin.id, actor_email=admin.email,
        details={"role": "admin"},
    ))
    db.session.commit()

    r = admin_client.get("/audit/")
    assert r.status_code == 200
    body = r.data
    # Local-time element appears.
    assert b"local-time" in body
    # The "UTC: <iso>" small text appears too so compliance auditors
    # see the canonical wire value.
    assert b"UTC:" in body
