# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase A
#                  architecture/failure_chains.yaml
"""
Phase A schema validation for architecture/failure_chains.yaml.

Verifies FC-01..FC-10 are all present, each has the required fields, every
touched_surface resolves in architecture/topology_surfaces.yaml, every test
path is a real file, and `not_topology_responsibility` is non-empty (the
field that prevents topology from becoming fake runtime authority).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAINS_PATH = REPO_ROOT / "architecture" / "failure_chains.yaml"
SURFACES_PATH = REPO_ROOT / "architecture" / "topology_surfaces.yaml"

ALLOWED_STATUS = {"active", "mitigated", "advisory", "retired"}

ALLOWED_MISSED_GUARD_TYPES = {
    "WRONG_LAYER_GUARD",
    "TEST_DOES_NOT_HIT_RUNTIME_PATH",
    "MOCK_ONLY_TEST",
    "SHADOW_PATH_UNCOVERED",
    "PATH_TRIGGER_ABSENT",
    "EXTERNAL_SEMANTICS_UNCHECKED",
    "STATIC_PATTERN_NOT_ENCODED",
    "MANIFEST_DRIFT",
    "DOC_AUTHORITY_OUTPACED_RUNTIME",
    "SCHEMA_STATE_UNTESTED",
    "CROSS_DB_BOUNDARY_UNTESTED",
    "ADVISORY_ONLY_GUARD",
    "REVIEW_SCOPE_MISCLASSIFIED",
}

REQUIRED_KEYS = {
    "name",
    "status",
    "root_hazard",
    "historical_evidence",
    "touched_surfaces",
    "money_path_segments",
    "missed_guard_type",
    "required_context_packs",
    "required_tests",
    "required_static_gates",
    "required_ci_workflows",
    "recurrence_escalation",
    "agent_runtime_warnings",
    "not_topology_responsibility",
    "owner",
}


@pytest.fixture(scope="module")
def chains() -> dict:
    assert CHAINS_PATH.exists(), f"missing: {CHAINS_PATH}"
    with CHAINS_PATH.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def surfaces() -> dict:
    with SURFACES_PATH.open() as f:
        return yaml.safe_load(f)


def test_schema_version(chains: dict) -> None:
    assert chains["schema_version"] == 1


def test_all_fc_01_to_fc_10_present(chains: dict) -> None:
    expected = {f"FC-{i:02d}" for i in range(1, 11)}
    actual = set(chains["chains"])
    missing = expected - actual
    assert not missing, f"missing failure chains: {missing}"


def test_every_chain_has_required_keys(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        missing = REQUIRED_KEYS - set(chain)
        assert not missing, f"chain {cid} missing keys: {missing}"


def test_chain_status_in_allowed(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        assert chain["status"] in ALLOWED_STATUS, (
            f"chain {cid}: invalid status {chain['status']!r}"
        )


def test_missed_guard_types_in_allowed(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        for mgt in chain["missed_guard_type"]:
            assert mgt in ALLOWED_MISSED_GUARD_TYPES, (
                f"chain {cid}: unknown missed_guard_type {mgt!r}"
            )


def test_touched_surfaces_resolve_in_surfaces_registry(
    chains: dict, surfaces: dict
) -> None:
    """Every touched_surfaces[] entry must be a key in topology_surfaces.yaml#surfaces."""
    known_surface_ids = set(surfaces["surfaces"])
    for cid, chain in chains["chains"].items():
        for sid in chain["touched_surfaces"]:
            assert sid in known_surface_ids, (
                f"chain {cid}: touched_surface {sid!r} not in topology_surfaces.yaml"
            )


def test_root_hazard_nonempty(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        hazard = chain["root_hazard"].strip()
        assert hazard, f"chain {cid}: root_hazard is empty"
        assert len(hazard) >= 40, (
            f"chain {cid}: root_hazard is suspiciously short ({len(hazard)} chars)"
        )


def test_not_topology_responsibility_nonempty(chains: dict) -> None:
    """
    The most important field — prevents topology from becoming fake runtime
    authority. Spec §0: topology routes context; tests/runtime decide truth.
    """
    for cid, chain in chains["chains"].items():
        ntr = chain["not_topology_responsibility"]
        assert isinstance(ntr, list) and len(ntr) >= 1, (
            f"chain {cid}: not_topology_responsibility must have ≥1 entry"
        )
        for item in ntr:
            assert isinstance(item, str) and item.strip(), (
                f"chain {cid}: empty not_topology_responsibility entry"
            )


def test_historical_evidence_has_prs_or_commits(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        ev = chain["historical_evidence"]
        prs = ev.get("prs", [])
        commits = ev.get("commits", [])
        notes = ev.get("notes", "")
        # At least one form of evidence
        assert prs or commits or notes.strip(), (
            f"chain {cid}: historical_evidence has no prs/commits/notes"
        )


def test_required_test_paths_exist(chains: dict) -> None:
    """Every required_tests[] path must be a real file in the repo."""
    for cid, chain in chains["chains"].items():
        for test_path in chain["required_tests"]:
            assert (REPO_ROOT / test_path).exists(), (
                f"chain {cid}: required_test {test_path!r} does not exist"
            )


def test_recurrence_escalation_complete(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        rec = chain["recurrence_escalation"]
        for key in ("advisory_signal", "threshold", "escalates_to"):
            assert key in rec and rec[key], (
                f"chain {cid}: recurrence_escalation missing/empty {key!r}"
            )


def test_agent_runtime_warnings_nonempty(chains: dict) -> None:
    for cid, chain in chains["chains"].items():
        warnings = chain["agent_runtime_warnings"]
        assert isinstance(warnings, list) and len(warnings) >= 1, (
            f"chain {cid}: agent_runtime_warnings must have ≥1 entry"
        )


def test_fc_id_format(chains: dict) -> None:
    import re
    pat = re.compile(r"^FC-\d{2}$")
    for cid in chains["chains"]:
        assert pat.match(cid), f"chain id {cid!r} not FC-NN format"
