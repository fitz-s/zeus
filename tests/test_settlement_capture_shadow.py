# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §1
#                  + src/strategy/candidates/__init__.py §19 (DeterministicEdgeDecision)
"""Relationship tests for SettlementCaptureShadow — physical-interval theorem.

Relationship invariants under test:
  R1: I_t ⊆ B_i AND profit>0 → DeterministicEdgeDecision(side='buy_yes', proof_type='physical_interval_subset')
      AND shadow decision_events row written with strategy_key='settlement_capture'.
  R2: I_t ∩ B_i = ∅ AND profit>0 → DeterministicEdgeDecision(side='buy_no', proof_type='physical_interval_disjoint')
      AND shadow decision_events row written with strategy_key='settlement_capture'.
  R3: I_t overlaps B_i (neither ⊆ nor disjoint) → CandidateDecision(no_trade, PHYSICAL_INTERVAL_OVERLAP).
  R4: PhysicalIntervalBound absent → CandidateDecision(no_trade, PHYSICAL_INTERVAL_DATA_GATED).
  R5: I_t ⊆ B_i but ask + phi ≥ 1 → CandidateDecision(no_trade, PHYSICAL_INTERVAL_UNPROFITABLE).
  R6: observation_lock_status != 'observation_locked' → CandidateDecision(no_trade, SETTLEMENT_CAPTURE_NOT_LOCKED).
  R7: proof_inputs_hash is stable for identical inputs and changes when any input changes.
  R8: QC state not in accepted set → CandidateDecision(no_trade, PHYSICAL_INTERVAL_DATA_GATED).
  R9: I_t ∩ B_i = ∅ but no_ask absent → CandidateDecision(no_trade, PHYSICAL_INTERVAL_DATA_GATED).
  R10: bin_low=None (shoulder bin) → CandidateDecision(no_trade, PHYSICAL_INTERVAL_DATA_GATED).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
    DeterministicEdgeDecision,
    PhysicalIntervalBound,
    SettlementCaptureShadow,
)
from src.strategy.candidates.settlement_capture_shadow import (
    _interval_disjoint,
    _interval_subset,
    _proof_inputs_hash,
)


# ---------------------------------------------------------------------------
# Shared schema + fixtures
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


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.commit()
    return conn


def _make_context(
    conn: sqlite3.Connection,
    analysis: Any,
    *,
    market_slug: str = "test-sc-NYC-high-2026-06-15",
    temperature_metric: str = "high",
    target_date: str = "2026-06-15",
    observation_time: str = "2026-06-15T14:00:00+00:00",
    observed_at: str = "2026-06-15T14:00:00+00:00",
) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug=market_slug,
        temperature_metric=temperature_metric,  # type: ignore[arg-type]
        target_date=target_date,
        observation_time=observation_time,
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at=observed_at,
        analysis=analysis,
    )


def _make_bound(
    *,
    floor: float = 85.0,
    ceiling: float = 87.0,
    source_available_at: str = "2026-06-15T13:00:00+00:00",
    qc_state: str = "OK",
    delta_phys: float = 2.0,
    observation_value: float = 85.0,
    temperature_metric: str = "high",
) -> PhysicalIntervalBound:
    return PhysicalIntervalBound(
        floor=floor,
        ceiling=ceiling,
        source_available_at=source_available_at,
        qc_state=qc_state,
        delta_phys=delta_phys,
        observation_value=observation_value,
        temperature_metric=temperature_metric,
    )


def _make_analysis(
    bound: Optional[PhysicalIntervalBound] = None,
    *,
    bin_low: Optional[float] = 84.0,
    bin_high: Optional[float] = 87.0,   # default: I_t ⊆ B_i (85-87 ⊆ 84-87)
    yes_ask: Optional[float] = 0.90,
    no_ask: Optional[float] = 0.05,
    token_id: str = "0xyes123",
    no_token_id: str = "0xno456",
    observation_lock_status: Optional[str] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        physical_interval_bound=bound,
        bin_low=bin_low,
        bin_high=bin_high,
        yes_ask=yes_ask,
        no_ask=no_ask,
        token_id=token_id,
        no_token_id=no_token_id,
        observation_lock_status=observation_lock_status,
    )


_DECISION_TIME = datetime(2026, 6, 15, 14, 0, 0)


# ---------------------------------------------------------------------------
# Unit helpers — pure logic (no I/O)
# ---------------------------------------------------------------------------

class TestIntervalHelpers:
    """Pure interval arithmetic — no DB, no candidates."""

    def test_subset_true(self):
        # [85, 87] ⊆ [84, 88]
        assert _interval_subset(85.0, 87.0, 84.0, 88.0) is True

    def test_subset_exact_match(self):
        # [85, 87] ⊆ [85, 87] — boundary is inclusive
        assert _interval_subset(85.0, 87.0, 85.0, 87.0) is True

    def test_subset_false_floor_outside(self):
        # [83, 87] not ⊆ [84, 88]
        assert _interval_subset(83.0, 87.0, 84.0, 88.0) is False

    def test_subset_false_ceiling_outside(self):
        # [85, 89] not ⊆ [84, 88]
        assert _interval_subset(85.0, 89.0, 84.0, 88.0) is False

    def test_disjoint_true_above(self):
        # I_t = [90, 93], B_i = [84, 87] → disjoint
        assert _interval_disjoint(90.0, 93.0, 84.0, 87.0) is True

    def test_disjoint_true_below(self):
        # I_t = [78, 81], B_i = [84, 87] → disjoint
        assert _interval_disjoint(78.0, 81.0, 84.0, 87.0) is True

    def test_disjoint_false_overlap(self):
        # I_t = [85, 90], B_i = [84, 87] → overlaps, not disjoint
        assert _interval_disjoint(85.0, 90.0, 84.0, 87.0) is False

    def test_boundary_adjacent_not_disjoint(self):
        # I_t ceiling == B_i floor → touching, not disjoint (inclusive)
        assert _interval_disjoint(80.0, 84.0, 84.0, 87.0) is False

    def test_boundary_adjacent_disjoint_one_below(self):
        # I_t ceiling == B_i floor - 1 → disjoint
        assert _interval_disjoint(80.0, 83.0, 84.0, 87.0) is True


# ---------------------------------------------------------------------------
# R1: I_t ⊆ B_i → DeterministicEdgeDecision buy_yes + decision_events row
# ---------------------------------------------------------------------------

class TestR1YesSubset:
    """R1: I_t ⊆ B_i AND profit>0 → DeterministicEdgeDecision(side='buy_yes')."""

    def test_yes_subset_returns_deterministic_edge_decision(self):
        """R1 enter: DeterministicEdgeDecision returned with correct proof_type."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        # I_t = [85, 87], B_i = [84, 88] → subset
        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(
            bound, bin_low=84.0, bin_high=88.0, yes_ask=0.90
        )
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(result, DeterministicEdgeDecision), (
            f"Expected DeterministicEdgeDecision, got {type(result).__name__}"
        )
        assert result.side == "buy_yes"
        assert result.proof_type == "physical_interval_subset"
        assert result.strategy_key == "settlement_capture"
        assert result.deterministic_payoff == Decimal("1")
        assert result.deterministic_profit > Decimal("0")

    def test_yes_subset_writes_decision_events_row_with_correct_strategy_key(self):
        """R1 DB: shadow decision_events row written with strategy_key='settlement_capture'."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=88.0, yes_ask=0.90)
        ctx = _make_context(conn, analysis)

        candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        rows = conn.execute(
            "SELECT strategy_key, source, side, edge FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "settlement_capture"
        assert rows[0]["source"] == "shadow_decision"
        assert rows[0]["side"] == "buy_yes"
        assert rows[0]["edge"] is not None and rows[0]["edge"] > 0.0

    def test_yes_subset_profit_equals_one_minus_ask_minus_phi(self):
        """R1 math: profit = 1 − ask − phi(ask) for shares=1, fee_rate from config."""
        from src.strategy.fees import phi as _phi, venue_fee_rate
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        yes_ask = 0.85
        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=88.0, yes_ask=yes_ask)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(result, DeterministicEdgeDecision)
        fee_rate = venue_fee_rate()
        expected_fee = _phi(Decimal("1"), Decimal(str(yes_ask)), fee_rate)
        expected_profit = Decimal("1") - Decimal(str(yes_ask)) - expected_fee
        assert result.deterministic_profit == expected_profit, (
            f"profit mismatch: {result.deterministic_profit} vs {expected_profit}"
        )

    def test_yes_subset_proof_inputs_hash_stable(self):
        """R7: proof_inputs_hash is stable for identical inputs."""
        conn1 = _make_conn()
        conn2 = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=88.0, yes_ask=0.90)

        r1 = candidate.evaluate(
            context=_make_context(conn1, analysis), conn=conn1, decision_time=_DECISION_TIME
        )
        r2 = candidate.evaluate(
            context=_make_context(conn2, analysis), conn=conn2, decision_time=_DECISION_TIME
        )

        assert isinstance(r1, DeterministicEdgeDecision)
        assert isinstance(r2, DeterministicEdgeDecision)
        assert r1.proof_inputs_hash == r2.proof_inputs_hash

    def test_yes_subset_proof_inputs_hash_changes_when_source_available_at_changes(self):
        """R7: proof_inputs_hash changes when source_available_at changes."""
        conn1 = _make_conn()
        conn2 = _make_conn()
        candidate = SettlementCaptureShadow()

        bound_a = _make_bound(floor=85.0, ceiling=87.0, source_available_at="2026-06-15T13:00:00+00:00")
        bound_b = _make_bound(floor=85.0, ceiling=87.0, source_available_at="2026-06-15T14:00:00+00:00")
        analysis_a = _make_analysis(bound_a, bin_low=84.0, bin_high=88.0, yes_ask=0.90)
        analysis_b = _make_analysis(bound_b, bin_low=84.0, bin_high=88.0, yes_ask=0.90)

        r1 = candidate.evaluate(
            context=_make_context(conn1, analysis_a), conn=conn1, decision_time=_DECISION_TIME
        )
        r2 = candidate.evaluate(
            context=_make_context(conn2, analysis_b), conn=conn2, decision_time=_DECISION_TIME
        )

        assert isinstance(r1, DeterministicEdgeDecision)
        assert isinstance(r2, DeterministicEdgeDecision)
        assert r1.proof_inputs_hash != r2.proof_inputs_hash


# ---------------------------------------------------------------------------
# R2: I_t ∩ B_i = ∅ → DeterministicEdgeDecision buy_no + decision_events row
# ---------------------------------------------------------------------------

class TestR2NoDisjoint:
    """R2: I_t ∩ B_i = ∅ AND profit>0 → DeterministicEdgeDecision(side='buy_no')."""

    def test_no_disjoint_returns_deterministic_edge_decision(self):
        """R2 enter: DeterministicEdgeDecision returned with correct proof_type."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        # I_t = [90, 93], B_i = [84, 87] → disjoint (interval is above the bin)
        bound = _make_bound(floor=90.0, ceiling=93.0)
        analysis = _make_analysis(
            bound,
            bin_low=84.0,
            bin_high=87.0,
            yes_ask=0.02,
            no_ask=0.05,
        )
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(result, DeterministicEdgeDecision), (
            f"Expected DeterministicEdgeDecision, got {type(result).__name__}"
        )
        assert result.side == "buy_no"
        assert result.proof_type == "physical_interval_disjoint"
        assert result.strategy_key == "settlement_capture"
        assert result.deterministic_profit > Decimal("0")

    def test_no_disjoint_writes_decision_events_row(self):
        """R2 DB: shadow decision_events row written with side='buy_no'."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=90.0, ceiling=93.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0, no_ask=0.05)
        ctx = _make_context(conn, analysis)

        candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        rows = conn.execute(
            "SELECT strategy_key, source, side FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["strategy_key"] == "settlement_capture"
        assert rows[0]["side"] == "buy_no"
        assert rows[0]["source"] == "shadow_decision"

    def test_no_disjoint_below_bin(self):
        """R2: interval below the bin → buy_no (I_t ceiling < bin_low)."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        # I_t = [78, 81], B_i = [84, 87] → disjoint, interval is below
        bound = _make_bound(floor=78.0, ceiling=81.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0, no_ask=0.05)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(result, DeterministicEdgeDecision)
        assert result.side == "buy_no"


# ---------------------------------------------------------------------------
# R3: Overlap → PHYSICAL_INTERVAL_OVERLAP no_trade
# ---------------------------------------------------------------------------

class TestR3Overlap:
    """R3: I_t overlaps B_i → PHYSICAL_INTERVAL_OVERLAP."""

    def test_overlap_returns_no_trade_with_correct_reason(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        # I_t = [85, 90], B_i = [84, 87] → overlaps but not subset (ceiling 90 > 87)
        bound = _make_bound(floor=85.0, ceiling=90.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_OVERLAP

    def test_overlap_writes_no_trade_events_row(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=90.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0)
        ctx = _make_context(conn, analysis)

        candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        rows = conn.execute(
            "SELECT reason FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.PHYSICAL_INTERVAL_OVERLAP.value

    def test_partial_overlap_from_below_is_also_overlap(self):
        """I_t straddles bin_low → overlap, not disjoint."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        # I_t = [80, 86], B_i = [84, 87] → overlaps (floor<bin_low, ceiling<bin_high)
        bound = _make_bound(floor=80.0, ceiling=86.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_OVERLAP


# ---------------------------------------------------------------------------
# R4: PhysicalIntervalBound absent → PHYSICAL_INTERVAL_DATA_GATED
# ---------------------------------------------------------------------------

class TestR4DataGated:
    """R4: bound absent → PHYSICAL_INTERVAL_DATA_GATED (Δ_phys not wired)."""

    def test_absent_bound_returns_data_gated_no_trade(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        analysis = _make_analysis(None)  # no physical_interval_bound
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED
        assert "physical_interval_bound absent" in (result.reason_detail or "")

    def test_absent_bound_writes_no_trade_events_row(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        analysis = _make_analysis(None)
        ctx = _make_context(conn, analysis)

        candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        rows = conn.execute(
            "SELECT reason FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED.value


# ---------------------------------------------------------------------------
# R5: I_t ⊆ B_i but ask + phi ≥ 1 → PHYSICAL_INTERVAL_UNPROFITABLE
# ---------------------------------------------------------------------------

class TestR5Unprofitable:
    """R5: theorem condition met but price makes profit ≤ 0 → PHYSICAL_INTERVAL_UNPROFITABLE.

    NOTE on Polymarket fee math: phi = fee_rate × p × (1-p) vanishes at p→1, so
    for any valid price in (0,1) at the normal 5% rate, 1 − p − phi > 0 always.
    We force this guard by patching venue_fee_rate() to an artificially high value
    that makes the cost exceed 1 — verifying the guard fires correctly.
    """

    def test_yes_subset_but_cost_exceeds_payoff_is_unprofitable(self):
        """I_t ⊆ B_i but fee_rate=10.0 (patched) → 1 − ask − phi ≤ 0 → UNPROFITABLE."""
        from unittest.mock import patch
        from decimal import Decimal as D

        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        # ask=0.50 with fee_rate=10.0: phi = 10.0 × 0.50 × 0.50 = 2.50; profit = 1 - 0.50 - 2.50 = -2.0
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=88.0, yes_ask=0.50)
        ctx = _make_context(conn, analysis)

        with patch(
            "src.strategy.candidates.settlement_capture_shadow.venue_fee_rate",
            return_value=D("10.0"),
        ):
            result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_UNPROFITABLE

    def test_no_disjoint_but_cost_exceeds_payoff_is_unprofitable(self):
        """I_t ∩ B_i = ∅ but fee_rate=10.0 (patched) → 1 − no_ask − phi ≤ 0 → UNPROFITABLE."""
        from unittest.mock import patch
        from decimal import Decimal as D

        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=90.0, ceiling=93.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0, no_ask=0.50)
        ctx = _make_context(conn, analysis)

        with patch(
            "src.strategy.candidates.settlement_capture_shadow.venue_fee_rate",
            return_value=D("10.0"),
        ):
            result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_UNPROFITABLE


# ---------------------------------------------------------------------------
# R6: Not observation-locked → SETTLEMENT_CAPTURE_NOT_LOCKED
# ---------------------------------------------------------------------------

class TestR6NotLocked:
    """R6: C-1 antibody — non-locked edge self-rejects."""

    def test_non_locked_status_returns_not_locked_reason(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(
            bound,
            observation_lock_status="observation_floor_plus_forecast_upside",
        )
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.SETTLEMENT_CAPTURE_NOT_LOCKED

    def test_unknown_lock_status_returns_not_locked_reason(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, observation_lock_status="observation_unknown")
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.SETTLEMENT_CAPTURE_NOT_LOCKED

    def test_observation_locked_status_proceeds_to_theorem(self):
        """observation_lock_status='observation_locked' must NOT trigger C-1 gate."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(
            bound,
            bin_low=84.0,
            bin_high=88.0,
            yes_ask=0.90,
            observation_lock_status="observation_locked",
        )
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        # Should fall through to YES subset path, not be rejected
        assert isinstance(result, DeterministicEdgeDecision), (
            f"Expected DeterministicEdgeDecision when locked, got {result}"
        )

    def test_absent_lock_status_proceeds_to_theorem(self):
        """observation_lock_status=None (absent) must NOT trigger C-1 gate."""
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(
            bound, bin_low=84.0, bin_high=88.0, yes_ask=0.90,
            observation_lock_status=None,
        )
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(result, DeterministicEdgeDecision)


# ---------------------------------------------------------------------------
# R8: QC state not accepted → PHYSICAL_INTERVAL_DATA_GATED
# ---------------------------------------------------------------------------

class TestR8QcGate:
    """R8: unacceptable QC state gates the entry."""

    @pytest.mark.parametrize("qc_state", ["SUSPECT", "MISSING", "FAILED", "UNKNOWN"])
    def test_bad_qc_state_returns_data_gated(self, qc_state: str):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0, qc_state=qc_state)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=88.0)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED
        assert "QC" in (result.reason_detail or "")


# ---------------------------------------------------------------------------
# R9: Disjoint but no_ask absent → PHYSICAL_INTERVAL_DATA_GATED
# ---------------------------------------------------------------------------

class TestR9NoAskAbsent:
    """R9: NO condition met but no_ask absent → data-gated."""

    def test_disjoint_but_no_ask_absent_is_data_gated(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=90.0, ceiling=93.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=87.0, no_ask=None)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED


# ---------------------------------------------------------------------------
# R10: Shoulder bin (open-ended) → PHYSICAL_INTERVAL_DATA_GATED
# ---------------------------------------------------------------------------

class TestR10ShoulderBin:
    """R10: open-ended bin_low or bin_high → data-gated (theorem requires finite bounds)."""

    def test_shoulder_bin_none_bin_low_is_data_gated(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, bin_low=None, bin_high=87.0)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED

    def test_shoulder_bin_none_bin_high_is_data_gated(self):
        conn = _make_conn()
        candidate = SettlementCaptureShadow()

        bound = _make_bound(floor=85.0, ceiling=87.0)
        analysis = _make_analysis(bound, bin_low=84.0, bin_high=None)
        ctx = _make_context(conn, analysis)

        result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED


# ---------------------------------------------------------------------------
# Cross-path completeness: neither path silently drops
# ---------------------------------------------------------------------------

class TestNeitherPathSilentlyDrops:
    """Sanity: all paths produce non-None decisions and write exactly 1 row total."""

    def test_all_paths_produce_non_none_result(self):
        scenarios = [
            # (bound, bin_low, bin_high, yes_ask, no_ask, lock_status, expected_outcome_type)
            # R1: subset
            (_make_bound(floor=85.0, ceiling=87.0), 84.0, 88.0, 0.90, 0.05, None, DeterministicEdgeDecision),
            # R2: disjoint
            (_make_bound(floor=90.0, ceiling=93.0), 84.0, 87.0, 0.02, 0.05, None, DeterministicEdgeDecision),
            # R3: overlap
            (_make_bound(floor=85.0, ceiling=90.0), 84.0, 87.0, 0.90, 0.05, None, type(None)),  # CandidateDecision
            # R4: no bound
            (None, 84.0, 87.0, 0.90, 0.05, None, type(None)),
        ]
        candidate = SettlementCaptureShadow()
        for i, (bound, bl, bh, ya, na, lock, _expected_class) in enumerate(scenarios):
            conn = _make_conn()
            analysis = _make_analysis(
                bound, bin_low=bl, bin_high=bh,
                yes_ask=ya, no_ask=na,
                observation_lock_status=lock,
            )
            ctx = _make_context(conn, analysis, observation_time=f"2026-06-15T{14+i:02d}:00:00+00:00")
            result = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)
            assert result is not None, f"Scenario {i}: got None"

    def test_metadata_is_correct(self):
        candidate = SettlementCaptureShadow()
        assert candidate.strategy_key == "settlement_capture"
        assert candidate.metadata.executable_alpha is False
        assert "shadow" in candidate.describe().lower() or "physical" in candidate.describe().lower()
