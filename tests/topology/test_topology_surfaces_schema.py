# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase A
#                  architecture/topology_surfaces.yaml
"""
Phase A schema validation for architecture/topology_surfaces.yaml.

Verifies the surface registry is well-formed: every surface has required keys,
every relationship_tests path is a real file, every scoped_agents path is a
real AGENTS.md, money_path_segments come from the canonical set.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SURFACES_PATH = REPO_ROOT / "architecture" / "topology_surfaces.yaml"
SCHEMA_PATH = REPO_ROOT / "architecture" / "context_pack_schema.yaml"

ALLOWED_TYPES = {
    "path",
    "symbol",
    "db_table",
    "source",
    "workflow",
    "test",
    "doc_authority",
    "operation",
    "manifest",
}

ALLOWED_TIERS = {"T0", "T1", "T2", "T3", "T4"}

REQUIRED_KEYS = {
    "type",
    "description",
    "patterns",
    "money_path_segments",
    "risk_tier",
    "scoped_agents",
    "context_packs",
    "manifests_required",
    "relationship_tests",
    "static_gates",
    "advisory_signals",
    "owners",
}


@pytest.fixture(scope="module")
def surfaces() -> dict:
    assert SURFACES_PATH.exists(), f"missing: {SURFACES_PATH}"
    with SURFACES_PATH.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def allowed_money_path_segments() -> set[str]:
    with SCHEMA_PATH.open() as f:
        schema = yaml.safe_load(f)
    return set(schema["allowed_money_path_segments"])


def test_schema_version(surfaces: dict) -> None:
    assert surfaces["schema_version"] == 1


def test_metadata_present(surfaces: dict) -> None:
    assert "metadata" in surfaces
    assert surfaces["metadata"]["owner"]
    assert surfaces["metadata"]["created"]


def test_surfaces_nonempty(surfaces: dict) -> None:
    assert len(surfaces["surfaces"]) >= 10, (
        "expected at least 10 surfaces covering FC-01..FC-10"
    )


def test_every_surface_has_required_keys(surfaces: dict) -> None:
    for sid, surface in surfaces["surfaces"].items():
        missing = REQUIRED_KEYS - set(surface)
        assert not missing, f"surface {sid} missing keys: {missing}"


def test_surface_type_in_allowed(surfaces: dict) -> None:
    for sid, surface in surfaces["surfaces"].items():
        assert surface["type"] in ALLOWED_TYPES, (
            f"surface {sid}: invalid type {surface['type']!r}"
        )


def test_surface_risk_tier_in_allowed(surfaces: dict) -> None:
    for sid, surface in surfaces["surfaces"].items():
        assert surface["risk_tier"] in ALLOWED_TIERS, (
            f"surface {sid}: invalid risk_tier {surface['risk_tier']!r}"
        )


def test_money_path_segments_in_allowed(
    surfaces: dict, allowed_money_path_segments: set[str]
) -> None:
    for sid, surface in surfaces["surfaces"].items():
        for seg in surface["money_path_segments"]:
            assert seg in allowed_money_path_segments, (
                f"surface {sid}: unknown money_path_segment {seg!r} "
                f"(allowed: {sorted(allowed_money_path_segments)})"
            )


def test_scoped_agents_paths_exist(surfaces: dict) -> None:
    for sid, surface in surfaces["surfaces"].items():
        for agents_path in surface["scoped_agents"]:
            assert (REPO_ROOT / agents_path).exists(), (
                f"surface {sid}: scoped_agents path {agents_path!r} does not exist"
            )


def test_relationship_test_paths_exist(surfaces: dict) -> None:
    """
    Every relationship_tests[].path must be a real file.
    REVIEW_REQUIRED entries (path containing 'review_required') are skipped.
    """
    for sid, surface in surfaces["surfaces"].items():
        for entry in surface["relationship_tests"]:
            path = entry["path"]
            if "review_required" in path:
                continue
            assert (REPO_ROOT / path).exists(), (
                f"surface {sid}: relationship_test {path!r} does not exist"
            )


def test_surface_ids_lower_snake_case(surfaces: dict) -> None:
    """Surface ids are lower_snake_case: letters/digits/underscores, no hyphens.
    (Renamed from ``test_surface_ids_kebab_or_snake_case`` per Copilot finding
    on PR #343 — name implied either was permitted, regex enforced snake only.)
    """
    import re
    pat = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]$")
    for sid in surfaces["surfaces"]:
        assert pat.match(sid), f"surface id {sid!r} not lower_snake_case"


def test_no_duplicate_surface_ids(surfaces: dict) -> None:
    # YAML dict keys are unique by definition; this test guards against
    # a future migration to a list shape regressing the property.
    ids = list(surfaces["surfaces"])
    assert len(ids) == len(set(ids)), f"duplicate surface ids: {ids}"


def test_fc_coverage_minimum(surfaces: dict) -> None:
    """At least these surface_ids must exist for FC-01..FC-10 routing."""
    must_exist = {
        "execution_cycle_runtime",     # FC-03
        "execution_venue_boundary",    # FC-03
        "executable_forecast_reader",  # FC-01
        "market_discovery_scanner",    # FC-02
        "ingest_scheduler",            # FC-04 + FC-05
        "forecast_live_daemon",        # FC-06
        "day0_observation_reader",     # FC-07
        "db_schema_authority",         # FC-08
        "topology_v_next",             # FC-09 (stdlib shadow)
        "review_scope_collect",        # FC-10
    }
    missing = must_exist - set(surfaces["surfaces"])
    assert not missing, f"missing required surfaces for FC coverage: {missing}"


def test_integrity_rules_present(surfaces: dict) -> None:
    rules = surfaces.get("integrity_rules", [])
    assert len(rules) >= 3
    for rule in rules:
        for key in ("id", "description", "severity"):
            assert key in rule, f"integrity rule missing {key!r}: {rule}"
