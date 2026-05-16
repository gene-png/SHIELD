"""Platform 2 — Zero Trust / CSF compliance posture."""
from flask import Blueprint

bp = Blueprint("p2", __name__, template_folder="../templates/p2")

from . import routes  # noqa: E402,F401
