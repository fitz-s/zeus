# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL replay redesign PR H — promotion/learning authority gates.
#   A SKILL/AUDIT_REPLAY result can never enter learning; learning requires a ForecastObject
#   identity + a learning_eligible SettlementResolution; trade_decisions (legacy_archived)
#   can never be an authority source.
"""Tests for the replay promotion/learning authority gates."""

from __future__ import annotations

import pytest

from src.backtest.purpose import (
    BacktestPurpose,
    AUDIT_REPLAY_CONTRACT,
    ECONOMICS_CONTRACT,
    LearningAuthorityViolation,
    PurposeContract,
    SKILL_CONTRACT,
    SKILL_PARITY,
    assert_not_legacy_authority,
    assert_learning_grade,
)
from src.contracts.calibration_bins import F_CANONICAL_GRID
from src.contracts.settlement_outcome import SettlementOutcome
from src.contracts.settlement_resolution import SettlementResolution


def _settlement(*, promotion_eligible: bool):
    interior = next(
        b for b in F_CANONICAL_GRID.as_bins() if not b.is_shoulder and b.low is not None
    )
    value = (interior.low + interior.high) / 2.0
    state = (
        SettlementOutcome.PHYSICALLY_CONFIRMED
        if promotion_eligible
        else SettlementOutcome.UMA_UNKNOWN_50_50
    )
    return SettlementResolution.from_settlement_row(
        {
            "city": "nyc",
            "target_date": "2026-05-20",
            "temperature_metric": "high",
            "settlement_value": value,
            "settlement_unit": "F",
        },
        F_CANONICAL_GRID,
        outcome_state=state,
    )


# ── structural antibody: SKILL/AUDIT_REPLAY cannot hold learning_authority=True ──


def test_skill_contract_cannot_be_learning_authority():
    with pytest.raises(LearningAuthorityViolation):
        PurposeContract(
            purpose=BacktestPurpose.SKILL,
            permitted_outputs=frozenset(),
            parity=SKILL_PARITY,
            learning_authority=True,
        )


def test_audit_replay_contract_cannot_be_learning_authority():
    with pytest.raises(LearningAuthorityViolation):
        PurposeContract(
            purpose=BacktestPurpose.AUDIT_REPLAY,
            permitted_outputs=frozenset(),
            parity=SKILL_PARITY,
            learning_authority=True,
        )


def test_canonical_contracts_still_construct():
    assert SKILL_CONTRACT.learning_authority is False
    assert AUDIT_REPLAY_CONTRACT.learning_authority is False
    assert ECONOMICS_CONTRACT.learning_authority is True


# ── trade_decisions can never be an authority source ──


def test_trade_decisions_refused_as_authority():
    with pytest.raises(LearningAuthorityViolation, match="legacy_archived"):
        assert_not_legacy_authority("trade_decisions")


def test_canonical_authority_table_allowed():
    # position_events is canonical entry truth — not refused.
    assert_not_legacy_authority("position_events")  # no raise
    assert_not_legacy_authority("settlement_outcomes")


# ── assert_learning_grade ──


def test_learning_grade_passes_with_full_identity():
    res = _settlement(promotion_eligible=True)
    assert_learning_grade(
        ECONOMICS_CONTRACT, forecast_object=object(), settlement_resolution=res
    )  # no raise


def test_skill_result_cannot_promote():
    res = _settlement(promotion_eligible=True)
    with pytest.raises(LearningAuthorityViolation, match="ECONOMICS"):
        assert_learning_grade(
            SKILL_CONTRACT, forecast_object=object(), settlement_resolution=res
        )


def test_promotion_requires_forecast_object():
    res = _settlement(promotion_eligible=True)
    with pytest.raises(LearningAuthorityViolation, match="ForecastObject"):
        assert_learning_grade(
            ECONOMICS_CONTRACT, forecast_object=None, settlement_resolution=res
        )


def test_promotion_requires_settlement_resolution():
    with pytest.raises(LearningAuthorityViolation, match="SettlementResolution"):
        assert_learning_grade(
            ECONOMICS_CONTRACT, forecast_object=object(), settlement_resolution=None
        )


def test_exceptional_settlement_cannot_promote():
    res = _settlement(promotion_eligible=False)  # UMA_UNKNOWN_50_50
    assert res.learning_eligible is False
    with pytest.raises(LearningAuthorityViolation, match="not learning_eligible"):
        assert_learning_grade(
            ECONOMICS_CONTRACT, forecast_object=object(), settlement_resolution=res
        )
