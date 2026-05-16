"""Vendor USWDS + HTMX into shield/static/ so the app is fully self-hosted.

Run once after clone:    python scripts/vendor_assets.py
Or via Make:             make vendor-assets

This downloads at build time only. The app never reaches out for assets
at runtime — important for the GCC High deployment posture.
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "shield" / "static"

USWDS_VERSION = "3.8.1"
USWDS_URL = f"https://github.com/uswds/uswds/releases/download/v{USWDS_VERSION}/uswds-{USWDS_VERSION}.zip"

HTMX_VERSION = "2.0.3"
HTMX_URL = f"https://unpkg.com/htmx.org@{HTMX_VERSION}/dist/htmx.min.js"


def _download(url: str) -> bytes:
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 -- pinned vendor URLs
        return r.read()


def vendor_uswds() -> None:
    target = STATIC / "uswds"
    target.mkdir(parents=True, exist_ok=True)
    print("USWDS:")
    data = _download(USWDS_URL)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            # The zip ships under "uswds-<version>/dist/..."
            parts = member.split("/")
            if len(parts) < 3 or parts[1] != "dist":
                continue
            rel = Path(*parts[2:])
            if not rel.parts:
                continue
            dest = target / rel
            if member.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
    print(f"  USWDS unpacked into {target}")


def vendor_htmx() -> None:
    target = STATIC / "htmx"
    target.mkdir(parents=True, exist_ok=True)
    print("HTMX:")
    (target / "htmx.min.js").write_bytes(_download(HTMX_URL))
    print(f"  HTMX written to {target}")


def main() -> int:
    try:
        vendor_uswds()
        vendor_htmx()
    except Exception as e:  # noqa: BLE001
        print(f"vendor failed: {e}", file=sys.stderr)
        print("If you're offline, the app still runs but USWDS/HTMX features will be missing.", file=sys.stderr)
        return 1
    print("Done. Commit the vendored files OR add them to .gitignore (default).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
