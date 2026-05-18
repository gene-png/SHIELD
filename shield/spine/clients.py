"""Clients Blueprint: top-tier client browsing + capability-list views.

v1.8 PR 5 additions:
  - /clients/queue       admin's default landing — clients with
                         pending intake or unactioned service
                         interests, separated by state.
  - /clients/<id>/intake admin's read-only view of what the client
                         submitted on /portal/welcome → /portal/about.
  - POST /clients/<id>/adopt-artifact/<artifact_id>
                         link a client-repository artifact to a
                         specific Project. Audited.
"""
from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Artifact,
    CapabilityList,
    Client,
    Notification,
    Origin,
    PlatformType,
    Project,
    ServiceRequest,
)
from .access import require_client_for_param, user_clients
from .audit import log_audit
from .exporters import capability_list_to_xlsx
from .rbac import admin_only

bp = Blueprint("clients", __name__, template_folder="../templates/clients")


@bp.route("/")
@login_required
def index():
    # Scoped: admins see every client; reviewers see assigned (or all
    # if un-assigned); clients see only their own organization.
    clients = user_clients()
    return render_template("clients/list.html", clients=clients)


# ====================================================================
# /clients/queue — admin's new default landing
# ====================================================================

@bp.route("/queue")
@login_required
@admin_only
def queue():
    """Admin action queue.

    Three groups, computed in one pass:
      1. New leads             — intake_completed_at IS NULL
      2. Waiting on us         — intake complete, has service interests,
                                  no matching Project yet
      3. Active                — has at least one non-repository Project
                                  (excluded from groups 1 + 2)

    Plus a "messages with unread admin replies needed" count per
    client so the queue surfaces conversational work too.
    """
    # We're admin (decorator above), so the user_clients() scope is
    # unrestricted — every client lands in the listing.
    all_clients = user_clients()

    # Round-3 §6: open service requests are their own queue row type.
    # Pull them in one query; bucket by client below.
    open_requests_by_client: dict[str, list[ServiceRequest]] = {}
    for sr in (
        db.session.query(ServiceRequest)
        .filter(ServiceRequest.fulfilled_project_id.is_(None),
                ServiceRequest.declined_at.is_(None))
        .order_by(ServiceRequest.requested_at.asc())
        .all()
    ):
        open_requests_by_client.setdefault(sr.client_id, []).append(sr)

    new_leads: list[Client] = []
    waiting: list[dict] = []
    active: list[dict] = []

    for c in all_clients:
        # Per-client projects (excluding the synthetic repo project).
        real_projects = [
            p for p in c.projects
            if not p.is_client_repository and not p.archived
        ]
        if c.intake_completed_at is None:
            new_leads.append(c)
            continue

        wanted = set(c.service_interests or [])
        existing = {p.platform.value for p in real_projects}
        # Service interests the client expressed that have NO matching
        # Project. Each pair is a "create the project, please" action.
        gaps = wanted - existing

        # Unread messages from this client to us. We treat a message
        # as "needs admin response" if the author is NOT an admin and
        # no consultant has marked it read yet. Strict enough for a
        # queue without spamming.
        from ..models import Message, Role, User
        msgs = (
            db.session.query(Message).filter_by(client_id=c.id).all()
        )
        author_ids = {m.author_id for m in msgs}
        roles_by_id = {
            u.id: u.role for u in
            db.session.query(User).filter(User.id.in_(author_ids)).all()
        } if author_ids else {}
        admin_replies_needed = sum(
            1 for m in msgs
            if roles_by_id.get(m.author_id) == Role.CLIENT
            and not any(
                u != m.author_id and roles_by_id.get(u) == Role.ADMIN
                for u in (m.read_at_map or {}).keys()
            )
        )

        open_requests = open_requests_by_client.get(c.id, [])

        # Round-8 §III: "waiting since" — for the Waiting bucket, the
        # most useful timestamp is when the client finished intake (the
        # moment the ball moved to us); for Active, the timestamp of
        # the earliest open project. Lets admin prioritize stale rows.
        if open_requests:
            waiting_since = min(sr.requested_at for sr in open_requests)
        else:
            waiting_since = c.intake_completed_at
        earliest_project_at = (
            min((p.created_at for p in real_projects), default=None)
        )
        row = {
            "client": c,
            "gaps": sorted(gaps),
            "projects": real_projects,
            "consult_requested": bool(c.consult_requested),
            "admin_replies_needed": admin_replies_needed,
            "open_requests": open_requests,
            "waiting_since": waiting_since,
            "earliest_project_at": earliest_project_at,
        }
        if gaps or c.consult_requested or open_requests:
            waiting.append(row)
        elif real_projects:
            active.append(row)

    # Round-8 §III: failed-jobs banner. Surface the count of failed RQ
    # jobs at the top of the queue so admins notice without having to
    # navigate to /jobs/.
    failed_jobs_count = 0
    try:
        from rq.job import JobStatus
        from ..tasks import get_queue
        q = get_queue()
        failed_jobs_count = q.failed_job_registry.count
    except Exception:
        # Best-effort — never block the queue page on Redis being down.
        failed_jobs_count = 0

    return render_template(
        "clients/queue.html",
        new_leads=new_leads,
        waiting=waiting,
        active=active,
        failed_jobs_count=failed_jobs_count,
    )


# ====================================================================
# Service request fulfill / decline (round-3 §6)
# ====================================================================
# Admin's actions on a ServiceRequest from /clients/queue.

_SERVICE_TO_PLATFORM_ADMIN = {
    "tech_debt":      PlatformType.TECH_DEBT,
    "zero_trust":     PlatformType.ZERO_TRUST,
    "attack_surface": PlatformType.ATTACK_SURFACE,
}


def _notify_client_users(client_id: str, *, event_type: str, title: str,
                         body: str | None, link: str) -> None:
    """Write a Notification row for every accepted member of `client_id`.

    Used by fulfill / decline so the client's bell catches up next time
    they're in the portal.
    """
    from ..models import ClientMembership
    rows = (
        db.session.query(ClientMembership)
        .filter(
            ClientMembership.client_id == client_id,
            ClientMembership.accepted_at.is_not(None),
        )
        .all()
    )
    for m in rows:
        db.session.add(Notification(
            user_id=m.user_id, client_id=client_id,
            event_type=event_type,
            title=title, body=body, link=link,
        ))


@bp.route("/<client_id>/requests/<request_id>/fulfill", methods=["GET", "POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def fulfill_request(client_id: str, request_id: str):
    """Create a real Project from a ServiceRequest.

    GET renders a small form pre-filled from the request (platform,
    suggested project name + the client's notes). POST creates the
    Project, sets `sr.fulfilled_project_id`, writes audit +
    notification, redirects to the new project's workspace.

    'unsure' requests can't be fulfilled this way — admin should
    decline-with-reason or have a conversation in messages first.
    """
    sr = db.session.get(ServiceRequest, request_id)
    if sr is None or sr.client_id != client_id:
        abort(404)
    if not sr.is_open:
        flash("This request is already resolved.", "info")
        return redirect(url_for("clients.intake_view", client_id=client_id))
    platform = _SERVICE_TO_PLATFORM_ADMIN.get(sr.service)
    if platform is None:
        flash("'I'm not sure' requests can't be fulfilled directly — "
              "reply in messages or decline with a reason.", "error")
        return redirect(url_for("clients.intake_view", client_id=client_id))

    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    from ..p2_zerotrust.frameworks import FRAMEWORKS

    if request.method == "POST":
        # Service is locked to the request's service (round-4 §3.2);
        # _create_project_from_form respects default_service.
        project, error = _create_project_from_form(
            client, request.form, default_service=sr.service,
        )
        if error:
            flash(error, "error")
            return redirect(url_for("clients.fulfill_request",
                                    client_id=client_id, request_id=request_id))

        # Round-4 §3.2 keeps the shared partial minimal; admins set
        # the optional client-facing label later from the project's
        # workspace, not here.
        db.session.add(project)
        db.session.flush()   # need project.id for the FK below

        sr.fulfilled_project_id = project.id

        log_audit(
            "project.created",
            actor=current_user,
            target_type="project", target_id=project.id,
            project_id=project.id, client_id=client.id,
            details={
                "platform": project.platform.value,
                "created_from": "fulfill_flow",
                "source_request_id": sr.id,
                "framework": project.framework,
            },
        )
        log_audit(
            "client.service_request_fulfilled",
            actor=current_user,
            target_type="service_request", target_id=sr.id,
            project_id=project.id, client_id=client.id,
            details={
                "service": sr.service,
                "project_id": project.id,
                "project_name": project.name,
            },
        )

        _notify_client_users(
            client.id,
            event_type="client.service_request_fulfilled",
            title=f"Your {sr.service.replace('_', ' ')} project is starting.",
            body=project.name,
            link=url_for("portal.index"),
        )
        db.session.commit()

        flash(f"Project '{project.name}' created. The client's dashboard will reflect it.", "info")
        return redirect(url_for(_WORKSPACE_ENDPOINT[platform], project_id=project.id))

    # Default project name suggestion follows the partial's pattern:
    # "{client label} — {service display} {year}". Pre-populated into
    # the partial's new_project_name input.
    return render_template(
        "clients/fulfill_request.html",
        client=client, request_=sr, platform=platform,
        frameworks=FRAMEWORKS,
        # Shared-partial parameters for the fulfill case:
        submit_url=url_for("clients.fulfill_request",
                           client_id=client.id, request_id=sr.id),
        cancel_url=url_for("clients.intake_view", client_id=client.id),
        show_existing_radio=False,
        lock_service=True,
        default_service=sr.service,
        submit_label_new=f"Create {sr.service.replace('_', ' ')} project",
        now_year=datetime.utcnow().year,
    )


@bp.route("/<client_id>/requests/<request_id>/decline", methods=["GET", "POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def decline_request(client_id: str, request_id: str):
    """Mark a ServiceRequest declined with a reason."""
    sr = db.session.get(ServiceRequest, request_id)
    if sr is None or sr.client_id != client_id:
        abort(404)
    if not sr.is_open:
        flash("This request is already resolved.", "info")
        return redirect(url_for("clients.intake_view", client_id=client_id))

    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        if len(reason) < 10:
            flash("Give a short reason so the client knows why.", "error")
            return redirect(url_for("clients.decline_request",
                                    client_id=client_id, request_id=request_id))
        from datetime import datetime as _dt
        sr.declined_at = _dt.utcnow()
        sr.declined_reason = reason
        db.session.commit()

        log_audit(
            "client.service_request_declined",
            actor=current_user,
            target_type="service_request", target_id=sr.id,
            client_id=client.id,
            details={"service": sr.service, "reason": reason},
        )
        _notify_client_users(
            client.id,
            event_type="client.service_request_declined",
            title=f"{sr.service.replace('_', ' ').title()} request not a fit right now.",
            body=reason,
            link=url_for("portal.index"),
        )
        db.session.commit()

        flash("Request declined. The client's dashboard will show your reason.", "info")
        return redirect(url_for("clients.intake_view", client_id=client_id))

    return render_template(
        "clients/decline_request.html",
        client=client, request_=sr,
    )


# ====================================================================
# /clients/<id>/intake — admin's read-only view of submitted intake
# ====================================================================

# Whitelist of Client columns the reset_intake_fields admin verb may
# touch. Mirrors `_ABOUT_FIELDS` in shield.spine.portal so the reset
# clears exactly the fields the /portal/about form writes — nothing
# else (industry, notes, service_interests, intake_completed_at all
# stay intact so the admin doesn't have to re-classify the client).
_ABOUT_FIELDS_RESETTABLE = frozenset({
    "legal_name", "dba_name", "website", "size_band",
    "primary_poc_name", "primary_poc_title", "primary_poc_email",
    "primary_poc_phone",
    "address_line1", "address_line2", "city", "state", "postal_code",
    "country",
    "prompting_context",
})


@bp.route("/<client_id>/intake/reset", methods=["POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def reset_intake_fields(client_id: str):
    """Clear every field the /portal/about form writes.

    Use case: the v1.8 /portal/about form had an HTMX scoping bug
    that wrote whichever input was first in the form to every column
    on each blur. v1.9 fixed the form (`hx-include="this"`) but
    existing rows that were saved before the fix still have the
    corruption baked in. This admin verb resets those rows back to
    NULL so the client can re-fill /portal/about cleanly.

    Does NOT touch service_interests, consult_requested, or
    intake_completed_at — those are correct even when the contact /
    address fields are corrupted. Audited.
    """
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    typed = (request.form.get("confirmation_phrase") or "").strip()
    if typed != client.name:
        flash(
            "Type the client's exact name to confirm the reset. "
            "Nothing was changed.",
            "error",
        )
        return redirect(url_for("clients.intake_view", client_id=client.id))
    cleared = []
    for col in _ABOUT_FIELDS_RESETTABLE:
        if getattr(client, col, None):
            setattr(client, col, None)
            cleared.append(col)
    db.session.commit()
    log_audit(
        "client.intake_fields_reset",
        actor=current_user,
        target_type="client", target_id=client.id, client_id=client.id,
        details={"cleared_fields": cleared,
                 "reason": "admin-triggered remediation for HTMX form bug"},
    )
    flash(
        f"Reset {len(cleared)} field(s). The client can refill them "
        f"via /portal/about and the values will save correctly now.",
        "info",
    )
    return redirect(url_for("clients.intake_view", client_id=client.id))


@bp.route("/<client_id>/intake")
@login_required
@require_client_for_param("client_id")
def intake_view(client_id: str):
    """Read-only view of what the client filled in on /portal/.

    Surfaces every Client metadata field + the documents they uploaded
    to the synthetic Client Repository project. The screen the admin
    opens before the kickoff call.
    """
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    # Documents in the client repository (origin=human_input).
    repo = (
        db.session.query(Project)
        .filter_by(client_id=client.id, is_client_repository=True)
        .first()
    )
    repo_uploads: list[Artifact] = []
    if repo is not None:
        repo_uploads = list(
            db.session.query(Artifact)
            .filter_by(project_id=repo.id, origin=Origin.HUMAN_INPUT)
            .order_by(Artifact.created_at.desc())
            .all()
        )

    # Real projects (not the synthetic repo) for the adoption UI.
    real_projects = [
        p for p in client.projects
        if not p.is_client_repository and not p.archived
    ]

    # v1.9.2: auto-detect the pre-v1.9.1 "every field saved as legal_name"
    # corruption shape. If 3+ of the intake fields share the same
    # non-empty value, the row almost certainly hit the old form bug.
    # We flag this so the template can render a prominent banner with
    # the reset button — admins shouldn't have to know to expand a
    # small <details> toggle.
    from collections import Counter
    intake_values = [
        getattr(client, col, None) for col in _ABOUT_FIELDS_RESETTABLE
    ]
    non_empty = [v for v in intake_values if v]
    most_common_count = (Counter(non_empty).most_common(1)[0][1]
                         if non_empty else 0)
    looks_corrupted = most_common_count >= 3

    return render_template(
        "clients/intake_view.html",
        client=client, repo_uploads=repo_uploads,
        repo=repo, real_projects=real_projects,
        looks_corrupted=looks_corrupted,
    )


# Maps a service string to the corresponding PlatformType. Also used
# by the post-submit redirect to figure out which workspace endpoint
# to send the admin to. Kept module-level so fulfill / create-from-
# client-detail can share it.
_SERVICE_PLATFORM_PAIRS = (
    ("tech_debt",      PlatformType.TECH_DEBT,      "p1.workspace"),
    ("zero_trust",     PlatformType.ZERO_TRUST,     "p2.workspace"),
    ("attack_surface", PlatformType.ATTACK_SURFACE, "p3.workspace"),
)
_PLATFORM_BY_SERVICE = {svc: plat for svc, plat, _ in _SERVICE_PLATFORM_PAIRS}
_WORKSPACE_ENDPOINT = {plat: ep for _, plat, ep in _SERVICE_PLATFORM_PAIRS}


def _create_project_from_form(client, form, default_service=None):
    """Build (but don't commit) a Project from the shared create-form.

    Returns (project, error) — error is a flash-able string on failure,
    None on success. The caller commits within the request transaction.

    Validates:
      - service must be one of the three platform keys (default_service
        overrides what's on the form, for fulfill / locked-service flows)
      - name must be non-empty
      - zero_trust must have a framework set
    """
    from ..p2_zerotrust.frameworks import FRAMEWORKS
    service = (form.get("new_project_service") or default_service or "").strip()
    if service not in _PLATFORM_BY_SERVICE:
        return None, "Pick a service for the new project."
    name = (form.get("new_project_name") or "").strip()
    if not name:
        return None, "Project name can't be empty."
    framework = None
    if service == "zero_trust":
        framework = (form.get("new_project_framework") or "").strip() or None
        if not framework:
            return None, "Pick a framework for the Zero Trust assessment."
        if framework not in FRAMEWORKS:
            return None, "That framework isn't one of the supported options."
    project = Project(
        client_id=client.id,
        platform=_PLATFORM_BY_SERVICE[service],
        name=name,
        stage="intake",
        framework=framework,
        created_by_id=current_user.id,
    )
    return project, None


@bp.route("/<client_id>/adopt-artifact/<artifact_id>", methods=["GET", "POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def adopt_artifact(client_id: str, artifact_id: str):
    """Adopt a client-repository file into a project.

    Round-4 refactor: GET renders a create-or-pick form so admins can
    create the receiving project inline. POST handles both target
    values atomically — if `target=new`, the Project is created in the
    same transaction as the artifact's project_id update.

    Origin stays HUMAN_INPUT regardless of path (origin is immutable
    and the trigger would refuse a change anyway). Per round-4 chat
    answer we keep stage='client_repository' to preserve the
    provenance signal; the audit row records prior_stage either way.
    """
    from ..p2_zerotrust.frameworks import FRAMEWORKS
    art = db.session.get(Artifact, artifact_id)
    if art is None or art.client_id != client_id:
        abort(404)
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    existing_projects = [
        p for p in client.projects
        if not p.is_client_repository and not p.archived
    ]
    existing_projects.sort(key=lambda p: p.created_at, reverse=True)

    if request.method == "GET":
        return render_template(
            "clients/adopt_artifact.html",
            client=client,
            artifact=art,
            existing_projects=existing_projects,
            frameworks=FRAMEWORKS,
            # Shared-partial parameters (round-4 §3.1). The adopt
            # picker shows both branches; default service blank so
            # admin picks; submit POSTs back to this same route.
            submit_url=url_for("clients.adopt_artifact",
                               client_id=client.id, artifact_id=art.id),
            cancel_url=url_for("clients.intake_view", client_id=client.id),
            show_existing_radio=True,
            lock_service=False,
            default_service="tech_debt",
            submit_label_new="Create project and adopt",
            submit_label_existing="Adopt into existing project",
            now_year=datetime.utcnow().year,
        )

    target = (request.form.get("target") or "").strip()

    if target == "existing":
        target_project_id = (request.form.get("existing_project_id") or "").strip()
        target_project = (
            db.session.get(Project, target_project_id)
            if target_project_id else None
        )
        if target_project is None \
                or target_project.client_id != client_id \
                or target_project.is_client_repository \
                or target_project.archived:
            flash("Pick a real project to adopt the file into.", "error")
            return redirect(url_for("clients.adopt_artifact",
                                    client_id=client_id, artifact_id=artifact_id))
        previous_stage = art.stage
        art.project_id = target_project.id
        db.session.commit()
        log_audit(
            "artifact.adopted_into_project",
            actor=current_user,
            target_type="artifact", target_id=art.id,
            project_id=target_project.id, client_id=client_id,
            details={
                "prior_stage": previous_stage,
                "to_project_id": target_project.id,
                "to_project_name": target_project.name,
                "via": "adopt_existing",
            },
        )
        flash(f"Adopted {art.title} into {target_project.name}.", "info")
        return redirect(url_for(
            _WORKSPACE_ENDPOINT[target_project.platform],
            project_id=target_project.id,
        ))

    if target == "new":
        project, error = _create_project_from_form(client, request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("clients.adopt_artifact",
                                    client_id=client_id, artifact_id=artifact_id))
        db.session.add(project)
        db.session.flush()   # need project.id for the artifact's FK

        previous_stage = art.stage
        art.project_id = project.id
        # Two audit rows in the same transaction so the pairing
        # (project.created + artifact.adopted_into_project at the same
        # actor + ts) proves the project was born specifically to
        # receive this file. Per round-4 §6.
        log_audit(
            "project.created",
            actor=current_user,
            target_type="project", target_id=project.id,
            project_id=project.id, client_id=client.id,
            details={
                "platform": project.platform.value,
                "created_from": "adopt_flow",
                "source_artifact_id": art.id,
                "framework": project.framework,
            },
        )
        log_audit(
            "artifact.adopted_into_project",
            actor=current_user,
            target_type="artifact", target_id=art.id,
            project_id=project.id, client_id=client.id,
            details={
                "prior_stage": previous_stage,
                "to_project_id": project.id,
                "to_project_name": project.name,
                "via": "adopt_create",
            },
        )
        db.session.commit()
        flash(f"Created {project.name} and adopted {art.title}.", "info")
        return redirect(url_for(
            _WORKSPACE_ENDPOINT[project.platform],
            project_id=project.id,
        ))

    flash("Pick existing or new before submitting.", "error")
    return redirect(url_for("clients.adopt_artifact",
                            client_id=client_id, artifact_id=artifact_id))


@bp.route("/<client_id>/projects/new", methods=["GET", "POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def new_project_for_client(client_id: str):
    """Admin entry point for creating a project from the client detail
    page (round-4 §3.3). Same shared partial as adopt + fulfill, with
    the existing-project branch hidden.

    On submit: create the Project, redirect to its workspace. No
    artifact adoption (this entry point isn't tied to a file).
    """
    from ..p2_zerotrust.frameworks import FRAMEWORKS
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    if request.method == "POST":
        project, error = _create_project_from_form(client, request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("clients.new_project_for_client",
                                    client_id=client_id))
        db.session.add(project)
        db.session.flush()
        log_audit(
            "project.created",
            actor=current_user,
            target_type="project", target_id=project.id,
            project_id=project.id, client_id=client.id,
            details={
                "platform": project.platform.value,
                "created_from": "client_detail",
                "framework": project.framework,
            },
        )
        db.session.commit()
        flash(f"Created {project.name}.", "info")
        return redirect(url_for(
            _WORKSPACE_ENDPOINT[project.platform], project_id=project.id,
        ))

    return render_template(
        "clients/new_project_for_client.html",
        client=client,
        frameworks=FRAMEWORKS,
        submit_url=url_for("clients.new_project_for_client",
                           client_id=client.id),
        cancel_url=url_for("clients.detail", client_id=client.id),
        show_existing_radio=False,
        lock_service=False,
        default_service="tech_debt",
        submit_label_new="Create project",
        existing_projects=[],
        now_year=datetime.utcnow().year,
    )


@bp.route("/<client_id>/value-loop")
@login_required
@require_client_for_param("client_id")
def value_loop(client_id: str):
    """Cross-service synthesis for one client (round-7 §8.4).

    Pulls the most recent Tech Debt overlap analysis, the most recent
    Attack Surface coverage run, and the most recent Zero Trust
    roadmap. Renders them side by side with the message:

        Here's the wasted spend we found, here's where to redirect
        it, here's the compliance framing.

    Admin/reviewer-only. The route is scoped via require_client_for_param
    so cross-client probes return 404.
    """
    import json as _json
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    # Latest P1 overlap analysis (one per Tech Debt project; pick the
    # most recent across all of them).
    overlap_art = (
        db.session.query(Artifact)
        .join(Project, Artifact.project_id == Project.id)
        .filter(Project.client_id == client_id,
                Project.platform == PlatformType.TECH_DEBT,
                Project.archived.is_(False),
                Artifact.origin == Origin.AI_GENERATED,
                Artifact.stage == "overlap_analysis")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    overlap_summary = None
    overlap_project = None
    if overlap_art is not None:
        overlap_project = db.session.get(Project, overlap_art.project_id)
        try:
            overlap_summary = (_json.loads(overlap_art.body_text or "")
                               .get("summary", {}))
        except (ValueError, TypeError):
            overlap_summary = None

    # Latest P3 coverage run.
    from ..models import CoverageRun
    coverage_run = (
        db.session.query(CoverageRun)
        .join(Project, CoverageRun.project_id == Project.id)
        .filter(Project.client_id == client_id,
                Project.platform == PlatformType.ATTACK_SURFACE,
                Project.archived.is_(False))
        .order_by(CoverageRun.created_at.desc())
        .first()
    )
    coverage_project = None
    if coverage_run is not None:
        coverage_project = db.session.get(Project, coverage_run.project_id)

    # Latest P2 roadmap.
    roadmap_art = (
        db.session.query(Artifact)
        .join(Project, Artifact.project_id == Project.id)
        .filter(Project.client_id == client_id,
                Project.platform == PlatformType.ZERO_TRUST,
                Project.archived.is_(False),
                Artifact.origin == Origin.AI_GENERATED,
                Artifact.stage == "transition_roadmap")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    roadmap_project = None
    roadmap_data = None
    if roadmap_art is not None:
        roadmap_project = db.session.get(Project, roadmap_art.project_id)
        try:
            roadmap_data = _json.loads(roadmap_art.body_text or "")
        except (ValueError, TypeError):
            roadmap_data = None

    return render_template(
        "clients/value_loop.html",
        client=client,
        overlap_summary=overlap_summary,
        overlap_project=overlap_project,
        overlap_art=overlap_art,
        coverage_run=coverage_run,
        coverage_project=coverage_project,
        roadmap_data=roadmap_data,
        roadmap_art=roadmap_art,
        roadmap_project=roadmap_project,
    )


@bp.route("/<client_id>")
@login_required
@require_client_for_param("client_id")
def detail(client_id: str):
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    lists = (
        db.session.query(CapabilityList)
        .filter_by(client_id=client.id)
        .order_by(CapabilityList.version.desc())
        .all()
    )
    # Same corruption-detection heuristic as intake_view so the
    # corruption banner appears on whichever page the admin lands on.
    from collections import Counter
    intake_values = [
        getattr(client, col, None) for col in _ABOUT_FIELDS_RESETTABLE
    ]
    non_empty = [v for v in intake_values if v]
    most_common_count = (Counter(non_empty).most_common(1)[0][1]
                         if non_empty else 0)
    looks_corrupted = most_common_count >= 3
    return render_template(
        "clients/detail.html",
        client=client, capability_lists=lists,
        looks_corrupted=looks_corrupted,
    )


@bp.route("/<client_id>/admin-field", methods=["POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def admin_field(client_id: str):
    """Inline-edit endpoint for admin-only fields on the client detail
    page (industry, internal notes). HTMX-friendly: returns a small
    "saved" pip when posted with HX-Request, otherwise redirects back
    to /clients/<id>. Round-8 §III: industry was display-only with no
    way to set it for self-registered clients."""
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    name = (request.form.get("name") or "").strip()
    if name not in {"industry", "notes"}:
        abort(400)
    raw = request.form.get(name)
    if raw is None:
        raw = request.form.get("value")
    setattr(client, name, (raw or "").strip() or None)
    db.session.commit()
    log_audit(
        "client.admin_field_updated",
        actor=current_user,
        target_type="client", target_id=client.id, client_id=client.id,
        details={"field": name},
    )
    if request.headers.get("HX-Request"):
        from flask import make_response
        return make_response(
            '<span class="usa-hint shield-portal-saved" style="color:#1a7733;">saved &#10003;</span>',
            200,
        )
    flash("Saved.", "info")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/<client_id>/capability-list/<list_id>")
@login_required
@require_client_for_param("client_id")
def capability_list_detail(client_id: str, list_id: str):
    cl = db.session.get(CapabilityList, list_id)
    if cl is None or cl.client_id != client_id:
        abort(404)
    return render_template("clients/capability_list_detail.html", cl=cl)


@bp.route("/<client_id>/capability-list/<list_id>/export.xlsx")
@login_required
@require_client_for_param("client_id")
def capability_list_export(client_id: str, list_id: str):
    """Polished XLSX download. Auditable provenance on sheet 1, items on sheet 2.

    The route-level @require_client_for_param replaces the v1.7
    _restrict_client_to_intake gate for fine-grained cross-client
    protection — a reviewer with assignments to client A still gets
    a 404 trying to export client B's list.
    """
    cl = db.session.get(CapabilityList, list_id)
    if cl is None or cl.client_id != client_id:
        abort(404)
    blob = capability_list_to_xlsx(cl)

    # Build a filename. HTTP `Content-Disposition: filename="..."` only
    # accepts ASCII per RFC 6266; non-ASCII (em-dash, smart quotes,
    # accents) in the value confuses the browser and Chrome silently
    # aborts the download. Strip the filename down to ASCII chars; let
    # Flask's send_file emit the RFC 5987 `filename*` variant for full
    # Unicode display in the save dialog.
    import io
    import unicodedata

    from flask import send_file
    raw = f"{cl.client.name}_v{cl.version}_{cl.label or 'list'}.xlsx"
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_safe = nfkd.encode("ascii", "ignore").decode("ascii")
    # Whitespace and slashes are ugly in filenames; the rest of ASCII
    # punctuation is fine.
    ascii_safe = ascii_safe.replace(" ", "_").replace("/", "-").strip("_")
    if not ascii_safe.lower().endswith(".xlsx"):
        ascii_safe = f"{ascii_safe[:80]}.xlsx"
    return send_file(
        io.BytesIO(blob),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=ascii_safe,
    )
