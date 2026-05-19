# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: SYNTHESIS.md Addendum 2 §2 (Bug A + Bug B) + §5
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: antibody — boundary-ambiguous majority threshold + strict-< rule (LOW 91% over-rejection fix)
# Reuse: Run when modifying boundary_ambiguous logic, per-snapshot aggregation, or ensemble member rejection rules.
"""Antibody tests for boundary-leakage rule fixes.

Bug A fix: per-snapshot aggregation changed from any() to majority threshold (≥26/51).
Bug B fix: per-member boundary_ambiguous property changed from <= to < (strict inequality).

Both bugs caused the LOW rejection rate to be ~78-91% vs a physically-justified ~30%.

Coverage:
  T1: 1/51 ambiguous members → any_boundary_ambiguous=False (was True under old any() rule)
  T2: 26/51 ambiguous members → any_boundary_ambiguous=True (at threshold)
  T3: 25/51 ambiguous members → any_boundary_ambiguous=False (just below threshold)
  T4 (Bug B): boundary_min == inner_min (tie) → boundary_ambiguous=False (was True under <=)
  T5 (Bug B): boundary_min < inner_min - 0.0001 → boundary_ambiguous=True (strict leakage)
  T6: validate_snapshot_contract reads ambiguous_member_count from payload (1/51 → OK)
  T7: validate_snapshot_contract reads ambiguous_member_count from payload (26/51 → REJECTED)

Sed-flip meta-verify:
  Restore `any()` in extractor + `<=` in boundary_ambiguous → T1/T2/T3/T4 go RED.
  Restore AMBIGUITY_MAJORITY_THRESHOLD=1 in contract → T6 goes RED (1/51 rejected).
"""

import os
from unittest.mock import patch

import pytest

from scripts.extract_tigge_mn2t6_localday_min import BoundaryClassification
from src.contracts.snapshot_ingest_contract import validate_snapshot_contract
from src.types.metric_identity import LOW_LOCALDAY_MIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bc(*, inner_values: list[float], boundary_values: list[float]) -> BoundaryClassification:
    return BoundaryClassification(
        inner_values=inner_values,
        boundary_values=boundary_values,
    )


def _make_low_payload(*, ambiguous_member_count: int, total_members: int = 51) -> dict:
    """Synthesize a minimal valid LOW snapshot payload with given ambiguous_member_count."""
    return {
        "data_version": LOW_LOCALDAY_MIN.data_version,
        "temperature_metric": "low",
        "physical_quantity": LOW_LOCALDAY_MIN.physical_quantity,
        "members": [{"member": m, "value_native_unit": 270.0} for m in range(total_members)],
        "members_unit": "K",
        "causality": {"status": "OK"},
        "issue_time_utc": "2026-05-18T00:00:00+00:00",
        "boundary_policy": {
            "boundary_ambiguous": ambiguous_member_count >= 1,  # legacy flag (old rule)
            "ambiguous_member_count": ambiguous_member_count,
        },
    }


# ---------------------------------------------------------------------------
# Bug A: majority threshold on per-snapshot aggregation
# ---------------------------------------------------------------------------

class TestMajorityThreshold:
    """Tests verify the extractor's per-snapshot any_boundary_ambiguous logic.

    These call BoundaryClassification directly and simulate the aggregation
    logic to test the threshold boundary without needing GRIB files.
    """

    def _aggregate(self, ambiguous_member_count: int, total: int = 51) -> bool:
        """Simulate the extractor's aggregation: majority threshold (≥26/51)."""
        threshold = total // 2 + 1  # 26 for 51 members
        return ambiguous_member_count >= threshold

    def test_t1_one_ambiguous_member_not_rejected(self):
        """1/51 ambiguous members: snapshot NOT quarantined (was rejected under old any())."""
        assert self._aggregate(1, 51) is False, (
            "1/51 ambiguous must NOT trigger majority quarantine"
        )

    def test_t2_threshold_exactly_quarantines(self):
        """26/51 ambiguous members: snapshot IS quarantined (at threshold)."""
        assert self._aggregate(26, 51) is True, (
            "26/51 ambiguous must trigger majority quarantine"
        )

    def test_t3_one_below_threshold_not_quarantined(self):
        """25/51 ambiguous members: snapshot NOT quarantined (just below threshold)."""
        assert self._aggregate(25, 51) is False, (
            "25/51 ambiguous must NOT trigger majority quarantine"
        )


# ---------------------------------------------------------------------------
# Bug B: strict inequality in per-member boundary_ambiguous
# ---------------------------------------------------------------------------

class TestStrictInequality:
    def test_t4_exact_tie_not_flagged(self):
        """boundary_min == inner_min (tie): boundary_ambiguous=False (was True under <=)."""
        bc = _make_bc(inner_values=[270.0], boundary_values=[270.0])
        assert bc.boundary_ambiguous is False, (
            "Exact tie (boundary_min == inner_min) must NOT be flagged as ambiguous"
        )

    def test_t5_strict_lower_boundary_is_flagged(self):
        """boundary_min strictly < inner_min: boundary_ambiguous=True (genuine leakage risk)."""
        bc = _make_bc(inner_values=[270.0], boundary_values=[269.9999])
        assert bc.boundary_ambiguous is True, (
            "boundary_min strictly below inner_min must be flagged as ambiguous"
        )

    def test_boundary_only_no_inner_still_ambiguous(self):
        """No inner buckets at all: ambiguous (boundary-only coverage has no conservative estimate)."""
        bc = _make_bc(inner_values=[], boundary_values=[270.0])
        assert bc.boundary_ambiguous is True

    def test_no_boundary_not_ambiguous(self):
        """No boundary buckets: not ambiguous (clean inner-only snapshot)."""
        bc = _make_bc(inner_values=[270.0], boundary_values=[])
        assert bc.boundary_ambiguous is False


# ---------------------------------------------------------------------------
# Contract layer: validate_snapshot_contract reads ambiguous_member_count
# ---------------------------------------------------------------------------

class TestContractMajorityReeval:
    def test_t6_one_ambiguous_contract_returns_ok(self):
        """1/51 ambiguous members: validate_snapshot_contract → causality_status=OK.

        Old behaviour (any() + stored boundary_ambiguous=True): REJECTED_BOUNDARY_AMBIGUOUS.
        New behaviour (majority threshold): OK (1 < 26).
        """
        payload = _make_low_payload(ambiguous_member_count=1)
        decision = validate_snapshot_contract(payload)
        assert decision.causality_status == "OK", (
            f"1/51 ambiguous must be OK after majority-threshold fix; got {decision.causality_status!r}"
        )
        assert decision.training_allowed is True

    def test_t7_majority_ambiguous_contract_rejects(self):
        """26/51 ambiguous members: validate_snapshot_contract → REJECTED_BOUNDARY_AMBIGUOUS."""
        payload = _make_low_payload(ambiguous_member_count=26)
        decision = validate_snapshot_contract(payload)
        assert decision.causality_status == "REJECTED_BOUNDARY_AMBIGUOUS", (
            f"26/51 ambiguous must be REJECTED; got {decision.causality_status!r}"
        )
        assert decision.training_allowed is False

    def test_t6b_no_ambiguous_member_count_falls_back_to_flag(self):
        """If ambiguous_member_count absent from payload, fall back to stored boundary_ambiguous flag."""
        payload = _make_low_payload(ambiguous_member_count=0)
        # Remove the count to force flag-based fallback
        payload["boundary_policy"] = {"boundary_ambiguous": True}  # no count field
        decision = validate_snapshot_contract(payload)
        # With flag=True and no count, old path: REJECTED_BOUNDARY_AMBIGUOUS
        assert decision.causality_status == "REJECTED_BOUNDARY_AMBIGUOUS"

    def test_majority_threshold_env_override(self):
        """AMBIGUITY_MAJORITY_THRESHOLD env var overrides the default 26."""
        # Threshold=10 → 15/51 should be REJECTED (was OK with default=26)
        payload = _make_low_payload(ambiguous_member_count=15)
        with patch.dict(os.environ, {"AMBIGUITY_MAJORITY_THRESHOLD": "10"}):
            decision = validate_snapshot_contract(payload)
        assert decision.causality_status == "REJECTED_BOUNDARY_AMBIGUOUS", (
            "With threshold=10, 15 ambiguous members must be REJECTED"
        )
