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

    @app.cli.command("vendor-attack")
    @click.option("--file", "file_", default=None, help="Local STIX JSON path.")
    @click.option("--url", default=None, help="Override STIX URL.")
    @click.option("--dry-run", is_flag=True, help="Parse only; no DB writes.")
    def vendor_attack_cmd(file_: str | None, url: str | None, dry_run: bool):
        """Vendor full MITRE ATT&CK Enterprise technique catalog into DB."""
        from scripts.vendor_attack import vendor_attack, STIX_URL
        source = file_ or url or STIX_URL
        result = vendor_attack(source, dry_run=dry_run)
        click.echo(
            f"vendor-attack: parsed={result['parsed']} "
            f"inserted={result['inserted']} updated={result['updated']}"
        )
