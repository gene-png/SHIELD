"""Shared services (text extraction, etc.).

Distinct from `spine/`, which holds the integrity primitives. Services
here are utility code — they transform inputs, but the integrity
contract does not depend on them. If a service breaks, the upload still
lands in the correct lane; only the convenience features degrade.
"""
