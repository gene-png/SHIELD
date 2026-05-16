"""SHIELD Flask application factory.

The three platforms (p1_techdebt, p2_zerotrust, p3_attack_surface) are
Blueprints that sit on a shared spine (identity, repository, audit,
capability list, shared components). See docs/architecture/INTEGRITY_MODEL.md.
"""
from __future__ import annotations

import os

from flask import Flask, redirect, render_template, url_for
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
    app.register_blueprint(identity_bp, url_prefix="/auth")
    app.register_blueprint(repo_bp, url_prefix="/repository")
    app.register_blueprint(clients_bp, url_prefix="/clients")

    # --- Platform Blueprints ---
    from .p1_techdebt import bp as p1_bp
    from .p2_zerotrust import bp as p2_bp
    from .p3_attack_surface import bp as p3_bp
    app.register_blueprint(p1_bp, url_prefix="/platform/tech-debt")
    app.register_blueprint(p2_bp, url_prefix="/platform/zero-trust")
    app.register_blueprint(p3_bp, url_prefix="/platform/attack-surface")

    # --- Top-level routes ---
    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return render_template("home.html")
        return redirect(url_for("identity.login"))

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
