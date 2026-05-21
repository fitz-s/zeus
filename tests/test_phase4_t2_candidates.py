# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase4_fdr_candidates/PHASE_4_PLAN.md §T2
"""Phase 4 T2 — relationship tests for stale_quote_detector + resolution_window_maker.

Two relationship assertions per candidate (per plan §T2 acceptance criteria):
  (i)  on enter-decision input → decision_events row with strategy_key == candidate_name.
  (ii) on no-trade input → no_trade_events row with reason == candidate's reason enum value.

Neither path silently drops.
Additional assertions:
  - strategy_profile.get(key).is_runtime_live() == False for each.
  - CandidateMetadata.executable_alpha == True for each.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
    ResolutionWindowMaker,
    StaleQuoteDetector,
)
from src.strategy.strategy_profile import get as get_strategy_profile


# ---------------------------------------------------------------------------
# Shared fixtures
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
    """Build a minimal MicrostructureMetrics-like object."""
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
# StaleQuoteDetector relationship tests
# ---------------------------------------------------------------------------

class TestStaleQuoteDetectorRelationship:
    """R-test: stale_quote_detector enter→decision_events, no_trade→no_trade_events."""

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: stale book hash → decision_events row with strategy_key='stale_quote_detector'."""
        conn = _make_conn()
        candidate = StaleQuoteDetector()

        # Stale condition: has info event (spread_observed_window_ms set), no hash transition
        metrics = _make_metrics(
            spread_observed_window_ms=5000,   # info event occurred
            raw_orderbook_hash_transition_delta_ms=None,  # no transition → stale
            depth_at_best_ask=10,
        )
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key, source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "stale_quote_detector", (
            f"strategy_key mismatch: {rows[0]['strategy_key']!r}"
        )
        assert rows[0]["source"] == "shadow_decision"

    def test_enter_path_missing_anchor_records_unknown_legacy_not_gamma_explicit(self):
        conn = _make_conn()
        candidate = StaleQuoteDetector()
        metrics = _make_metrics(
            spread_observed_window_ms=5000,
            raw_orderbook_hash_transition_delta_ms=None,
            depth_at_best_ask=10,
            polymarket_end_anchor_source=None,
        )
        ctx = _make_context(conn, SimpleNamespace(metrics=metrics))

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter"
        row = conn.execute(
            "SELECT source, polymarket_end_anchor_source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchone()
        assert row["source"] == "shadow_decision"
        assert row["polymarket_end_anchor_source"] == "unknown_legacy"

    def test_no_trade_path_writes_no_trade_events_row_with_correct_reason(self):
        """No-trade path: fresh book → no_trade_events row with reason=STALE_QUOTE_FILL_INFEASIBLE."""
        conn = _make_conn()
        candidate = StaleQuoteDetector()

        # Fresh condition: hash transitioned within threshold → not stale
        metrics = _make_metrics(
            spread_observed_window_ms=5000,
            raw_orderbook_hash_transition_delta_ms=1000,  # fresh: 1s < 120s threshold
        )
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE.value, (
            f"reason mismatch: {rows[0]['reason']!r}"
        )
        assert rows[0]["schema_compatibility"] == "current"
        assert "shadow_runtime=true" in rows[0]["reason_detail"]
        assert "candidate_strategy_key=stale_quote_detector" in rows[0]["reason_detail"]

    def test_neither_path_silently_drops(self):
        """Sanity: both paths produce non-None decisions and write exactly 1 row."""
        conn = _make_conn()
        candidate = StaleQuoteDetector()

        # Path 1: enter
        metrics_stale = _make_metrics(
            spread_observed_window_ms=5000,
            raw_orderbook_hash_transition_delta_ms=None,
            depth_at_best_ask=5,
        )
        ctx1 = _make_context(conn, SimpleNamespace(metrics=metrics_stale))
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # Path 2: no_trade (different observation_time to avoid PK collision)
        ctx2 = _make_context(
            conn, SimpleNamespace(metrics=_make_metrics(spread_observed_window_ms=None)),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        total_de = conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        total_nte = conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0]
        assert total_de == 1, f"Expected 1 decision_events row, got {total_de}"
        assert total_nte == 1, f"Expected 1 no_trade_events row, got {total_nte}"

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("stale_quote_detector").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert StaleQuoteDetector().metadata.executable_alpha is True


# ---------------------------------------------------------------------------
# ResolutionWindowMaker relationship tests
# ---------------------------------------------------------------------------

class TestResolutionWindowMakerRelationship:
    """R-test: resolution_window_maker enter→decision_events, no_trade→no_trade_events."""

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: UMA resolved → decision_events row with strategy_key='resolution_window_maker'."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()

        metrics = _make_metrics()
        analysis = SimpleNamespace(
            metrics=metrics,
            uma_resolution_status="resolved",
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "resolution_window_maker"

    def test_no_trade_path_writes_no_trade_events_row_with_correct_reason(self):
        """No-trade path: UMA disputed → no_trade_events row with reason=RESOLUTION_DISPUTED."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()

        metrics = _make_metrics()
        analysis = SimpleNamespace(
            metrics=metrics,
            uma_resolution_status="disputed",
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.RESOLUTION_DISPUTED

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.RESOLUTION_DISPUTED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "shadow_runtime=true" in rows[0]["reason_detail"]
        assert "candidate_strategy_key=resolution_window_maker" in rows[0]["reason_detail"]

    def test_absent_uma_status_writes_no_trade_row(self):
        """No-trade path: uma_resolution_status absent → no_trade with RESOLUTION_DISPUTED."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()

        metrics = _make_metrics()
        # No uma_resolution_status attribute at all
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_DISPUTED
        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.RESOLUTION_DISPUTED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=resolution_window_maker" in rows[0]["reason_detail"]

    def test_neither_path_silently_drops(self):
        conn = _make_conn()
        candidate = ResolutionWindowMaker()

        # Enter path
        ctx1 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics(), uma_resolution_status="asserted"),
        )
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path
        ctx2 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics(), uma_resolution_status="unknown"),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 1

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("resolution_window_maker").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert ResolutionWindowMaker().metadata.executable_alpha is True
