"""Async AI jobs. Run in the RQ worker, not in the web request thread.

Routes enqueue work here so a slow Anthropic call (10–30 s) doesn't tie
up a gunicorn worker. The web side returns immediately with a redirect
to /jobs/<id>/wait, which polls the job status until done.

Each job below is a top-level function (RQ pickles function refs by
import path) that:
  1. Creates a fresh Flask app context (workers live outside Flask).
  2. Re-loads the necessary rows from the DB by ID (RQ pickles only
     primitive args, never ORM objects).
  3. Runs the AI call.
  4. Writes the AI artifact via spine.repository.
  5. Returns the new artifact's ID so the wait page can redirect to it.

Errors propagate to RQ as a failed job and are surfaced in the wait UI.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

QUEUE_NAME = "shield-ai"


# --------------------------------------------------------------------
# Queue plumbing — lazy so importing this module from the test config
# (which has no Redis) doesn't fail.
# --------------------------------------------------------------------

_redis = None
_queue = None


def _redis_conn():
    global _redis
    if _redis is None:
        from redis import Redis
        url = os.environ.get("REDIS_URL") or "redis://redis:6379/0"
        _redis = Redis.from_url(url)
    return _redis


def get_queue():
    """Return the RQ Queue, lazily creating it on first access."""
    global _queue
    if _queue is None:
        from rq import Queue
        _queue = Queue(QUEUE_NAME, connection=_redis_conn(), default_timeout=600)
    return _queue


def enqueue_ai(fn: Callable[..., Any], *args, **kwargs):
    """Enqueue an AI job. Returns the rq.Job."""
    return get_queue().enqueue(fn, *args, **kwargs)


# --------------------------------------------------------------------
# Per-job worker entrypoint. The Flask app context is created here so
# every job function below can assume db / current_app / etc. work.
# --------------------------------------------------------------------

def _within_app(fn: Callable[..., Any], *args, **kwargs):
    """Ensure an app context, then call the job body.

    In the worker container there is no Flask app, so we have to build
    one with `create_app()`. In the web request path (and in tests that
    run RQ in sync mode), an app context is already active and we just
    call through — building a second app would point the job's DB
    session at the wrong database.
    """
    from flask import current_app
    try:
        current_app._get_current_object()
    except RuntimeError:
        # No app context — worker case.
        from shield import create_app
        app = create_app()
        with app.app_context():
            return fn(*args, **kwargs)
    else:
        return fn(*args, **kwargs)


def _prompt(name: str) -> str:
    return (Path(__file__).resolve().parent / "ai" / "prompts" / name).read_text(encoding="utf-8")


def _parse_ai_coverage_response(text: str) -> dict:
    """Parse the P3 coverage JSON; recover as much as possible if truncated.

    The Anthropic SDK can stop generation mid-JSON when the response hits
    `max_tokens`. The body_text we get is a prefix of valid JSON cut at
    an arbitrary character. This function tries the happy path first;
    on failure it walks the prefix tracking brace depth, finds the last
    complete `}` at depth 1 (which is a complete object inside the
    "findings" array), and closes the JSON manually with `]}`.

    If recovery yields findings but no executive_summary, the summary is
    synthesized from the recovered findings' coverage counts. That way
    the UI shows real numbers (e.g. "180 of 222 evaluated") instead of
    silent zeros.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Walk the prefix tracking brace depth + JSON strings; remember the
    # last position where we closed a depth-1 object (i.e. a complete
    # finding inside the findings array).
    depth = 0
    in_string = False
    escape = False
    last_complete_at_depth_1 = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 1:
                last_complete_at_depth_1 = i

    if last_complete_at_depth_1 < 0:
        return {"findings": [], "executive_summary": {}}

    repaired = text[: last_complete_at_depth_1 + 1] + "]}"
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        return {"findings": [], "executive_summary": {}}

    findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
    if not parsed.get("executive_summary"):
        covered = sum(1 for f in findings if (f or {}).get("coverage") == "covered")
        partial = sum(1 for f in findings if (f or {}).get("coverage") == "partial")
        uncovered = sum(1 for f in findings if (f or {}).get("coverage") == "uncovered")
        parsed["executive_summary"] = {
            "total_techniques": len(findings),
            "covered": covered,
            "partial": partial,
            "uncovered": uncovered,
            "headline": (
                f"Coverage evaluated against {len(findings)} ATT&CK techniques "
                f"(response was truncated; counts derive from recovered findings)."
            ),
            "top_three_blind_spots": [
                f.get("technique_id") for f in findings
                if (f or {}).get("coverage") == "uncovered"
            ][:3],
        }
    return parsed


# ====================================================================
# Platform 1 — Tech Debt
# ====================================================================

def p1_extract_job(project_id: str, source_artifact_id: str) -> str:
    return _within_app(_p1_extract, project_id, source_artifact_id)


def _p1_extract(project_id: str, source_artifact_id: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import Artifact, Origin, Project
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    src = db.session.get(Artifact, source_artifact_id)
    if project is None or src is None or src.origin != Origin.HUMAN_INPUT:
        raise ValueError("Project or source artifact missing / wrong origin")

    source_text = src.body_text or "(binary content; extraction stub returned)"
    text, lineage = AIClient().complete(
        system=_prompt("p1_extraction.md"),
        user=source_text[:200_000],
        prompt_version="p1_extraction.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="ai_extraction",
        title=f"AI extraction of {src.title}",
        body_text=text,
        input_artifact_ids=[src.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        additional_lineage=lineage,
    )
    return art.id


def p1_overlap_job(project_id: str, confirmed_artifact_id: str) -> str:
    return _within_app(_p1_overlap, project_id, confirmed_artifact_id)


def _p1_overlap(project_id: str, confirmed_artifact_id: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import Artifact, Origin, Project
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    confirmed = db.session.get(Artifact, confirmed_artifact_id)
    if project is None or confirmed is None or confirmed.origin != Origin.HUMAN_AI_INFORMED:
        raise ValueError("Project or confirmed-extraction artifact missing / wrong origin")

    text, lineage = AIClient().complete(
        system=_prompt("p1_overlap.md"),
        user=confirmed.body_text or "[]",
        prompt_version="p1_overlap.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="overlap_analysis",
        title="AI overlap analysis",
        body_text=text,
        input_artifact_ids=[confirmed.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        additional_lineage=lineage,
    )
    return art.id


def p1_chat_job(project_id: str, question: str) -> str:
    return _within_app(_p1_chat, project_id, question)


def _p1_chat(project_id: str, question: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import Artifact, Origin, Project
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    if project is None:
        raise ValueError("Project missing")
    confirmed = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.HUMAN_AI_INFORMED, stage="extraction_review")
        .order_by(Artifact.created_at.desc()).first()
    )
    overlap_art = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.AI_GENERATED, stage="overlap_analysis")
        .order_by(Artifact.created_at.desc()).first()
    )
    if confirmed is None or overlap_art is None:
        raise ValueError("Chat requires confirmed extraction AND overlap analysis")

    try:
        cap = json.loads(confirmed.body_text or "[]")
    except json.JSONDecodeError:
        cap = confirmed.body_text
    try:
        overlap = json.loads(overlap_art.body_text or "{}")
    except json.JSONDecodeError:
        overlap = overlap_art.body_text

    payload = {"capability_list": cap, "overlap_analysis": overlap, "question": question}
    text, lineage = AIClient().complete(
        system=_prompt("p1_chat.md"),
        user=json.dumps(payload),
        prompt_version="p1_chat.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="conversational_interrogation",
        title=f"Q: {question[:80]}{'…' if len(question) > 80 else ''}",
        body_text=text,
        input_artifact_ids=[confirmed.id, overlap_art.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        additional_lineage={**lineage, "question": question},
    )
    return art.id


# ====================================================================
# Platform 2 — Zero Trust
# ====================================================================

def p2_analyze_job(project_id: str) -> str:
    return _within_app(_p2_analyze, project_id)


def _p2_analyze(project_id: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import Project, QuestionnaireResponse
    from .p2_zerotrust.frameworks import FRAMEWORKS
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    framework = FRAMEWORKS.get(project.framework or "") if project else None
    if project is None or framework is None:
        raise ValueError("Project or framework missing")

    cl = project.capability_snapshot
    capabilities = []
    if cl is not None:
        capabilities = [
            {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
            for i in cl.items
        ]
    responses = db.session.query(QuestionnaireResponse).filter_by(project_id=project.id).all()
    payload = {
        "framework": framework.id,
        "controls": [{"id": c.id, "title": c.title, "pillar": c.pillar} for c in framework.controls],
        "responses": [
            {"control_id": r.control_id, "answer": r.answer, "rationale": r.rationale,
             "trust_tier": r.trust_tier.value}
            for r in responses
        ],
        "capabilities": capabilities,
    }
    text, lineage = AIClient().complete(
        system=_prompt("p2_posture.md"),
        user=json.dumps(payload, indent=2),
        prompt_version="p2_posture.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="current_state_assessment",
        title="AI posture analysis — current state",
        body_text=text,
        input_artifact_ids=[],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id if cl else None,
        additional_lineage=lineage,
    )
    return art.id


def p2_roadmap_job(project_id: str) -> str:
    return _within_app(_p2_roadmap, project_id)


def _p2_roadmap(project_id: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import Artifact, Origin, Project
    from .p2_zerotrust.frameworks import FRAMEWORKS
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    framework = FRAMEWORKS.get(project.framework or "") if project else None
    if project is None or framework is None:
        raise ValueError("Project or framework missing")

    current = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.AI_GENERATED, stage="current_state_assessment")
        .order_by(Artifact.created_at.desc()).first()
    )
    desired = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.HUMAN_INPUT, stage="desired_future_state")
        .order_by(Artifact.created_at.desc()).first()
    )
    if current is None or desired is None:
        raise ValueError("Roadmap requires both current-state AND desired-state artifacts")

    cl = project.capability_snapshot
    capabilities = []
    if cl is not None:
        capabilities = [
            {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
            for i in cl.items
        ]
    try:
        current_payload = json.loads(current.body_text or "{}")
    except json.JSONDecodeError:
        current_payload = current.body_text
    try:
        desired_payload = json.loads(desired.body_text or "{}")
    except json.JSONDecodeError:
        desired_payload = desired.body_text
    payload = {
        "framework": framework.id,
        "controls": [{"id": c.id, "title": c.title, "pillar": c.pillar} for c in framework.controls],
        "current_state": current_payload,
        "desired_future_state": desired_payload,
        "capabilities": capabilities,
    }
    text, lineage = AIClient().complete(
        system=_prompt("p2_roadmap.md"),
        user=json.dumps(payload, indent=2),
        prompt_version="p2_roadmap.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="transition_roadmap",
        title="AI transition roadmap (draft — for admin validation)",
        body_text=text,
        input_artifact_ids=[current.id, desired.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id if cl else None,
        additional_lineage=lineage,
    )
    return art.id


# ====================================================================
# Platform 3 — Attack Surface
# ====================================================================

def p3_coverage_job(project_id: str) -> str:
    return _within_app(_p3_coverage, project_id)


def _p3_coverage(project_id: str) -> str:
    from .ai.client import AIClient
    from .extensions import db
    from .models import CoverageFinding, CoverageRun, MitreTechnique, Project
    from .p3_attack_surface.attack_data import TECHNIQUES as STARTER
    from .spine.repository import write_ai_artifact

    project = db.session.get(Project, project_id)
    if project is None:
        raise ValueError("Project missing")
    cl = project.capability_snapshot
    if cl is None:
        raise ValueError("Coverage requires a linked capability list")

    # DB-backed with fallback (mirrors the route logic).
    rows = db.session.query(MitreTechnique).order_by(MitreTechnique.technique_id).all()
    # Send id + name + tactic only. Claude knows the ATT&CK matrix
    # natively; including 1-4 KB descriptions per technique pushed the
    # input payload to ~118K tokens, made the API call take >120s, and
    # the SDK's retry loop blew through the 600s RQ timeout. id+name+tactic
    # is ~3K input tokens for the whole 222-technique catalog.
    techniques = [
        {"technique_id": r.technique_id, "name": r.name, "tactic": r.tactic}
        for r in rows
    ] or [
        # In-memory fallback for tests: strip description from the
        # starter set too, so the payload shape is identical.
        {"technique_id": t["technique_id"], "name": t["name"], "tactic": t["tactic"]}
        for t in STARTER
    ]

    capabilities = [
        {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
        for i in cl.items
    ]
    payload = {"capabilities": capabilities, "techniques": techniques}
    text, lineage = AIClient().complete(
        system=_prompt("p3_attack_coverage.md"),
        user=json.dumps(payload),
        prompt_version="p3_attack_coverage.v1",
        json_response=True,
    )
    art = write_ai_artifact(
        project=project, stage="attack_coverage",
        title="AI ATT&CK coverage analysis",
        body_text=text,
        input_artifact_ids=[],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id,
        additional_lineage=lineage,
    )

    # Materialize the run + per-technique findings.
    # Use the truncation-aware parser: if the AI response hit the
    # max_output_tokens cap mid-JSON, recover as many complete findings
    # as we can and synthesize the summary counts from them.
    parsed = _parse_ai_coverage_response(text)
    run = CoverageRun(
        project_id=project.id,
        capability_list_version_id=cl.id,
        artifact_id=art.id,
        summary=parsed.get("executive_summary", {}),
    )
    db.session.add(run)
    db.session.flush()
    for finding in parsed.get("findings", []):
        db.session.add(CoverageFinding(
            coverage_run_id=run.id,
            technique_id=finding.get("technique_id", ""),
            coverage=finding.get("coverage", "uncovered"),
            detection_tools=finding.get("detection_tools", []),
            prevention_tools=finding.get("prevention_tools", []),
            response_tools=finding.get("response_tools", []),
            rationale=finding.get("rationale", ""),
        ))
    db.session.commit()
    return art.id
