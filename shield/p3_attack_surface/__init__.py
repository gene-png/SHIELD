"""Platform 3 — MITRE ATT&CK attack-surface / gap analysis."""
from flask import Blueprint

bp = Blueprint("p3", __name__, template_folder="../templates/p3")

from . import routes  # noqa: E402,F401
