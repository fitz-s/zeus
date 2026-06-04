# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: PR-2 D2 ARM-artifact boot binding (F1 Option C); structural fix plan
#   §arm-binding-safety. Antibody: flipping real_order_submit_enabled=true (canary OR live)
#   without the settlement-grounded ARM evidence artifact must be a BOOT FAILURE, not a
#   runtime path. The artifact is PRODUCED by scripts/measure_arm_gate_settlement.py (PR-1);
#   this file enforces the contract at boot.
"""Relationship test: live/canary boot REQUIRES the ARM-gate evidence artifact.

The contract under test crosses two modules:
  * scripts/measure_arm_gate_settlement.py (producer, PR-1) writes
    state/edli_arm_gate_artifact.json with the settlement-grounded capital-weighted EV.
  * src/main.py boot (consumer) must REFUSE to arm (real_order_submit_enabled OR canary)
    unless that artifact exists, matches HEAD, and proves a positive capital-weighted edge
    with coverage licensed.

The pure verifier ``verify_edli_arm_gate_artifact`` is the seam both can be tested against
without booting the daemon or touching git.
"""
from __future__ import annotations

import pytest

from src.events.live_profit_audit import (
    ARM_GATE_ARTIFACT_SCHEMA,
    verify_edli_arm_gate_artifact,
)

HEAD = "a" * 40


def _good_artifact(**overrides) -> dict:
    art = {
        "schema": ARM_GATE_ARTIFACT_SCHEMA,
        "commit_sha": HEAD,
        "measurement_cmd_hash": "deadbeef" * 8,
        "capital_weighted_ev": 0.012,
        "gate_pass_n": 40,
        "per_city_n": {"shanghai": 6, "singapore": 7},
        "ev_sigma": 2.1,
        "date_coverage": ["2026-06-01", "2026-06-02"],
        "coverage_licensed": True,
    }
    art.update(overrides)
    return art


def test_good_artifact_verifies_ok():
    v = verify_edli_arm_gate_artifact(_good_artifact(), head_sha=HEAD)
    assert v.ok is True, v.reason


def test_missing_artifact_is_none_denies():
    v = verify_edli_arm_gate_artifact(None, head_sha=HEAD)
    assert v.ok is False
    assert "ARM_GATE_ARTIFACT" in v.reason


def test_sha_mismatch_denies():
    v = verify_edli_arm_gate_artifact(_good_artifact(commit_sha="b" * 40), head_sha=HEAD)
    assert v.ok is False
    assert "SHA" in v.reason or "COMMIT" in v.reason


def test_nonpositive_capital_weighted_ev_denies():
    for ev in (0.0, -0.01):
        v = verify_edli_arm_gate_artifact(_good_artifact(capital_weighted_ev=ev), head_sha=HEAD)
        assert v.ok is False, ev
        assert "EV" in v.reason


def test_coverage_not_licensed_denies():
    for cov in (False, None, "true", 1):
        v = verify_edli_arm_gate_artifact(_good_artifact(coverage_licensed=cov), head_sha=HEAD)
        assert v.ok is False, cov
        assert "COVERAGE" in v.reason


def test_wrong_schema_denies():
    v = verify_edli_arm_gate_artifact(_good_artifact(schema="something_else"), head_sha=HEAD)
    assert v.ok is False
    assert "SCHEMA" in v.reason


def test_missing_required_field_denies():
    # Each load-bearing field, dropped one at a time, must fail closed.
    for field in (
        "commit_sha",
        "measurement_cmd_hash",
        "capital_weighted_ev",
        "gate_pass_n",
        "per_city_n",
        "ev_sigma",
        "date_coverage",
        "coverage_licensed",
    ):
        art = _good_artifact()
        del art[field]
        v = verify_edli_arm_gate_artifact(art, head_sha=HEAD)
        assert v.ok is False, field
