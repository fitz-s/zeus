# Lifecycle: created=2026-05-21; last_reviewed=2026-05-22; last_reused=never
# Purpose: Phase 4 T4 relationship tests for cross_market_correlation_hedge + neg_risk_basket candidates
# Reuse: verify NoTradeReason enum has CORR_HEDGE_REGIME_UNAVAILABLE + NEGRISK_FAMILY_INCOMPLETE before running
"""Phase 4 T4 — relationship tests for cross_market_correlation_hedge + neg_risk_basket.

Two relationship assertions per candidate (per plan §T4 acceptance criteria):
  (i)  on enter-decision input → decision_events row with strategy_key == candidate_name.
  (ii) on no-trade input → no_trade_events row with reason == candidate's reason enum value.

Neither path silently drops.
Additional assertions:
  - UNKNOWN regime fallback test (no exception propagation).
  - strategy_profile.get(key).is_runtime_live() == False for each.
  - CandidateMetadata.executable_alpha == True for each.
  - Import-level: NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE and NEGRISK_FAMILY_INCOMPLETE exist.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.weather_regime_tag import WeatherRegimeTag
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
    CrossMarketCorrelationHedge,
    FamilyOrderBookSnapshot,
    LegBook,
    NegRiskBasket,
    PriceLevel,
    VectorEdgeDecision,
)
from src.strategy.strategy_profile import get as get_strategy_profile

# ---------------------------------------------------------------------------
# Import-level enum existence assertions
# ---------------------------------------------------------------------------
assert NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE, (
    "NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE must exist (Phase 4 T4)"
)
assert NoTradeReason.NEGRISK_FAMILY_INCOMPLETE, (
    "NoTradeReason.NEGRISK_FAMILY_INCOMPLETE must exist (Phase 4 T4)"
)
assert NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET, (
    "NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET must exist (feat-negrisk-basket-exact-arb)"
)

# ---------------------------------------------------------------------------
# Shared DDL
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
    market_slug: str = "test-market-NYC-high-2026-06-15",
    temperature_metric: str = "high",
    target_date: str = "2026-06-15",
    observation_time: str = "2026-06-15T10:00:00+00:00",
    observed_at: str = "2026-06-15T10:00:00+00:00",
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


def _make_metrics(**kwargs: Any) -> SimpleNamespace:
    """Build a minimal MicrostructureMetrics-like namespace."""
    defaults = dict(
        snapshot_id="hash-abc123",
        event_slug="test-slug",
        condition_id="0xabc",
        captured_at_iso="2026-06-15T09:00:00+00:00",
        wide_spread_display_substitution=False,
        spread_observed_window_ms=None,
        depth_at_best_ask=5,
        polymarket_end_anchor_source="gamma_explicit",
        bin_grid_id=None,
        bin_schema_version=None,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)

# ---------------------------------------------------------------------------
# CrossMarketCorrelationHedge relationship tests
# ---------------------------------------------------------------------------

class TestCrossMarketCorrelationHedgeRelationship:
    """R-tests: cross_market_correlation_hedge enter→decision_events, no_trade→no_trade_events."""

    def _make_enter_conn_and_context(self) -> tuple[sqlite3.Connection, CandidateContext]:
        """Build an in-memory conn seeded with market_events_v2 + regime_correlation_cache."""
        conn = _make_conn()
        # Seed market_events_v2 so city can be resolved.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS market_events_v2 (
                market_slug TEXT, city TEXT, target_date TEXT,
                temperature_metric TEXT, condition_id TEXT,
                token_id TEXT, range_label TEXT, range_low REAL, range_high REAL,
                outcome TEXT, created_at TEXT, recorded_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO market_events_v2 (market_slug, city) VALUES (?, ?)",
            ("test-market-NYC-high-2026-06-15", "New York"),
        )
        # Seed regime_correlation_cache with a 2-city matrix.
        import json, numpy as np
        conn.execute(
            """CREATE TABLE IF NOT EXISTS regime_correlation_cache (
                regime TEXT PRIMARY KEY,
                cities_json TEXT NOT NULL,
                matrix_json TEXT NOT NULL,
                fitted_at TEXT NOT NULL
            )"""
        )
        cities = ["New York", "Chicago"]
        # High correlation matrix (off-diagonal = 0.80 > 0.10 threshold).
        matrix = np.array([[1.0, 0.80], [0.80, 1.0]])
        conn.execute(
            "INSERT INTO regime_correlation_cache (regime, cities_json, matrix_json, fitted_at) VALUES (?, ?, ?, ?)",
            (
                WeatherRegimeTag.COLD_SNAP.value,
                json.dumps(cities),
                json.dumps(matrix.tolist()),
                "2026-06-15T09:00:00+00:00",
            ),
        )
        conn.commit()
        metrics = _make_metrics()
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)
        return conn, ctx

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self, monkeypatch):
        """Enter path: regime known + store fitted + corr>=threshold → decision_events row."""
        conn, ctx = self._make_enter_conn_and_context()
        candidate = CrossMarketCorrelationHedge()

        # Monkeypatch regime_tag_for to return COLD_SNAP (non-UNKNOWN).
        monkeypatch.setattr(
            "src.strategy.candidates.cross_market_correlation_hedge.regime_tag_for",
            lambda city, target_date, decision_time, conn: WeatherRegimeTag.COLD_SNAP,
        )

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key, source, polymarket_end_anchor_source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "cross_market_correlation_hedge", (
            f"strategy_key mismatch: {rows[0]['strategy_key']!r}"
        )
        assert rows[0]["source"] == "shadow_decision"
        assert rows[0]["polymarket_end_anchor_source"] == "gamma_explicit"

    def test_no_trade_path_writes_no_trade_events_row_with_correct_reason(self):
        """No-trade path: UNKNOWN regime (in-memory conn) → CORR_HEDGE_REGIME_UNAVAILABLE."""
        conn = _make_conn()
        # No market_events_v2 table — city resolution fails; emit CORR_HEDGE_REGIME_UNAVAILABLE.
        candidate = CrossMarketCorrelationHedge()
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=cross_market_correlation_hedge" in rows[0]["reason_detail"]

    def test_unknown_regime_does_not_propagate_exception(self, monkeypatch):
        """UNKNOWN regime fallback: regime_tag_for returns UNKNOWN → no_trade, no exception raised."""
        conn = _make_conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS market_events_v2 (
                market_slug TEXT, city TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO market_events_v2 (market_slug, city) VALUES (?, ?)",
            ("test-market-NYC-high-2026-06-15", "New York"),
        )
        conn.commit()

        monkeypatch.setattr(
            "src.strategy.candidates.cross_market_correlation_hedge.regime_tag_for",
            lambda city, target_date, decision_time, conn: WeatherRegimeTag.UNKNOWN,
        )
        candidate = CrossMarketCorrelationHedge()
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        # Must not raise.
        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE

    def test_neither_path_silently_drops(self, monkeypatch):
        """Sanity: both paths return non-None and write exactly 1 row each."""
        conn, ctx_enter = self._make_enter_conn_and_context()
        candidate = CrossMarketCorrelationHedge()

        monkeypatch.setattr(
            "src.strategy.candidates.cross_market_correlation_hedge.regime_tag_for",
            lambda city, target_date, decision_time, conn: WeatherRegimeTag.COLD_SNAP,
        )
        d1 = candidate.evaluate(context=ctx_enter, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path: different observation_time to avoid PK collision.
        ctx_notrade = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics()),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        # Patch to UNKNOWN for no-trade.
        monkeypatch.setattr(
            "src.strategy.candidates.cross_market_correlation_hedge.regime_tag_for",
            lambda city, target_date, decision_time, conn: WeatherRegimeTag.UNKNOWN,
        )
        d2 = candidate.evaluate(context=ctx_notrade, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        de_count = conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        nte_count = conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0]
        assert de_count == 1, f"Expected 1 decision_events row, got {de_count}"
        assert nte_count == 1, f"Expected 1 no_trade_events row, got {nte_count}"

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("cross_market_correlation_hedge").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert CrossMarketCorrelationHedge().metadata.executable_alpha is True


# ---------------------------------------------------------------------------
# NegRiskBasket relationship tests
# ---------------------------------------------------------------------------

def _make_profitable_yes_family() -> FamilyOrderBookSnapshot:
    """3-leg family where YES basket has positive exact-arb profit."""
    # YES asks [0.20, 0.22, 0.18] → sum = 0.60 < 1 → Π_Y = 0.40*q - fee > 0
    legs = tuple(
        LegBook(
            condition_id=f"0xcond{i:02d}",
            yes_levels=(PriceLevel(price=p, quantity=Decimal("10")),),
            no_levels=(PriceLevel(price=Decimal("0.85"), quantity=Decimal("10")),),
        )
        for i, p in enumerate([Decimal("0.20"), Decimal("0.22"), Decimal("0.18")])
    )
    return FamilyOrderBookSnapshot(
        legs=legs,
        neg_risk_market_id="nrm-t4-001",
        captured_at_iso="2026-06-15T09:00:00+00:00",
    )


class TestNegRiskBasketRelationship:
    """R-tests: neg_risk_basket enter→decision_events, no_trade→no_trade_events.

    Reframed 2026-05-22: NegRiskBasket now requires FamilyOrderBookSnapshot
    (exact-arb per zeus_math_spec §11.6), not legacy neg_risk_yes_ask_sum threshold.
    """

    def _make_enter_analysis(self) -> SimpleNamespace:
        """Analysis with profitable YES basket via FamilyOrderBookSnapshot."""
        return SimpleNamespace(
            metrics=_make_metrics(),
            family_book_snapshot=_make_profitable_yes_family(),
        )

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: profitable FamilyOrderBookSnapshot → decision_events row."""
        conn = _make_conn()
        candidate = NegRiskBasket()
        analysis = self._make_enter_analysis()
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key, source, polymarket_end_anchor_source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "neg_risk_basket", (
            f"strategy_key mismatch: {rows[0]['strategy_key']!r}"
        )
        assert rows[0]["source"] == "shadow_decision"
        assert rows[0]["polymarket_end_anchor_source"] == "gamma_explicit"

    def test_no_trade_path_missing_family_snapshot_writes_no_trade_row(self):
        """No-trade path: FamilyOrderBookSnapshot absent → NEGRISK_FAMILY_INCOMPLETE."""
        conn = _make_conn()
        candidate = NegRiskBasket()
        # Analysis with no family_book_snapshot at all.
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.NEGRISK_FAMILY_INCOMPLETE

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.NEGRISK_FAMILY_INCOMPLETE.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=neg_risk_basket" in rows[0]["reason_detail"]

    def test_no_trade_path_no_profitable_basket_writes_no_trade_row(self):
        """No-trade path: book present but max(Π_Y, Π_N) ≤ 0 → NEGRISK_NO_PROFITABLE_BASKET."""
        conn = _make_conn()
        candidate = NegRiskBasket()
        # 2-leg family, YES and NO asks both at 0.52 → both baskets unprofitable
        legs = tuple(
            LegBook(
                condition_id=f"0xcond{i:02d}",
                yes_levels=(PriceLevel(price=Decimal("0.52"), quantity=Decimal("10")),),
                no_levels=(PriceLevel(price=Decimal("0.52"), quantity=Decimal("10")),),
            )
            for i in range(2)
        )
        family = FamilyOrderBookSnapshot(
            legs=legs, neg_risk_market_id="nrm-t4-002", captured_at_iso="2026-06-15T09:00:00+00:00"
        )
        analysis = SimpleNamespace(metrics=_make_metrics(), family_book_snapshot=family)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=neg_risk_basket" in rows[0]["reason_detail"]

    def test_no_trade_path_missing_fields_no_exception(self):
        """Bare analysis (no family_book_snapshot) → no_trade without exception."""
        conn = _make_conn()
        candidate = NegRiskBasket()
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.NEGRISK_FAMILY_INCOMPLETE

    def test_neither_path_silently_drops(self):
        """Sanity: both paths return non-None and write exactly 1 row each."""
        conn = _make_conn()
        candidate = NegRiskBasket()

        # Enter path.
        ctx1 = _make_context(conn, self._make_enter_analysis())
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path (different observation_time, bare analysis).
        ctx2 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics()),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        de_count = conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        nte_count = conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0]
        assert de_count == 1, f"Expected 1 decision_events row, got {de_count}"
        assert nte_count == 1, f"Expected 1 no_trade_events row, got {nte_count}"

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("neg_risk_basket").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert NegRiskBasket().metadata.executable_alpha is True
