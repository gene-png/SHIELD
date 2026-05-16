# Attributions

SHIELD bundles or depends on the following third-party assets and data sets.

## MITRE ATT&CK

[https://attack.mitre.org/](https://attack.mitre.org/)
© The MITRE Corporation. Used under MITRE's [Terms of Use](https://attack.mitre.org/resources/terms-of-use/).
The Enterprise ATT&CK technique slice bundled in
`shield/p3_attack_surface/attack_data.py` is derived from ATT&CK
Enterprise v14.x. SHIELD does not modify the meaning of any technique
definition.

## USWDS — U.S. Web Design System

[https://designsystem.digital.gov/](https://designsystem.digital.gov/)
Public domain (CC0 1.0). Vendored into `shield/static/uswds/` via
`scripts/vendor_assets.py`. Self-hosted; no CDN reference.

## HTMX

[https://htmx.org/](https://htmx.org/)
BSD-2-Clause. Vendored into `shield/static/htmx/` via
`scripts/vendor_assets.py`.

## Python dependencies

See `requirements.txt`. All pinned. Licenses listed in their respective
project repositories.

## Anthropic Claude

In-app AI processing and the headless dev agent call the Anthropic API.
SHIELD never sends data to any other LLM provider.
