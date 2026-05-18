"""Round-7 §21/§24/§25: section-and-question questionnaires.

The legacy `frameworks.py` exposes flat control catalogs (one row per
subcategory) which the existing P2 questionnaire table renders. This
new module sits alongside it and exposes the section-and-question
shape from the spec — same source YAML, but with the full per-question
content (stem, interviewer cues, framework mappings, maturity
dimensions, implementation groups).

When the section-by-section in-app questionnaire UI lands (§21.7),
it'll consume `load_questionnaire(framework_id)`. Until then, this
module is the data + dispatch layer and is testable independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).parent / "catalogs"


@dataclass(frozen=True)
class AnswerOption:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    number: str
    stem: str
    cues: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    # Framework-specific mappings — the loader sets whichever of these
    # is non-empty depending on which catalog the question came from.
    csf_subcategories: tuple[str, ...] = ()
    implementation_groups: tuple[int, ...] = ()
    ztmm_functions: tuple[str, ...] = ()
    dod_activities: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    id: str
    number: int
    title: str
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class Questionnaire:
    framework: str
    display_name: str
    source: str
    version: str
    impact_level: str | None
    sections: tuple[Section, ...]
    answer_scale: tuple[AnswerOption, ...]
    documents: tuple[str, ...]
    system_profile_fields: tuple[dict[str, Any], ...] = ()

    @property
    def question_count(self) -> int:
        return sum(len(s.questions) for s in self.sections)

    def section_by_id(self, section_id: str) -> Section | None:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None

    def question_by_id(self, question_id: str) -> Question | None:
        for s in self.sections:
            for q in s.questions:
                if q.id == question_id:
                    return q
        return None


def _as_question(d: dict) -> Question:
    return Question(
        id=d["id"],
        number=str(d.get("number") or ""),
        stem=str(d["stem"]).strip(),
        cues=tuple(d.get("cues") or ()),
        dimensions=tuple(d.get("dimensions") or ()),
        csf_subcategories=tuple(d.get("csf_subcategories") or ()),
        implementation_groups=tuple(d.get("implementation_groups") or ()),
        ztmm_functions=tuple(d.get("ztmm_functions") or ()),
        dod_activities=tuple(d.get("dod_activities") or ()),
        phases=tuple(d.get("phases") or ()),
    )


def _as_section(d: dict) -> Section:
    return Section(
        id=d["id"],
        number=int(d.get("number") or 0),
        title=str(d.get("title") or ""),
        questions=tuple(_as_question(q) for q in (d.get("questions") or [])),
    )


def _as_option(d: dict) -> AnswerOption:
    return AnswerOption(
        id=d["id"],
        label=str(d.get("label") or d["id"]),
        description=str(d.get("description") or ""),
    )


def _load_yaml(path: Path) -> Questionnaire:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Questionnaire(
        framework=payload["framework"],
        display_name=str(payload.get("display_name") or payload["framework"]),
        source=str(payload.get("source") or ""),
        version=str(payload.get("version") or ""),
        impact_level=payload.get("impact_level"),
        sections=tuple(_as_section(s) for s in payload.get("sections", [])),
        answer_scale=tuple(_as_option(o) for o in payload.get("answer_scale", [])),
        documents=tuple(payload.get("documents") or ()),
        system_profile_fields=tuple(payload.get("system_profile_fields") or ()),
    )


# Filename for each known framework id. Adding a new framework? Drop
# its YAML in catalogs/ and add the mapping here.
_QUESTIONNAIRE_FILES = {
    "csf_2_0_high":  "csf_2_0_high.yaml",
    "cisa_ztmm_v2":  "cisa_ztmm_v2.yaml",
    "dod_zt":        "dod_zt.yaml",
}


@lru_cache(maxsize=8)
def load_questionnaire(framework_id: str) -> Questionnaire:
    """Return the parsed questionnaire for `framework_id`.

    Cached after first read; the YAML files are bundled with the app
    and don't change at runtime. Raises FileNotFoundError if there's
    no YAML for the requested framework — that's the right failure
    shape (a user shouldn't be able to land on a questionnaire we
    don't have a catalog for).
    """
    if framework_id not in _QUESTIONNAIRE_FILES:
        raise KeyError(f"Unknown questionnaire framework: {framework_id!r}")
    return _load_yaml(CATALOG_DIR / _QUESTIONNAIRE_FILES[framework_id])


def list_questionnaires() -> list[str]:
    """Stable-ordered list of framework ids we have a catalog for."""
    return sorted(_QUESTIONNAIRE_FILES)
