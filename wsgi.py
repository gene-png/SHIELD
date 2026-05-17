"""Gunicorn / Flask entry point."""
from shield import create_app

app = create_app()
