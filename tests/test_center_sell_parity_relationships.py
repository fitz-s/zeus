# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §6
#                  + docs/reference/zeus_strategy_spec.md §8.3-8.5
"""center_sell YES/NO parity arbitrage — deterministic layer relationship tests.

Six cross-module invariants (P1-P6):
  P1  Payoff identity: binary YES+NO pair fully collateralised to $1 →
      vector_payoff = q_star always (NOT (K-1)*q; K=2 but only 1 pays out per leg).
  P2  Profit formula: Π = q − A_YES(q) − A_NO(q) − F_YES(q) − F_NO(q) matches
      manual sweep using phi().
  P3  Edge condition: enter iff a_YES+a_NO+fees < 1 at q*; at/above threshold →
      CENTER_PAIR_PARITY_NO_EDGE.
  P4  Two-leg structure: len(legs)==2, one buy_yes and one buy_no, SAME condition_id.
  P5  q* lands on a depth breakpoint of the merged YES+NO depth ladder.
  P6  Missing binary_book_snapshot → CENTER_PAIR_PARITY_BOOK_UNAVAILABLE.

Theorem (§6 / §8.3):
    Π = q − A_YES(q) − A_NO(q) − F_YES(q) − F_NO(q)  where:
      A_side(q) = Σ_ℓ p_ℓ · Δq_ℓ     (sweep notional, §11.5)
      F_side(q) = Σ_ℓ phi(Δq_ℓ, p_ℓ, r)  (taker fee per level)

Shadow candidate only — no evaluator routing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.candidates import (
    CandidateContext,
    LegBook,
    LegIntent,
    PriceLevel,
    VectorEdgeDecision,
)
from src.strategy.candidates.center_sell_parity import CenterSellParity
from src.strategy.fees import phi, venue_fee_rate

# ---------------------------------------------------------------------------
# Shared schema (mirrors neg_risk_basket tests for consistency)
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
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL DEFAULT '',
    polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy',
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
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
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    schema_compatibility TEXT NOT NULL DEFAULT 'current',
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(_DECISION_EVENTS_DDL)
    c.execute(_NO_TRADE_EVENTS_DDL)
    c.commit()
    return c


def _ctx(
    conn: sqlite3.Connection,
    analysis: Any,
    *,
    obs_time: str = "2026-06-15T10:00:00+00:00",
) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug="test-mkt-NYC-high-2026-06-15",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time=obs_time,
        decision_seq=0,
    )
    return CandidateContext(natural_key=nk, observed_at=obs_time, analysis=analysis)


def _metrics(**kw: Any) -> SimpleNamespace:
    defaults = dict(
        snapshot_id="snap-001",
        event_slug="test-slug",
        condition_id="0xtest",
        captured_at_iso="2026-06-15T09:00:00+00:00",
        wide_spread_display_substitution=False,
        spread_observed_window_ms=None,
        depth_at_best_ask=5,
        polymarket_end_anchor_source="gamma_explicit",
        bin_grid_id=None,
        bin_schema_version=None,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


_DT = datetime(2026, 6, 15, 10, 0, 0)
_CONDITION_ID = "0xcond-binary-01"


def _make_binary_leg(
    yes_price: Decimal,
    yes_qty: Decimal,
    no_price: Decimal,
    no_qty: Decimal,
    condition_id: str = _CONDITION_ID,
) -> LegBook:
    """Single LegBook for a binary YES/NO token with one level per side."""
    return LegBook(
        condition_id=condition_id,
        yes_levels=(PriceLevel(price=yes_price, quantity=yes_qty),),
        no_levels=(PriceLevel(price=no_price, quantity=no_qty),),
    )


def _make_analysis(leg: LegBook) -> SimpleNamespace:
    """Wrap LegBook as analysis.binary_book_snapshot."""
    return SimpleNamespace(metrics=_metrics(), binary_book_snapshot=leg)


def _manual_sweep(
    levels: tuple[PriceLevel, ...], q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal]:
    """Manual A(q) and F(q) using phi() (§11.5 level-by-level ascending price)."""
    remaining = q
    cost = Decimal(0)
    fee = Decimal(0)
    for lv in sorted(levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        cost += lv.price * fill
        fee += phi(fill, lv.price, fee_rate)
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee


# ---------------------------------------------------------------------------
# P1 — Payoff identity: vector_payoff == q_star always
# ---------------------------------------------------------------------------

class TestP1PayoffIdentity:
    """For binary YES+NO pair: YES wins → $1; NO wins → $1. Payoff = q* always."""

    def test_payoff_equals_q_star_when_yes_wins(self):
        """YES token settles at 1; NO settles at 0. Net payoff = q* * 1 = q*."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", f"Expected enter; got {dec!r}"
        assert isinstance(dec, VectorEdgeDecision)
        assert dec.vector_payoff == dec.q_star, (
            f"Payoff identity: expected vector_payoff=={dec.q_star}, got {dec.vector_payoff}"
        )

    def test_payoff_is_q_star_not_two_q_star(self):
        """Unlike neg_risk K=2 NO basket (payoff=(K-1)*q=q), parity payoff = q_star.

        Parity: buy YES + buy NO. Settlement: whichever side wins pays 1*q.
        The PAIR together settles to exactly $q*, not $2q* (one leg always = 0).
        """
        leg = _make_binary_leg(
            yes_price=Decimal("0.25"),
            yes_qty=Decimal("5"),
            no_price=Decimal("0.25"),
            no_qty=Decimal("5"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", f"Expected enter; got {dec!r}"
        assert isinstance(dec, VectorEdgeDecision)
        # Core invariant: payoff is q*, never 2*q*
        assert dec.vector_payoff == dec.q_star
        assert dec.vector_payoff != 2 * dec.q_star, (
            "Parity payoff must NOT be 2*q_star (that would be K=2 neg_risk NO basket error)"
        )


# ---------------------------------------------------------------------------
# P2 — Profit formula: Π = q − A_YES − A_NO − F_YES − F_NO
# ---------------------------------------------------------------------------

class TestP2ProfitFormula:
    """vector_profit == q − A_YES(q) − A_NO(q) − F_YES(q) − F_NO(q) via manual phi."""

    def test_profit_matches_manual_sweep_single_level(self):
        """Buy YES @ 0.30 + NO @ 0.35; profit = q−(0.30+0.35)*q − fees."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.35"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", f"Expected enter; got {dec!r}"
        assert isinstance(dec, VectorEdgeDecision)

        fee_rate = venue_fee_rate()
        q = dec.q_star
        a_yes, f_yes = _manual_sweep(leg.yes_levels, q, fee_rate)
        a_no, f_no = _manual_sweep(leg.no_levels, q, fee_rate)
        expected_profit = q - a_yes - a_no - f_yes - f_no

        assert float(dec.vector_profit) == pytest.approx(float(expected_profit), rel=1e-6), (
            f"Profit formula mismatch: expected {expected_profit}, got {dec.vector_profit}"
        )

    def test_profit_accounts_for_fee_on_both_legs(self):
        """Fee must be charged on BOTH legs (YES and NO), not just one."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.20"),
            yes_qty=Decimal("8"),
            no_price=Decimal("0.20"),
            no_qty=Decimal("8"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)

        fee_rate = venue_fee_rate()
        q = dec.q_star
        _, f_yes = _manual_sweep(leg.yes_levels, q, fee_rate)
        _, f_no = _manual_sweep(leg.no_levels, q, fee_rate)
        total_fee = f_yes + f_no

        assert float(dec.vector_fee) == pytest.approx(float(total_fee), rel=1e-6), (
            f"vector_fee must cover BOTH legs; expected {total_fee}, got {dec.vector_fee}"
        )


# ---------------------------------------------------------------------------
# P3 — Edge condition: enter iff a_YES+a_NO+fees < 1 at q*
# ---------------------------------------------------------------------------

class TestP3EdgeCondition:
    """Enter iff total_cost+total_fee < q (equivalently: a_YES+a_NO+fees < 1 per share)."""

    def test_enter_when_sum_ask_lt_1_minus_fees(self):
        """YES 0.30 + NO 0.30 = 0.60 < 1; clear parity edge → enter."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", f"Expected enter (0.60 < 1); got {dec!r}"
        assert isinstance(dec, VectorEdgeDecision)
        assert dec.vector_profit > Decimal(0)

    def test_no_edge_when_sum_ask_equals_1(self):
        """YES 0.50 + NO 0.50 = 1.00 exactly; fees push above breakeven → no_trade."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.50"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.50"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        # At 0.50+0.50=1.00, fees push total_cost+total_fee > q → no profitable arb
        assert dec.outcome == "no_trade", (
            f"Expected no_trade when YES+NO asks sum to 1.00 (fees eliminate edge); got {dec!r}"
        )
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE

    def test_no_edge_when_sum_ask_above_1(self):
        """YES 0.55 + NO 0.55 = 1.10 > 1; total_cost > q → no_trade."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.55"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.55"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade", (
            f"Expected no_trade when YES+NO asks sum to 1.10; got {dec!r}"
        )
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE

    def test_just_below_threshold_enters(self):
        """YES 0.40 + NO 0.40 = 0.80; well below threshold → enter."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.40"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.40"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", (
            f"Expected enter (0.80 < 1 − fees); got {dec!r}"
        )


# ---------------------------------------------------------------------------
# P4 — Two-leg structure: len(legs)==2, buy_yes+buy_no, SAME condition_id
# ---------------------------------------------------------------------------

class TestP4TwoLegStructure:
    """VectorEdgeDecision must have exactly 2 legs on the same condition_id."""

    def test_exactly_two_legs_in_decision(self):
        """VectorEdgeDecision.legs has len==2."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        assert len(dec.legs) == 2, f"Expected 2 legs, got {len(dec.legs)}"

    def test_one_buy_yes_and_one_buy_no(self):
        """Legs must be one buy_yes and one buy_no."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        sides = {li.side for li in dec.legs}
        assert sides == {"buy_yes", "buy_no"}, (
            f"Expected {{buy_yes, buy_no}}, got {sides}"
        )

    def test_both_legs_share_same_condition_id(self):
        """Both LegIntents must have the same condition_id (same binary market)."""
        cid = "0xcond-binary-unique"
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
            condition_id=cid,
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        condition_ids = {li.condition_id for li in dec.legs}
        assert condition_ids == {cid}, (
            f"Both legs must share condition_id={cid!r}; got {condition_ids}"
        )

    def test_strategy_key_is_center_sell(self):
        """strategy_key must be 'center_sell' and proof_type 'center_pair_parity'."""
        leg = _make_binary_leg(
            yes_price=Decimal("0.30"),
            yes_qty=Decimal("10"),
            no_price=Decimal("0.30"),
            no_qty=Decimal("10"),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        assert dec.strategy_key == "center_sell", (
            f"Expected strategy_key='center_sell', got {dec.strategy_key!r}"
        )
        assert dec.proof_type == "center_pair_parity", (
            f"Expected proof_type='center_pair_parity', got {dec.proof_type!r}"
        )


# ---------------------------------------------------------------------------
# P5 — q* lands on a depth breakpoint of the merged YES+NO depth ladder
# ---------------------------------------------------------------------------

class TestP5QStarAtBreakpoint:
    """q* = argmax Π(q) over the merged YES+NO cumulative-depth breakpoints."""

    def test_q_star_at_depth_constrained_by_shallower_leg(self):
        """YES depth=5, NO depth=10 → q* bounded by min(5,10)=5."""
        leg = LegBook(
            condition_id=_CONDITION_ID,
            yes_levels=(PriceLevel(Decimal("0.20"), Decimal("5")),),
            no_levels=(PriceLevel(Decimal("0.20"), Decimal("10")),),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        # q* cannot exceed min(YES depth, NO depth) = 5
        assert dec.q_star <= Decimal("5"), (
            f"q* must not exceed shallowest-leg depth of 5; got {dec.q_star}"
        )

    def test_q_star_picks_best_profit_over_multi_level_ladder(self):
        """Multi-level YES book; q* picks the breakpoint maximising Π, not just max depth.

        Design:
          YES: level1 (0.10, qty=3), level2 (0.95, qty=10) — deep level is nearly worthless
          NO:  level1 (0.10, qty=10)

          At q=3 (early breakpoint):
            cost_YES = 0.10×3 = 0.30; cost_NO = 0.10×3 = 0.30; total=0.60
            profit ≈ 3 − 0.60 − fees ≈ 2.37 ✓

          At q=10 (crosses expensive YES level2 at 0.95):
            cost_YES = 0.10×3 + 0.95×7 = 6.95; cost_NO = 1.00; total=7.95
            profit ≈ 10 − 7.95 − fees ≈ 1.97  ← LOWER than q=3

          Implementation must argmax over breakpoints, not naively pick max depth.
        """
        leg = LegBook(
            condition_id=_CONDITION_ID,
            yes_levels=(
                PriceLevel(Decimal("0.10"), Decimal("3")),
                PriceLevel(Decimal("0.95"), Decimal("10")),
            ),
            no_levels=(PriceLevel(Decimal("0.10"), Decimal("10")),),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter"
        assert isinstance(dec, VectorEdgeDecision)
        # q* must be 3 (early breakpoint); picking max depth q=10 crosses the 0.95 level
        # which destroys more profit than it adds.
        assert dec.q_star == Decimal("3"), (
            f"q* should pick early profitable breakpoint=3 (argmax); got {dec.q_star}"
        )


# ---------------------------------------------------------------------------
# P6 — Missing binary_book_snapshot → CENTER_PAIR_PARITY_BOOK_UNAVAILABLE
# ---------------------------------------------------------------------------

class TestP6MissingBook:
    """analysis.binary_book_snapshot absent → no_trade CENTER_PAIR_PARITY_BOOK_UNAVAILABLE."""

    def test_missing_book_snapshot_gives_no_trade(self):
        """No binary_book_snapshot on analysis → guard path fires."""
        analysis = SimpleNamespace(metrics=_metrics())  # no binary_book_snapshot
        conn = _conn()
        ctx = _ctx(conn, analysis)
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade", (
            f"Expected no_trade when binary_book_snapshot absent; got {dec!r}"
        )
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_BOOK_UNAVAILABLE

    def test_none_book_snapshot_gives_no_trade(self):
        """binary_book_snapshot=None → guard path fires."""
        analysis = SimpleNamespace(metrics=_metrics(), binary_book_snapshot=None)
        conn = _conn()
        ctx = _ctx(conn, analysis)
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_BOOK_UNAVAILABLE

    def test_empty_yes_levels_gives_no_edge(self):
        """YES levels empty → q* = 0 → no profitable arb."""
        leg = LegBook(
            condition_id=_CONDITION_ID,
            yes_levels=(),
            no_levels=(PriceLevel(Decimal("0.20"), Decimal("10")),),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE

    def test_empty_no_levels_gives_no_edge(self):
        """NO levels empty → q* = 0 → no profitable arb."""
        leg = LegBook(
            condition_id=_CONDITION_ID,
            yes_levels=(PriceLevel(Decimal("0.20"), Decimal("10")),),
            no_levels=(),
        )
        conn = _conn()
        ctx = _ctx(conn, _make_analysis(leg))
        dec = CenterSellParity().evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE
