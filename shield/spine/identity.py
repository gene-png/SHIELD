"""Identity Blueprint. OIDC against Keycloak (which stands in for GCC High Entra ID)."""
from __future__ import annotations

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, redirect, session, url_for
from flask_login import login_required, login_user, logout_user

from ..extensions import db
from ..models import Role, User
from .audit import log_audit

bp = Blueprint("identity", __name__, template_folder="../templates")

_oauth: OAuth | None = None


def _oauth_client():
    global _oauth
    if _oauth is None:
        _oauth = OAuth(current_app)
        _oauth.register(
            name="keycloak",
            server_metadata_url=(
                f"{current_app.config['KEYCLOAK_URL']}"
                f"/realms/{current_app.config['KEYCLOAK_REALM']}"
                f"/.well-known/openid-configuration"
            ),
            client_id=current_app.config["KEYCLOAK_CLIENT_ID"],
            client_secret=current_app.config["KEYCLOAK_CLIENT_SECRET"],
            client_kwargs={
                "scope": "openid profile email",
                # Keycloak 25's `shield-app` client requires PKCE; without
                # this, /auth/callback fails with "Missing parameter:
                # code_challenge_method". Authlib will auto-generate the
                # verifier and challenge when this kwarg is present.
                "code_challenge_method": "S256",
            },
        )
    return _oauth.keycloak  # type: ignore[attr-defined]


def load_user(user_id: str) -> User | None:
    return db.session.get(User, user_id)


def _upsert_user_from_claims(claims: dict) -> User:
    sub = claims["sub"]
    email = claims.get("email") or claims.get("preferred_username") or sub
    name = claims.get("name") or email
    realm_roles = (
        claims.get("realm_access", {}).get("roles", [])
        if isinstance(claims.get("realm_access"), dict)
        else []
    )
    role = Role.CLIENT
    if "admin" in realm_roles:
        role = Role.ADMIN
    elif "reviewer" in realm_roles:
        role = Role.REVIEWER

    # Match by sub OR email. The seeded demo users have placeholder
    # subs like `seed-admin@demo`; their real Keycloak UUIDs only appear
    # at first login. Without the email fallback the upsert would try to
    # INSERT a new row with the same unique email and fail.
    from sqlalchemy import or_
    user = (
        db.session.query(User)
        .filter(or_(User.sub == sub, User.email == email))
        .first()
    )
    if user is None:
        user = User(sub=sub, email=email, display_name=name, role=role)
        db.session.add(user)
    else:
        # Existing row (seeded placeholder OR prior real login).
        # The Keycloak `sub` is now the authoritative identifier.
        user.sub = sub
        user.email = email
        user.display_name = name
        user.role = role
    db.session.commit()
    return user


@bp.route("/login")
def login():
    redirect_uri = url_for("identity.callback", _external=True)
    return _oauth_client().authorize_redirect(redirect_uri)


@bp.route("/callback")
def callback():
    token = _oauth_client().authorize_access_token()
    # Keycloak's userinfo endpoint does not return realm_access by default,
    # so role mapping relies on the id_token. Parse both and merge with
    # id_token claims winning — they're the signed assertion the client
    # is supposed to trust for identity.
    id_token_claims: dict = {}
    try:
        id_token_claims = _oauth_client().parse_id_token(token, None) or {}
    except Exception:
        id_token_claims = {}
    userinfo = token.get("userinfo") or {}
    claims = {**userinfo, **id_token_claims}
    user = _upsert_user_from_claims(dict(claims))
    login_user(user, remember=False)
    log_audit("auth.login", actor=user, details={"sub": user.sub, "role": user.role.value})
    next_url = session.pop("post_login_redirect", url_for("home"))
    return redirect(next_url)


@bp.route("/logout")
@login_required
def logout():
    from flask_login import current_user
    if current_user.is_authenticated:
        log_audit("auth.logout", actor=current_user)
    logout_user()
    return redirect(url_for("identity.login"))
