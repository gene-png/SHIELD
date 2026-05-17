"""SHIELD data model.

Reflects the unified-portal spec §5 (entities, not schema).

Hard invariants enforced here AND by a DB trigger (see migrations):
  * Artifact.origin is set once, at creation, and is immutable.
  * A file never moves lanes; it copies forward.
  * The AI lane is written only by `RepositoryWriter.write_ai_artifact`.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship, validates

from .extensions import db


def _uuid() -> str:
    return str(uuid.uuid4())


def _enum_values(enum_cls):
    """Tell SQLAlchemy to persist enum members by .value (lowercase), not .name.

    Pass to ``Enum(..., values_callable=_enum_values)``. Without this,
    SQLAlchemy stores ``Role.ADMIN`` as ``"ADMIN"`` and Postgres rejects
    it (the type was created with lowercase variants).
    """
    return [e.value for e in enum_cls]


# ============================================================
# Enums
# ============================================================

class Origin(str, enum.Enum):
    """Artifact origin. Immutable once set."""
    HUMAN_INPUT       = "human_input"        # client or admin upload / entry
    AI_GENERATED      = "ai_generated"       # produced by an AI processing step
    HUMAN_AI_INFORMED = "human_ai_informed"  # synthesis written by a human, citing AI findings
    # NOTE: "promoted" is a STATUS on AI_GENERATED, not an origin. See Artifact.reuse_status.


class ReuseStatus(str, enum.Enum):
    DRAFT     = "draft"
    APPROVED  = "approved"   # promoted: AI-origin artifact reviewed by an authorized human
    SUPERSEDED = "superseded"


class TrustTier(str, enum.Enum):
    """Platform 2 input trust tiers (subclassifications of HUMAN_INPUT)."""
    CLIENT_ASSERTED          = "client_asserted"
    ADMIN_ASSISTED           = "admin_assisted"
    ADMIN_ENTERED_ON_BEHALF  = "admin_entered_on_behalf"
    CLIENT_PROVIDED_EVIDENCE = "client_provided_evidence"
    NOT_APPLICABLE           = "not_applicable"


class PlatformType(str, enum.Enum):
    TECH_DEBT      = "tech_debt"
    ZERO_TRUST     = "zero_trust"
    ATTACK_SURFACE = "attack_surface"


class Role(str, enum.Enum):
    CLIENT   = "client"
    ADMIN    = "admin"
    REVIEWER = "reviewer"


# ============================================================
# Identity
# ============================================================

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    sub = Column(String(255), unique=True, index=True, nullable=False)  # Keycloak subject
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    # Optional richer identity captured on /portal/settings (v1.8).
    # Not required for auth — Keycloak still owns email + sub.
    title = Column(String(255))
    phone = Column(String(64))
    role = Column(Enum(Role, name="user_role", values_callable=_enum_values), nullable=False, default=Role.CLIENT)
    is_active_flag = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_reviewer(self) -> bool:
        return self.role == Role.REVIEWER

    @property
    def is_active(self) -> bool:  # Flask-Login override
        return bool(self.is_active_flag)


# ============================================================
# Client tier (above projects)
# ============================================================

class Client(db.Model):
    __tablename__ = "clients"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False, unique=True)
    industry = Column(String(120))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ---- Intake metadata (v1.8 client portal) -----------------------
    # Filled in by the client during the /portal/welcome → /portal/about
    # flow. All optional at the column level; `intake_completed_at`
    # being non-NULL is the signal that the client finished the wizard.
    # NULL means "new lead, intake not yet finished" — admin's queue
    # surfaces those separately.
    legal_name              = Column(String(255))
    dba_name                = Column(String(255))
    website                 = Column(String(255))
    size_band               = Column(String(32))   # '1-50','51-500','501-5000','5000+'
    primary_poc_name        = Column(String(255))
    primary_poc_title       = Column(String(255))
    primary_poc_email       = Column(String(255))
    primary_poc_phone       = Column(String(64))
    address_line1           = Column(String(255))
    address_line2           = Column(String(255))
    city                    = Column(String(128))
    state                   = Column(String(64))
    postal_code             = Column(String(32))
    country                 = Column(String(64), default="United States")
    # JSON list (not Postgres ARRAY) so the test suite's SQLite backend
    # can carry them too. Allowed values: cisa_ztmm, dod_zt, nist_csf,
    # hipaa, soc2, fedramp, cmmc. Free-form on write so future
    # frameworks don't need a schema change.
    compliance_frameworks   = Column(JSON, default=list, nullable=False)
    compliance_deadline     = Column(Date)
    prompting_context       = Column(Text)   # "why are you engaging us now?"
    # service_interests drives visibility for the client, the admin
    # queue, and the platform indexes. Allowed values are the platform
    # type strings: 'tech_debt', 'zero_trust', 'attack_surface'.
    service_interests       = Column(JSON, default=list, nullable=False)
    # The "I'm not sure / talk to a consultant first" option on the
    # welcome screen. Co-exists with service_interests rather than
    # being mutually exclusive (per UX doc round 2, §8.1 answer):
    # a client can both flag interest and request a consult call.
    consult_requested       = Column(Boolean, default=False, nullable=False)
    intake_completed_at     = Column(DateTime)

    projects = relationship("Project", back_populates="client", cascade="all,delete-orphan")
    capability_lists = relationship(
        "CapabilityList", back_populates="client", cascade="all,delete-orphan",
        order_by="CapabilityList.version.desc()",
    )

    def latest_capability_list(self) -> CapabilityList | None:
        return next(iter(self.capability_lists), None)


# ============================================================
# Capability list — versioned, per-client. The shared spine asset.
# ============================================================

class CapabilityList(db.Model):
    __tablename__ = "capability_lists"
    __table_args__ = (UniqueConstraint("client_id", "version", name="uq_client_version"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    label = Column(String(255), nullable=False)
    origin = Column(Enum(Origin, name="origin", values_callable=_enum_values), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = Column(String(36), ForeignKey("users.id"))

    client = relationship("Client", back_populates="capability_lists")
    created_by = relationship("User", foreign_keys=[created_by_id])
    items = relationship(
        "CapabilityListItem", back_populates="capability_list",
        cascade="all,delete-orphan", order_by="CapabilityListItem.category, CapabilityListItem.name",
    )


class CapabilityListItem(db.Model):
    __tablename__ = "capability_list_items"

    id = Column(String(36), primary_key=True, default=_uuid)
    capability_list_id = Column(String(36), ForeignKey("capability_lists.id"), nullable=False, index=True)

    name = Column(String(255), nullable=False)         # e.g. "Splunk Enterprise"
    vendor = Column(String(255))
    category = Column(String(120), nullable=False)     # e.g. "SIEM", "EDR"
    function = Column(String(255))                     # e.g. "log aggregation + correlation"
    annual_cost_usd = Column(Integer)                  # may be null
    license_count = Column(Integer)
    notes = Column(Text)

    capability_list = relationship("CapabilityList", back_populates="items")


# ============================================================
# Projects (one platform each)
# ============================================================

class Project(db.Model):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    platform = Column(Enum(PlatformType, name="platform_type", values_callable=_enum_values), nullable=False)
    name = Column(String(255), nullable=False)
    stage = Column(String(120), nullable=False, default="intake")
    capability_list_version_id = Column(String(36), ForeignKey("capability_lists.id"))
    framework = Column(String(120))   # Platform 2 only
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = Column(String(36), ForeignKey("users.id"))
    archived = Column(Boolean, default=False, nullable=False)
    # Per UX doc round 2, §10 Option B: each Client gets exactly one
    # synthetic Project with is_client_repository=True. Client-tier
    # uploads (from /portal/documents) land here with origin=human_input
    # and stage='client_repository'. Admin later "adopts" them into a
    # real project. The synthetic project is hidden from the platform
    # indexes and the workspace browser; only the client portal's
    # repository view and the admin's intake-view surface it.
    is_client_repository = Column(Boolean, default=False, nullable=False)

    client = relationship("Client", back_populates="projects")
    artifacts = relationship("Artifact", back_populates="project", cascade="all,delete-orphan")
    capability_snapshot = relationship("CapabilityList", foreign_keys=[capability_list_version_id])


# ============================================================
# Artifacts — origin is immutable; AI lane is automatic only
# ============================================================

class Artifact(db.Model):
    __tablename__ = "artifacts"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    # Denormalized client_id, set at creation from project.client_id.
    # Drives every per-client scoping query without an extra Project
    # join. The integrity-model triggers don't reference this column,
    # so it adds no constraint surface beyond an index.
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    stage = Column(String(120), nullable=False)

    # ORIGIN IS IMMUTABLE — enforced by DB trigger (see initial migration).
    origin = Column(Enum(Origin, name="origin", values_callable=_enum_values), nullable=False)
    trust_tier = Column(
        Enum(TrustTier, name="trust_tier", values_callable=_enum_values),
        default=TrustTier.NOT_APPLICABLE, nullable=False,
    )
    reuse_status = Column(
        Enum(ReuseStatus, name="reuse_status", values_callable=_enum_values),
        default=ReuseStatus.DRAFT, nullable=False,
    )

    title = Column(String(255), nullable=False)
    filename = Column(String(255))
    mime_type = Column(String(120))
    size_bytes = Column(Integer)
    storage_key = Column(String(512))      # path on disk for now; S3 key in prod
    body_text = Column(Text)               # short structured payloads (e.g. AI JSON) live here

    # Lineage: ids of input artifacts, AI prompt version, model, etc.
    lineage = Column(JSON, default=dict, nullable=False)

    actor_id = Column(String(36), ForeignKey("users.id"))
    actor_role = Column(Enum(Role, name="user_role", values_callable=_enum_values))
    capability_list_version_id = Column(String(36), ForeignKey("capability_lists.id"))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    promoted_at = Column(DateTime)
    promoted_by_id = Column(String(36), ForeignKey("users.id"))
    promotion_reason = Column(Text)

    project = relationship("Project", back_populates="artifacts")

    @validates("origin")
    def _validate_origin(self, _key, value):
        """Python-level rejection of origin mutation.

        Belt-and-suspenders alongside the `before_update` event listener
        below and the Postgres trigger in the initial migration: this
        fires on Python attribute assignment, so an attempt to do
        ``art.origin = Origin.AI_GENERATED`` raises immediately rather
        than waiting until flush. Important for the test suite (SQLite
        has no trigger) and for catching mistakes earlier in dev.
        """
        current = self.__dict__.get("origin")
        if current is not None and value != current:
            raise ValueError(
                f"Artifact.origin is immutable (was {current!r}, attempted {value!r})"
            )
        return value


# ============================================================
# Audit log — append-only
# ============================================================

class AuditEntry(db.Model):
    __tablename__ = "audit_entries"

    id = Column(String(36), primary_key=True, default=_uuid)
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor_id = Column(String(36), ForeignKey("users.id"))
    actor_email = Column(String(255))           # captured at write time so the entry survives user deletion
    action = Column(String(120), nullable=False)
    target_type = Column(String(120))
    target_id = Column(String(36))
    project_id = Column(String(36), ForeignKey("projects.id"))
    client_id = Column(String(36), ForeignKey("clients.id"))
    details = Column(JSON, default=dict, nullable=False)


# ============================================================
# Platform 2 — questionnaire structures (lifted in concept from cyberdashboardV2)
# ============================================================

class QuestionnaireResponse(db.Model):
    __tablename__ = "questionnaire_responses"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    framework = Column(String(120), nullable=False)
    control_id = Column(String(120), nullable=False, index=True)  # e.g. ZTMM 2.0 control ref
    # answer is a free-form short string: "implemented" / "partial" / "not_implemented" / "na"
    answer = Column(String(40), nullable=False)
    rationale = Column(Text)
    evidence_artifact_id = Column(String(36), ForeignKey("artifacts.id"))
    trust_tier = Column(Enum(TrustTier, name="trust_tier", values_callable=_enum_values), nullable=False)
    attributed_user_id = Column(String(36), ForeignKey("users.id"))
    submitted_at = Column(DateTime)
    locked = Column(Boolean, default=False, nullable=False)

    # Convenience relationship for templates: r.evidence_artifact -> Artifact | None.
    evidence_artifact = relationship("Artifact", foreign_keys=[evidence_artifact_id])


# ============================================================
# Platform 3 — MITRE ATT&CK
# ============================================================

class MitreTechnique(db.Model):
    __tablename__ = "mitre_techniques"

    id = Column(String(36), primary_key=True, default=_uuid)
    technique_id = Column(String(40), unique=True, nullable=False)  # e.g. "T1059"
    name = Column(String(255), nullable=False)
    tactic = Column(String(120), nullable=False)
    description = Column(Text)
    matrix = Column(String(60), default="enterprise", nullable=False)


class CoverageRun(db.Model):
    __tablename__ = "coverage_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    capability_list_version_id = Column(String(36), ForeignKey("capability_lists.id"), nullable=False)
    artifact_id = Column(String(36), ForeignKey("artifacts.id"))   # the AI output artifact
    summary = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CoverageFinding(db.Model):
    __tablename__ = "coverage_findings"

    id = Column(String(36), primary_key=True, default=_uuid)
    coverage_run_id = Column(String(36), ForeignKey("coverage_runs.id"), nullable=False, index=True)
    technique_id = Column(String(40), nullable=False)
    coverage = Column(String(40), nullable=False)   # "covered" / "partial" / "uncovered"
    detection_tools = Column(JSON, default=list, nullable=False)
    prevention_tools = Column(JSON, default=list, nullable=False)
    response_tools = Column(JSON, default=list, nullable=False)
    rationale = Column(Text)


# ============================================================
# v1.8 client portal — memberships, invitations, messages,
# notifications, deliverables, reviewer assignments.
#
# All purely additive to the integrity model: origin is still set once
# at creation, the audit log is still append-only, the picker is still
# the only path to AI-origin reuse. These tables wrap the experience
# around the spine, not into it.
# ============================================================


class ClientMembership(db.Model):
    """Many-to-many between users and clients.

    For CLIENT-role users, at least one accepted membership is the
    proof of "which client(s) you belong to" and the basis for every
    scoping query (see `shield.spine.access`).

    `membership_role` lets us distinguish the primary point of contact
    (who can invite colleagues) from regular members. ADMIN and
    REVIEWER role users typically have zero memberships — they're
    consulting-firm staff and access is controlled separately. The
    REVIEWER scoping uses the dedicated `ReviewerAssignment` table.
    """
    __tablename__ = "client_memberships"
    __table_args__ = (
        UniqueConstraint("client_id", "user_id", name="uq_client_user"),
        Index("ix_client_memberships_user_id", "user_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    # 'primary_poc' or 'member'. Free-form on write so we can add roles
    # without a schema change; the route layer rejects unknown values.
    membership_role = Column(String(32), nullable=False, default="member")
    invited_by_id = Column(String(36), ForeignKey("users.id"))
    invited_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # NULL until the invitee follows the invitation link. A pending
    # invitation row exists even before accepted_at is set so the
    # primary POC can see "Alice — pending" in /portal/settings.
    accepted_at = Column(DateTime)

    client = relationship("Client", foreign_keys=[client_id])
    user = relationship("User", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_id])


class ClientInvitation(db.Model):
    """Tokenized invite for a not-yet-existent or not-yet-linked user.

    The inviter at a client triggers POST /portal/settings/invite which
    creates a row here. Only the SHA-256 of the random token is stored
    so the DB row alone can't be used to log in. The plaintext token
    is shown to the inviter once (per UX doc round 2, §10 answer —
    no SMTP for v1; the inviter copy-pastes the URL to wherever).
    """
    __tablename__ = "client_invitations"
    __table_args__ = (
        Index("ix_client_invitations_client_id", "client_id"),
        Index("ix_client_invitations_email", "email"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False)
    invited_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    email = Column(String(255), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime)
    revoked_at = Column(DateTime)


class Message(db.Model):
    """Lightweight client ↔ consulting-firm thread.

    `project_id` NULL means the general "message your consultant"
    thread at the client level. Per-project threads use a real
    project_id. Append-only — edits are a v2 feature. Authors can be
    any role; the recipient set is derived from membership +
    reviewer-assignment at read time.
    """
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_client_id_created_at", "client_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"))
    author_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # { user_id: iso-timestamp-of-first-read }. JSON not JSONB so the
    # column is portable to SQLite for the test suite.
    read_at_map = Column(JSON, default=dict, nullable=False)


class Notification(db.Model):
    """In-app notification. Drives the top-nav bell.

    Written synchronously by the action that produces them (per UX doc
    round 2 §10 answer: no RQ dependency for notifications). The
    `link` field is the relative in-app URL the bell-click navigates
    to. Email digest is a v2 feature.
    """
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_read_at", "user_id", "read_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    client_id = Column(String(36), ForeignKey("clients.id"))
    project_id = Column(String(36), ForeignKey("projects.id"))
    # See `shield.spine.audit` for the canonical event-type strings.
    event_type = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text)
    link = Column(String(512))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read_at = Column(DateTime)


class Deliverable(db.Model):
    """Clearance-ready, client-facing snapshot of an artifact.

    Finalizing an artifact creates a Deliverable; the underlying
    artifact is unchanged (origin stays whatever it was, the working
    repository keeps the original). Clients see Deliverables in
    /portal/deliverables/, not raw artifacts. The download route reads
    from the linked Artifact.storage_key — no duplicated bytes (per
    UX doc round 2 §10 answer).

    `superseded_at` / `superseded_by` columns ship in v1.8; the UI for
    listing superseded versions is a v2 follow-up.
    """
    __tablename__ = "deliverables"
    __table_args__ = (
        Index("ix_deliverables_client_id", "client_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    artifact_id = Column(String(36), ForeignKey("artifacts.id"), nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text)
    finalized_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finalized_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    superseded_at = Column(DateTime)
    superseded_by = Column(String(36), ForeignKey("deliverables.id"))


class ReviewerAssignment(db.Model):
    """Scopes a REVIEWER-role user to a specific client.

    Per UX doc round 2 answer: kept separate from ClientMembership so
    the table boundary mirrors the org boundary — ClientMembership is
    "people who work at the client", ReviewerAssignment is "consulting
    firm reviewers given access to this client".

    Soft-revocable via `revoked_at` so the audit trail keeps the
    historical assignment intact.

    Note on default scope (per UX doc round 2 answer): a REVIEWER with
    zero rows here still sees every client — the current behavior is
    preserved until at least one assignment exists. Flipping that to
    "whitelist only" is a v2 decision.
    """
    __tablename__ = "reviewer_assignments"
    __table_args__ = (
        UniqueConstraint("reviewer_id", "client_id", name="uq_reviewer_client"),
        Index("ix_reviewer_assignments_reviewer_id", "reviewer_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    reviewer_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False)
    assigned_by_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime)


# ============================================================
# Application-layer enforcement of origin immutability.
#
# The DB trigger handles this in Postgres. This event listener handles
# it on any backend (including SQLite for the test suite), so tests
# don't depend on Postgres-specific features to verify the invariant.
# ============================================================

@event.listens_for(Artifact, "before_update", propagate=True)
def _artifact_origin_is_immutable(_mapper, _connection, target):
    """Defense in depth: even if @validates is bypassed (e.g. direct
    `db.session.execute(update(...))`), this listener catches changes at
    flush time. The earlier `and hist.deleted` filter was incorrect:
    `hist.deleted` can be empty after `expire_on_commit=True` even when a
    change is pending, so the listener silently no-op'd in tests.
    """
    hist = db.inspect(target).attrs.origin.history
    if hist.has_changes():
        raise ValueError(f"Artifact.origin is immutable (id={target.id})")
