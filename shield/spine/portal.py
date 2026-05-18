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
from typing import Any

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

# Round-8 §III: auto-create a stub Project for each selected service
# so the admin queue + platform indexes surface the engagement
# immediately (not just the Zero Trust path).
_SERVICE_TO_PLATFORM_TYPE = {
    "tech_debt":      "TECH_DEBT",
    "zero_trust":     "ZERO_TRUST",
    "attack_surface": "ATTACK_SURFACE",
}


def _ensure_projects_for_services(client_obj, service_keys) -> None:
    """For each service key in `service_keys`, make sure the client has
    a non-archived, non-repository Project of that platform.

    Idempotent; skips services that already have a project. The
    framework default for ZT is CISA ZTMM 2.0 (we can let the admin
    relink later from the workspace).
    """
    from ..models import PlatformType
    for key in service_keys:
        plat_name = _SERVICE_TO_PLATFORM_TYPE.get(key)
        if plat_name is None:
            continue
        platform = getattr(PlatformType, plat_name)
        existing = (
            db.session.query(Project)
            .filter(Project.client_id == client_obj.id,
                    Project.platform == platform,
                    Project.archived.is_(False),
                    Project.is_client_repository.is_(False))
            .first()
        )
        if existing is not None:
            continue
        label = {
            "tech_debt":      "Tech Debt",
            "zero_trust":     "Zero Trust",
            "attack_surface": "Attack Surface",
        }[key]
        framework = "cisa_ztmm_v2" if key == "zero_trust" else None
        p = Project(
            client_id=client_obj.id,
            platform=platform,
            name=f"{client_obj.legal_name or client_obj.name} — {label}",
            stage="intake",
            framework=framework,
            created_by_id=getattr(current_user, "id", None),
        )
        db.session.add(p)
        db.session.flush()
        log_audit(
            "project.create",
            actor=current_user if getattr(current_user, "is_authenticated", False) else None,
            target_type="project", target_id=p.id,
            project_id=p.id, client_id=client_obj.id,
            details={
                "platform": key,
                "framework": framework,
                "created_from": "service_selection_auto",
                "name": p.name,
            },
        )
    db.session.commit()


def _notify_admins_of_new_client(client_obj, kind: str = "self_signup") -> None:
    """Drop a Notification row for every active admin. Used on
    self-signup and on the first service-interest write so admins see
    new clients in the bell immediately.

    Reviewer §III: the platform was failing to fire any notification
    on new self-signups — the queue surfaced them, but only if an
    admin happened to refresh /clients/queue.
    """
    from ..models import Notification, Role, User as _User
    admins = (
        db.session.query(_User)
        .filter(_User.role == Role.ADMIN, _User.is_active_flag.is_(True))
        .all()
    )
    if not admins:
        return
    title_word = "registered" if kind == "self_signup" else "completed intake"
    title = f"New client {title_word}: {client_obj.legal_name or client_obj.name}"
    body = (
        "Open the admin queue to fulfill any service requests "
        "and assign a reviewer."
    )
    link = "/clients/queue"
    for u in admins:
        db.session.add(Notification(
            user_id=u.id,
            client_id=client_obj.id,
            event_type=f"client.{kind}",
            title=title,
            body=body,
            link=link,
        ))
    db.session.commit()


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
        # Round-8 §III: auto-create a Project for each NEWLY-selected
        # service. Previously only the Zero Trust path created a project
        # (via /portal/zero-trust's find-or-create). The reviewer caught
        # that Attack Surface was selected on welcome but had no
        # project anywhere — admins saw "wants: Attack Surface" with
        # no backing record. Now every selected service gets a stub
        # project on welcome so the admin queue + platform indexes
        # surface it.
        _ensure_projects_for_services(client, new_interests)
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
    """Step 2 of intake — about your org.

    Reviewer §IV: pre-populate the contact email from the user's
    session so they don't enter it a third time (after registration +
    initial sign-in). Same for name (mirrors User.display_name) +
    phone + title where the user's profile has them. The client can
    still override any pre-filled value.
    """
    client = _require_client(_current_client())
    if client.primary_poc_email is None and getattr(current_user, "email", None):
        client.primary_poc_email = current_user.email
    if client.primary_poc_name is None and getattr(current_user, "display_name", None):
        client.primary_poc_name = current_user.display_name
    user = db.session.get(User, current_user.id)
    if user is not None:
        if client.primary_poc_title is None and user.title:
            client.primary_poc_title = user.title
        if client.primary_poc_phone is None and user.phone:
            client.primary_poc_phone = user.phone
    db.session.commit()
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
        {name: "<col>", <col>: "<text>"}
    The server reads `name` (which column this is) from hx-vals and
    then reads the value from the same-named form field.

    v1.9.1 fix: previously every input shared `name="value"` and the
    server read `request.form.get("value")` — which returned the
    FIRST "value" param in the request (always legal_name's text)
    regardless of which input the user was editing. Result: every
    column got saved as legal_name. The template now gives each input
    its column name + an `hx-params` whitelist; the server reads the
    value via lookup so collisions can't happen.

    For backward compatibility we also accept the legacy
    `request.form.get("value")` path so any client still posting in
    the old shape doesn't 400.

    AND for the §IV intake-flow review item: we mirror profile-ish
    fields (primary_poc_name, primary_poc_title, primary_poc_phone)
    to the current_user's User row so the Settings profile picks
    them up without re-entry.
    """
    client = _require_client(_current_client())
    name = (request.form.get("name") or "").strip()
    if name not in _ABOUT_FIELDS:
        abort(400)
    # Read the value from the same-named field first; fall back to the
    # legacy "value" key if the client posted in the old shape.
    raw = request.form.get(name)
    if raw is None:
        raw = request.form.get("value") or ""
    cleaned = (raw or "").strip() or None
    setattr(client, name, cleaned)

    # Mirror personal contact fields onto the User row so the Settings
    # profile reflects the same data. Reviewer §IV called out the
    # double-entry pain: title + phone entered on the intake form
    # didn't show in Settings. (display_name is already populated by
    # Keycloak; we don't overwrite that.)
    _mirror_to_user_profile = {
        "primary_poc_title": "title",
        "primary_poc_phone": "phone",
    }
    if name in _mirror_to_user_profile:
        try:
            user = db.session.get(User, current_user.id)
            if user is not None:
                setattr(user, _mirror_to_user_profile[name], cleaned)
        except Exception:
            # Mirroring is best-effort; the primary write to Client is
            # the source of truth.
            pass

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
    """Documents page — two modes.

    v1.9 §1: split the onboarding-wizard rendering from the
    steady-state document library. Returning users (intake complete)
    hit a clean library page; users still finishing intake get the
    "Step 3 of 4" wizard framing. Same URL, mode chosen by the
    client's `intake_completed_at` stamp.
    """
    client = _require_client(_current_client())
    repo = _client_repository_project(client)
    uploads = (
        db.session.query(Artifact)
        .filter_by(project_id=repo.id, origin=Origin.HUMAN_INPUT)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    in_onboarding = client.intake_completed_at is None
    return render_template(
        "portal/documents.html",
        client=client, repo=repo, uploads=uploads,
        in_onboarding=in_onboarding,
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

# ====================================================================
# Zero Trust questionnaire — the client-facing entry point.
#
# Round-7: after intake the client should immediately be able to start
# answering the Zero Trust questions, not bounce to a dashboard with
# nothing to do. The existing P2 routes are admin-only; this is the
# matching client-side surface. Same QuestionnaireResponse table,
# same control catalog, locked at submit just like the P2 flow.
# ====================================================================

_VALID_ANSWERS = {"implemented", "partial", "not_implemented", "na"}


def _find_or_create_zt_project(client) -> Project:
    """Resolve the client's Zero Trust project, creating one if missing.

    Picks the framework from `client.compliance_frameworks` when
    available so an HHS-adjacent client lands on CSF and a federal
    civilian client lands on CISA ZTMM. Falls back to CISA ZTMM 2.0
    as the safe default.
    """
    p = (
        db.session.query(Project)
        .filter(Project.client_id == client.id,
                Project.platform == PlatformType.ZERO_TRUST,
                Project.archived.is_(False),
                Project.is_client_repository.is_(False))
        .order_by(Project.created_at.desc())
        .first()
    )
    if p is not None:
        return p
    # Compliance hint → framework choice (defaults to CISA ZTMM).
    frameworks = list(client.compliance_frameworks or [])
    if "nist_csf" in frameworks:
        framework = "nist_csf_v2"
    elif "dod_zt" in frameworks:
        framework = "dod_zt"
    else:
        framework = "cisa_ztmm_v2"
    p = Project(
        client_id=client.id,
        platform=PlatformType.ZERO_TRUST,
        name=f"{client.legal_name or client.name} — Zero Trust",
        stage="intake",
        framework=framework,
        created_by_id=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(p)
    db.session.commit()
    log_audit(
        "project.create",
        actor=current_user if current_user.is_authenticated else None,
        target_type="project", target_id=p.id,
        project_id=p.id, client_id=client.id,
        details={
            "platform": "zero_trust",
            "framework": framework,
            "created_from": "portal_zero_trust",
            "name": p.name,
        },
    )
    return p


@bp.route("/zero-trust", methods=["GET"])
@login_required
def zero_trust():
    """Client-side Zero Trust questionnaire.

    Resolves or creates a ZT project for the client, then renders the
    framework's controls as a one-question-per-row form. Each answer
    auto-saves on change; the client locks the answers explicitly with
    a "Submit final answers" button at the bottom.
    """
    from ..models import QuestionnaireResponse
    from ..p2_zerotrust.frameworks import FRAMEWORKS
    client = _require_client(_current_client())
    if "zero_trust" not in (client.service_interests or []):
        flash(
            "Zero Trust isn't one of your selected services. "
            "Add it from the services page if you'd like to start.",
            "info",
        )
        return redirect(url_for("portal.services"))
    project = _find_or_create_zt_project(client)
    framework = FRAMEWORKS.get(project.framework or "cisa_ztmm_v2")
    responses = {
        r.control_id: r
        for r in db.session.query(QuestionnaireResponse)
                  .filter_by(project_id=project.id)
    }
    is_submitted = project.stage == "submitted"
    return render_template(
        "portal/zero_trust.html",
        client=client, project=project, framework=framework,
        responses=responses, is_submitted=is_submitted,
        valid_answers=sorted(_VALID_ANSWERS),
    )


@bp.route("/zero-trust/answer", methods=["POST"])
@login_required
def zero_trust_answer():
    """Save a single answer. Mirrors p2.answer but CLIENT-allowed.

    Trust tier is always CLIENT_ASSERTED here — only the admin
    workspace creates ADMIN_ASSISTED rows. Locked responses refuse
    edits with a flash + redirect.

    v1.9 auto-save: when the request carries an `HX-Request` header
    (set by the HTMX library), return a tiny "saved ✓" fragment with
    200 instead of a flash+redirect. The template auto-saves each
    row's answer + rationale on change/blur instead of requiring a
    Save click per row.
    """
    from ..models import QuestionnaireResponse, TrustTier
    is_htmx = bool(request.headers.get("HX-Request"))
    client = _require_client(_current_client())
    project = _find_or_create_zt_project(client)

    def _resp_error(msg: str, anchor: str | None = None) -> Any:
        if is_htmx:
            return make_response(
                f'<span class="usa-hint" style="color:#b50909;">{msg}</span>',
                200,
            )
        flash(msg, "error")
        return redirect(url_for("portal.zero_trust") + (f"#{anchor}" if anchor else ""))

    def _resp_saved() -> Any:
        if is_htmx:
            return make_response(
                '<span class="usa-hint shield-portal-saved" style="color:#1a7733;">saved &#10003;</span>',
                200,
            )
        flash("Answer saved.", "info")
        return redirect(url_for("portal.zero_trust"))

    if project.stage == "submitted":
        return _resp_error("Answers are submitted and locked.")
    control_id = (request.form.get("control_id") or "").strip()
    ans = (request.form.get("answer") or "").strip()
    rationale = (request.form.get("rationale") or "").strip()
    if not control_id or ans not in _VALID_ANSWERS:
        return _resp_error("Pick a valid answer.", anchor=f"c-{control_id}")
    existing = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id, control_id=control_id)
        .one_or_none()
    )
    if existing and existing.locked:
        return _resp_error("That answer is locked.", anchor=f"c-{control_id}")
    if existing:
        existing.answer = ans
        existing.rationale = rationale
        existing.trust_tier = TrustTier.CLIENT_ASSERTED
        existing.attributed_user_id = current_user.id
    else:
        db.session.add(QuestionnaireResponse(
            project_id=project.id,
            framework=project.framework or "cisa_ztmm_v2",
            control_id=control_id,
            answer=ans,
            rationale=rationale,
            trust_tier=TrustTier.CLIENT_ASSERTED,
            attributed_user_id=current_user.id,
        ))
    db.session.commit()
    return _resp_saved()


# ====================================================================
# §21.7 — section-by-section progressive questionnaire (v1.9 item 2)
# ====================================================================
# Consumes the rich YAML catalogs from shield.p2_zerotrust.questionnaires
# instead of the flat legacy `frameworks.py` catalog. One section at a
# time, with per-question stem + cues + current-state + target-state +
# N/A + framework-mapping chips. Auto-saves each field via HTMX.
#
# QuestionnaireResponse.control_id stores the YAML question.id
# ("cisa.s1.q1" etc); QuestionnaireResponse.framework stores the
# questionnaire framework_id; the new `extra` JSON blob carries
# target_state_score, target_state_notes, not_applicable,
# not_applicable_reason, current_state_text.


# Map the YAML framework id ("cisa_ztmm_v2") to a stable Project.framework
# value. We pick the YAML id directly so the questionnaire loader can
# round-trip without translation.
def _yaml_framework_for_project(project) -> str:
    if project.framework in {"cisa_ztmm_v2", "dod_zt", "csf_2_0_high"}:
        return project.framework
    # Legacy projects might carry the older flat-catalog framework ids
    # ("nist_csf_v2", etc) — map them to a YAML-shipped variant where
    # possible, else default to CISA ZTMM.
    return "cisa_ztmm_v2"


def _resolve_questionnaire(project):
    """Loaded section-and-question catalog for this project, or None if
    the configured framework has no YAML variant on hand."""
    from ..p2_zerotrust.questionnaires import load_questionnaire
    try:
        return load_questionnaire(_yaml_framework_for_project(project))
    except (KeyError, FileNotFoundError):
        return None


def _section_completion(project, q):
    """Return {section_id: (answered_count, total_count)} so the
    overview + section header can show progress."""
    from ..models import QuestionnaireResponse
    responses = (db.session.query(QuestionnaireResponse)
                 .filter_by(project_id=project.id)
                 .all())
    answered_ids = {r.control_id for r in responses
                    if r.answer and r.answer.strip()}
    result = {}
    for section in q.sections:
        total = len(section.questions)
        done = sum(1 for question in section.questions
                   if question.id in answered_ids)
        result[section.id] = (done, total)
    return result


@bp.route("/zero-trust/sections", methods=["GET"])
@login_required
def zero_trust_sections():
    """Overview of the section-by-section questionnaire: progress per
    section + a "Resume" button that drops the user into the first
    unfinished section."""
    client = _require_client(_current_client())
    if "zero_trust" not in (client.service_interests or []):
        flash("Zero Trust isn't one of your selected services. ",
              "info")
        return redirect(url_for("portal.services"))
    project = _find_or_create_zt_project(client)
    q = _resolve_questionnaire(project)
    if q is None:
        flash(
            "No questionnaire is available for this framework yet — "
            "showing the one-page view instead.",
            "info",
        )
        return redirect(url_for("portal.zero_trust"))
    completion = _section_completion(project, q)
    # Find the first incomplete section (or 1 if everything's done).
    resume_number = 1
    for section in q.sections:
        done, total = completion[section.id]
        if done < total:
            resume_number = section.number
            break
    return render_template(
        "portal/zero_trust_overview.html",
        client=client, project=project, questionnaire=q,
        completion=completion, resume_number=resume_number,
        is_submitted=(project.stage == "submitted"),
    )


@bp.route("/zero-trust/section/<int:section_number>", methods=["GET"])
@login_required
def zero_trust_section(section_number: int):
    """One section's worth of questions, progressively-navigable."""
    from ..models import QuestionnaireResponse
    client = _require_client(_current_client())
    if "zero_trust" not in (client.service_interests or []):
        return redirect(url_for("portal.services"))
    project = _find_or_create_zt_project(client)
    q = _resolve_questionnaire(project)
    if q is None:
        return redirect(url_for("portal.zero_trust"))
    sections_list = list(q.sections)
    if section_number < 1 or section_number > len(sections_list):
        return redirect(url_for("portal.zero_trust_sections"))
    section = sections_list[section_number - 1]
    responses_by_qid = {
        r.control_id: r for r in
        db.session.query(QuestionnaireResponse).filter_by(project_id=project.id)
    }
    completion = _section_completion(project, q)
    return render_template(
        "portal/zero_trust_section.html",
        client=client, project=project, questionnaire=q,
        section=section, section_number=section_number,
        total_sections=len(sections_list),
        responses_by_qid=responses_by_qid,
        completion=completion,
        is_submitted=(project.stage == "submitted"),
    )


@bp.route("/zero-trust/section/answer", methods=["POST"])
@login_required
def zero_trust_section_answer():
    """Auto-save endpoint for the section-by-section flow.

    Accepts:
      question_id           — the YAML question.id (e.g. "cisa.s1.q1")
      current_state_score   — one of the framework's scale ids
      current_state_text    — freeform narrative
      target_state_score    — one of the framework's scale ids
      target_state_notes    — freeform why-this-target
      not_applicable        — "yes" or omitted
      not_applicable_reason — freeform N/A justification

    All fields are optional; the row updates whichever ones are sent.
    Always responds with the small HTMX `saved ✓` fragment.
    """
    from ..models import QuestionnaireResponse, TrustTier
    client = _require_client(_current_client())
    project = _find_or_create_zt_project(client)
    q = _resolve_questionnaire(project)
    is_htmx = bool(request.headers.get("HX-Request"))

    def _frag(msg: str, color: str) -> Any:
        return make_response(
            f'<span class="usa-hint" style="color:{color};">{msg}</span>',
            200,
        )

    if project.stage == "submitted":
        return _frag("locked", "#b50909") if is_htmx else (
            redirect(url_for("portal.zero_trust_sections")))
    qid = (request.form.get("question_id") or "").strip()
    if not q or not qid:
        return _frag("bad input", "#b50909")
    # Validate that the question id belongs to this framework's catalog.
    if q.question_by_id(qid) is None:
        return _frag("unknown question", "#b50909")

    valid_scale = {opt.id for opt in q.answer_scale}
    current_score = (request.form.get("current_state_score") or "").strip() or None
    current_text  = (request.form.get("current_state_text") or "").strip() or None
    target_score  = (request.form.get("target_state_score") or "").strip() or None
    target_notes  = (request.form.get("target_state_notes") or "").strip() or None
    na_on         = request.form.get("not_applicable") == "yes"
    na_reason     = (request.form.get("not_applicable_reason") or "").strip() or None
    if current_score and current_score not in valid_scale:
        return _frag("invalid current-state score", "#b50909")
    if target_score and target_score not in valid_scale:
        return _frag("invalid target-state score", "#b50909")

    response = (db.session.query(QuestionnaireResponse)
                .filter_by(project_id=project.id, control_id=qid)
                .one_or_none())
    framework_id = _yaml_framework_for_project(project)
    if response is None:
        response = QuestionnaireResponse(
            project_id=project.id,
            framework=framework_id,
            control_id=qid,
            answer=current_score or "",
            rationale=current_text or "",
            trust_tier=TrustTier.CLIENT_ASSERTED,
            attributed_user_id=current_user.id,
            extra={},
        )
        db.session.add(response)
    else:
        if response.locked:
            return _frag("locked", "#b50909")
        if current_score is not None:
            response.answer = current_score
        if current_text is not None:
            response.rationale = current_text
        response.attributed_user_id = current_user.id

    extra = dict(response.extra or {})
    if target_score is not None:
        extra["target_state_score"] = target_score
    if target_notes is not None:
        extra["target_state_notes"] = target_notes
    extra["not_applicable"] = na_on
    if na_reason is not None:
        extra["not_applicable_reason"] = na_reason
    response.extra = extra
    db.session.commit()
    return _frag("saved &#10003;", "#1a7733")


@bp.route("/zero-trust/section/submit", methods=["POST"])
@login_required
def zero_trust_section_submit():
    """Lock all answers across every section. Same effect as the
    legacy /portal/zero-trust/submit, just reachable from the bottom
    of section N (= total)."""
    return zero_trust_submit()


@bp.route("/zero-trust/submit", methods=["POST"])
@login_required
def zero_trust_submit():
    """Lock every answer and stamp the project as submitted.

    Mirrors p2.submit's contract — once locked, attribution is
    immutable. The auto-progress hook then queues the current-state
    assessment if the admin has it enabled.
    """
    from ..models import QuestionnaireResponse
    client = _require_client(_current_client())
    project = _find_or_create_zt_project(client)
    if project.stage == "submitted":
        flash("Answers are already submitted.", "info")
        return redirect(url_for("portal.zero_trust"))
    now = datetime.utcnow()
    locked = 0
    for r in (db.session.query(QuestionnaireResponse)
              .filter_by(project_id=project.id, locked=False)):
        r.locked = True
        r.submitted_at = now
        locked += 1
    project.stage = "submitted"
    db.session.commit()
    log_audit(
        "p2.submit",
        actor=current_user,
        target_type="project", target_id=project.id,
        project_id=project.id, client_id=project.client_id,
        details={"locked_responses": locked, "submitted_via": "portal"},
    )
    # Auto-progress: kick off the current-state assessment.
    from .auto_progress import maybe_auto_progress_p2_after_submit
    maybe_auto_progress_p2_after_submit(project, actor=current_user)
    flash(
        f"Submitted {locked} answer(s). Your consultant will review "
        "and run the current-state assessment next.",
        "info",
    )
    return redirect(url_for("portal.index"))


@bp.route("/confirm", methods=["GET", "POST"])
@login_required
def confirm():
    """Final step of the wizard. POST marks `intake_completed_at`.

    Round-7: if Zero Trust is one of the client's selected services,
    route them straight into the questionnaire so the engagement
    starts immediately instead of dead-ending on the dashboard with
    nothing to do.
    """
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
        if "zero_trust" in (client.service_interests or []):
            return redirect(url_for("portal.zero_trust"))
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
    """My Services — the overview/view page (v1.9 §1).

    The reviewer correctly flagged that this route used to drop the
    user straight into an edit form. The view shows each service as
    a card with its current status; the edit form lives at
    /portal/services/edit and is reachable via the "Change services"
    button at the bottom of this page.

    POST is retained on this URL for backward compatibility with
    earlier callers that submitted directly to /portal/services —
    routes the request through the same write logic as
    /portal/services/edit.
    """
    client = _require_client(_current_client())
    if request.method == "POST":
        return welcome()  # shared write path
    # Build status cards using the same resolver the dashboard uses
    # so the user sees identical state info on both pages.
    cards = []
    for key in SERVICE_KEYS:
        card = _service_card_state(client, key)
        if card is not None:
            cards.append(card)
    return render_template(
        "portal/services.html",
        client=client,
        cards=cards,
        consult_requested=bool(client.consult_requested),
    )


@bp.route("/services/edit", methods=["GET", "POST"])
@login_required
def services_edit():
    """The actual checkbox form. Same write logic as welcome."""
    client = _require_client(_current_client())
    if request.method == "POST":
        return welcome()
    return render_template(
        "portal/services_edit.html",
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

        # Round-7: for Zero Trust the client can start answering questions
        # immediately — no need to wait on admin fulfillment. The admin
        # notification still fires above so they know to engage; the
        # client meanwhile has a task to do rather than a static
        # "Requested" card on the dashboard.
        if service == "zero_trust":
            flash(
                "Request submitted — and we've started your questionnaire "
                "below. Your consultant will reach out about next steps.",
                "info",
            )
            return redirect(url_for("portal.zero_trust"))
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
        # Round-8 §III: notify every admin that a new client has
        # self-registered. Until this fix the audit row was the only
        # signal — admins didn't know unless they happened to load the
        # queue.
        _notify_admins_of_new_client(client, kind="self_signup")
        flash(f"Welcome to SHIELD, {client.legal_name}.", "info")
        return redirect(url_for("portal.welcome"))

    return render_template("portal/start_organization.html")


__all__ = ["bp", "landing_url_for", "SERVICE_KEYS"]


# Re-export so callers don't have to import json in templates.
def _ensure_json_default():  # pragma: no cover — type compat helper
    json.dumps({})
