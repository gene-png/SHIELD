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

from flask import (
    Blueprint,
    Response,
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
    Origin,
    Project,
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

        row = {
            "client": c,
            "gaps": sorted(gaps),
            "projects": real_projects,
            "consult_requested": bool(c.consult_requested),
            "admin_replies_needed": admin_replies_needed,
        }
        if gaps or c.consult_requested:
            waiting.append(row)
        elif real_projects:
            active.append(row)

    return render_template(
        "clients/queue.html",
        new_leads=new_leads,
        waiting=waiting,
        active=active,
    )


# ====================================================================
# /clients/<id>/intake — admin's read-only view of submitted intake
# ====================================================================

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
    return render_template(
        "clients/intake_view.html",
        client=client, repo_uploads=repo_uploads,
        repo=repo, real_projects=real_projects,
    )


@bp.route("/<client_id>/adopt-artifact/<artifact_id>", methods=["POST"])
@login_required
@admin_only
@require_client_for_param("client_id")
def adopt_artifact(client_id: str, artifact_id: str):
    """Link a client-repository artifact to a real Project.

    The artifact stays origin=human_input (origin is immutable per the
    integrity model — and the trigger would reject a change anyway).
    Only project_id moves. Audited as `artifact.adopted_into_project`.
    """
    target_project_id = (request.form.get("project_id") or "").strip()
    target_project = db.session.get(Project, target_project_id) if target_project_id else None
    if target_project is None or target_project.client_id != client_id \
       or target_project.is_client_repository:
        flash("Pick a real project to adopt the file into.", "error")
        return redirect(url_for("clients.intake_view", client_id=client_id))

    art = db.session.get(Artifact, artifact_id)
    if art is None or art.client_id != client_id:
        abort(404)

    previous_project_id = art.project_id
    art.project_id = target_project.id
    # Keep `stage` whatever it was when it landed — typically
    # 'client_repository'. The route layer of the receiving project
    # can re-stage it later via its own writers.
    db.session.commit()

    log_audit(
        "artifact.adopted_into_project",
        actor=current_user,
        target_type="artifact", target_id=art.id,
        project_id=target_project.id, client_id=client_id,
        details={
            "from_project_id": previous_project_id,
            "to_project_id": target_project.id,
            "to_project_name": target_project.name,
        },
    )
    flash(f"Adopted into {target_project.name}.", "info")
    return redirect(url_for("clients.intake_view", client_id=client_id))


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
    return render_template("clients/detail.html", client=client, capability_lists=lists)


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
    safe_label = (cl.label or "list").replace(" ", "_").replace("/", "-")[:60]
    filename = f"{cl.client.name}_v{cl.version}_{safe_label}.xlsx".replace(" ", "_")
    return Response(
        blob,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
