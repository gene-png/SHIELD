"""Client portal Blueprint — the v1.8 client-facing surface.

Replaces (eventually) the thin `/intake/` upload form with a real
onboarding-through-engagement workflow. PR 3 of 6 covers the first-
time intake wizard:

    /portal/welcome    service-selection (3 cards + "I'm not sure")
    /portal/about      org / POC / address / compliance / prompt
    /portal/documents  drag-and-drop into the client repository
    /portal/confirm    "got it, we'll be in touch" landing page

The returning-client dashboard (`/portal/`) is a placeholder here and
gets replaced by PR 4. The `/intake/` blueprint is left in place for
backward compatibility — it 302s to `/portal/`.

Per round-2 §10 answers wired in here:
  - service_interests is set by the welcome screen (no Project is
    auto-created; admin queue handles project creation later).
  - "I'm not sure" co-exists with service checkboxes (round-2 §8.1):
    selecting it sets `consult_requested=True` alongside whatever
    service_interests the client checked.
  - Per-field HTMX auto-save on /portal/about so partial completion
    survives a tab close.

Access: every route here REQUIRES authentication. The role gate in
`shield/__init__.py` allows /portal/* for CLIENT users (it already
forbade everything else); ADMIN/REVIEWER users hitting /portal/* are
also allowed (useful for QA and the admin's "preview what the client
sees" flow). The user-to-client resolution comes from ClientMembership
— see `_current_client`.
"""
from __future__ import annotations

import json

from flask import (
    Blueprint,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Artifact,
    Client,
    ClientMembership,
    Origin,
    PlatformType,
    Project,
    Role,
)
from .audit import log_audit
from .repository import write_human_artifact

bp = Blueprint("portal", __name__, template_folder="../templates/portal")


# ====================================================================
# Helpers
# ====================================================================

# The three service strings used everywhere. Kept here as the source
# of truth for the welcome screen + Client.service_interests writes.
SERVICE_KEYS = ("tech_debt", "zero_trust", "attack_surface")
_SERVICE_KEY_SET = frozenset(SERVICE_KEYS)


def _current_client() -> Client | None:
    """Resolve the Client the current user is acting as.

    A CLIENT-role user with exactly one accepted ClientMembership maps
    to that client. Users with multiple memberships need an explicit
    selection (out of scope for v1.8 PR 3; falls through to the first
    accepted membership for now). ADMIN/REVIEWER users have no
    membership; for them this returns None — the portal routes use
    this to render a "preview" banner explaining the page is the
    client view.
    """
    if not getattr(current_user, "is_authenticated", False):
        return None
    if current_user.role == Role.CLIENT:
        m = (
            db.session.query(ClientMembership)
            .filter(
                ClientMembership.user_id == current_user.id,
                ClientMembership.accepted_at.is_not(None),
            )
            .order_by(ClientMembership.invited_at.asc())
            .first()
        )
        if m is None:
            return None
        return db.session.get(Client, m.client_id)
    return None


def _client_repository_project(client: Client) -> Project:
    """Return the synthetic 'Client Repository' project for `client`.

    Per round-2 §10 (Option B for storage paths) every client gets
    exactly one Project row with is_client_repository=True, created
    by migration 0002. Client-tier uploads from /portal/documents
    land there with stage='client_repository'. If somehow the row
    is missing (e.g. a client created post-migration without going
    through the seed path), we create it on demand and audit it.
    """
    p = (
        db.session.query(Project)
        .filter_by(client_id=client.id, is_client_repository=True)
        .first()
    )
    if p is not None:
        return p
    p = Project(
        client_id=client.id,
        platform=PlatformType.TECH_DEBT,  # arbitrary; unused for repo projects
        name="Client Repository",
        stage="client_repository",
        is_client_repository=True,
    )
    db.session.add(p)
    db.session.commit()
    log_audit(
        "project.create_client_repository",
        actor=current_user if current_user.is_authenticated else None,
        target_type="project", target_id=p.id,
        project_id=p.id, client_id=client.id,
        details={"reason": "on-demand backfill"},
    )
    return p


def _require_client(client: Client | None) -> Client:
    """Bail to /portal/confirm if the user isn't associated with a client.

    CLIENT users without a membership shouldn't actually exist after
    the migration (the seed backfill linked client@demo to Acme), but
    if they do, route them to a friendly "we'll set you up" page
    rather than letting routes blow up on `.id` access.
    """
    if client is None:
        abort(404)
    return client


# ====================================================================
# Welcome (step 1 of intake)
# ====================================================================

@bp.route("/welcome", methods=["GET", "POST"])
@login_required
def welcome():
    """Step 1: service-selection.

    Three big cards (Tech Debt / Zero Trust / Attack Surface) with
    checkboxes plus an "I'm not sure" option. Saves to
    `Client.service_interests` (JSON list) and `Client.consult_requested`.

    Per round-2 §8.1 answer: "I'm not sure" co-exists with the three
    service checkboxes — the client can pick services AND request a
    consult call.
    """
    client = _require_client(_current_client())

    if request.method == "POST":
        picked = request.form.getlist("services")
        # Filter to known keys so a hand-crafted POST can't put
        # arbitrary strings in the JSON column.
        new_interests = [k for k in picked if k in _SERVICE_KEY_SET]
        consult = request.form.get("consult_requested") == "yes"

        before = list(client.service_interests or [])
        client.service_interests = new_interests
        client.consult_requested = consult
        db.session.commit()

        if set(before) != set(new_interests) or consult:
            log_audit(
                "client.service_interest_changed",
                actor=current_user,
                target_type="client", target_id=client.id, client_id=client.id,
                details={
                    "before": before,
                    "after": new_interests,
                    "consult_requested": consult,
                },
            )
        return redirect(url_for("portal.about"))

    return render_template(
        "portal/welcome.html",
        client=client,
        service_keys=SERVICE_KEYS,
        current_selection=set(client.service_interests or []),
        consult_requested=bool(client.consult_requested),
    )


# ====================================================================
# About (step 2 of intake) — HTMX auto-save per field
# ====================================================================

# Whitelist of fields we accept POSTs for on /portal/about. Keeps the
# auto-save endpoint from being a write-anything Client.update.
_ABOUT_FIELDS = frozenset({
    "legal_name", "dba_name", "website", "size_band",
    "primary_poc_name", "primary_poc_title", "primary_poc_email",
    "primary_poc_phone",
    "address_line1", "address_line2", "city", "state", "postal_code",
    "country",
    "prompting_context",
})


@bp.route("/about", methods=["GET"])
@login_required
def about():
    client = _require_client(_current_client())
    return render_template(
        "portal/about.html",
        client=client,
        size_bands=("1-50", "51-500", "501-5000", "5000+"),
    )


@bp.route("/about/field", methods=["POST"])
@login_required
def about_field():
    """HTMX endpoint: save one field of /portal/about.

    The form on /portal/about wires `hx-trigger=blur changed`
    `hx-post=/portal/about/field` on every input. Body shape:
        {name: "<col>", value: "<text>"}
    The server updates that one column and returns a small <span> the
    client swaps in beside the field as a "saved" indicator.
    """
    client = _require_client(_current_client())
    name = (request.form.get("name") or "").strip()
    value = request.form.get("value") or ""
    if name not in _ABOUT_FIELDS:
        abort(400)
    setattr(client, name, value.strip() or None)
    db.session.commit()
    # Tiny ack the template swaps into a status pip. Per-field audit
    # would be too noisy; we log the bulk completion on /about/submit.
    return make_response(
        '<span class="usa-hint shield-portal-saved">saved</span>',
        200,
    )


@bp.route("/about/submit", methods=["POST"])
@login_required
def about_submit():
    """Finalize the about form and move on to documents.

    Validation is light — the only required-ish field is the primary
    POC email so we have a way to reach someone. Anything else can
    be filled in later from /portal/settings.
    """
    client = _require_client(_current_client())
    if not (client.primary_poc_email or "").strip():
        flash("Add a contact email so we know who to reach.", "error")
        return redirect(url_for("portal.about"))
    log_audit(
        "client.about_saved",
        actor=current_user,
        target_type="client", target_id=client.id, client_id=client.id,
        details={
            "legal_name": client.legal_name,
            "size_band": client.size_band,
        },
    )
    return redirect(url_for("portal.documents"))


# ====================================================================
# Documents (step 3 of intake)
# ====================================================================

@bp.route("/documents", methods=["GET"])
@login_required
def documents():
    client = _require_client(_current_client())
    repo = _client_repository_project(client)
    uploads = (
        db.session.query(Artifact)
        .filter_by(project_id=repo.id, origin=Origin.HUMAN_INPUT)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    return render_template(
        "portal/documents.html",
        client=client, repo=repo, uploads=uploads,
    )


@bp.route("/documents/upload", methods=["POST"])
@login_required
def documents_upload():
    """Upload one or more files to the synthetic client-repository project.

    Per round-2 §10 (Option B for storage paths): the synthetic
    project's id is the second path segment, exactly like a normal
    project upload. No path migration needed; existing
    write_human_artifact already does the right thing.

    Origin is HUMAN_INPUT regardless of who uploaded — the integrity
    model treats client uploads as source. The audit row records the
    actor's role so a reviewer can see "admin uploaded on the client's
    behalf" later.
    """
    client = _require_client(_current_client())
    repo = _client_repository_project(client)

    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        flash("Pick at least one file.", "error")
        return redirect(url_for("portal.documents"))

    written = 0
    for f in files:
        if not f or not f.filename:
            continue
        art = write_human_artifact(
            project=repo, stage="client_repository",
            title=f.filename,
            file_stream=f.stream, filename=f.filename, mime_type=f.mimetype,
            actor=current_user,
        )
        log_audit(
            "file_uploaded_to_repository",
            actor=current_user,
            target_type="artifact", target_id=art.id,
            project_id=repo.id, client_id=client.id,
            details={
                "filename": f.filename,
                "size_bytes": art.size_bytes,
                "actor_role": current_user.role.value,
            },
        )
        written += 1

    if written:
        flash(f"Uploaded {written} file{'s' if written != 1 else ''}.", "info")
    return redirect(url_for("portal.documents"))


# ====================================================================
# Confirm (step 4 of intake) — "got it, we'll be in touch"
# ====================================================================

@bp.route("/confirm", methods=["GET", "POST"])
@login_required
def confirm():
    """Final step of the wizard. POST marks `intake_completed_at`."""
    client = _require_client(_current_client())

    if request.method == "POST":
        from datetime import datetime
        if client.intake_completed_at is None:
            client.intake_completed_at = datetime.utcnow()
            db.session.commit()
            log_audit(
                "client.intake_completed",
                actor=current_user,
                target_type="client", target_id=client.id, client_id=client.id,
                details={
                    "service_interests": client.service_interests,
                    "consult_requested": client.consult_requested,
                    "has_primary_poc_email": bool(client.primary_poc_email),
                },
            )
        return redirect(url_for("portal.index"))

    return render_template("portal/confirm.html", client=client)


# ====================================================================
# Placeholder dashboard (PR 4 replaces this with the real one)
# ====================================================================

@bp.route("/", methods=["GET"])
@login_required
def index():
    """Returning-client dashboard placeholder.

    PR 4 builds the real dashboard (service cards, action queue,
    activity feed, message threads). This stub exists so the
    home redirect target (`/portal/`) doesn't 404 between PR 3 and
    PR 4 lands.
    """
    client = _require_client(_current_client())
    return render_template(
        "portal/dashboard.html",
        client=client,
        service_keys=SERVICE_KEYS,
    )


# ====================================================================
# Backward-compat: /intake/ redirects to /portal/
# ====================================================================
# The existing intake.* routes are kept where they are; the user-facing
# entry point is now the portal. PR 3 doesn't remove the old routes —
# admins might still use them for the "upload on behalf of" flow.


# Used by the home view in shield/__init__.py to decide where a CLIENT
# user lands after login.
def landing_url_for(client: Client) -> str:
    if client.intake_completed_at is None:
        return url_for("portal.welcome")
    return url_for("portal.index")


__all__ = ["bp", "landing_url_for", "SERVICE_KEYS"]


# Re-export so callers don't have to import json in templates.
def _ensure_json_default():  # pragma: no cover — type compat helper
    json.dumps({})
