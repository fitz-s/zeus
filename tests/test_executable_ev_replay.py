# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: may4math ExecutableEVReplay research module
# Lifecycle: created=2026-05-04; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: Protect read-only executable EV replay math and divergence detection.
# Reuse: Confirm replay remains pure and does not claim promotion-grade economics without decision-time truth.
"""Executable EV replay verifier tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.backtest.decision_time_truth import (
    AvailabilityProvenance,
    DecisionTimeTruth,
    HindsightLeakageRefused,
)
from src.backtest.executable_ev_replay import (
    ExecutableEVReplayInput,
    compute_executable_ev_replay,
    verify_recorded_decision,
)
from src.backtest.purpose import BacktestPurpose
from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError


def _truth(provenance: AvailabilityProvenance = AvailabilityProvenance.RECORDED) -> DecisionTimeTruth:
    return DecisionTimeTruth(
        snapshot_id="snap-1",
        available_at=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
        provenance=provenance,
    )


def _price(value: float = 0.52) -> ExecutionPrice:
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


def _input(**overrides: object) -> ExecutableEVReplayInput:
    values = {
        "city": "Tokyo",
        "target_date": "2026-05-05",
        "snapshot_id": "snap-1",
        "bin_label": "75+",
        "p_cal": 0.70,
        "p_market": 0.50,
        "alpha_fusion": 0.80,
        "entry_price": _price(),
        "bankroll_usd": 100.0,
        "kelly_multiplier": 0.25,
        "decision_time_truth": _truth(),
        "min_order_usd": 1.0,
    }
    values.update(overrides)
    return ExecutableEVReplayInput(**values)  # type: ignore[arg-type]


def test_compute_executable_ev_uses_fee_adjusted_price_and_kelly() -> None:
    result = compute_executable_ev_replay(_input())

    assert result.p_posterior == pytest.approx(0.66)
    assert result.edge_per_share == pytest.approx(0.14)
    assert result.kelly_size_usd > 0.0
    assert result.executable_size_usd >= 1.0
    assert result.passes_executable_ev is True


def test_replay_refuses_implied_probability_at_kelly_boundary() -> None:
    unsafe = ExecutionPrice(
        value=0.52,
        price_type="implied_probability",
        fee_deducted=True,
        currency="probability_units",
    )

    with pytest.raises(ExecutionPriceContractError):
        compute_executable_ev_replay(_input(entry_price=unsafe))


def test_economics_purpose_refuses_reconstructed_truth() -> None:
    with pytest.raises(HindsightLeakageRefused):
        compute_executable_ev_replay(
            _input(
                purpose=BacktestPurpose.ECONOMICS,
                decision_time_truth=_truth(AvailabilityProvenance.RECONSTRUCTED),
            )
        )


def test_slippage_can_remove_executable_edge() -> None:
    result = compute_executable_ev_replay(_input(slippage_per_share=0.20))

    assert result.passes_executable_ev is False
    assert result.why_no_trade == "NO_POSITIVE_EXECUTABLE_EDGE"
    assert result.executable_size_usd == 0.0


def test_verify_recorded_decision_detects_gate_divergence() -> None:
    computed = compute_executable_ev_replay(_input())
    verdict = verify_recorded_decision(
        computed,
        {"should_trade": False, "size_usd": 0.0, "edge": computed.edge_per_share},
    )

    assert verdict.verdict == "DIVERGENCE"
    assert verdict.severity == "CRITICAL"
    assert any("edge_gate" in reason for reason in verdict.divergence_reasons)
