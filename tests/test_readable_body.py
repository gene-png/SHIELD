"""Tests for the `from_json` template filter and the readable_body partial.

These don't render the partial in isolation — Jinja includes resolve
against the full app's template loader and require a request context —
so we exercise the filter directly and rely on the artifact_detail
view to integration-cover the partial.
"""
from __future__ import annotations

import json

import pytest

from shield import create_app
from shield.config import TestConfig


@pytest.fixture()
def app():
    return create_app(TestConfig)


def test_from_json_parses_object(app):
    f = app.jinja_env.filters["from_json"]
    assert f('{"a": 1}') == {"a": 1}


def test_from_json_parses_array(app):
    f = app.jinja_env.filters["from_json"]
    assert f("[1, 2, 3]") == [1, 2, 3]


def test_from_json_returns_none_on_garbage(app):
    """The filter must NEVER raise — templates branch on the result."""
    f = app.jinja_env.filters["from_json"]
    assert f("not json at all") is None
    assert f("{ unterminated") is None
    assert f("") is None
    assert f(None) is None


def test_from_json_roundtrips_complex_shape(app):
    f = app.jinja_env.filters["from_json"]
    payload = {"findings": [{"id": "T1059", "coverage": "partial"}],
               "executive_summary": {"total_techniques": 222}}
    out = f(json.dumps(payload))
    assert out == payload
