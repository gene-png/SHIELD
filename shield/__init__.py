"""SHIELD Flask application factory.

The three platforms (p1_techdebt, p2_zerotrust, p3_attack_surface) are
Blueprints that sit on a shared spine (identity, repository, audit,
capability list, shared components). See docs/architecture/INTEGRITY_MODEL.md.
"""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user

from .config import Config
from .extensions import csrf, db, limiter, login_manager, migrate
from .security import register_security_headers


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    app.config.from_object(config_object)

    # --- Extensions ---
    db.init_app(app)
    migrate.init_app(app, db, directory="migrations")
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # --- Security headers + CSP for GCC High self-hosting posture ---
    register_security_headers(app)

    # --- Template filters ---
    # `from_json`: safe JSON parse in Jinja. Returns None on failure
    # rather than raising — templates branch on the result. Used by
    # _components/readable_body.html to render AI artifact bodies
    # as structured content instead of opaque <pre> dumps.
    import json as _json

    def _from_json(text):
        if not text:
            return None
        try:
            return _json.loads(text)
        except (ValueError, TypeError):
            return None
    app.jinja_env.filters["from_json"] = _from_json

    # `client_label`: render a Client's display label, preferring what
    # the client typed during /portal/about over the system-assigned
    # name. The round-3 UX doc is explicit: every header / banner /
    # flash that says "who you are" must NOT show the seed name
    # ("Acme Co") unless the user actually typed it. Used as
    # `{{ client | client_label }}` in templates.
    def _client_label(client):
        if client is None:
            return ""
        return (client.legal_name or "").strip() or client.name or ""
    app.jinja_env.filters["client_label"] = _client_label

    # --- Models must be imported before migrations ---
    from . import models  # noqa: F401

    # --- Auth user loader ---
    from .spine.identity import load_user
    login_manager.user_loader(load_user)
    login_manager.login_view = "identity.login"

    # --- Spine Blueprints ---
    from .spine.admin_views import bp as admin_bp
    from .spine.audit_views import bp as audit_bp
    from .spine.clients import bp as clients_bp
    from .spine.identity import bp as identity_bp
    from .spine.intake import bp as intake_bp
    from .spine.jobs import bp as jobs_bp
    from .spine.portal import bp as portal_bp
    from .spine.projects import bp as projects_bp
    from .spine.repository_views import bp as repo_bp
    app.register_blueprint(identity_bp, url_prefix="/auth")
    app.register_blueprint(repo_bp, url_prefix="/repository")
    app.register_blueprint(clients_bp, url_prefix="/clients")
    app.register_blueprint(intake_bp, url_prefix="/intake")
    app.register_blueprint(portal_bp, url_prefix="/portal")
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(jobs_bp, url_prefix="/jobs")
    app.register_blueprint(audit_bp, url_prefix="/audit")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # --- Platform Blueprints ---
    from .p1_techdebt import bp as p1_bp
    from .p2_zerotrust import bp as p2_bp
    from .p3_attack_surface import bp as p3_bp
    app.register_blueprint(p1_bp, url_prefix="/platform/tech-debt")
    app.register_blueprint(p2_bp, url_prefix="/platform/zero-trust")
    app.register_blueprint(p3_bp, url_prefix="/platform/attack-surface")

    # --- Role gate (spec §6.6 + v1.8 portal) ---------------------------
    # CLIENT-role users see ONLY the portal surface (and the legacy
    # /intake/ blueprint for backward compatibility). Every other URL
    # 302s them to the portal. The nav already hides those links; this
    # server-side gate makes sure URL-typing doesn't bypass it.
    from .models import Role as _Role
    @app.before_request
    def _restrict_client_to_portal():
        if not current_user.is_authenticated:
            return None
        if current_user.role != _Role.CLIENT:
            return None
        path = request.path
        if (
            path.startswith("/portal")
            or path.startswith("/auth")
            or path.startswith("/static")
            or path == "/"
            or path == "/healthz"
        ):
            return None
        # /intake/ used to be the v1.0 client surface; v1.8 retired it
        # in favor of /portal/ and round-3 closes the loophole (round-3
        # §2.3 — clients should never see "Acme Co" on a project page
        # by side-channelling through the legacy URL).
        return redirect(url_for("portal.index"))

    # --- Top-level routes ---
    @app.route("/")
    def home():
        if not current_user.is_authenticated:
            return redirect(url_for("identity.login"))
        from .models import Role
        # CLIENT users:
        #   - With a membership → /portal/welcome (intake not done) or
        #     /portal/ (intake done) per landing_url_for.
        #   - Without a membership (self-signups, brand new) →
        #     /portal/start-organization to name their org.
        if current_user.role == Role.CLIENT:
            from .spine.portal import _current_client, landing_url_for
            client = _current_client()
            if client is not None:
                return redirect(landing_url_for(client))
            return redirect(url_for("portal.start_organization"))
        # ADMIN: the queue is the new default landing (v1.8 PR 5).
        if current_user.role == Role.ADMIN:
            return redirect(url_for("clients.queue"))
        # REVIEWER: the existing home page lists what's available.
        return render_template("home.html")

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @app.errorhandler(404)
    def _404(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def _500(e):
        return render_template("errors/500.html"), 500

    # --- CLI ---
    from .cli import register_cli
    register_cli(app)

    return app
