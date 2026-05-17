"""Seed SHIELD with one demo client and a 75-product capability list.

Usage:    flask --app wsgi:app seed
Or:       docker compose exec app flask --app wsgi:app seed
Or:       make seed
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shield import create_app
from shield.extensions import db
from shield.models import (
    CapabilityList,
    CapabilityListItem,
    Client,
    MitreTechnique,
    Origin,
    PlatformType,
    Project,
    Role,
    User,
)
from shield.p3_attack_surface.attack_data import TECHNIQUES as MITRE_TECHNIQUES
from shield.spine.audit import log_audit
from scripts.seed_catalog import CATALOG


SEED_USERS = [
    ("admin@demo",    "Demo Admin",    Role.ADMIN),
    ("client@demo",   "Demo Client",   Role.CLIENT),
    ("reviewer@demo", "Demo Reviewer", Role.REVIEWER),
]


def seed() -> None:
    app = create_app()
    with app.app_context():
        # --- Users ---
        users: dict[str, User] = {}
        for email, name, role in SEED_USERS:
            existing = db.session.query(User).filter_by(email=email).one_or_none()
            if existing:
                users[email] = existing
                continue
            u = User(
                # Keycloak `sub` is its own UUID; here we just match email until first
                # OIDC login, at which point identity.py upserts by `sub`.
                sub=f"seed-{email}",
                email=email,
                display_name=name,
                role=role,
            )
            db.session.add(u)
            users[email] = u
        db.session.commit()
        admin = users["admin@demo"]

        # --- Client ---
        acme = db.session.query(Client).filter_by(name="Acme Co").one_or_none()
        if acme is None:
            acme = Client(name="Acme Co", industry="Financial services",
                          notes="Demo client seeded by scripts/seed.py.")
            db.session.add(acme)
            db.session.commit()

        # --- Capability list (only seed once) ---
        if not acme.capability_lists:
            cl = CapabilityList(
                client_id=acme.id, version=1, label="Q1 2026 baseline",
                origin=Origin.HUMAN_INPUT,
                created_by_id=admin.id,
                notes="Seeded by scripts/seed.py — 75 varied tooling entries with deliberate overlaps.",
            )
            db.session.add(cl)
            db.session.flush()
            for entry in CATALOG:
                db.session.add(CapabilityListItem(capability_list_id=cl.id, **entry))
            db.session.commit()
            log_audit(
                "seed.capability_list",
                actor=admin,
                target_type="capability_list",
                target_id=cl.id,
                client_id=acme.id,
                details={"version": cl.version, "items": len(CATALOG)},
            )

        # --- MITRE ATT&CK technique catalog (Platform 3) ---
        # The starter set is curated in shield/p3_attack_surface/attack_data.py.
        # Full STIX import is the planned `make vendor-attack` follow-up.
        if db.session.query(MitreTechnique).count() == 0:
            for t in MITRE_TECHNIQUES:
                db.session.add(MitreTechnique(
                    technique_id=t["technique_id"],
                    name=t["name"],
                    tactic=t["tactic"],
                    description=t.get("description", ""),
                    matrix="enterprise",
                ))
            db.session.commit()
            log_audit(
                "seed.mitre",
                actor=admin,
                target_type="mitre_technique_catalog",
                details={"count": len(MITRE_TECHNIQUES), "matrix": "enterprise"},
            )

        # --- One project per platform ---
        latest_cl = acme.latest_capability_list()
        for platform, name, framework in [
            (PlatformType.TECH_DEBT,      "Tech Debt Q1 2026",            None),
            (PlatformType.ZERO_TRUST,     "Zero Trust posture (CISA ZTMM)", "cisa_ztmm_v2"),
            (PlatformType.ATTACK_SURFACE, "ATT&CK coverage Q1 2026",      None),
        ]:
            existing = (
                db.session.query(Project)
                .filter_by(client_id=acme.id, platform=platform)
                .one_or_none()
            )
            if existing:
                continue
            p = Project(
                client_id=acme.id,
                platform=platform,
                name=name,
                stage="intake",
                framework=framework,
                created_by_id=admin.id,
                capability_list_version_id=latest_cl.id if latest_cl else None,
            )
            db.session.add(p)
        db.session.commit()
        mitre_count = db.session.query(MitreTechnique).count()
        log_audit("seed.complete", actor=admin, client_id=acme.id,
                  details={"items": len(CATALOG), "projects": 3, "mitre_techniques": mitre_count})

        print(
            f"Seeded: client={acme.name}, users={len(users)}, "
            f"items={len(CATALOG)}, projects=3, mitre_techniques={mitre_count}"
        )


if __name__ == "__main__":
    seed()
