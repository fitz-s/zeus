# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md
"""Relationship antibodies for backtest purpose contracts (S1).

Locks the four structural decisions D1-D4 in the upgrade design:
- D1: BacktestPurpose typed; outputs disjoint per purpose.
- D3: Sizing/Selection sentinels; ECONOMICS requires full parity.
- D4: AvailabilityProvenance typed; ECONOMICS rejects non-promotion-grade.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.decision_time_truth import (
    AvailabilityProvenance,
    DecisionTimeTruth,
    HindsightLeakageRefused,
    ecmwf_ens_available_at,
    gate_for_purpose,
)
from src.backtest.purpose import (
    BacktestPurpose,
    DIAGNOSTIC_CONTRACT,
    DIAGNOSTIC_FIELDS,
    ECONOMICS_CONTRACT,
    ECONOMICS_FIELDS,
    Selection,
    SKILL_CONTRACT,
    SKILL_FIELDS,
    Sizing,
)


def test_purpose_outputs_pairwise_disjoint():
    assert SKILL_FIELDS.isdisjoint(ECONOMICS_FIELDS)
    assert SKILL_FIELDS.isdisjoint(DIAGNOSTIC_FIELDS)
    assert ECONOMICS_FIELDS.isdisjoint(DIAGNOSTIC_FIELDS)


def test_promotion_authority_only_economics():
    assert SKILL_CONTRACT.promotion_authority is False
    assert DIAGNOSTIC_CONTRACT.promotion_authority is False
    assert ECONOMICS_CONTRACT.promotion_authority is True


def test_purpose_contracts_frozen():
    with pytest.raises(Exception):
        SKILL_CONTRACT.promotion_authority = True


def test_economics_parity_requires_full_linkage_kelly_fdr():
    assert ECONOMICS_CONTRACT.parity.market_price_linkage == "full"
    assert ECONOMICS_CONTRACT.parity.sizing is Sizing.KELLY_BOOTSTRAP
    assert ECONOMICS_CONTRACT.parity.selection is Selection.BH_FDR


def test_skill_parity_no_sizing_no_selection():
    assert SKILL_CONTRACT.parity.sizing is Sizing.NONE
    assert SKILL_CONTRACT.parity.selection is Selection.NONE
    assert SKILL_CONTRACT.parity.market_price_linkage == "none"


def test_diagnostic_uses_flat_diagnostic_sizing_marker():
    assert DIAGNOSTIC_CONTRACT.parity.sizing is Sizing.FLAT_DIAGNOSTIC
    assert DIAGNOSTIC_CONTRACT.parity.selection is Selection.NONE


def test_ecmwf_ens_dissemination_day0_is_base_plus_6h40m():
    base = datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc)
    assert ecmwf_ens_available_at(base, 0) == base + timedelta(hours=6, minutes=40)


def test_ecmwf_ens_dissemination_day15_is_base_plus_7h40m():
    base = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    assert ecmwf_ens_available_at(base, 15) == base + timedelta(hours=7, minutes=40)


def test_ecmwf_ens_negative_lead_day_rejected():
    with pytest.raises(ValueError):
        ecmwf_ens_available_at(datetime(2026, 4, 27, tzinfo=timezone.utc), -1)


def test_decision_time_truth_promotion_grade_for_recorded_and_fetch_time():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    for prov in (AvailabilityProvenance.FETCH_TIME, AvailabilityProvenance.RECORDED):
        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
        assert truth.is_promotion_grade()
        assert not truth.is_diagnostic_only()


def test_decision_time_truth_diagnostic_only_for_derived_and_reconstructed():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    for prov in (
        AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
        AvailabilityProvenance.RECONSTRUCTED,
    ):
        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
        assert truth.is_diagnostic_only()
        assert not truth.is_promotion_grade()


def test_economics_purpose_rejects_reconstructed():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    truth = DecisionTimeTruth(
        snapshot_id="s1",
        available_at=base,
        provenance=AvailabilityProvenance.RECONSTRUCTED,
    )
    with pytest.raises(HindsightLeakageRefused):
        gate_for_purpose(truth, BacktestPurpose.ECONOMICS)


def test_economics_purpose_rejects_derived_dissemination():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    truth = DecisionTimeTruth(
        snapshot_id="s1",
        available_at=base,
        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
    )
    with pytest.raises(HindsightLeakageRefused):
        gate_for_purpose(truth, BacktestPurpose.ECONOMICS)


def test_economics_purpose_accepts_recorded_and_fetch_time():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    for prov in (AvailabilityProvenance.FETCH_TIME, AvailabilityProvenance.RECORDED):
        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
        assert gate_for_purpose(truth, BacktestPurpose.ECONOMICS) is truth


def test_skill_purpose_rejects_reconstructed():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    truth = DecisionTimeTruth(
        snapshot_id="s1",
        available_at=base,
        provenance=AvailabilityProvenance.RECONSTRUCTED,
    )
    with pytest.raises(HindsightLeakageRefused):
        gate_for_purpose(truth, BacktestPurpose.SKILL)


def test_skill_purpose_accepts_derived_dissemination():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    truth = DecisionTimeTruth(
        snapshot_id="s1",
        available_at=base,
        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
    )
    assert gate_for_purpose(truth, BacktestPurpose.SKILL) is truth


def test_diagnostic_purpose_accepts_all_provenance_tiers():
    base = datetime(2026, 4, 27, tzinfo=timezone.utc)
    for prov in AvailabilityProvenance:
        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
        assert gate_for_purpose(truth, BacktestPurpose.DIAGNOSTIC) is truth
