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

    # `audit_summary`: render an audit row's `details` dict as a single
    # plain-English sentence. The raw JSON stays available on the page
    # via a "Show raw JSON" toggle for compliance auditors who want the
    # structured form. Round-7 §6.6.
    from .spine.audit_render import render_audit_details as _render_audit_details

    def _audit_summary(entry):
        if entry is None:
            return "—"
        action = getattr(entry, "action", "") or ""
        details = getattr(entry, "details", None)
        return _render_audit_details(action, details)
    app.jinja_env.filters["audit_summary"] = _audit_summary

    # `local_time`: render a datetime as `<time datetime="...UTC...">` so
    # the client-side `local-time.js` can convert it in place. Round-7
    # §18: storage + audit + wire stay UTC; only the display layer
    # converts. Usage in templates:
    #     {{ project.created_at | local_time }}
    #     {{ project.created_at | local_time(fmt='date') }}   # date only
    from markupsafe import Markup, escape

    def _local_time(value, fmt: str = "datetime"):
        if value is None:
            return Markup("&mdash;")
        # Datetime stored as naive UTC throughout the app. `Z` suffix
        # makes the JS Date parser treat it as UTC regardless of viewer.
        iso = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        # Fallback text the page shows before the JS runs (or if it's
        # disabled): pretty UTC. The JS overwrites .textContent on load.
        if fmt == "date":
            fallback = value.strftime("%Y-%m-%d")
        else:
            fallback = value.strftime("%Y-%m-%d %H:%M UTC")
        return Markup(
            f'<time datetime="{escape(iso)}" data-fmt="{escape(fmt)}" '
            f'class="local-time">{escape(fallback)}</time>'
        )
    app.jinja_env.filters["local_time"] = _local_time

    # `friendly_label`: map raw enum/slug strings to display labels.
    # Round-8 §III flagged "Zero_trust · intake", "attack_coverage",
    # "admin_final" rendered raw. This filter centralizes the mapping.
    _FRIENDLY = {
        # platform / service
        "tech_debt":               "Tech Debt",
        "zero_trust":              "Zero Trust",
        "attack_surface":          "Attack Surface",
        # project / artifact stage
        "intake":                  "Intake",
        "raw_intake":              "Files uploaded",
        "ai_extraction":           "Initial reading",
        "extraction_review":       "Your review",
        "overlap_analysis":        "Overlap check",
        "conversational_interrogation": "Questions and answers",
        "admin_final":             "Final list",
        "current_state_assessment": "Where you are today",
        "desired_future_state":    "Where you want to be",
        "transition_roadmap":      "How to get there",
        "client_repository":       "Client repository",
        "coverage_analysis":       "Coverage analysis",
        "attack_coverage":         "ATT&CK coverage",
        "evidence":                "Evidence",
        "submitted":               "Submitted",
        # framework ids
        "cisa_ztmm_v2":            "CISA ZTMM 2.0",
        "dod_zt":                  "DoD ZT",
        "nist_csf_v2":             "NIST CSF 2.0",
        "csf_2_0_high":            "NIST CSF 2.0 (HIGH)",
        # origin / trust tier user-facing variants are handled by the
        # existing origin_badge component; not duplicated here.
    }

    def _friendly(s, fallback=None):
        if s is None:
            return fallback if fallback is not None else "—"
        key = str(s)
        if key in _FRIENDLY:
            return _FRIENDLY[key]
        # Last-ditch: replace underscores with spaces + title-case so
        # raw values are still readable.
        return key.replace("_", " ").title()
    app.jinja_env.filters["friendly_label"] = _friendly

    # `shield_unread_notifications`: context processor that injects the
    # logged-in user's unread Notification count + first page of items
    # into every template, so the top-nav bell can render its badge.
    @app.context_processor
    def _inject_notifications():
        from flask_login import current_user as _u
        if not getattr(_u, "is_authenticated", False):
            return {"shield_unread_notifications": 0,
                    "shield_recent_notifications": []}
        from .models import Notification as _N
        from .extensions import db as _db
        unread = (_db.session.query(_N)
                  .filter(_N.user_id == _u.id, _N.read_at.is_(None))
                  .count())
        recent = (_db.session.query(_N)
                  .filter(_N.user_id == _u.id)
                  .order_by(_N.created_at.desc())
                  .limit(5)
                  .all())
        return {"shield_unread_notifications": unread,
                "shield_recent_notifications": recent}

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
    from .spine.lifecycle import bp as lifecycle_bp
    from .spine.notifications import bp as notifications_bp
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
    app.register_blueprint(lifecycle_bp, url_prefix="/admin/lifecycle")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")

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
            or path.startswith("/notifications")
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

    @app.errorhandler(405)
    def _405(e):
        # v1.9: render 405s inside the app shell. Pre-fix, hitting a
        # POST-only route via GET produced a bare browser 405 page with
        # no header, nav, or way back. Reviewers correctly flagged that
        # broke recovery.
        return render_template("errors/405.html"), 405

    @app.errorhandler(500)
    def _500(e):
        return render_template("errors/500.html"), 500

    # --- CLI ---
    from .cli import register_cli
    register_cli(app)

    return app
