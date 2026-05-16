"""Clients Blueprint: top-tier client browsing + capability-list views."""
from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import login_required

from ..extensions import db
from ..models import CapabilityList, Client

bp = Blueprint("clients", __name__, template_folder="../templates/clients")


@bp.route("/")
@login_required
def index():
    clients = db.session.query(Client).order_by(Client.name).all()
    return render_template("clients/list.html", clients=clients)


@bp.route("/<client_id>")
@login_required
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
def capability_list_detail(client_id: str, list_id: str):
    cl = db.session.get(CapabilityList, list_id)
    if cl is None or cl.client_id != client_id:
        abort(404)
    return render_template("clients/capability_list_detail.html", cl=cl)
