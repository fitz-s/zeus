# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/PROMOTION_PIPELINE_DESIGN.md §4
"""Track L-1 relationship tests — fail-open live shadow-capture hook.

Three required relationship assertions (per PROMOTION_PIPELINE_DESIGN §4):
  (i)  flag OFF  → no decision_events rows written (no-op).
  (ii) flag ON + exception inside dispatch → live decision returned unchanged (fail-open).
  (iii) flag ON + enter-trigger input → decision_events row with correct strategy_key.

Cross-module invariant: the dispatch_shadow_candidates() function must NEVER
affect the live decision path — any exception is absorbed and logged.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import CandidateContext


# ---------------------------------------------------------------------------
# DDL (minimal set needed by shadow_candidate_dispatch)
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


def _make_metrics(**kwargs: Any) -> SimpleNamespace:
    """Minimal metrics namespace sufficient for most candidates to produce no_trade."""
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
        # StaleQuoteDetector gates
        info_event_observed=False,
        p_after_lower_bound=None,
        stale_quote_price=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)
_MARKET_SLUG = "test-market-NYC-high-2026-06-15"
_TEMP_METRIC = "high"
_TARGET_DATE = "2026-06-15"
_OBS_TIME = "2026-06-15T10:00:00+00:00"


def _make_natural_key(observation_time: str = _OBS_TIME):
    return make_decision_natural_key(
        market_slug=_MARKET_SLUG,
        temperature_metric=_TEMP_METRIC,  # type: ignore[arg-type]
        target_date=_TARGET_DATE,
        observation_time=observation_time,
        decision_seq=0,
    )


# ---------------------------------------------------------------------------
# R-1: flag OFF → no-op (no rows written)
# ---------------------------------------------------------------------------

class TestFlagOffNoOp:
    """When shadow_candidate_capture_enabled() is False, dispatch is a no-op."""

    def test_flag_off_writes_zero_decision_events_rows(self, monkeypatch):
        """R-1(i): flag OFF → zero decision_events rows even with enter-trigger analysis."""
        import src.engine.shadow_candidate_dispatch as scd
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: False)

        conn = _make_conn()
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            passive_maker_estimate=SimpleNamespace(
                expected_fill_probability=Decimal("0.9"),
                queue_depth_ahead=None,
                adverse_selection_score=Decimal("0.01"),
                evidence_order_count=10,
                evidence_fill_count=9,
                evidence_source="venue_command_trade_history",
            ),
            alert_source="noaa_alerts",
            active_weather_alert=True,
        )
        nk = _make_natural_key()

        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        de_count = conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        nte_count = conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0]
        assert de_count == 0, f"Expected 0 decision_events rows when flag OFF, got {de_count}"
        assert nte_count == 0, f"Expected 0 no_trade_events rows when flag OFF, got {nte_count}"


# ---------------------------------------------------------------------------
# R-2: fail-open — exception inside dispatch never propagates
# ---------------------------------------------------------------------------

class TestFailOpen:
    """When an exception is raised inside the dispatch block, it is absorbed.

    The caller's live decision path must not be affected. The exception is
    caught, logged, and execution continues — never re-raised.
    """

    def test_exception_in_dispatch_does_not_propagate(self, monkeypatch):
        """R-1(ii): exception inside dispatch absorbed; live flow continues."""
        import src.engine.shadow_candidate_dispatch as scd
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)

        # Inject a candidate list that always raises
        class _BombCandidate:
            strategy_key = "bomb_candidate"

            def evaluate(self, *, context, conn, decision_time):
                raise RuntimeError("simulated dispatch failure")

        monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [_BombCandidate()])

        conn = _make_conn()
        analysis = SimpleNamespace(metrics=_make_metrics())
        nk = _make_natural_key()

        # Must not raise — fail-open contract
        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        # Nothing written (the bomb raises before any write)
        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 0

    def test_exception_per_candidate_does_not_abort_remaining(self, monkeypatch):
        """R-1(ii) extension: per-candidate exception skips that candidate, runs others."""
        import src.engine.shadow_candidate_dispatch as scd
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)

        written: list[str] = []

        class _BombFirst:
            strategy_key = "bomb_first"
            def evaluate(self, *, context, conn, decision_time):
                raise ValueError("first always fails")

        class _GoodSecond:
            strategy_key = "good_second"
            def evaluate(self, *, context, conn, decision_time):
                written.append("good_second")
                from src.strategy.candidates import CandidateDecision
                from src.contracts.no_trade_reason import NoTradeReason
                return CandidateDecision(
                    outcome="no_trade",
                    reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                    reason_detail="test no-trade",
                )

        monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [_BombFirst(), _GoodSecond()])

        conn = _make_conn()
        analysis = SimpleNamespace(metrics=_make_metrics())
        nk = _make_natural_key()

        # Must not raise, and good_second must have run
        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        assert "good_second" in written, "Remaining candidates must run after per-candidate exception"


# ---------------------------------------------------------------------------
# R-3: flag ON + enter-trigger → decision_events row with correct strategy_key
# ---------------------------------------------------------------------------

class TestFlagOnWritesDecisionEvents:
    """When flag is ON and a candidate evaluates to enter, decision_events row is written."""

    def test_flag_on_weather_enter_writes_decision_events_row(self, monkeypatch):
        """R-1(iii): flag ON + WeatherEventArbitrage enter → decision_events with strategy_key."""
        import src.engine.shadow_candidate_dispatch as scd
        from src.strategy.candidates import WeatherEventArbitrage
        from src.strategy.bayes_alert import LRRecord
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)

        # Supply a stub LR table that returns a high-LR record so the Bayes gate passes.
        class _HighLRTable:
            def lookup(self, **kwargs):  # noqa: ANN001
                return LRRecord(point=6.0, lower=5.0, alert_type="ExtremeHeat",
                                city="chicago", season="summer", lead_time_hours=12)

        candidate = WeatherEventArbitrage(lr_table=_HighLRTable())
        monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [candidate])

        conn = _make_conn()
        # WeatherEventArbitrage enter: trusted source + active alert + prior_p=0.10
        # + LR=5.0 → p'⁻ ≈ 0.357; best_ask=0.30 → edge positive.
        analysis = SimpleNamespace(
            metrics=_make_metrics(best_ask=Decimal("0.30")),
            alert_source="noaa_alerts",
            active_weather_alert=True,
            alert_prior_p=0.10,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=12,
        )
        nk = _make_natural_key()

        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        rows = conn.execute(
            "SELECT strategy_key, source FROM decision_events WHERE market_slug=?",
            (_MARKET_SLUG,),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "weather_event_arbitrage"
        assert rows[0]["source"] == "shadow_decision"

    def test_flag_on_liqprov_enter_writes_decision_events_row(self, monkeypatch):
        """R-1(iii): flag ON + LiquidityProvisionWithHeartbeat enter → decision_events row."""
        import src.engine.shadow_candidate_dispatch as scd
        from src.strategy.candidates import LiquidityProvisionWithHeartbeat
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)
        monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [LiquidityProvisionWithHeartbeat()])

        conn = _make_conn()
        # LiquidityProvisionWithHeartbeat enter (G2 interface):
        # market_clob_adverse_selection.p_fair_lower_bound - maker_bid - adverse_selection_upper_bound > 0
        # 0.65 - 0.55 - 0.05 = 0.05 > 0
        analysis = SimpleNamespace(
            metrics=_make_metrics(depth_at_best_ask=8),
            market_clob_adverse_selection=SimpleNamespace(
                p_fair_lower_bound=Decimal("0.65"),
                maker_bid=Decimal("0.55"),
                adverse_selection_upper_bound=Decimal("0.05"),
            ),
        )
        nk = _make_natural_key()

        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        rows = conn.execute(
            "SELECT strategy_key, source FROM decision_events WHERE market_slug=?",
            (_MARKET_SLUG,),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "liquidity_provision_with_heartbeat"
        assert rows[0]["source"] == "shadow_decision"

    def test_flag_on_no_trade_writes_no_trade_events_row(self, monkeypatch):
        """R-1(iii): flag ON + no-trade condition → no_trade_events row (not silent drop)."""
        import src.engine.shadow_candidate_dispatch as scd
        from src.strategy.candidates import WeatherEventArbitrage
        monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)
        monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [WeatherEventArbitrage()])

        conn = _make_conn()
        # WeatherEventArbitrage no-trade: untrusted source
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            alert_source="random_twitter",
            active_weather_alert=True,
        )
        nk = _make_natural_key()

        scd.dispatch_shadow_candidates(
            analysis=analysis,
            natural_key=nk,
            observed_at=_OBS_TIME,
            conn=conn,
            decision_time=_DECISION_TIME,
        )

        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 0
        nte_rows = conn.execute("SELECT reason FROM no_trade_events").fetchall()
        assert len(nte_rows) == 1
        assert "weather_alert_source_untrusted" in nte_rows[0]["reason"]
