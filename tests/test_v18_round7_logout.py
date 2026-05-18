"""Round-7 bug fix: clicking Log Out actually logs the user out.

Pre-fix shape: `/auth/logout` called `logout_user()` then redirected to
`/auth/login`. The Flask session cookie cleared, but Keycloak's SSO
cookie didn't — so the OIDC kick-off at `/auth/login` silently
re-authenticated the user. In the browser this looked like "clicking
Log Out does nothing."

After-fix shape: `/auth/logout` clears the Flask session AND redirects
to Keycloak's `/protocol/openid-connect/logout` end-session endpoint
with `id_token_hint` + `post_logout_redirect_uri` so the SSO cookie
dies too. The test config has TESTING=True which falls back to a local
`/auth/login` redirect (we don't want the test suite hitting a real
Keycloak), but the production path is exercised by inspecting the
URL helper directly.
"""
from __future__ import annotations


def test_logout_redirects_to_login_in_test_mode(admin_client):
    """Under TESTING=True the logout route should bounce to /auth/login
    locally rather than the Keycloak end-session URL."""
    r = admin_client.get("/auth/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/auth/login")


def test_logout_clears_flask_session(admin_client, admin):
    """After Log Out, hitting an admin-only page should bounce the
    caller back through the auth flow rather than render the page."""
    # Confirm we start logged in.
    r = admin_client.get("/clients/queue", follow_redirects=False)
    assert r.status_code == 200
    # Log out.
    admin_client.get("/auth/logout", follow_redirects=False)
    # Now /clients/queue should not render.
    r = admin_client.get("/clients/queue", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_logout_when_already_logged_out_does_not_500(client):
    """A second click on Log Out (or one from a session that already
    expired) must NOT 401 — it should still cleanly land at the login
    screen."""
    r = client.get("/auth/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/auth/login")


def test_keycloak_end_session_url_includes_post_logout_redirect():
    """The end-session URL helper must include a post_logout_redirect_uri
    so Keycloak knows where to send the user back to."""
    from shield import create_app
    from shield.config import TestConfig
    app = create_app(TestConfig)
    # Build a non-testing-style config so we can hit the prod branch.
    app.config["KEYCLOAK_URL"] = "https://keycloak.example"
    app.config["KEYCLOAK_REALM"] = "shield-dev"
    app.config["KEYCLOAK_CLIENT_ID"] = "shield-app"
    with app.test_request_context("/auth/logout"):
        from shield.spine.identity import _keycloak_end_session_url
        url = _keycloak_end_session_url(
            "https://shield.example/auth/login", id_token_hint="fake-id-token",
        )
    assert url.startswith("https://keycloak.example/realms/shield-dev/protocol/openid-connect/logout")
    assert "post_logout_redirect_uri=" in url
    assert "id_token_hint=fake-id-token" in url
    # Should NOT include client_id when an id_token_hint is present.
    assert "client_id=" not in url


def test_keycloak_end_session_url_falls_back_to_client_id_without_id_token():
    """When no id_token is stashed (test session, expired token, etc.)
    we fall back to client_id so Keycloak still recognizes the RP."""
    from shield import create_app
    from shield.config import TestConfig
    app = create_app(TestConfig)
    app.config["KEYCLOAK_URL"] = "https://keycloak.example"
    app.config["KEYCLOAK_REALM"] = "shield-dev"
    app.config["KEYCLOAK_CLIENT_ID"] = "shield-app"
    with app.test_request_context("/auth/logout"):
        from shield.spine.identity import _keycloak_end_session_url
        url = _keycloak_end_session_url(
            "https://shield.example/auth/login", id_token_hint=None,
        )
    assert "client_id=shield-app" in url
    assert "id_token_hint=" not in url
