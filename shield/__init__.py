"""SHIELD Flask application factory.

The three platforms (p1_techdebt, p2_zerotrust, p3_attack_surface) are
Blueprints that sit on a shared spine (identity, repository, audit,
capability list, shared components). See docs/architecture/INTEGRITY_MODEL.md.
"""
from __future__ import annotations

import os

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .config import Config
from .extensions import db, login_manager, migrate, csrf, limiter
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

    # --- Models must be imported before migrations ---
    from . import models  # noqa: F401

    # --- Auth user loader ---
    from .spine.identity import load_user
    login_manager.user_loader(load_user)
    login_manager.login_view = "identity.login"

    # --- Spine Blueprints ---
    from .spine.identity import bp as identity_bp
    from .spine.repository_views import bp as repo_bp
    from .spine.clients import bp as clients_bp
    from .spine.intake import bp as intake_bp
    from .spine.projects import bp as projects_bp
    app.register_blueprint(identity_bp, url_prefix="/auth")
    app.register_blueprint(repo_bp, url_prefix="/repository")
    app.register_blueprint(clients_bp, url_prefix="/clients")
    app.register_blueprint(intake_bp, url_prefix="/intake")
    app.register_blueprint(projects_bp, url_prefix="/projects")

    # --- Platform Blueprints ---
    from .p1_techdebt import bp as p1_bp
    from .p2_zerotrust import bp as p2_bp
    from .p3_attack_surface import bp as p3_bp
    app.register_blueprint(p1_bp, url_prefix="/platform/tech-debt")
    app.register_blueprint(p2_bp, url_prefix="/platform/zero-trust")
    app.register_blueprint(p3_bp, url_prefix="/platform/attack-surface")

    # --- Role gate (spec §6.6) -----------------------------------------
    # CLIENT-role users see ONLY the intake surface — no repository
    # browsing, no picker, no AI-lane visibility, no platform workflows.
    # The nav already hides those links; this server-side gate makes
    # sure URL-typing doesn't bypass it.
    from .models import Role as _Role
    @app.before_request
    def _restrict_client_to_intake():
        if not current_user.is_authenticated:
            return None
        if current_user.role != _Role.CLIENT:
            return None
        path = request.path
        if (
            path.startswith("/intake")
            or path.startswith("/auth")
            or path.startswith("/static")
            or path == "/"
            or path == "/healthz"
        ):
            return None
        return redirect(url_for("intake.index"))

    # --- Top-level routes ---
    @app.route("/")
    def home():
        if not current_user.is_authenticated:
            return redirect(url_for("identity.login"))
        # Per spec §6.6, the client intake surface is stripped — no
        # repository browsing, no picker, no AI-lane visibility. Routing
        # CLIENT-role users straight to /intake keeps them out of the
        # admin-flow surface entirely.
        from .models import Role
        if current_user.role == Role.CLIENT:
            return redirect(url_for("intake.index"))
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
