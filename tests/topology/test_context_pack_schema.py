# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase A
#                  architecture/context_pack_schema.yaml
"""
Phase A schema validation for architecture/context_pack_schema.yaml.

Verifies the contract is well-formed and self-consistent. Does NOT validate
any concrete Context Pack instance — that comes in Phase B integration tests
with PR325/PR330/PR335/PR312/PR306 fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "architecture" / "context_pack_schema.yaml"


@pytest.fixture(scope="module")
def schema() -> dict:
    """Load the schema once per test module."""
    assert SCHEMA_PATH.exists(), f"missing: {SCHEMA_PATH}"
    with SCHEMA_PATH.open() as f:
        return yaml.safe_load(f)


def test_schema_version_is_one(schema: dict) -> None:
    """schema_version must be 1; bumping requires explicit migration plan."""
    assert schema["schema_version"] == 1


def test_metadata_required_keys(schema: dict) -> None:
    md = schema["metadata"]
    for key in ("created", "owner", "status", "refines", "inputs"):
        assert key in md, f"metadata missing {key!r}"


def test_metadata_inputs_resolve(schema: dict) -> None:
    """Every referenced input manifest must be a real file in the repo."""
    for ref in schema["metadata"]["inputs"]:
        assert (REPO_ROOT / ref).exists(), f"missing input manifest: {ref}"


def test_required_fields_list_nonempty(schema: dict) -> None:
    req = schema["required_fields"]
    assert isinstance(req, list) and len(req) >= 10
    expected_minimum = {
        "id",
        "title",
        "status",
        "owner",
        "risk_tier",
        "matched_surfaces",
        "required_reads",
        "failure_chains",
        "fatal_misreads",
        "required_relationship_tests",
        "ci_classification",
        "not_topology_responsibility",
    }
    assert expected_minimum.issubset(set(req)), (
        f"missing required_fields: {expected_minimum - set(req)}"
    )


def test_allowed_status_values(schema: dict) -> None:
    assert set(schema["allowed_status"]) == {"active", "provisional", "retired"}


def test_allowed_risk_tier_values(schema: dict) -> None:
    assert set(schema["allowed_risk_tier"]) == {"T0", "T1", "T2", "T3", "T4"}


def test_allowed_ci_classification_values(schema: dict) -> None:
    assert set(schema["allowed_ci_classification"]) == {
        "blocking_static",
        "blocking_relationship",
        "advisory",
        "nightly",
        "manual",
    }


def test_allowed_money_path_segments_covers_full_chain(schema: dict) -> None:
    """Spec §0 Zeus money path: contract→source→forecast→calibration→edge→exec→monitor→settle→learn."""
    expected = {
        "contract_semantics",
        "source_truth",
        "forecast_signal",
        "calibration",
        "market_prior",
        "executable_edge",
        "sizing",
        "execution",
        "monitoring",
        "settlement",
        "learning",
    }
    assert set(schema["allowed_money_path_segments"]) == expected


def test_multi_file_union_policy_present(schema: dict) -> None:
    """Operator caveat 2026-05-26: multi-file PRs must NOT fall to generic profile."""
    policy = schema["multi_file_union_policy"]
    assert policy["default_mode"] == "emit_per_surface"
    assert "alternate_mode" in policy
    assert "forbidden" in policy
    # The most important forbidden behavior:
    forbidden = " ".join(policy["forbidden"]).lower()
    assert "generic" in forbidden
    assert "selecting one surface and discarding others" in forbidden


def test_integrity_rules_have_required_keys(schema: dict) -> None:
    """Every integrity rule must declare id + description + severity."""
    for rule in schema["integrity_rules"]:
        for key in ("id", "description", "severity"):
            assert key in rule, f"integrity rule missing {key!r}: {rule}"
        assert rule["severity"] in {"blocking", "important", "advisory"}


def test_integrity_rules_unique_ids(schema: dict) -> None:
    ids = [r["id"] for r in schema["integrity_rules"]]
    assert len(ids) == len(set(ids)), f"duplicate integrity rule ids: {ids}"


def test_active_invariants_source_enum_lists_canonical_sources(schema: dict) -> None:
    """
    architecture/invariants.yaml DOES exist (INV-NN structural-law invariants)
    alongside architecture/money_path_ci.yaml (MP-NN money-path invariants)
    and architecture/money_path_objects.yaml. The active_invariants.source
    enum must permit all three plus `custom`.

    (Earlier version of this test was based on a false-negative existence
    check; corrected per Copilot finding on PR #343.)
    """
    schema_text = SCHEMA_PATH.read_text()
    # Strip line-comments before scanning to avoid matching documentation.
    code_lines = []
    for line in schema_text.splitlines():
        if "#" in line:
            line = line.split("#", 1)[0]
        code_lines.append(line)
    code_text = "\n".join(code_lines)

    active_section = code_text.split("active_invariants:")[1].split(
        "required_relationship_tests:"
    )[0]
    for required_source in (
        "architecture/money_path_ci.yaml",
        "architecture/money_path_objects.yaml",
        "architecture/invariants.yaml",
        "custom",
    ):
        assert required_source in active_section, (
            f"active_invariants.source enum must include {required_source!r}"
        )


def test_context_pack_shape_has_required_fields(schema: dict) -> None:
    """The context_pack: definition must enumerate all required_fields."""
    pack = schema["context_pack"]
    for field in schema["required_fields"]:
        assert field in pack, f"context_pack: missing field {field!r}"
