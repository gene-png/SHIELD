"""Flask CLI commands."""
from __future__ import annotations

import click
from flask import Flask


def register_cli(app: Flask) -> None:

    @app.cli.command("seed")
    def seed_cmd():
        """Load demo client, users, 75-product capability list, projects."""
        from scripts.seed import seed
        seed()
        click.echo("Seed complete.")

    @app.cli.command("vendor-assets")
    def vendor_cmd():
        """Download USWDS + HTMX into shield/static/."""
        from scripts import vendor_assets
        vendor_assets.main()

    @app.cli.command("reset-db")
    @click.option("--yes", is_flag=True, help="confirm")
    def reset_db_cmd(yes: bool):
        """Drop and re-create all tables. DEV ONLY."""
        if not yes:
            click.echo("Refusing without --yes")
            return
        from .extensions import db
        db.drop_all()
        db.create_all()
        click.echo("Database reset. Run `flask seed` next.")
