"""Seed SHIELD with two demo clients, a 75-product capability list,
and the membership rows that link client@demo to Acme and client@beta
to Beta Corp.

Usage:    flask --app wsgi:app seed
Or:       docker compose exec app flask --app wsgi:app seed
Or:       make seed
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Allow running as a script directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_catalog import CATALOG
from shield import create_app
from shield.extensions import db
from shield.models import (
    CapabilityList,
    CapabilityListItem,
    Client,
    ClientMembership,
    MitreTechnique,
    Origin,
    PlatformType,
    Project,
    Role,
    User,
)
from shield.p3_attack_surface.attack_data import TECHNIQUES as MITRE_TECHNIQUES
from shield.spine.audit import log_audit

SEED_USERS = [
    ("admin@demo",    "Demo Admin",      Role.ADMIN),
    ("client@demo",   "Acme Demo User",  Role.CLIENT),
    ("client@beta",   "Beta Demo User",  Role.CLIENT),
    ("reviewer@demo", "Demo Reviewer",   Role.REVIEWER),
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

        # --- Clients ---
        # Acme Co — the primary demo client. Populated with full intake
        # metadata so the portal renders meaningfully out of the box.
        acme = db.session.query(Client).filter_by(name="Acme Co").one_or_none()
        if acme is None:
            acme = Client(name="Acme Co", industry="Financial services",
                          notes="Demo client seeded by scripts/seed.py.")
            db.session.add(acme)
            db.session.commit()
        if acme.intake_completed_at is None:
            acme.legal_name           = "Acme Co"
            acme.dba_name             = ""
            acme.website              = "https://acme.example"
            acme.size_band            = "501-5000"
            acme.primary_poc_name     = "Acme Demo User"
            acme.primary_poc_title    = "Director of IT Security"
            acme.primary_poc_email    = "client@demo"
            acme.primary_poc_phone    = "+1 555 010 0001"
            acme.address_line1        = "100 Demo Plaza"
            acme.city                 = "New York"
            acme.state                = "NY"
            acme.postal_code          = "10001"
            acme.country              = "United States"
            acme.compliance_frameworks = ["soc2", "nist_csf"]
            acme.service_interests    = ["tech_debt", "zero_trust", "attack_surface"]
            acme.prompting_context    = (
                "Pre-audit posture review and rationalization of overlapping "
                "security tooling. Targeting consolidation savings + a defensible "
                "Zero Trust roadmap before the FY26 budget cycle."
            )
            acme.intake_completed_at  = datetime.utcnow()
            db.session.commit()

        # Beta Corp — second seeded client. Exists so the test surface
        # has a real cross-client target for scoping regressions and the
        # admin queue has more than one row to render in development.
        beta = db.session.query(Client).filter_by(name="Beta Corp").one_or_none()
        if beta is None:
            beta = Client(
                name="Beta Corp", industry="Healthcare",
                legal_name="Beta Corp",
                website="https://beta.example",
                size_band="51-500",
                primary_poc_name="Beta Demo User",
                primary_poc_title="CISO",
                primary_poc_email="client@beta",
                primary_poc_phone="+1 555 020 0002",
                address_line1="200 Sample Way",
                city="Boston", state="MA", postal_code="02110",
                country="United States",
                compliance_frameworks=["hipaa", "nist_csf"],
                service_interests=["zero_trust"],
                prompting_context="HIPAA refresh; CISA ZTMM aspiration.",
                intake_completed_at=datetime.utcnow(),
                notes="Second demo client — used to verify cross-client scoping.",
            )
            db.session.add(beta)
            db.session.commit()

        # --- Membership rows tying CLIENT users to their orgs ---
        # The access layer (shield.spine.access) reads accepted_at to
        # decide what each user may see. Without this, client@demo
        # would have zero scope and the portal would dead-end.
        def _ensure_membership(client_obj: Client, user: User,
                               *, primary: bool, invited_by: User) -> None:
            existing = (
                db.session.query(ClientMembership)
                .filter_by(client_id=client_obj.id, user_id=user.id)
                .one_or_none()
            )
            if existing:
                if existing.accepted_at is None:
                    existing.accepted_at = datetime.utcnow()
                    db.session.commit()
                return
            db.session.add(ClientMembership(
                client_id=client_obj.id,
                user_id=user.id,
                membership_role="primary_poc" if primary else "member",
                invited_by_id=invited_by.id,
                accepted_at=datetime.utcnow(),
            ))
            db.session.commit()

        _ensure_membership(acme, users["client@demo"], primary=True, invited_by=admin)
        _ensure_membership(beta, users["client@beta"], primary=True, invited_by=admin)

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
            f"Seeded: clients=[{acme.name}, {beta.name}], users={len(users)}, "
            f"items={len(CATALOG)}, projects=3, mitre_techniques={mitre_count}"
        )


if __name__ == "__main__":
    seed()
