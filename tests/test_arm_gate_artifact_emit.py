# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: STRUCTURAL_FIX_PLAN_2026-06-03 §P0.3 (D2 — ARM artifact
#   producer/consumer gap; H3) + line 104: live boot REQUIRES
#   state/edli_arm_gate_artifact.json with {schema, commit_sha,
#   measurement_cmd_hash, capital_weighted_ev>0, gate_pass_n, per_city_n,
#   ev_sigma, date_coverage, coverage_licensed:true}; missing / SHA-mismatch /
#   ev<=0 / coverage_licensed:false → RuntimeError at boot.
#
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: Relationship test across the PRODUCER (measure_arm_gate_settlement
#   --emit-artifact) and the CONSUMER contract (PR-2's D2 boot gate). The gap
#   this closes: a consumer existed (boot gate reads the artifact) but NO
#   producer wrote it, so arming was structurally impossible. These tests prove:
#     (a) emitting on the CURRENT DENIED cohort yields an artifact the consumer
#         REJECTS (capital_weighted_ev<=0 AND coverage_licensed:false) — arming
#         stays correctly blocked, NOT papered over;
#     (b) a hand-constructed all-pass artifact is ACCEPTED — so the end-to-end
#         path is provably FUNCTIONAL, just correctly gated;
#     (c) the producer NEVER manufactures an ARM_ELIGIBLE artifact on DENIED data.
#
#   ANTI-FABRICATION: the consumer contract below is the SAME 9-field rule PR-2's
#   boot gate enforces (STRUCTURAL_FIX_PLAN line 104). If PR-2 lands a real
#   validator module, repoint _consumer_accepts at it; the assertions are the
#   contract, not the implementation.
"""Relationship tests: ARM-gate artifact producer ⟷ boot-gate consumer contract."""
from __future__ import annotations

import json

import pytest

from scripts.measure_arm_gate_settlement import (
    ARM_ARTIFACT_REQUIRED_FIELDS,
    ARM_ARTIFACT_SCHEMA,
    CapitalWeightedArmVerdict,
    build_arm_artifact,
    emit_arm_artifact,
)


# ---------------------------------------------------------------------------
# CONSUMER CONTRACT — PR-2's D2 boot gate, encoded as the rejection rule it
# enforces (STRUCTURAL_FIX_PLAN line 104). This IS the contract the producer
# must satisfy; it is intentionally strict and fail-closed.
# ---------------------------------------------------------------------------
def _consumer_accepts(artifact: dict, *, running_commit_sha: str) -> tuple[bool, str]:
    """Return (accepted, reason) per the D2 boot-gate contract.

    REJECTS unless ALL hold:
      - every required field present
      - schema matches
      - commit_sha matches the running checkout (no stale artifact)
      - capital_weighted_ev > 0   (the cohort makes money once sized)
      - coverage_licensed is True (settlement-calibrated coverage license held)
    """
    missing = ARM_ARTIFACT_REQUIRED_FIELDS - set(artifact)
    if missing:
        return False, f"REJECT: missing fields {sorted(missing)}"
    if artifact["schema"] != ARM_ARTIFACT_SCHEMA:
        return False, f"REJECT: schema {artifact['schema']!r} != {ARM_ARTIFACT_SCHEMA!r}"
    if artifact["commit_sha"] != running_commit_sha:
        return False, "REJECT: commit_sha mismatch (stale artifact)"
    if not (artifact["capital_weighted_ev"] > 0.0):
        return False, f"REJECT: capital_weighted_ev={artifact['capital_weighted_ev']} <= 0"
    if not artifact["coverage_licensed"]:
        return False, "REJECT: coverage_licensed is false"
    return True, "ACCEPT"


def _denied_verdict() -> CapitalWeightedArmVerdict:
    """The current-data shape: empty gate-PASS cohort → zero verdict (DENIED)."""
    return CapitalWeightedArmVerdict(
        equal_row_win_rate=0.0,
        equal_row_ev_sigma=0.0,
        capital_weighted_roi=0.0,
        capital_weighted_ev_sigma=0.0,
        per_city_cw_roi={},
        n=0,
        per_city_n={},
    )


# ---------------------------------------------------------------------------
# (a) DENIED data → artifact the consumer REJECTS (arming stays blocked)
# ---------------------------------------------------------------------------
def test_denied_cohort_emits_blocking_artifact(tmp_path):
    """Emitting on the current DENIED cohort must produce a BLOCKING artifact:
    capital_weighted_ev<=0 AND coverage_licensed:false → consumer REJECTS."""
    verdict = _denied_verdict()
    artifact = build_arm_artifact(verdict, [], argv=[], coverage_licensed=False)

    # All 9 required fields are present (a missing key would be a producer bug).
    assert ARM_ARTIFACT_REQUIRED_FIELDS <= set(artifact)

    # The honest DENIED signals:
    assert artifact["capital_weighted_ev"] <= 0.0
    assert artifact["coverage_licensed"] is False

    # Round-trips through disk (the real producer writes a file).
    path = str(tmp_path / "edli_arm_gate_artifact.json")
    emit_arm_artifact(path, artifact)
    on_disk = json.loads(open(path, encoding="utf-8").read())

    accepted, reason = _consumer_accepts(on_disk, running_commit_sha=on_disk["commit_sha"])
    assert accepted is False, f"DENIED artifact was wrongly accepted: {reason}"
    assert "REJECT" in reason


def test_producer_never_emits_eligible_on_denied_data(tmp_path):
    """The producer must NEVER manufacture an arming license the measurement did
    not earn: on DENIED data, coverage_licensed cannot be flipped True by the
    producer, and ev stays <=0. (coverage_licensed is hardcoded False until a
    K3 license exists — this guards that no DENIED path emits an eligible one.)"""
    verdict = _denied_verdict()
    artifact = build_arm_artifact(verdict, [], argv=[], coverage_licensed=False)
    # Even if a caller mistakenly believed it was licensed, the DENIED ev blocks.
    accepted, _ = _consumer_accepts(artifact, running_commit_sha=artifact["commit_sha"])
    assert accepted is False


# ---------------------------------------------------------------------------
# (b) hand-constructed ALL-PASS artifact → consumer ACCEPTS (path is functional)
# ---------------------------------------------------------------------------
def test_handbuilt_all_pass_artifact_is_accepted():
    """Prove the end-to-end gate path is FUNCTIONAL, not permanently jammed: a
    fully-passing artifact (positive ev, licensed coverage, matching SHA) is
    ACCEPTED. This is the positive control — the gate rejects DENIED data on the
    MERITS, not because nothing can ever pass."""
    running_sha = "deadbeefcafef00d" * 2  # any concrete 40-ish char sha stand-in
    all_pass = {
        "schema": ARM_ARTIFACT_SCHEMA,
        "commit_sha": running_sha,
        "measurement_cmd_hash": "f" * 64,
        "capital_weighted_ev": 0.12,        # > 0 → makes money once sized
        "gate_pass_n": 40,
        "per_city_n": {"Tokyo": 8, "Seoul": 8, "Paris": 8, "NYC": 8, "Warsaw": 8},
        "ev_sigma": 2.7,
        "date_coverage": {"n_pairs": 25, "pairs": []},
        "coverage_licensed": True,          # K3 license held
    }
    accepted, reason = _consumer_accepts(all_pass, running_commit_sha=running_sha)
    assert accepted is True, f"all-pass artifact rejected: {reason}"


def test_all_pass_rejected_on_sha_mismatch():
    """Even an all-pass artifact is rejected when its commit_sha does not match
    the running checkout — no stale artifact can arm a different code version."""
    all_pass = {
        "schema": ARM_ARTIFACT_SCHEMA,
        "commit_sha": "a" * 40,
        "measurement_cmd_hash": "f" * 64,
        "capital_weighted_ev": 0.12,
        "gate_pass_n": 40,
        "per_city_n": {"Tokyo": 8},
        "ev_sigma": 2.7,
        "date_coverage": {"n_pairs": 25, "pairs": []},
        "coverage_licensed": True,
    }
    accepted, reason = _consumer_accepts(all_pass, running_commit_sha="b" * 40)
    assert accepted is False
    assert "commit_sha" in reason


def test_all_pass_rejected_when_field_missing():
    """A producer that drops a required field must be caught: the consumer
    rejects on any missing key (fail-closed against an under-populated artifact)."""
    base = {
        "schema": ARM_ARTIFACT_SCHEMA,
        "commit_sha": "c" * 40,
        "measurement_cmd_hash": "f" * 64,
        "capital_weighted_ev": 0.12,
        "gate_pass_n": 40,
        "per_city_n": {"Tokyo": 8},
        "ev_sigma": 2.7,
        "date_coverage": {"n_pairs": 25, "pairs": []},
        "coverage_licensed": True,
    }
    for drop in sorted(ARM_ARTIFACT_REQUIRED_FIELDS):
        partial = {k: v for k, v in base.items() if k != drop}
        accepted, reason = _consumer_accepts(partial, running_commit_sha="c" * 40)
        assert accepted is False, f"missing {drop!r} was accepted: {reason}"


# ---------------------------------------------------------------------------
# Producer field-completeness: build_arm_artifact fills EXACTLY the contract.
# ---------------------------------------------------------------------------
def test_build_artifact_has_exactly_required_fields():
    artifact = build_arm_artifact(_denied_verdict(), [], argv=["--emit-artifact", "x"])
    assert set(artifact) == ARM_ARTIFACT_REQUIRED_FIELDS, (
        f"producer field-set drift: extra={set(artifact)-ARM_ARTIFACT_REQUIRED_FIELDS} "
        f"missing={ARM_ARTIFACT_REQUIRED_FIELDS-set(artifact)}"
    )


def test_measurement_cmd_hash_is_argset_sensitive():
    """Different arg-sets produce different measurement_cmd_hash (the boot gate
    re-derives this; drift must change it)."""
    a = build_arm_artifact(_denied_verdict(), [], argv=["--emit-artifact", "p1"])
    b = build_arm_artifact(_denied_verdict(), [], argv=["--emit-artifact", "p2", "--x"])
    assert a["measurement_cmd_hash"] != b["measurement_cmd_hash"]
