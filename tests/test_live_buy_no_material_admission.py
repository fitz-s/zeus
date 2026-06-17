# Created: 2026-06-07
# Last reused/audited: 2026-06-17
# Authority basis: PR_SPEC.md §2 FIX-4 (close the buy_no escape hatch; allow-list ⊆ carrier
#   vocab) plus operator directive 2026-06-17: near-settled entry prices are not
#   exploitable Day0 opportunities and must not enter live entry evaluation.
"""Live admission antibodies for material-YES buy_no and near-settled prices.

The escape hatch: a material-YES-bin buy_no was ADMITTED without an allowed native
NO LCB source whenever ``conservative_edge > confidence_gap`` — a self-referential
test on the SAME un-provenanced q_lcb. FIX-4 deletes that waiver: material-YES buy_no
requires an allowed native NO source unconditionally. The allow-list must also be a
subset of the q_lcb carrier vocabulary (CALIBRATION_SOURCES); YES_UCB_DERIVED is
removed because it is not a CalibrationSource.
"""
from __future__ import annotations

from src.calibration.qlcb_provenance import CALIBRATION_SOURCES
from src.strategy.live_inference.live_admission import (
    LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
    live_buy_no_conservative_evidence_rejection_reason,
    live_near_settled_entry_price_rejection_reason,
)


def test_material_yes_buy_no_without_allowed_source_is_rejected_even_with_positive_edge() -> None:
    """The deleted waiver would have admitted this (conservative_edge > confidence_gap);
    FIX-4 requires an allowed native NO source unconditionally, so it is rejected."""

    # conservative_edge = q_lcb - price = 0.90 - 0.10 = 0.80
    # confidence_gap   = q_direction - q_lcb = 0.92 - 0.90 = 0.02
    # 0.80 > 0.02 -> the old waiver returned None (ADMIT). FIX-4 must reject.
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",  # NOT in the allow-list
        same_bin_yes_posterior=0.40,  # material YES mass (>= 0.20 floor)
    )

    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")


def test_material_yes_buy_no_with_allowed_native_no_source_is_admitted() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="EMOS_ANALYTIC",  # allowed native NO source
        same_bin_yes_posterior=0.40,
    )

    assert reason is None


def test_immaterial_yes_buy_no_is_not_gated_by_source() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.05,  # below the 0.20 material floor
    )

    assert reason is None


def test_allow_list_is_subset_of_calibration_sources() -> None:
    """Invariant: every allowed buy_no LCB source must be a member of the q_lcb
    carrier vocabulary. A source the carrier cannot even express (e.g. the removed
    YES_UCB_DERIVED) can never be honestly provenanced through QlcbByDirection."""

    assert LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES <= CALIBRATION_SOURCES
    assert "YES_UCB_DERIVED" not in LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES


def test_near_settled_entry_price_rejects_999() -> None:
    reason = live_near_settled_entry_price_rejection_reason(execution_price=0.999)

    assert reason is not None
    assert reason.startswith("ADMISSION_NEAR_SETTLED_PRICE:")
    assert "price=0.999000" in reason


def test_near_settled_entry_price_boundary_is_rejected() -> None:
    reason = live_near_settled_entry_price_rejection_reason(execution_price=0.99)

    assert reason is not None
    assert reason.startswith("ADMISSION_NEAR_SETTLED_PRICE:")


def test_entry_price_below_near_settled_ceiling_stays_admissible_to_other_gates() -> None:
    assert live_near_settled_entry_price_rejection_reason(execution_price=0.989) is None


def test_missing_entry_price_is_not_near_settled() -> None:
    assert live_near_settled_entry_price_rejection_reason(execution_price=None) is None
