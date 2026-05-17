"""Seed catalog invariants."""
from scripts.seed_catalog import CATALOG


def test_catalog_size_at_least_75():
    assert len(CATALOG) >= 75, f"expected >= 75 items, got {len(CATALOG)}"


def test_catalog_has_deliberate_overlaps():
    by_category: dict[str, int] = {}
    for entry in CATALOG:
        by_category[entry["category"]] = by_category.get(entry["category"], 0) + 1
    # Each of these categories MUST appear twice or more so the overlap
    # analysis has real findings on first run.
    for required in ("SIEM", "EDR", "Productivity", "MFA", "Backup"):
        assert by_category.get(required, 0) >= 2, f"need >=2 in {required}, got {by_category.get(required, 0)}"


def test_catalog_total_cost_positive():
    from scripts.seed_catalog import total_cost_usd
    assert total_cost_usd() > 0
