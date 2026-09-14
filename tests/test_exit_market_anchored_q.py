# Created: 2026-09-04
# Last reused or audited: 2026-09-14
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   (market-anchored calibrator, item 9) + this task's fix — the exit stop was
#   comparing against the RAW posterior-predictive point (measured +0.170
#   over-biased on filled positions), while entries already act on the
#   market-anchored CORRECTED probability. src/state/portfolio.py
#   Position._exit_q_mean_and_source / Position.evaluate_exit;
#   src/calibration/market_anchored_live_fit.py register_active_provider /
#   get_active_provider / corrected_probability.
"""The immediate stop shares the global TAKER SELL bid calibration anchor.
Missing live entry proof fails closed; an absent provider is offline-only
compatibility. Evaluating the stop never opens a DB connection of its own.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.calibration.market_anchored_live_fit import (
    corrected_probability,
    get_active_provider,
    register_active_provider,
)
from src.calibration.market_anchored_residual import LEAD_BUCKETS, ResidualCalibratorArtifact
from src.state.portfolio import ExitContext, Position


@pytest.fixture(autouse=True)
def _clear_active_provider():
    """The active provider is process-global state; never let one test's
    registration leak into another."""
    assert get_active_provider() is None, "a prior test left a provider registered"
    yield
    register_active_provider(None)


def _stub_artifact(*, alpha_day0: float = 0.09, beta: float = 0.0) -> ResidualCalibratorArtifact:
    return ResidualCalibratorArtifact(
        alpha={"day0": alpha_day0, "day1": 0.0, "day2": 0.0},
        beta=beta,
        lambda_=1.0,
        clip_d=3.0,
        p_clip=(0.005, 0.995),
        lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-09-04T00:00:00Z",
        n_train=100,
        n_excluded=0,
        excluded_reasons={},
        param_hash="stub",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("Warsaw", "Europe/Warsaw"),),
    )


class _StubBinding:
    family_key = "Warsaw|2026-09-04|high"
    bin_id = "20-21C"

    def __init__(self, artifact: ResidualCalibratorArtifact) -> None:
        self._artifact = artifact

    def corrected_probability(self, **kwargs):
        applied = corrected_probability(
            self._artifact,
            p0=kwargs["p0"], q_raw=kwargs["raw_q"], city=kwargs["city"],
            target_date=kwargs["target_date"], decision_at=kwargs["decision_at"],
            side=kwargs["side"],
        )
        assert applied is not None
        return type("Correction", (), {"corrected_q": applied[0]})()


class _StubProvider:
    """A pre-bound ENTRY reader that cannot fit or open a connection."""

    def __init__(self, artifact: ResidualCalibratorArtifact | None) -> None:
        self._binding = None if artifact is None else _StubBinding(artifact)

    def load(self, **_kwargs):
        if self._binding is None:
            raise RuntimeError("entry proof unavailable")
        return self._binding


def _held_position(direction: str = "buy_yes", *, target_date: str = "2026-09-04") -> Position:
    return Position(
        trade_id="pos-market-anchored-exit",
        market_id="mkt-market-anchored-exit",
        city="Warsaw",
        cluster="europe",
        target_date=target_date,
        bin_label="20-21C",
        direction=direction,
        entry_price=0.30,
        size_usd=20.0,
        shares=40.0,
        cost_basis_usd=20.0,
        p_posterior=0.50,
        token_id="yes-token",
        no_token_id="no-token",
    )


def _exit_context(
    *,
    fresh_prob: float,
    current_market_price: float,
    best_bid: float,
    best_ask: float | None = None,
) -> ExitContext:
    return ExitContext(
        exit_reason="",
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        best_ask=best_ask if best_ask is not None else best_bid + 0.02,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        day0_active=False,
        whale_toxicity=False,
        divergence_score=0.0,
        market_velocity_1h=0.0,
        current_ci=(0.05, 0.95),
        belief_available=True,
    )


# --- (a) buy_yes: market-anchored correction replaces the raw point ---------


def test_buy_yes_exit_uses_market_anchored_corrected_q():
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=0.09)))
    # decision_date == target_date (both "today" UTC) => lead_bucket day0.
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 1.0 / (1.0 + math.exp(-(math.log(0.15 / 0.85) + 0.09)))
    assert float(q_mean) == pytest.approx(expected, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:market_anchored" in decision.applied_validations
    assert "exit_q:raw" not in decision.applied_validations


# --- (b) buy_no: complement law applied --------------------------------------


def test_buy_no_exit_applies_the_complement_law():
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=0.09)))
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_no", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 1.0 - 1.0 / (1.0 + math.exp(-(math.log(0.85 / 0.15) + 0.09)))
    assert float(q_mean) == pytest.approx(expected, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:market_anchored" in decision.applied_validations


# --- (c) provider unavailable -> fail open to the raw point ------------------


def test_no_provider_registered_falls_back_to_raw_q():
    assert get_active_provider() is None
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "raw"
    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:raw" in decision.applied_validations
    assert "exit_q:market_anchored" not in decision.applied_validations


def test_missing_entry_proof_makes_statistical_exit_evidence_unavailable():
    register_active_provider(_StubProvider(None))
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"
    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)


def test_live_reader_unavailable_never_reverts_to_raw_statistical_q():
    from src.calibration.market_anchored_live_fit import UnavailableHeldEntryCalibrationProvider

    register_active_provider(UnavailableHeldEntryCalibrationProvider())
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)
    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"


# --- (d) evaluate_exit never opens its own DB connection ---------------------


def test_evaluate_exit_never_opens_its_own_db_connection(monkeypatch):
    register_active_provider(_StubProvider(_stub_artifact()))

    def _explode(*_args, **_kwargs):
        raise AssertionError("evaluate_exit must never open its own DB connection")

    monkeypatch.setattr(sqlite3, "connect", _explode)

    pos = _held_position(
        direction="buy_yes", target_date=datetime.now(timezone.utc).date().isoformat()
    )
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    # Must not raise: only the immutable ENTRY binding is consulted.
    decision = pos.evaluate_exit(ctx)
    assert decision.trigger in {"HOLD", "SELL_REVERSAL", "EVIDENCE_UNAVAILABLE"}
    applied = set(decision.applied_validations)
    assert "exit_q:market_anchored" in applied


@pytest.mark.parametrize("direction,alpha", [("buy_yes", -0.30), ("buy_no", 0.30)])
@pytest.mark.parametrize("ask,market_price", [(0.62, 0.61), (0.95, 0.775)])
def test_spread_cannot_hide_immediate_sell_reversal(direction, alpha, ask, market_price):
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=alpha)))
    pos = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(
        fresh_prob=0.70, current_market_price=market_price, best_bid=0.60, best_ask=ask,
    )
    ctx = replace(ctx, bid_ladder=((0.60, 40.0),))

    q, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    # Same gross bid and held-side residual as the canonical TAKER SELL.
    expected_q = 1.0 / (1.0 + math.exp(-(math.log(0.60 / 0.40) - 0.30)))
    assert evidence_ok and source == "market_anchored"
    assert float(q) == pytest.approx(expected_q)
    assert pos.evaluate_exit(ctx).trigger == "SELL_REVERSAL"


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("bid", [None, float("nan"), float("inf"), -0.1, 0.0, 1.0, 1.1])
def test_live_sell_anchor_never_falls_back_to_midpoint(direction, bid):
    register_active_provider(_StubProvider(_stub_artifact()))
    pos = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(fresh_prob=0.5, current_market_price=0.6, best_bid=0.5, best_ask=0.7)
    ctx = replace(ctx, best_bid=bid)

    _, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"
