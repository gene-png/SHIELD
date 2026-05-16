"""Security headers + Content Security Policy.

CSP is strict and self-hosted ONLY. No third-party CDNs. This matches the
GCC High posture from the spec (Section 3): no assets, fonts, icons, or
scripts from arbitrary public CDNs.
"""
from __future__ import annotations

from flask import Flask, Response


CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "          # USWDS uses some inline; consider tightening with hashes later
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def _set_headers(resp: Response) -> Response:
        resp.headers.setdefault("Content-Security-Policy", CSP_POLICY)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if not app.debug:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp
