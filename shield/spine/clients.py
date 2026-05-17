"""Clients Blueprint: top-tier client browsing + capability-list views."""
from __future__ import annotations

from flask import Blueprint, Response, abort, render_template
from flask_login import login_required

from ..extensions import db
from ..models import CapabilityList, Client
from .access import require_client_for_param, user_clients
from .exporters import capability_list_to_xlsx

bp = Blueprint("clients", __name__, template_folder="../templates/clients")


@bp.route("/")
@login_required
def index():
    # Scoped: admins see every client; reviewers see assigned (or all
    # if un-assigned); clients see only their own organization.
    clients = user_clients()
    return render_template("clients/list.html", clients=clients)


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
