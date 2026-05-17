"""Admin-side views that don't fit elsewhere on the spine.

Currently:
  - /admin/messages/   cross-client unread inbox for the consulting team.
                       Companion to the per-client thread UI in
                       shield.spine.portal.
  - /admin/messages/<client_id>/<thread_key>
                       admin's view of one thread (with the same
                       composition affordance the client side has).

The admin side reuses the Message model; the only difference from
the portal's view is that the admin can see messages across every
client they have access to.
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
from sqlalchemy import select

from ..extensions import db
from ..models import Client, Message, Project, Role, User
from .access import client_ids_for_user, require_client_access, scope_query
from .audit import log_audit

bp = Blueprint("admin_views", __name__, template_folder="../templates/admin")


@bp.route("/messages/")
@login_required
def messages_inbox():
    """Cross-client inbox of recent threads.

    Each row is one thread (client x project_id) with the latest
    message, the unread count for THIS admin (messages whose author
    is a CLIENT user and which this admin hasn't marked read), and
    a deep link to /admin/messages/<client_id>/<thread_key>.

    Scope: admins see every thread; reviewers see only their assigned
    clients' threads. The scope_query helper does the right thing.
    """
    if current_user.role not in (Role.ADMIN, Role.REVIEWER):
        abort(404)

    stmt = select(Message).order_by(Message.created_at.desc())
    stmt = scope_query(stmt, Message)
    msgs = list(db.session.scalars(stmt))

    # Bucket by (client_id, project_id) so each thread appears once.
    threads: dict[tuple[str, str | None], dict] = {}
    user_ids = {m.author_id for m in msgs}
    role_by_id = {
        u.id: u.role for u in
        db.session.query(User).filter(User.id.in_(user_ids)).all()
    } if user_ids else {}

    for m in msgs:
        key = (m.client_id, m.project_id)
        if key in threads:
            continue
        unread = sum(
            1 for x in msgs
            if x.client_id == m.client_id and x.project_id == m.project_id
            and role_by_id.get(x.author_id) == Role.CLIENT
            and not (x.read_at_map or {}).get(current_user.id)
        )
        threads[key] = {
            "client_id": m.client_id,
            "project_id": m.project_id,
            "latest": m,
            "unread": unread,
        }

    client_ids = {t["client_id"] for t in threads.values()}
    project_ids = {t["project_id"] for t in threads.values() if t["project_id"]}
    clients = {
        c.id: c for c in
        db.session.query(Client).filter(Client.id.in_(client_ids)).all()
    } if client_ids else {}
    projects = {
        p.id: p for p in
        db.session.query(Project).filter(Project.id.in_(project_ids)).all()
    } if project_ids else {}

    rows = sorted(
        threads.values(),
        key=lambda t: (-t["unread"], -t["latest"].created_at.timestamp()),
    )

    return render_template(
        "admin/messages_inbox.html",
        rows=rows, clients=clients, projects=projects,
    )


@bp.route("/messages/<client_id>/<thread_key>", methods=["GET", "POST"])
@login_required
def messages_thread(client_id: str, thread_key: str):
    """Admin's view of a single thread + reply form."""
    if current_user.role not in (Role.ADMIN, Role.REVIEWER):
        abort(404)
    require_client_access(client_id)
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    project = None
    if thread_key != "general":
        project = db.session.get(Project, thread_key)
        if project is None or project.client_id != client_id:
            abort(404)

    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Type a message first.", "error")
            return redirect(url_for("admin_views.messages_thread",
                                    client_id=client_id, thread_key=thread_key))
        m = Message(
            client_id=client_id,
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
            client_id=client_id,
            details={"length": len(body),
                     "thread": "general" if project is None else "project",
                     "actor_role": current_user.role.value},
        )
        return redirect(url_for("admin_views.messages_thread",
                                client_id=client_id, thread_key=thread_key))

    # GET: mark read on the admin's behalf + fetch.
    pid = None if thread_key == "general" else thread_key
    msgs = (
        db.session.query(Message)
        .filter(Message.client_id == client_id, Message.project_id == pid)
        .order_by(Message.created_at.asc())
        .all()
    )
    now = datetime.utcnow().isoformat() + "Z"
    changed = False
    for m in msgs:
        if m.author_id == current_user.id:
            continue
        rmap = dict(m.read_at_map or {})
        if current_user.id not in rmap:
            rmap[current_user.id] = now
            m.read_at_map = rmap
            changed = True
    if changed:
        db.session.commit()

    authors = {
        u.id: u for u in (
            db.session.query(User)
            .filter(User.id.in_({m.author_id for m in msgs} | {current_user.id}))
            .all()
        )
    }
    return render_template(
        "admin/messages_thread.html",
        client=client, project=project, thread_key=thread_key,
        messages=msgs, authors=authors,
    )


# silence the linter on the unused-but-doc-relevant helper.
_ = client_ids_for_user
