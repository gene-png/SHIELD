"""Platform 1 — Technical Debt / Overlap analysis."""
from flask import Blueprint

bp = Blueprint("p1", __name__, template_folder="../templates/p1")

from . import routes  # noqa: E402,F401
