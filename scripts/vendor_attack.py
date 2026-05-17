"""Vendor MITRE ATT&CK Enterprise techniques into the mitre_techniques table.

Usage:
    flask --app wsgi:app vendor-attack
    flask --app wsgi:app vendor-attack --file /path/to/enterprise-attack.json
    flask --app wsgi:app vendor-attack --dry-run

Or directly:
    docker compose exec app python scripts/vendor_attack.py

Source:
    https://github.com/mitre/cti — the canonical MITRE ATT&CK STIX bundle.
    This script downloads the latest `enterprise-attack.json` (≈30 MB) from
    the `master` branch and upserts the top-level techniques into the
    `mitre_techniques` table.

What gets vendored:
    - Only `attack-pattern` objects (techniques and sub-techniques).
    - Only top-level techniques (we skip sub-techniques to keep the AI
      coverage payload manageable; the parent technique covers the
      semantic ground for v1.1). Sub-tech support is a v1.2 follow-up.
    - Only those with a `mitre-attack` external_id (the `T####` ID).

Idempotent: re-running upserts (insert-or-update on `technique_id`).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Allow running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shield import create_app
from shield.extensions import db
from shield.models import MitreTechnique

STIX_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)


def _fetch_stix(source: str) -> dict:
    """Fetch the STIX bundle from a URL or local file path."""
    if source.startswith(("http://", "https://")):
        print(f"Downloading {source} (this may take a moment) ...")
        try:
            # Source defaults to the pinned MITRE GitHub raw URL above;
            # admin can override via --url/--file. Provision-time only;
            # never influenced by web-app user input. nosec covers the
            # static-analysis warning; noqa silences ruff.
            with urllib.request.urlopen(source, timeout=60) as r:  # noqa: S310  # nosec B310
                return json.load(r)
        except urllib.error.URLError as e:
            raise SystemExit(f"Failed to download STIX bundle: {e}") from e
    p = Path(source)
    if not p.exists():
        raise SystemExit(f"STIX file not found: {source}")
    return json.loads(p.read_text(encoding="utf-8"))


def _extract_techniques(stix: dict) -> list[dict]:
    """Parse STIX -> list of dicts ready for MitreTechnique upsert."""
    out: list[dict] = []
    for obj in stix.get("objects", []):
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("x_mitre_is_subtechnique"):
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        tech_id = None
        for ref in obj.get("external_references", []):
            if ref.get("source_name") == "mitre-attack":
                tech_id = ref.get("external_id")
                break
        if not tech_id:
            continue
        kcps = obj.get("kill_chain_phases", [])
        if not kcps:
            continue
        # `phase_name` is like "initial-access"; turn into "Initial Access"
        tactic = kcps[0].get("phase_name", "").replace("-", " ").title()
        out.append({
            "technique_id": tech_id,
            "name": obj.get("name", "") or tech_id,
            "tactic": tactic or "Unknown",
            # Descriptions can be very long (MITRE has paragraphs);
            # truncate to keep DB row sizes reasonable. The artifact
            # detail view shows what we have.
            "description": (obj.get("description") or "")[:4000],
        })
    return out


def vendor_attack(source: str = STIX_URL, *, dry_run: bool = False) -> dict:
    """Main entry. Returns counts for caller / CLI reporting."""
    stix = _fetch_stix(source)
    techniques = _extract_techniques(stix)
    print(f"Parsed {len(techniques)} top-level Enterprise techniques.")
    if dry_run:
        for t in techniques[:5]:
            print(f"  preview: {t['technique_id']} · {t['name']} ({t['tactic']})")
        if len(techniques) > 5:
            print(f"  ... +{len(techniques) - 5} more")
        return {"parsed": len(techniques), "inserted": 0, "updated": 0}

    app = create_app()
    with app.app_context():
        inserted = 0
        updated = 0
        for t in techniques:
            existing = (
                db.session.query(MitreTechnique)
                .filter_by(technique_id=t["technique_id"])
                .one_or_none()
            )
            if existing is None:
                db.session.add(MitreTechnique(matrix="enterprise", **t))
                inserted += 1
            else:
                existing.name = t["name"]
                existing.tactic = t["tactic"]
                existing.description = t["description"]
                updated += 1
        db.session.commit()
        print(f"Done. Inserted: {inserted}. Updated: {updated}.")
        return {"parsed": len(techniques), "inserted": inserted, "updated": updated}


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", help="Local STIX JSON file (skip download).")
    p.add_argument("--url", default=STIX_URL, help=f"STIX URL (default: {STIX_URL})")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse only; do not write to DB.")
    args = p.parse_args()
    vendor_attack(args.file or args.url, dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()
