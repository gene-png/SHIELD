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

import hashlib
import json
import secrets
from datetime import datetime, timedelta

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
    ClientInvitation,
    ClientMembership,
    Deliverable,
    Message,
    Notification,
    Origin,
    PlatformType,
    Project,
    Role,
    ServiceRequest,
    User,
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
# Returning-client dashboard
# ====================================================================

SERVICE_LABELS = {
    "tech_debt":      "Tech Debt",
    "zero_trust":     "Zero Trust",
    "attack_surface": "Attack Surface",
}
# Maps a service_interest key to the platform enum used to find an
# existing project for the client. Drives the per-service card status.
_SERVICE_TO_PLATFORM = {
    "tech_debt":      PlatformType.TECH_DEBT,
    "zero_trust":     PlatformType.ZERO_TRUST,
    "attack_surface": PlatformType.ATTACK_SURFACE,
}


# State machine per round-3 §5. A card resolves to exactly one of
# these states. The dashboard's `_resolve_service_cards` walks the
# client's `service_interests` and any open/declined `ServiceRequest`
# rows, then evaluates per the precedence below:
#
#   project state > request state
#
# That precedence is the round-3 rule: "A service that has both an open
# request and an active project renders the project state, not the
# request state."
_CARD_STATES = (
    "requested",
    "declined",
    "setup",
    "awaiting_docs",
    "in_review",
    "ready_to_view",
    "complete",
)

# Project.stage → card state. Stages not in this map fall back to
# "in_review" (a project that's been started and isn't done is, from
# the client's POV, in review by their consultant).
_STAGE_TO_CARD = {
    "intake":            "awaiting_docs",
    "raw_intake":        "awaiting_docs",
    "client_repository": "awaiting_docs",
    "extraction_review":          "in_review",
    "overlap_analysis":           "in_review",
    "conversational_interrogation": "in_review",
    "current_state_assessment":   "in_review",
    "transition_roadmap":         "in_review",
    "attack_coverage":            "in_review",
    "admin_final":                "in_review",
    "archived": "complete",
    "complete": "complete",
}


def _service_card_state(client: Client, service_key: str) -> dict | None:
    """Resolve one card's state, or None if the service should produce
    no card at all.

    "Not interested" rule (round-3 §5): if the service is NOT in
    `client.service_interests` AND no `ServiceRequest` row exists,
    return None — render nothing.

    Returns a dict with at least `state`, `key`, `label`, and the
    relevant context for the card (project, request, deliverable as
    applicable).
    """
    platform = _SERVICE_TO_PLATFORM.get(service_key)
    label = SERVICE_LABELS.get(service_key, service_key)

    # Most-recent open request (if any) and most-recent declined.
    open_request = (
        db.session.query(ServiceRequest)
        .filter(
            ServiceRequest.client_id == client.id,
            ServiceRequest.service == service_key,
            ServiceRequest.fulfilled_project_id.is_(None),
            ServiceRequest.declined_at.is_(None),
        )
        .order_by(ServiceRequest.requested_at.desc())
        .first()
    )
    declined_request = (
        db.session.query(ServiceRequest)
        .filter(
            ServiceRequest.client_id == client.id,
            ServiceRequest.service == service_key,
            ServiceRequest.declined_at.is_not(None),
        )
        .order_by(ServiceRequest.declined_at.desc())
        .first()
    )

    # Active project for this platform (most recent, non-archived,
    # non-repository).
    project = None
    if platform is not None:
        project = (
            db.session.query(Project)
            .filter_by(client_id=client.id, platform=platform,
                       archived=False, is_client_repository=False)
            .order_by(Project.created_at.desc())
            .first()
        )

    # Project state takes precedence over request state per round-3 §5.
    if project is not None:
        deliverable = (
            db.session.query(Deliverable)
            .filter_by(client_id=client.id, project_id=project.id,
                       superseded_at=None)
            .order_by(Deliverable.finalized_at.desc())
            .first()
        )
        if deliverable is not None:
            state = "ready_to_view"
        else:
            state = _STAGE_TO_CARD.get(project.stage, "in_review")
        return {
            "key": service_key,
            "label": label,
            "state": state,
            "project": project,
            "deliverable": deliverable,
            "request": open_request,   # purely informational for the template
        }

    # No project. Check for an open request.
    if open_request is not None:
        return {
            "key": service_key,
            "label": label,
            "state": "requested",
            "project": None,
            "deliverable": None,
            "request": open_request,
        }

    # No project, no open request. If there's a declined request, that
    # surfaces as a "Declined" card with the reason and a re-request CTA.
    if declined_request is not None:
        return {
            "key": service_key,
            "label": label,
            "state": "declined",
            "project": None,
            "deliverable": None,
            "request": declined_request,
        }

    # No project, no request — only render a card if the service is
    # actively in service_interests (i.e. the client picked it on
    # welcome). Otherwise: no card (round-3 §5 "Not interested").
    if service_key in (client.service_interests or []):
        return {
            "key": service_key,
            "label": label,
            "state": "setup",   # picked but nothing started yet
            "project": None,
            "deliverable": None,
            "request": None,
        }
    return None


def _unread_count(client: Client, user) -> int:
    """How many messages in any of the client's threads this user
    hasn't read yet. Drives the "Messages (N)" badge in the nav."""
    user_id = user.id
    rows = (
        db.session.query(Message)
        .filter(Message.client_id == client.id)
        .all()
    )
    n = 0
    for m in rows:
        # The author always counts the message as read.
        if m.author_id == user_id:
            continue
        if not (m.read_at_map or {}).get(user_id):
            n += 1
    return n


@bp.route("/", methods=["GET"])
@login_required
def index():
    """Returning-client dashboard.

    Service cards driven by client.service_interests. Each card shows
    one of three states (awaiting / active / delivered). Plus a
    "recent activity" rail with the latest deliverables and messages.
    """
    client = _require_client(_current_client())

    # Build the universe of services to consider: anything in
    # service_interests, plus any service the client has open/declined
    # requests for, plus any service with an active project. The
    # resolver returns None for services with no card-worthy state.
    keys = set(client.service_interests or [])
    keys.update(
        r[0] for r in
        db.session.query(ServiceRequest.service)
        .filter_by(client_id=client.id).all()
    )
    # Also any platform we have a non-repo project for.
    for p in db.session.query(Project).filter_by(
        client_id=client.id, archived=False, is_client_repository=False,
    ).all():
        keys.add(p.platform.value)
    # Preserve a stable display order (the three real services first,
    # then anything else like 'unsure' which produces no card anyway).
    ordered_keys = [k for k in SERVICE_KEYS if k in keys] + [
        k for k in sorted(keys) if k not in SERVICE_KEYS
    ]

    cards = [c for c in (_service_card_state(client, k) for k in ordered_keys)
             if c is not None]

    recent_deliverables = (
        db.session.query(Deliverable)
        .filter_by(client_id=client.id, superseded_at=None)
        .order_by(Deliverable.finalized_at.desc())
        .limit(5)
        .all()
    )
    recent_messages = (
        db.session.query(Message)
        .filter_by(client_id=client.id)
        .order_by(Message.created_at.desc())
        .limit(5)
        .all()
    )
    unread_total = _unread_count(client, current_user)

    return render_template(
        "portal/dashboard.html",
        client=client,
        service_keys=SERVICE_KEYS,
        cards=cards,
        recent_deliverables=recent_deliverables,
        recent_messages=recent_messages,
        unread_total=unread_total,
    )


# ====================================================================
# Services — change which platforms the client is engaging on
# ====================================================================
# /portal/services is conceptually the same form as /portal/welcome
# but framed as a settings change. POST handler is shared with the
# welcome route so the audit-write path is identical.

@bp.route("/services", methods=["GET", "POST"])
@login_required
def services():
    client = _require_client(_current_client())
    if request.method == "POST":
        # Reuse the welcome handler's exact write logic by delegating.
        return welcome()
    return render_template(
        "portal/services.html",
        client=client,
        service_keys=SERVICE_KEYS,
        current_selection=set(client.service_interests or []),
        consult_requested=bool(client.consult_requested),
    )


# ====================================================================
# Request a service — round-3 §4
# ====================================================================
# A CLIENT clicks "Add another service" on the dashboard → opens this
# form → submits → writes a ServiceRequest row and bumps
# service_interests. Admin sees the request on /clients/queue and
# either fulfills it (creates a Project) or declines it with a reason.
# The dashboard card transitions from "Requested" to whatever the
# project's stage is once admin acts.

_VALID_REQUEST_SERVICES = frozenset({*SERVICE_KEYS, "unsure"})


@bp.route("/services/request", methods=["GET", "POST"])
@login_required
def services_request():
    client = _require_client(_current_client())

    if request.method == "POST":
        service = (request.form.get("service") or "").strip()
        notes = (request.form.get("notes") or "").strip() or None
        deadline_raw = (request.form.get("deadline") or "").strip()
        if service not in _VALID_REQUEST_SERVICES:
            flash("Pick which service you'd like help with.", "error")
            return redirect(url_for("portal.services_request"))

        deadline = None
        if deadline_raw:
            try:
                deadline = datetime.strptime(deadline_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Deadline didn't look like a date — leaving it blank.", "info")

        sr = ServiceRequest(
            client_id=client.id, requested_by=current_user.id,
            service=service, notes=notes, deadline=deadline,
        )
        db.session.add(sr)

        # Round-3 §4.3 "Optional but recommended" — also append to
        # service_interests so the dashboard card-resolver picks it up
        # regardless of whether the request stays open or gets fulfilled.
        # 'unsure' is a request flavor, not a real platform key, so we
        # skip it here (the dashboard renders nothing for unsure-only).
        if service in SERVICE_KEYS:
            interests = list(client.service_interests or [])
            if service not in interests:
                interests.append(service)
                client.service_interests = interests
        db.session.commit()

        log_audit(
            "client.service_requested",
            actor=current_user,
            target_type="service_request", target_id=sr.id,
            client_id=client.id,
            details={
                "service": service,
                "has_notes": bool(notes),
                "deadline": deadline.isoformat() if deadline else None,
            },
        )

        # Notify every ADMIN of the request (round-3 §6.2). Reviewers
        # don't get notified — the queue isn't theirs to action.
        admins = (
            db.session.query(User)
            .filter(User.role == Role.ADMIN, User.is_active_flag.is_(True))
            .all()
        )
        for u in admins:
            db.session.add(Notification(
                user_id=u.id, client_id=client.id,
                event_type="client.service_requested",
                title=f"New service request from {client.legal_name or client.name}",
                body=(notes[:200] if notes else None),
                link=url_for("clients.intake_view", client_id=client.id),
            ))
        if admins:
            db.session.commit()

        flash("Request submitted. Your consultant will reach out within one business day.", "info")
        return redirect(url_for("portal.index"))

    return render_template(
        "portal/services_request.html",
        client=client,
        service_keys=SERVICE_KEYS,
    )


# ====================================================================
# Deliverables — read-only, finalized snapshots
# ====================================================================

@bp.route("/deliverables/", methods=["GET"])
@login_required
def deliverables_list():
    client = _require_client(_current_client())
    rows = (
        db.session.query(Deliverable)
        .filter_by(client_id=client.id, superseded_at=None)
        .order_by(Deliverable.finalized_at.desc())
        .all()
    )
    # Group by project for readable rendering. Dict insertion order is
    # by the finalized_at ordering already, which matches "most recent
    # project first."
    grouped: dict[str, list[Deliverable]] = {}
    for d in rows:
        grouped.setdefault(d.project_id, []).append(d)

    return render_template(
        "portal/deliverables_list.html",
        client=client, grouped=grouped,
        projects_by_id={p.id: p for p in
                        db.session.query(Project)
                        .filter(Project.id.in_(grouped.keys())).all()},
    )


@bp.route("/deliverables/<deliverable_id>", methods=["GET"])
@login_required
def deliverable_detail(deliverable_id: str):
    client = _require_client(_current_client())
    d = db.session.get(Deliverable, deliverable_id)
    if d is None or d.client_id != client.id:
        abort(404)
    art = db.session.get(Artifact, d.artifact_id)
    project = db.session.get(Project, d.project_id)
    return render_template(
        "portal/deliverable_detail.html",
        client=client, deliverable=d, artifact=art, project=project,
    )


# ====================================================================
# Messages
# ====================================================================
# Two thread kinds:
#   - "general"       → project_id NULL, client-level conversation
#   - "<project_id>"  → per-project conversation
# The thread_key in the URL is the same as the routing key here.

GENERAL_THREAD = "general"


def _project_or_none(thread_key: str) -> Project | None:
    """Resolve a thread key to a Project, or None for the general thread."""
    if thread_key == GENERAL_THREAD:
        return None
    p = db.session.get(Project, thread_key)
    return p


def _mark_thread_read(client: Client, thread_key: str, user) -> None:
    """Stamp `user`'s first-read timestamp on every message they hadn't
    read yet. Idempotent — a second visit doesn't overwrite the timestamp.
    """
    project_id_filter = None if thread_key == GENERAL_THREAD else thread_key
    q = (
        db.session.query(Message)
        .filter(Message.client_id == client.id)
        .filter(Message.project_id == project_id_filter)
    )
    now = datetime.utcnow().isoformat() + "Z"
    changed = False
    for m in q.all():
        if m.author_id == user.id:
            continue
        rmap = dict(m.read_at_map or {})
        if user.id not in rmap:
            rmap[user.id] = now
            m.read_at_map = rmap
            changed = True
    if changed:
        db.session.commit()


@bp.route("/messages/", methods=["GET"])
@login_required
def messages_list():
    """Thread overview: the general thread + one per project."""
    client = _require_client(_current_client())

    # Build the project list and merge in the "general" virtual thread.
    project_rows = (
        db.session.query(Project)
        .filter_by(client_id=client.id, archived=False,
                   is_client_repository=False)
        .order_by(Project.created_at.desc())
        .all()
    )
    threads = [{"key": GENERAL_THREAD, "label": "General",
                "project": None}]
    for p in project_rows:
        threads.append({"key": p.id, "label": p.name, "project": p})

    # Per-thread latest + unread count.
    for t in threads:
        pid = None if t["key"] == GENERAL_THREAD else t["key"]
        msgs = (
            db.session.query(Message)
            .filter(Message.client_id == client.id,
                    Message.project_id == pid)
            .order_by(Message.created_at.desc())
            .all()
        )
        t["latest"] = msgs[0] if msgs else None
        t["unread"] = sum(
            1 for m in msgs
            if m.author_id != current_user.id
            and not (m.read_at_map or {}).get(current_user.id)
        )

    return render_template(
        "portal/messages_list.html", client=client, threads=threads,
    )


@bp.route("/messages/<thread_key>", methods=["GET", "POST"])
@login_required
def messages_thread(thread_key: str):
    client = _require_client(_current_client())
    project = _project_or_none(thread_key)
    if thread_key != GENERAL_THREAD:
        if project is None or project.client_id != client.id:
            abort(404)

    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Type a message first.", "error")
            return redirect(url_for("portal.messages_thread",
                                    thread_key=thread_key))
        m = Message(
            client_id=client.id,
            project_id=(project.id if project else None),
            author_id=current_user.id,
            body=body,
        )
        db.session.add(m)
        db.session.commit()
        log_audit(
            "message.posted",
            actor=current_user,
            target_type="message", target_id=m.id,
            project_id=(project.id if project else None),
            client_id=client.id,
            details={"length": len(body),
                     "thread": "general" if project is None else "project"},
        )
        return redirect(url_for("portal.messages_thread",
                                thread_key=thread_key))

    # GET — render thread, mark read, fetch author display names.
    _mark_thread_read(client, thread_key, current_user)
    pid = None if thread_key == GENERAL_THREAD else thread_key
    msgs = (
        db.session.query(Message)
        .filter(Message.client_id == client.id,
                Message.project_id == pid)
        .order_by(Message.created_at.asc())
        .all()
    )
    authors = {
        u.id: u for u in (
            db.session.query(User)
            .filter(User.id.in_({m.author_id for m in msgs} | {current_user.id}))
            .all()
        )
    }
    return render_template(
        "portal/messages_thread.html",
        client=client, project=project, thread_key=thread_key,
        messages=msgs, authors=authors,
    )


# ====================================================================
# Settings + invite-a-colleague
# ====================================================================

INVITE_EXPIRY_DAYS = 7


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _is_primary_poc(user, client) -> bool:
    m = (
        db.session.query(ClientMembership)
        .filter_by(client_id=client.id, user_id=user.id)
        .first()
    )
    return m is not None and m.membership_role == "primary_poc"


@bp.route("/settings/", methods=["GET", "POST"])
@login_required
def settings():
    client = _require_client(_current_client())
    if request.method == "POST":
        # Allow the user to update their own User fields here. This is
        # narrower than /portal/about (which writes to Client) — these
        # land on the User row.
        user = db.session.get(User, current_user.id)
        for col in ("display_name", "title", "phone"):
            val = (request.form.get(col) or "").strip() or None
            setattr(user, col, val)
        db.session.commit()
        flash("Saved.", "info")
        return redirect(url_for("portal.settings"))

    # List existing accepted members + pending invites for the
    # primary-POC's invite UI.
    members = (
        db.session.query(ClientMembership, User)
        .join(User, User.id == ClientMembership.user_id)
        .filter(ClientMembership.client_id == client.id)
        .order_by(ClientMembership.invited_at.asc())
        .all()
    )
    invites = (
        db.session.query(ClientInvitation)
        .filter_by(client_id=client.id, accepted_at=None, revoked_at=None)
        .order_by(ClientInvitation.expires_at.desc())
        .all()
    )
    return render_template(
        "portal/settings.html",
        client=client,
        me=db.session.get(User, current_user.id),
        members=members,
        invites=invites,
        is_primary_poc=_is_primary_poc(current_user, client),
        new_invite_url=request.args.get("invite_url"),
    )


@bp.route("/settings/invite", methods=["POST"])
@login_required
def settings_invite():
    """Create a tokenized invite. Per round-2 §10 answer: no email is
    sent; we redirect back to /portal/settings with the invite URL in
    the query string so the inviter can copy and forward it.
    """
    client = _require_client(_current_client())
    if not _is_primary_poc(current_user, client):
        abort(403)

    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("portal.settings"))

    plaintext = secrets.token_urlsafe(32)
    inv = ClientInvitation(
        client_id=client.id,
        invited_by=current_user.id,
        email=email,
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.session.add(inv)
    db.session.commit()
    log_audit(
        "client.invited_user",
        actor=current_user,
        target_type="client_invitation", target_id=inv.id,
        client_id=client.id,
        details={"email": email,
                 "expires_at": inv.expires_at.isoformat() + "Z"},
    )

    invite_url = url_for("portal.invitation_accept",
                         token=plaintext, _external=True)
    flash("Invite created. Share the link below.", "info")
    return redirect(url_for("portal.settings", invite_url=invite_url))


@bp.route("/settings/invite/<invite_id>/revoke", methods=["POST"])
@login_required
def settings_invite_revoke(invite_id: str):
    client = _require_client(_current_client())
    if not _is_primary_poc(current_user, client):
        abort(403)
    inv = db.session.get(ClientInvitation, invite_id)
    if inv is None or inv.client_id != client.id:
        abort(404)
    if inv.revoked_at is None and inv.accepted_at is None:
        inv.revoked_at = datetime.utcnow()
        db.session.commit()
        log_audit(
            "client.invitation_revoked",
            actor=current_user,
            target_type="client_invitation", target_id=inv.id,
            client_id=client.id,
            details={"email": inv.email},
        )
    return redirect(url_for("portal.settings"))


@bp.route("/invitations/accept/<token>", methods=["GET", "POST"])
@login_required
def invitation_accept(token: str):
    """Accept an invite. The invitee must already be logged in via
    Keycloak's existing OIDC flow (per round-2 §10 answer). The
    invite token only links email → membership; it doesn't create
    the auth account.

    Email-mismatch protection: the inviter typed an email when
    creating the invite. The invitee's logged-in email must match
    (case-insensitive) or we refuse — that way a forwarded link
    used by the wrong person doesn't grant them membership.
    """
    digest = _hash_token(token)
    inv = (
        db.session.query(ClientInvitation)
        .filter_by(token_hash=digest)
        .first()
    )
    now = datetime.utcnow()
    if inv is None:
        abort(404)
    if inv.revoked_at is not None:
        abort(404)
    if inv.accepted_at is not None:
        abort(404)
    if inv.expires_at < now:
        abort(404)
    if (current_user.email or "").lower() != inv.email.lower():
        flash(
            "This invitation was sent to a different email address. "
            "Log out and back in with that account, or ask the inviter "
            "to send a new invite to your email.",
            "error",
        )
        # 403 not 404 here because the link is real — we want to be
        # explicit that the auth is wrong.
        abort(403)

    if request.method == "POST":
        # Idempotent: if there's already a row, accept it; otherwise
        # create one.
        existing = (
            db.session.query(ClientMembership)
            .filter_by(client_id=inv.client_id, user_id=current_user.id)
            .first()
        )
        if existing is None:
            db.session.add(ClientMembership(
                client_id=inv.client_id,
                user_id=current_user.id,
                membership_role="member",
                invited_by_id=inv.invited_by,
                invited_at=inv.expires_at - timedelta(days=INVITE_EXPIRY_DAYS),
                accepted_at=now,
            ))
        elif existing.accepted_at is None:
            existing.accepted_at = now
        inv.accepted_at = now
        db.session.commit()
        log_audit(
            "client.user_joined",
            actor=current_user,
            target_type="client", target_id=inv.client_id,
            client_id=inv.client_id,
            details={"via_invitation_id": inv.id, "email": inv.email},
        )
        return redirect(url_for("portal.index"))

    client = db.session.get(Client, inv.client_id)
    return render_template(
        "portal/invitation_accept.html",
        client=client, inv=inv,
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


# ====================================================================
# Self-signup: bootstrap a Client + ClientMembership for a new user
# ====================================================================
# Keycloak's realm has registrationAllowed=true so anyone can create a
# Keycloak account. That gives them a User row (role=CLIENT by default).
# But CLIENT users with no ClientMembership are dead-ended at any
# scoped route. This route lets them name their organization, which
# atomically creates the Client + their primary_poc ClientMembership.

@bp.route("/start-organization", methods=["GET", "POST"])
@login_required
def start_organization():
    """First-login flow for self-signed-up users: name your org.

    GET renders a 1-field form. POST creates the Client row, the
    ClientMembership linking the user as primary_poc, audits, and
    redirects to /portal/welcome so the user walks the wizard.

    If the user already has an accepted membership, redirect them
    away — this route is only for the brand-new case.
    """
    # If they already have a Client, send them to the right place.
    existing_client = _current_client()
    if existing_client is not None:
        return redirect(landing_url_for(existing_client))

    if request.method == "POST":
        name = (request.form.get("organization_name") or "").strip()
        if not name:
            flash("Tell us what to call your organization.", "error")
            return redirect(url_for("portal.start_organization"))
        if len(name) > 255:
            flash("That name's a bit long — keep it under 255 characters.", "error")
            return redirect(url_for("portal.start_organization"))

        # Uniqueness: Client.name is UNIQUE in the DB. If the typed
        # name collides, append a short suffix so the create succeeds
        # rather than 500. The user can rename later from /portal/about.
        import uuid as _uuid
        proposed = name
        existing = db.session.query(Client).filter_by(name=proposed).first()
        if existing is not None:
            proposed = f"{name} ({_uuid.uuid4().hex[:6]})"

        client = Client(name=proposed, legal_name=name)
        db.session.add(client)
        db.session.flush()

        db.session.add(ClientMembership(
            client_id=client.id, user_id=current_user.id,
            membership_role="primary_poc",
            invited_at=datetime.utcnow(),
            accepted_at=datetime.utcnow(),
        ))
        db.session.commit()

        log_audit(
            "client.self_signup_bootstrap",
            actor=current_user,
            target_type="client", target_id=client.id, client_id=client.id,
            details={
                "legal_name": client.legal_name,
                "name_collision_suffix_applied": proposed != name,
            },
        )
        flash(f"Welcome to SHIELD, {client.legal_name}.", "info")
        return redirect(url_for("portal.welcome"))

    return render_template("portal/start_organization.html")


__all__ = ["bp", "landing_url_for", "SERVICE_KEYS"]


# Re-export so callers don't have to import json in templates.
def _ensure_json_default():  # pragma: no cover — type compat helper
    json.dumps({})
