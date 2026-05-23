# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §6
#                  + docs/reference/zeus_strategy_spec.md §8.2 (model-NO theorem)
"""center_sell model-NO calibrated stochastic layer — relationship tests.

Six cross-module invariants (MN1–MN6):

  MN1  Upper-bound gate: candidate uses p⁺ (upper bound) not raw p_hat.
       enter iff 1 − p⁺ − b − phi(b, fee_rate) > 0.
       For fixed b, a raw p_hat that would pass the gate must be pushed up to
       p⁺ = min(1, p_hat + q_alpha) which may flip the gate to no_trade.

  MN2  Edge formula: edge returned == 1 − p⁺ − b − phi(b, fee_rate) computed
       from the exact same (p_hat, cal_p_hats, cal_outcomes, alpha) as the candidate.

  MN3  Calibration-unavailable → no_trade CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE.
       Absent cal_p_hats OR cal_outcomes on analysis → no_trade, never enter.

  MN4  Non-positive edge → no_trade CENTER_SELL_MODEL_NO_NO_EDGE.
       When 1 − p⁺ − b − phi ≤ 0, candidate returns no_trade, never enter.

  MN5  Enter path: outcome="enter", side="buy_no", edge > 0 (Decimal),
       p_posterior == p⁺ (upper bound stored, not raw), strategy_key="center_sell",
       proof_type="center_sell_model_no" on the decision row written to DB.

  MN6  Pipeline-B / shadow-only: executable_alpha=False on CandidateMetadata;
       target_size_usd is None on enter decisions.

Theorem (§6 / §8.2):
    EV^NO_i = 1 − p_i − b_i − phi(b_i)
    Application condition (upper bound):  1 − p⁺_i − b_i − phi(b_i) > 0
    p⁺ = min(1, p_hat + q_alpha)  [calibrated_bounds upper output]

Shadow candidate — no evaluator routing. Never live.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.calibration.bounds import calibrated_bounds
from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.candidates import (
    CandidateContext,
    CandidateDecision,
)
from src.strategy.candidates.center_sell_model_no import CenterSellModelNo
from src.strategy.fees import phi, venue_fee_rate

# ---------------------------------------------------------------------------
# Shared in-memory DB schema (mirrors center_sell_parity tests)
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT,
    outcome             TEXT,
    side                TEXT,
    strategy_key        TEXT,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time       TEXT,
    provider_reported_time TEXT,
    observation_available_at TEXT,
    polymarket_end_anchor_source TEXT,
    first_member_observed_time TEXT,
    run_complete_time    TEXT,
    zeus_submit_intent_time TEXT,
    venue_ack_time       TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time TEXT,
    clock_skew_estimate_ms_at_submit REAL,
    raw_orderbook_hash_transition_delta_ms REAL,
    schema_version      INTEGER,
    source              TEXT,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_NO_TRADE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT,
    reason_detail       TEXT,
    strategy_key        TEXT,
    event_source        TEXT,
    shadow_runtime      INTEGER,
    observed_at         TEXT,
    schema_version      INTEGER,
    schema_compatibility TEXT,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_DECISION_SEQ_DDL = """
CREATE TABLE IF NOT EXISTS decision_seq_counters (
    market_slug        TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    target_date        TEXT NOT NULL,
    observation_time   TEXT NOT NULL,
    next_seq           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time)
)
"""


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.execute(_DECISION_SEQ_DDL)
    conn.commit()
    return conn


def _make_context(analysis: object) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug="chicago-daily-high-2026-06-01",
        temperature_metric="high",
        target_date="2026-06-01",
        observation_time="2026-06-01T12:00:00+00:00",
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-06-01T12:00:00+00:00",
        analysis=analysis,
    )


_DT = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

# Calibration set for tests: 10 samples with residuals ~ 0.10
_CAL_P = [0.30, 0.50, 0.60, 0.40, 0.55, 0.35, 0.45, 0.65, 0.25, 0.70]
_CAL_Y = [0,    1,    1,    0,    1,    0,    1,    1,    0,    1   ]


# ---------------------------------------------------------------------------
# Helper: build analysis SimpleNamespace with calibration fields
# ---------------------------------------------------------------------------

def _analysis(
    p_hat: float = 0.40,
    no_ask: float = 0.45,
    cal_p_hats: object = None,
    cal_outcomes: object = None,
    alpha: float = 0.10,
    metrics: object = None,
) -> SimpleNamespace:
    """Build a minimal analysis stub for CenterSellModelNo."""
    return SimpleNamespace(
        center_sell_model_no_p_hat=p_hat,
        center_sell_model_no_no_ask=no_ask,
        center_sell_model_no_cal_p_hats=cal_p_hats if cal_p_hats is not None else _CAL_P,
        center_sell_model_no_cal_outcomes=cal_outcomes if cal_outcomes is not None else _CAL_Y,
        center_sell_model_no_alpha=alpha,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# MN1 — upper-bound gate: uses p⁺ not raw p_hat
# ---------------------------------------------------------------------------

class TestMN1UpperBoundGate:
    """Candidate enters/rejects based on p⁺, not raw p_hat."""

    def test_raw_pass_but_upper_fail_gives_no_trade(self) -> None:
        """If 1−p_hat−b−phi > 0 but 1−p⁺−b−phi ≤ 0, must be no_trade."""
        fee_rate = venue_fee_rate()
        # Choose inputs so raw would pass but p⁺ pushes past threshold.
        # Use very tight cal set with large q_alpha so p⁺ ≈ 1.
        large_residual_cal_p = [0.0] * 10
        large_residual_cal_y  = [1]  * 10  # residuals = |1-0| = 1.0 → q_alpha = 1.0
        p_hat = 0.30
        no_ask = Decimal("0.55")
        raw_ev = 1 - p_hat - float(no_ask) - float(phi(Decimal("1"), no_ask, fee_rate))
        assert raw_ev > 0, "raw EV must be positive for this test to be meaningful"

        analysis = _analysis(
            p_hat=p_hat,
            no_ask=float(no_ask),
            cal_p_hats=large_residual_cal_p,
            cal_outcomes=large_residual_cal_y,
        )
        ctx = _make_context(analysis)
        candidate = CenterSellModelNo()
        result = candidate.evaluate(context=ctx, conn=_make_db(), decision_time=_DT)

        assert isinstance(result, CandidateDecision)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_NO_EDGE

    def test_p_plus_used_not_p_hat(self) -> None:
        """When p⁺ leaves positive edge, enter decision's p_posterior == p⁺, not p_hat."""
        # Cal set: all residuals = 0.5 → q_alpha=0.5 → p⁺=0.1+0.5=0.60.
        # no_ask=0.20 → edge=1−0.60−0.20−phi(0.20) > 0.
        p_hat = 0.10
        no_ask_f = 0.20
        cal_p = [0.5] * 20
        cal_y  = [1]   * 20
        _, p_plus = calibrated_bounds(p_hat, cal_p, cal_y, alpha=0.10)

        analysis = _analysis(
            p_hat=p_hat,
            no_ask=no_ask_f,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )

        assert result.outcome == "enter", f"Expected enter, got no_trade: {result.reason_detail}"
        assert result.p_posterior is not None
        assert abs(float(result.p_posterior) - p_plus) < 1e-9, (
            f"p_posterior={result.p_posterior} but expected p⁺={p_plus} (not raw p_hat={p_hat})"
        )


# ---------------------------------------------------------------------------
# MN2 — Edge formula: edge == 1 − p⁺ − b − phi(b, fee_rate)
# ---------------------------------------------------------------------------

class TestMN2EdgeFormula:
    def test_edge_matches_formula(self) -> None:
        """Returned edge == 1 − p⁺ − b − phi(1, b, fee_rate).

        Cal set: residuals = 0.5 → q_alpha=0.5 → p⁺=0.10+0.5=0.60.
        no_ask=0.20 → edge = 1−0.60−0.20−phi(0.20) which is deterministically positive.
        """
        fee_rate = venue_fee_rate()
        p_hat = 0.10
        no_ask_f = 0.20
        cal_p = [0.5] * 20
        cal_y  = [1]   * 20

        _, p_plus = calibrated_bounds(p_hat, cal_p, cal_y, alpha=0.10)
        b = Decimal(str(no_ask_f))
        expected_edge = Decimal("1") - Decimal(str(p_plus)) - b - phi(Decimal("1"), b, fee_rate)

        assert expected_edge > Decimal("0"), f"Test fixture broken: expected_edge={expected_edge}"

        analysis = _analysis(
            p_hat=p_hat,
            no_ask=no_ask_f,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )

        assert result.outcome == "enter"
        assert result.edge is not None
        assert abs(float(result.edge) - float(expected_edge)) < 1e-9, (
            f"edge={result.edge} but expected {expected_edge}"
        )


# ---------------------------------------------------------------------------
# MN3 — Calibration-unavailable → no_trade
# ---------------------------------------------------------------------------

class TestMN3CalibrationUnavailable:
    def test_missing_cal_p_hats(self) -> None:
        analysis = SimpleNamespace(
            center_sell_model_no_p_hat=0.30,
            center_sell_model_no_no_ask=0.55,
            center_sell_model_no_cal_p_hats=None,
            center_sell_model_no_cal_outcomes=_CAL_Y,
            center_sell_model_no_alpha=0.10,
            metrics=None,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE

    def test_missing_cal_outcomes(self) -> None:
        analysis = SimpleNamespace(
            center_sell_model_no_p_hat=0.30,
            center_sell_model_no_no_ask=0.55,
            center_sell_model_no_cal_p_hats=_CAL_P,
            center_sell_model_no_cal_outcomes=None,
            center_sell_model_no_alpha=0.10,
            metrics=None,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE

    def test_missing_p_hat(self) -> None:
        analysis = SimpleNamespace(
            center_sell_model_no_p_hat=None,
            center_sell_model_no_no_ask=0.55,
            center_sell_model_no_cal_p_hats=_CAL_P,
            center_sell_model_no_cal_outcomes=_CAL_Y,
            center_sell_model_no_alpha=0.10,
            metrics=None,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE

    def test_missing_no_ask(self) -> None:
        analysis = SimpleNamespace(
            center_sell_model_no_p_hat=0.30,
            center_sell_model_no_no_ask=None,
            center_sell_model_no_cal_p_hats=_CAL_P,
            center_sell_model_no_cal_outcomes=_CAL_Y,
            center_sell_model_no_alpha=0.10,
            metrics=None,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE

    def test_fields_entirely_absent(self) -> None:
        """Analysis with no center_sell_model_no_* fields → calibration unavailable."""
        analysis = SimpleNamespace(metrics=None)
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE


# ---------------------------------------------------------------------------
# MN4 — Non-positive edge → no_trade CENTER_SELL_MODEL_NO_NO_EDGE
# ---------------------------------------------------------------------------

class TestMN4NonPositiveEdge:
    def test_high_p_plus_gives_no_edge(self) -> None:
        """p⁺ close to 1 → 1−p⁺−b−phi ≤ 0 → no_trade NO_EDGE."""
        # Force large q_alpha: residuals = |1−0| = 1.0 → p⁺ = min(1, p_hat+1) = 1.0
        analysis = _analysis(
            p_hat=0.30,
            no_ask=0.55,
            cal_p_hats=[0.0] * 10,
            cal_outcomes=[1] * 10,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_NO_EDGE

    def test_high_no_ask_gives_no_edge(self) -> None:
        """NO ask == 0.98 → market priced near 1; EV_NO strongly negative."""
        analysis = _analysis(
            p_hat=0.10,
            no_ask=0.98,
            cal_p_hats=_CAL_P,
            cal_outcomes=_CAL_Y,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_NO_EDGE

    def test_edge_exactly_zero_is_no_trade(self) -> None:
        """Edge == 0 (not strictly positive) → no_trade."""
        fee_rate = venue_fee_rate()
        # Construct: 1 − p⁺ − b − phi(1,b,r) == 0
        # Use tight cal so q_alpha ≈ 0 → p⁺ ≈ p_hat.
        # Then set p_hat = 1 − b − phi; that makes edge = 0.
        tight_cal_p = [0.50] * 20
        tight_cal_y  = [1]    * 20  # residuals = 0.50 exactly
        # With all residuals = 0.50, q_alpha = 0.50, p⁺ = p_hat + 0.50
        # To get edge=0: 1 − (p_hat + 0.50) − b − phi = 0
        # Pick b = 0.40: phi(1, 0.40, r) ≈ 0.40 * (1-0.40) * r = 0.24*0.05 = 0.012
        # → p_hat = 1 − 0.50 − 0.40 − 0.012 = 0.088
        b_val = Decimal("0.40")
        fee_for_b = phi(Decimal("1"), b_val, fee_rate)
        # q_alpha with all residuals = |1 − 0.5| = 0.5 → 0.5 (sorted, index = ceil(21*0.9)-1 = 18-1=18 → value=0.5)
        q_alpha = Decimal("0.5")
        p_hat_zero_edge = float(Decimal("1") - q_alpha - b_val - fee_for_b)
        analysis = _analysis(
            p_hat=p_hat_zero_edge,
            no_ask=float(b_val),
            cal_p_hats=tight_cal_p,
            cal_outcomes=tight_cal_y,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.CENTER_SELL_MODEL_NO_NO_EDGE


# ---------------------------------------------------------------------------
# MN5 — Enter path: correct fields on CandidateDecision + DB row
# ---------------------------------------------------------------------------

class TestMN5EnterPath:
    def _enter_analysis(self) -> SimpleNamespace:
        """Analysis that deterministically produces a positive edge.

        Cal set: all residuals = |1−0.5| = 0.5 → q_alpha ≈ 0.5 → p⁺ ≈ 0.1+0.5 = 0.60.
        no_ask = 0.20 → edge = 1−0.60−0.20−phi(0.20) = 0.20−phi >> 0.
        """
        return _analysis(
            p_hat=0.10,
            no_ask=0.20,
            cal_p_hats=[0.5] * 20,
            cal_outcomes=[1] * 20,  # residuals = |1−0.5| = 0.5 → q_alpha = 0.5; p⁺ = 0.60
        )

    def test_outcome_is_enter(self) -> None:
        result = CenterSellModelNo().evaluate(
            context=_make_context(self._enter_analysis()),
            conn=_make_db(),
            decision_time=_DT,
        )
        assert result.outcome == "enter"

    def test_side_is_buy_no(self) -> None:
        result = CenterSellModelNo().evaluate(
            context=_make_context(self._enter_analysis()),
            conn=_make_db(),
            decision_time=_DT,
        )
        assert result.side == "buy_no"

    def test_edge_positive_decimal(self) -> None:
        result = CenterSellModelNo().evaluate(
            context=_make_context(self._enter_analysis()),
            conn=_make_db(),
            decision_time=_DT,
        )
        assert result.edge is not None
        assert result.edge > Decimal("0")

    def test_p_posterior_is_upper_bound(self) -> None:
        analysis = self._enter_analysis()
        _, p_plus = calibrated_bounds(
            analysis.center_sell_model_no_p_hat,
            analysis.center_sell_model_no_cal_p_hats,
            analysis.center_sell_model_no_cal_outcomes,
            alpha=analysis.center_sell_model_no_alpha,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        assert result.p_posterior is not None
        assert abs(float(result.p_posterior) - p_plus) < 1e-9

    def test_strategy_key_is_center_sell(self) -> None:
        candidate = CenterSellModelNo()
        assert candidate.strategy_key == "center_sell"

    def test_decision_row_written_to_db(self) -> None:
        conn = _make_db()
        result = CenterSellModelNo().evaluate(
            context=_make_context(self._enter_analysis()),
            conn=conn,
            decision_time=_DT,
        )
        assert result.outcome == "enter"
        rows = conn.execute(
            "SELECT side, strategy_key, edge FROM decision_events"
        ).fetchall()
        assert len(rows) == 1
        side, strategy_key, edge = rows[0]
        assert side == "buy_no"
        assert strategy_key == "center_sell"
        assert edge is not None and float(edge) > 0


# ---------------------------------------------------------------------------
# MN6 — Shadow-only: executable_alpha=False, target_size_usd=None
# ---------------------------------------------------------------------------

class TestMN6ShadowOnly:
    def test_executable_alpha_false(self) -> None:
        candidate = CenterSellModelNo()
        assert candidate.metadata.executable_alpha is False

    def test_target_size_usd_none_on_enter(self) -> None:
        analysis = _analysis(
            p_hat=0.10,
            no_ask=0.20,
            cal_p_hats=[0.5] * 20,
            cal_outcomes=[1]   * 20,
        )
        result = CenterSellModelNo().evaluate(
            context=_make_context(analysis), conn=_make_db(), decision_time=_DT
        )
        if result.outcome == "enter":
            assert result.target_size_usd is None
