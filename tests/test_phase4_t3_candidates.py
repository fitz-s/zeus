# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase4_fdr_candidates/PHASE_4_PLAN.md §T3
"""Phase 4 T3 — relationship tests for liquidity_provision_with_heartbeat + weather_event_arbitrage.

Two relationship assertions per candidate (per plan §T2/T3 acceptance criteria):
  (i)  on enter-decision input → decision_events row with strategy_key == candidate_name.
  (ii) on no-trade input → no_trade_events row with reason == candidate's reason enum value.

T3 additional requirement (per plan §T3):
  - The missing-field guard path must be exercised in at least one test
    (NoTradeReason emission, not just happy-path enter).
    liquidity_provision_with_heartbeat: passive_maker_estimate absent → LIQPROV_HEARTBEAT_ABSENT.
    weather_event_arbitrage: alert_source absent → WEATHER_ALERT_SOURCE_UNTRUSTED.

Neither path silently drops.
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
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
    LiquidityProvisionWithHeartbeat,
    WeatherEventArbitrage,
)
from src.strategy.strategy_profile import get as get_strategy_profile


# ---------------------------------------------------------------------------
# Shared fixture helpers (duplicated from T2 tests for isolation)
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


def _make_passive_estimate(fill_prob: Decimal = Decimal("0.6")) -> SimpleNamespace:
    return SimpleNamespace(
        expected_fill_probability=fill_prob,
        queue_depth_ahead=None,
        adverse_selection_score=Decimal("0.01"),
        evidence_order_count=10,
        evidence_fill_count=6,
        evidence_source="venue_command_trade_history",
    )


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)


# ---------------------------------------------------------------------------
# LiquidityProvisionWithHeartbeat relationship tests
# ---------------------------------------------------------------------------

class TestLiquidityProvisionWithHeartbeatRelationship:
    """R-test: liqprov_with_heartbeat enter→decision_events, no_trade→no_trade_events."""

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: fill_probability sufficient → decision_events row with correct strategy_key."""
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        metrics = _make_metrics(depth_at_best_ask=8)
        analysis = SimpleNamespace(
            metrics=metrics,
            passive_maker_estimate=_make_passive_estimate(Decimal("0.55")),
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key, source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "liquidity_provision_with_heartbeat"
        assert rows[0]["source"] == "shadow_decision"

    def test_enter_path_missing_anchor_records_unknown_legacy_not_gamma_explicit(self):
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()
        metrics = _make_metrics(depth_at_best_ask=8, polymarket_end_anchor_source=None)
        analysis = SimpleNamespace(
            metrics=metrics,
            passive_maker_estimate=_make_passive_estimate(Decimal("0.55")),
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter"
        row = conn.execute(
            "SELECT source, polymarket_end_anchor_source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchone()
        assert row["source"] == "shadow_decision"
        assert row["polymarket_end_anchor_source"] == "unknown_legacy"

    def test_no_trade_path_writes_no_trade_events_row_with_correct_reason(self):
        """No-trade path: fill_prob below minimum → no_trade_events row with LIQPROV_HEARTBEAT_ABSENT."""
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        metrics = _make_metrics(depth_at_best_ask=5)
        analysis = SimpleNamespace(
            metrics=metrics,
            passive_maker_estimate=_make_passive_estimate(Decimal("0.10")),  # below 0.30
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.LIQPROV_HEARTBEAT_ABSENT

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.LIQPROV_HEARTBEAT_ABSENT.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "shadow_runtime=true" in rows[0]["reason_detail"]
        assert (
            "candidate_strategy_key=liquidity_provision_with_heartbeat"
            in rows[0]["reason_detail"]
        )

    def test_missing_field_guard_passive_estimate_absent_writes_no_trade_row(self):
        """T3 required missing-field guard: passive_maker_estimate absent → LIQPROV_HEARTBEAT_ABSENT.

        Per plan §T3: the missing-field guard path must be exercised (NoTradeReason
        emission, not just happy-path enter). This is the canonical test for that.
        """
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # No passive_maker_estimate attribute at all
        metrics = _make_metrics()
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        # The missing-field guard fires
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.LIQPROV_HEARTBEAT_ABSENT
        assert "fill_probability absent" in (decision.reason_detail or ""), (
            f"Expected 'fill_probability absent' in reason_detail; got {decision.reason_detail!r}"
        )

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.LIQPROV_HEARTBEAT_ABSENT.value
        assert rows[0]["schema_compatibility"] == "current"
        assert (
            "candidate_strategy_key=liquidity_provision_with_heartbeat"
            in rows[0]["reason_detail"]
        )

    def test_neither_path_silently_drops(self):
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        ctx1 = _make_context(
            conn,
            SimpleNamespace(
                metrics=_make_metrics(depth_at_best_ask=5),
                passive_maker_estimate=_make_passive_estimate(Decimal("0.80")),
            ),
        )
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        ctx2 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics()),  # no passive_estimate
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 1

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("liquidity_provision_with_heartbeat").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert LiquidityProvisionWithHeartbeat().metadata.executable_alpha is True


# ---------------------------------------------------------------------------
# WeatherEventArbitrage relationship tests
# ---------------------------------------------------------------------------

class TestWeatherEventArbitrageRelationship:
    """R-test: weather_event_arbitrage enter→decision_events, no_trade→no_trade_events."""

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: trusted alert + active → decision_events with correct strategy_key."""
        conn = _make_conn()
        candidate = WeatherEventArbitrage()

        metrics = _make_metrics()
        analysis = SimpleNamespace(
            metrics=metrics,
            alert_source="noaa_alerts",
            active_weather_alert=True,
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"

        rows = conn.execute(
            "SELECT strategy_key FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["strategy_key"] == "weather_event_arbitrage"

    def test_no_trade_path_writes_no_trade_events_row_with_correct_reason(self):
        """No-trade path: untrusted source → no_trade_events with WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        candidate = WeatherEventArbitrage()

        metrics = _make_metrics()
        analysis = SimpleNamespace(
            metrics=metrics,
            alert_source="random_twitter_feed",  # not in trusted set
            active_weather_alert=True,
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", f"Expected no_trade, got {decision.outcome}"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=weather_event_arbitrage" in rows[0]["reason_detail"]

    def test_missing_field_guard_alert_source_absent_writes_no_trade_row(self):
        """T3 required missing-field guard: alert_source absent → WEATHER_ALERT_SOURCE_UNTRUSTED.

        Per plan §T3: the missing-field guard path must be exercised. This proves
        that the 'external alert feed not wired' condition fires and writes a row.
        """
        conn = _make_conn()
        candidate = WeatherEventArbitrage()

        # No alert_source attribute — feed not wired
        metrics = _make_metrics()
        analysis = SimpleNamespace(metrics=metrics)
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED
        assert "not wired" in (decision.reason_detail or ""), (
            f"Expected 'not wired' in reason_detail; got {decision.reason_detail!r}"
        )

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=weather_event_arbitrage" in rows[0]["reason_detail"]

    def test_neither_path_silently_drops(self):
        conn = _make_conn()
        candidate = WeatherEventArbitrage()

        # Enter path
        ctx1 = _make_context(
            conn,
            SimpleNamespace(
                metrics=_make_metrics(),
                alert_source="nws_alerts",
                active_weather_alert=True,
            ),
        )
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path (absent alert source)
        ctx2 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics()),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 1

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("weather_event_arbitrage").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert WeatherEventArbitrage().metadata.executable_alpha is True
