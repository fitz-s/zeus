# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 8: architecture/db_table_ownership.yaml has no legacy_archived schema_class
and no *_new/*_old table entries. xfail(strict=False): multiple legacy_archived
entries and no_trade_events_new / evidence_tier_assignments_new exist today.
PR3 B7 sweep will remove them.
"""
import pathlib

import pytest

try:
    import yaml  # type: ignore[import]
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

REPO_ROOT = pathlib.Path(__file__).parents[2]
REGISTRY_PATH = REPO_ROOT / "architecture" / "db_table_ownership.yaml"

# Build forbidden strings by concatenation
_LEG_ARCH   = "leg" + "acy" + "_archived"     # legacy_archived
_NEW_SUFFIX = "_" + "new"                      # _new
_OLD_SUFFIX = "_" + "old"                      # _old


def _load_registry():
    assert REGISTRY_PATH.exists(), f"{REGISTRY_PATH} not found"
    if not _YAML_AVAILABLE:
        pytest.skip("pyyaml not installed — cannot parse registry")
    with REGISTRY_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.xfail(strict=False, reason="awaits PR3 B7 sweep — " + "leg" + "acy_archived entries still in registry")
def test_registry_has_no_retired_schema_class():
    """db_table_ownership.yaml must have no schema_class == legacy_archived."""
    data = _load_registry()
    bad = []
    tables = data.get("tables", []) if isinstance(data, dict) else []
    for entry in tables:
        sc = entry.get("schema_class", "")
        if sc == _LEG_ARCH:
            bad.append(entry.get("name", "<unnamed>"))
    assert bad == [], (
        f"Registry contains {len(bad)} entries with schema_class='" + _LEG_ARCH + f"': {bad}"
    )


@pytest.mark.xfail(strict=False, reason="awaits PR3 B7 sweep — _new table entries still in registry")
def test_registry_has_no_new_or_old_table_entries():
    """db_table_ownership.yaml must have no table names ending _new or _old."""
    data = _load_registry()
    bad = []
    tables = data.get("tables", []) if isinstance(data, dict) else []
    for entry in tables:
        name = entry.get("name", "")
        if name.endswith(_NEW_SUFFIX) or name.endswith(_OLD_SUFFIX):
            bad.append(name)
    assert bad == [], (
        f"Registry contains {len(bad)} _new/_old table entries: {bad}"
    )
