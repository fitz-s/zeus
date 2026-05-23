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
# LiquidityProvisionWithHeartbeat relationship tests — adverse-selection model
#
# Reframe per STRATEGY_TAXONOMY_DIRECTIVE.md §11 + zeus_strategy_spec.md §15.
# EV_maker = Pr(F)·[p_fair − q_bid − AS]; sign = p⁻_fair − q_bid − AS⁺ > 0.
# Pr(F) decides volume, NOT sign. Data-gated: AS from full-market CLOB unwired.
# ---------------------------------------------------------------------------

def _make_as_estimate(
    p_fair_lower: Decimal = Decimal("0.50"),
    maker_bid: Decimal = Decimal("0.45"),
    as_upper: Decimal = Decimal("0.02"),
) -> SimpleNamespace:
    """Mock market_clob_adverse_selection on analysis.

    Fields:
      p_fair_lower_bound: calibrated lower bound on fair price (p⁻_fair).
      maker_bid:          quote bid price (q_bid).
      adverse_selection_upper_bound: AS⁺ = E[p_after−p_before|F] upper bound.
    """
    return SimpleNamespace(
        p_fair_lower_bound=p_fair_lower,
        maker_bid=maker_bid,
        adverse_selection_upper_bound=as_upper,
        source="full_market_clob_public_trades",  # NOT Zeus self-history
    )


class TestLiquidityProvisionWithHeartbeatRelationship:
    """R-tests: adverse-selection maker model (§11/§15 reframe).

    Core invariants:
      1. Pr(F) does NOT drive sign — varying fill_probability with fixed AS does not flip outcome.
      2. AS bound drives sign — varying AS across the threshold flips outcome.
      3. Data-gated: AS-estimator absent → LIQPROV_ADVERSE_SELECTION_UNWIRED, never enter.
      4. No self-reference: legacy fill_probability present but AS absent → still no_trade.
      5. Post-only maker: phi uses fee_rate=0 (maker fee is zero per §0 + §15.2).
      6. Enter→decision_events row; no_trade→no_trade_events row.
    """

    # ── R1: Pr(F) decides volume, NOT sign ─────────────────────────────────

    def test_fill_probability_does_not_determine_sign(self):
        """R1 — Sign-volume separation: varying Pr(F) with fixed AS does not flip decision.

        Hold p⁻_fair=0.55, q_bid=0.45, AS⁺=0.02 → edge=0.08 > 0 → always enter.
        Vary fill_probability across 0.01 and 0.99. Decision must be 'enter' both times.
        """
        conn1 = _make_conn()
        conn2 = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # Low fill probability (0.01)
        analysis_low = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=Decimal("0.55"),
                maker_bid=Decimal("0.45"),
                as_upper=Decimal("0.02"),
            ),
            passive_maker_estimate=SimpleNamespace(expected_fill_probability=Decimal("0.01")),
        )
        d_low = candidate.evaluate(
            context=_make_context(conn1, analysis_low),
            conn=conn1,
            decision_time=_DECISION_TIME,
        )

        # High fill probability (0.99)
        analysis_high = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=Decimal("0.55"),
                maker_bid=Decimal("0.45"),
                as_upper=Decimal("0.02"),
            ),
            passive_maker_estimate=SimpleNamespace(expected_fill_probability=Decimal("0.99")),
        )
        d_high = candidate.evaluate(
            context=_make_context(conn2, analysis_high),
            conn=conn2,
            decision_time=_DECISION_TIME,
        )

        assert d_low.outcome == "enter", (
            f"R1 violation: low fill_prob=0.01 should enter (AS in budget); got {d_low.outcome}"
        )
        assert d_high.outcome == "enter", (
            f"R1 violation: high fill_prob=0.99 should enter (AS in budget); got {d_high.outcome}"
        )

    # ── R2: AS bound drives sign ────────────────────────────────────────────

    def test_as_bound_determines_sign_at_threshold(self):
        """R2 — AS bound drives sign: straddling p⁻_fair − q_bid flips the decision.

        p⁻_fair=0.55, q_bid=0.45 → threshold for AS = 0.10.
        AS⁺=0.09 → edge=0.01>0 → enter.
        AS⁺=0.11 → edge=−0.01<0 → no_trade.
        """
        conn_enter = _make_conn()
        conn_no_trade = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # Below threshold → enter
        analysis_enter = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=Decimal("0.55"),
                maker_bid=Decimal("0.45"),
                as_upper=Decimal("0.09"),
            ),
        )
        d_enter = candidate.evaluate(
            context=_make_context(conn_enter, analysis_enter),
            conn=conn_enter,
            decision_time=_DECISION_TIME,
        )

        # Above threshold → no_trade
        analysis_no_trade = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=Decimal("0.55"),
                maker_bid=Decimal("0.45"),
                as_upper=Decimal("0.11"),
            ),
        )
        d_no_trade = candidate.evaluate(
            context=_make_context(conn_no_trade, analysis_no_trade),
            conn=conn_no_trade,
            decision_time=_DECISION_TIME,
        )

        assert d_enter.outcome == "enter", (
            f"R2 violation: AS=0.09 < 0.10 threshold → expected enter; got {d_enter.outcome}"
        )
        assert d_no_trade.outcome == "no_trade", (
            f"R2 violation: AS=0.11 > 0.10 threshold → expected no_trade; got {d_no_trade.outcome}"
        )

    # ── R3: Data-gated: AS-estimator absent → LIQPROV_ADVERSE_SELECTION_UNWIRED ──

    def test_as_data_absent_emits_adverse_selection_unwired_no_trade(self):
        """R3 — Data-gate: market_clob_adverse_selection absent → LIQPROV_ADVERSE_SELECTION_UNWIRED.

        External CLOB order-flow data for AS estimation is unwired. Until wired,
        the strategy must emit no_trade with this specific reason.
        """
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # No market_clob_adverse_selection attribute — data not wired
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", (
            f"R3 violation: AS data absent must emit no_trade; got {decision.outcome}"
        )
        assert decision.reason == NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED, (
            f"R3 violation: reason must be LIQPROV_ADVERSE_SELECTION_UNWIRED; got {decision.reason}"
        )
        assert "adverse selection" in (decision.reason_detail or "").lower(), (
            f"Expected 'adverse selection' in reason_detail; got {decision.reason_detail!r}"
        )

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=liquidity_provision_with_heartbeat" in rows[0]["reason_detail"]

    # ── R4: No self-reference: legacy fill_prob present but AS absent → no_trade ──

    def test_legacy_fill_probability_present_but_as_absent_still_no_trade(self):
        """R4 — No self-reference: passive_maker_estimate (venue-command fill prob) present
        but market_clob_adverse_selection absent → still no_trade.

        Proves the new implementation does NOT use Zeus's own fill-probability
        as a sign oracle. The AS field is the gating input, not fill_probability.
        """
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # Legacy field present but AS estimator absent
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            passive_maker_estimate=_make_passive_estimate(Decimal("0.90")),
            # No market_clob_adverse_selection
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade", (
            "R4 violation: even with high fill_probability (legacy), "
            f"AS absent must block entry; got {decision.outcome}"
        )
        assert decision.reason == NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED, (
            f"R4 violation: reason must be LIQPROV_ADVERSE_SELECTION_UNWIRED; got {decision.reason}"
        )

    # ── R5: Maker fee = 0 (structural) ─────────────────────────────────────

    def test_maker_fee_zero_in_ev_computation(self):
        """R5 — Maker fee is zero: post-only guarantees maker role; phi(q, p, 0) = 0.

        With AS⁺=0.09 (edge positive), entry occurs. Edge stored on decision
        must equal p⁻_fair − q_bid − AS⁺ (no fee deduction, fee=0 for maker).
        Authority: STRATEGY_TAXONOMY_DIRECTIVE §11 + zeus_strategy_spec §15.2.
        """
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        p_fair_lower = Decimal("0.55")
        maker_bid = Decimal("0.45")
        as_upper = Decimal("0.09")
        expected_edge = p_fair_lower - maker_bid - as_upper  # 0.01, no fee

        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=p_fair_lower,
                maker_bid=maker_bid,
                as_upper=as_upper,
            ),
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter", f"R5: expected enter; got {decision.outcome}"
        # Decision row in decision_events should store the correct edge
        row = conn.execute(
            "SELECT edge FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchone()
        assert row is not None
        stored_edge = Decimal(str(row["edge"]))
        assert stored_edge == expected_edge, (
            f"R5 violation: edge in decision_events={stored_edge} "
            f"!= p⁻−bid−AS⁺={expected_edge} (maker fee must be 0)"
        )

    # ── R6: Writer coverage ─────────────────────────────────────────────────

    def test_enter_writes_decision_events_row_with_correct_strategy_key(self):
        """R6a — Enter path writes decision_events row with strategy_key='liquidity_provision_with_heartbeat'."""
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            market_clob_adverse_selection=_make_as_estimate(
                p_fair_lower=Decimal("0.55"),
                maker_bid=Decimal("0.45"),
                as_upper=Decimal("0.02"),
            ),
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "enter"
        rows = conn.execute(
            "SELECT strategy_key, source FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"R6a: expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "liquidity_provision_with_heartbeat"
        assert rows[0]["source"] == "shadow_decision"

    def test_no_trade_writes_no_trade_events_row(self):
        """R6b — No-trade path (AS data absent) writes no_trade_events row."""
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"R6b: expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "shadow_runtime=true" in rows[0]["reason_detail"]
        assert "candidate_strategy_key=liquidity_provision_with_heartbeat" in rows[0]["reason_detail"]

    def test_neither_path_silently_drops(self):
        """Both enter and no_trade paths produce non-None decisions."""
        conn = _make_conn()
        candidate = LiquidityProvisionWithHeartbeat()

        # Enter path (edge positive)
        ctx1 = _make_context(
            conn,
            SimpleNamespace(
                metrics=_make_metrics(),
                market_clob_adverse_selection=_make_as_estimate(
                    p_fair_lower=Decimal("0.55"),
                    maker_bid=Decimal("0.45"),
                    as_upper=Decimal("0.02"),
                ),
            ),
        )
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path (AS absent)
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
