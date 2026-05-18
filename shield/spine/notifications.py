"""Notifications blueprint.

The Notification table is written synchronously by various actions
(service requests, message posts, deliverables finalized, …). This
blueprint is the read surface + a single "mark all read" verb.

A context processor in `shield/__init__.py` injects the unread count
into every template so the top-nav bell can render its badge without
each route having to query.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Notification

bp = Blueprint("notifications", __name__, template_folder="../templates")


@bp.route("/")
@login_required
def index():
    """Full list, newest first. Marks all rows read on visit so the
    bell's badge drops to zero — same behavior as email inboxes."""
    items = (db.session.query(Notification)
             .filter(Notification.user_id == current_user.id)
             .order_by(Notification.created_at.desc())
             .limit(200)
             .all())
    # Mark unread items as read.
    now = datetime.utcnow()
    touched = 0
    for n in items:
        if n.read_at is None:
            n.read_at = now
            touched += 1
    if touched:
        db.session.commit()
    return render_template("notifications/index.html", items=items)


@bp.route("/mark-read", methods=["POST"])
@login_required
def mark_read():
    """Mark all unread notifications read without going to the index.
    Used by the bell dropdown's 'mark all read' link."""
    now = datetime.utcnow()
    (db.session.query(Notification)
     .filter(Notification.user_id == current_user.id,
             Notification.read_at.is_(None))
     .update({Notification.read_at: now}, synchronize_session=False))
    db.session.commit()
    return redirect(request.referrer or url_for("home"))
