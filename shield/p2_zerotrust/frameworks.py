"""Zero-Trust framework definitions.

The catalogs live as JSON in `shield/p2_zerotrust/catalogs/` and are
loaded at import. Regenerate them with:

    docker compose exec app python scripts/vendor_zt_catalogs.py

That script pulls NIST CSF 2.0 from its OSCAL JSON release and writes
all three frameworks into the catalogs directory. CISA ZTMM 2.0 and
the DoD Zero Trust Strategy are mirrored structurally (the official
documents are PDFs); rerun the script after either body publishes a
revision and commit the regenerated JSON.

Loading from JSON (rather than pinning the controls in Python) gives
us two things:
  1. Updates are diff-reviewable as data, not as code edits to a 200-
     line literal.
  2. CI / tests assert against the JSON contract, so a malformed
     catalog produced by a future vendor-script change fails loudly
     instead of silently shipping a bad framework.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CATALOG_DIR = Path(__file__).parent / "catalogs"


@dataclass
class Control:
    id: str
    pillar: str
    title: str
    description: str = ""


@dataclass
class Framework:
    id: str
    name: str
    pillars: list[str]
    controls: list[Control] = field(default_factory=list)
    source: str = ""
    version: str = ""


def _load(filename: str) -> Framework:
    """Load one catalog JSON and build a Framework dataclass.

    Raises FileNotFoundError at import time if the catalog hasn't been
    vendored — that's the right failure: app startup should refuse to
    run with a missing framework rather than serve an empty pillar list.
    """
    path = CATALOG_DIR / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Framework(
        id=payload["id"],
        name=payload["name"],
        pillars=list(payload["pillars"]),
        controls=[
            Control(
                id=c["id"],
                pillar=c["pillar"],
                title=c["title"],
                description=c.get("description", ""),
            )
            for c in payload["controls"]
        ],
        source=payload.get("source", ""),
        version=payload.get("version", ""),
    )


CISA_ZTMM = _load("cisa_ztmm_v2.json")
DOD_ZT = _load("dod_zt.json")
NIST_CSF = _load("nist_csf_v2.json")

FRAMEWORKS: dict[str, Framework] = {
    f.id: f for f in (CISA_ZTMM, DOD_ZT, NIST_CSF)
}
