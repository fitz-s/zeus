"""Executable EV replay verification.

Pure, read-only research module for replaying decision-time EV math against a
recorded decision. It does not call the live evaluator and does not write DB or
state; callers provide frozen inputs from their replay corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from src.backtest.decision_time_truth import DecisionTimeTruth, gate_for_purpose
from src.backtest.purpose import BacktestPurpose
from src.contracts.execution_price import ExecutionPrice
from src.strategy.kelly import kelly_size


EVReplayVerdictLabel = Literal["MATCH", "DIVERGENCE", "UNVERIFIABLE"]
EVReplaySeverity = Literal["NONE", "MINOR", "MAJOR", "CRITICAL"]


@dataclass(frozen=True, slots=True)
class ExecutableEVReplayInput:
    city: str
    target_date: str
    snapshot_id: str
    bin_label: str
    p_cal: float
    p_market: float
    alpha_fusion: float
    entry_price: ExecutionPrice
    bankroll_usd: float
    kelly_multiplier: float
    decision_time_truth: DecisionTimeTruth
    purpose: BacktestPurpose = BacktestPurpose.DIAGNOSTIC
    min_order_usd: float = 1.0
    slippage_per_share: float = 0.0

    def __post_init__(self) -> None:
        for name in ("p_cal", "p_market", "alpha_fusion"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        if self.bankroll_usd < 0.0:
            raise ValueError("bankroll_usd must be non-negative")
        if self.kelly_multiplier < 0.0:
            raise ValueError("kelly_multiplier must be non-negative")
        if self.min_order_usd < 0.0:
            raise ValueError("min_order_usd must be non-negative")
        if self.slippage_per_share < 0.0:
            raise ValueError("slippage_per_share must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutableEVReplayResult:
    city: str
    target_date: str
    snapshot_id: str
    bin_label: str
    p_posterior: float
    all_in_price: float
    edge_per_share: float
    kelly_size_usd: float
    executable_size_usd: float
    expected_value_usd: float
    passes_executable_ev: bool
    why_no_trade: str = ""


@dataclass(frozen=True, slots=True)
class ExecutableEVReplayVerdict:
    city: str
    target_date: str
    snapshot_id: str
    verdict: EVReplayVerdictLabel
    severity: EVReplaySeverity
    divergence_reasons: tuple[str, ...]
    computed: ExecutableEVReplayResult
    recorded_should_trade: bool
    recorded_size_usd: float
    recorded_edge: float


def compute_executable_ev_replay(replay_input: ExecutableEVReplayInput) -> ExecutableEVReplayResult:
    """Compute decision-time EV with executable price semantics."""
    gate_for_purpose(replay_input.decision_time_truth, replay_input.purpose)
    replay_input.entry_price.assert_kelly_safe()

    all_in_price = replay_input.entry_price.value + replay_input.slippage_per_share
    p_posterior = (
        replay_input.alpha_fusion * replay_input.p_cal
        + (1.0 - replay_input.alpha_fusion) * replay_input.p_market
    )
    edge_per_share = p_posterior - all_in_price

    if all_in_price <= 0.0 or all_in_price >= 1.0:
        return ExecutableEVReplayResult(
            city=replay_input.city,
            target_date=replay_input.target_date,
            snapshot_id=replay_input.snapshot_id,
            bin_label=replay_input.bin_label,
            p_posterior=p_posterior,
            all_in_price=all_in_price,
            edge_per_share=edge_per_share,
            kelly_size_usd=0.0,
            executable_size_usd=0.0,
            expected_value_usd=0.0,
            passes_executable_ev=False,
            why_no_trade="ALL_IN_PRICE_OUT_OF_RANGE",
        )

    executable_price = ExecutionPrice(
        value=all_in_price,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )
    kelly_usd = kelly_size(
        p_posterior=p_posterior,
        entry_price=executable_price,
        bankroll=replay_input.bankroll_usd,
        kelly_mult=replay_input.kelly_multiplier,
    )
    shares = kelly_usd / all_in_price if all_in_price > 0.0 else 0.0
    expected_value_usd = shares * edge_per_share

    why = ""
    if edge_per_share <= 0.0:
        why = "NO_POSITIVE_EXECUTABLE_EDGE"
    elif kelly_usd < replay_input.min_order_usd:
        why = "BELOW_MIN_ORDER"
    elif expected_value_usd <= 0.0:
        why = "NON_POSITIVE_EV_AFTER_COSTS"

    passes = why == ""
    return ExecutableEVReplayResult(
        city=replay_input.city,
        target_date=replay_input.target_date,
        snapshot_id=replay_input.snapshot_id,
        bin_label=replay_input.bin_label,
        p_posterior=p_posterior,
        all_in_price=all_in_price,
        edge_per_share=edge_per_share,
        kelly_size_usd=kelly_usd,
        executable_size_usd=kelly_usd if passes else 0.0,
        expected_value_usd=expected_value_usd if passes else 0.0,
        passes_executable_ev=passes,
        why_no_trade=why,
    )


def verify_recorded_decision(
    computed: ExecutableEVReplayResult,
    recorded_decision: Mapping[str, object],
    *,
    edge_tolerance: float = 0.001,
    size_tolerance_usd: float = 0.01,
) -> ExecutableEVReplayVerdict:
    """Compare replayed executable EV against a recorded decision dict."""
    recorded_should_trade = bool(recorded_decision.get("should_trade", False))
    recorded_size_usd = float(recorded_decision.get("size_usd", 0.0) or 0.0)
    recorded_edge = float(recorded_decision.get("edge", 0.0) or 0.0)

    reasons: list[str] = []
    if computed.passes_executable_ev != recorded_should_trade:
        reasons.append(
            f"edge_gate computed={computed.passes_executable_ev} recorded={recorded_should_trade}"
        )
    if abs(computed.executable_size_usd - recorded_size_usd) > size_tolerance_usd:
        reasons.append(
            f"size_usd computed={computed.executable_size_usd:.4f} recorded={recorded_size_usd:.4f}"
        )
    if abs(computed.edge_per_share - recorded_edge) > edge_tolerance:
        reasons.append(
            f"edge computed={computed.edge_per_share:.4f} recorded={recorded_edge:.4f}"
        )

    if not reasons:
        verdict: EVReplayVerdictLabel = "MATCH"
        severity: EVReplaySeverity = "NONE"
    else:
        verdict = "DIVERGENCE"
        severity = "CRITICAL" if computed.passes_executable_ev != recorded_should_trade else "MAJOR"

    return ExecutableEVReplayVerdict(
        city=computed.city,
        target_date=computed.target_date,
        snapshot_id=computed.snapshot_id,
        verdict=verdict,
        severity=severity,
        divergence_reasons=tuple(reasons),
        computed=computed,
        recorded_should_trade=recorded_should_trade,
        recorded_size_usd=recorded_size_usd,
        recorded_edge=recorded_edge,
    )
