"""Round-7 follow-up: /portal/about/field saves ONLY the field that
triggered the post, not whichever happens to be first in the form.

Pre-fix every input on /portal/about had `name="value"` and no
`hx-include` scope, so HTMX's default behavior shipped every form
input on each blur. The server's request.form.get("value") then
returned the first one (legal_name's text), and the column named in
hx-vals got that value — regardless of what the user actually typed
in the triggering input. Net effect: typing "Acme" in legal_name,
then "John" in primary_poc_name, persisted "Acme" to primary_poc_name.

The template fix is `hx-include="this"` on every field. This test
exercises the server-side contract that produces correct results when
the request is well-formed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    Role,
    User,
)


@pytest.fixture()
def about_portal(client, app, admin):
    c = Client(name="About Test")
    db.session.add(c)
    db.session.commit()
    u = User(sub="about-user", email="about@example",
             display_name="About User", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=c.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    from tests.conftest import _login
    _login(client, u.id)
    return client, c


def test_field_endpoint_saves_only_named_column(about_portal):
    """Posting a single {name, value} pair to /portal/about/field
    writes to that column and no other."""
    client, c = about_portal
    r = client.post(
        "/portal/about/field",
        data={"name": "primary_poc_name", "value": "Dana Q"},
    )
    assert r.status_code == 200
    db.session.refresh(c)
    assert c.primary_poc_name == "Dana Q"
    # And nothing else got the same value as a side effect.
    assert c.legal_name in (None, "")
    assert c.primary_poc_email in (None, "")


def test_field_endpoint_rejects_unknown_column(about_portal):
    """The about_field allowlist (`_ABOUT_FIELDS`) is the guardrail
    against a hand-crafted POST writing arbitrary Client attributes."""
    client, c = about_portal
    r = client.post(
        "/portal/about/field",
        data={"name": "is_active_flag", "value": "False"},
    )
    assert r.status_code == 400


def test_about_template_scopes_htmx_post_per_field(about_portal):
    """v1.9.1: each input is named after its column (legal_name,
    primary_poc_email, etc) and uses `hx-params` to whitelist only the
    csrf + the two relevant keys, so a blur on one input cannot ship
    every other field's value as collateral.

    The v1.9 approach was `hx-include="this"`, which was wrong —
    HTMX `hx-include` ADDS to the include set, it doesn't replace
    the default form-include. The whole form still shipped. The
    user-reported regression "every field shows the org name" was
    that bug surfacing on a fresh self-signup."""
    client, _ = about_portal
    r = client.get("/portal/about")
    assert r.status_code == 200
    body = r.data
    # Each input now carries its column name + an hx-params whitelist.
    assert b'name="legal_name"' in body
    assert b'name="primary_poc_email"' in body
    assert b'hx-params="csrf_token,name,legal_name"' in body
    assert b'hx-params="csrf_token,name,primary_poc_email"' in body
