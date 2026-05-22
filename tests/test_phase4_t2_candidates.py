# Created: 2026-05-21
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase4_fdr_candidates/PHASE_4_PLAN.md §T2
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §2
#                  + docs/reference/zeus_strategy_spec.md §19.2
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

from decimal import Decimal

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.settlement_outcome import SettlementOutcome
from src.state.db import SCHEMA_VERSION
from src.state.decision_events import write_shadow_decision_event
from src.strategy.candidates import (
    CandidateContext,
    DeterministicEdgeDecision,
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
    """Build a minimal MicrostructureMetrics-like object.

    FOK arb fields (2026-05-22 reframe): info_event_observed, p_after_lower_bound,
    stale_quote_price default to data-gated values (False / None) matching
    MarketAnalysisVNext.compute() defaults.
    """
    from decimal import Decimal

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
        # FOK arb fields — data-gated defaults (2026-05-22 reframe)
        info_event_observed=False,
        p_after_lower_bound=None,
        stale_quote_price=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)


def test_init_schema_migrates_decision_events_shadow_provenance_check() -> None:
    """Existing v24 decision_events CHECKs must upgrade before shadow writes."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_events (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
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
            observation_available_at   TEXT NOT NULL,
            polymarket_end_anchor_source TEXT NOT NULL CHECK (
                polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback')
            ),
            first_member_observed_time TEXT,
            run_complete_time          TEXT,
            zeus_submit_intent_time    TEXT,
            venue_ack_time             TEXT,
            first_inclusion_block_time TEXT,
            finality_confirmed_time    TEXT,
            clock_skew_estimate_ms_at_submit INTEGER,
            raw_orderbook_hash_transition_delta_ms INTEGER,
            schema_version INTEGER NOT NULL CHECK (schema_version IN (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24)),
            source         TEXT NOT NULL CHECK (source IN ('phase0_backfill', 'live_decision')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
        """
    )
    init_schema(conn)
    ctx = _make_context(conn, SimpleNamespace(metrics=_make_metrics()))

    write_shadow_decision_event(
        ctx.natural_key,
        decision_time=_DECISION_TIME.isoformat(),
        side="buy_yes",
        strategy_key="stale_quote_detector",
        conn=conn,
        polymarket_end_anchor_source=None,
    )

    row = conn.execute(
        "SELECT source, polymarket_end_anchor_source, schema_version FROM decision_events"
    ).fetchone()
    assert row["source"] == "shadow_decision"
    assert row["polymarket_end_anchor_source"] == "unknown_legacy"
    assert row["schema_version"] == SCHEMA_VERSION


def test_init_schema_removes_interrupted_decision_events_temp_table() -> None:
    """Current decision_events schema plus stale rebuild temp must boot cleanly."""
    from src.state.db import init_schema
    from src.state.table_registry import DBIdentity, assert_db_matches_registry

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("CREATE TABLE decision_events_new AS SELECT * FROM decision_events")

    init_schema(conn)

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='decision_events_new'"
        ).fetchone()[0]
        == 0
    )
    assert_db_matches_registry(conn, DBIdentity.WORLD)


# ---------------------------------------------------------------------------
# StaleQuoteDetector relationship tests
# ---------------------------------------------------------------------------

class TestStaleQuoteDetectorRelationship:
    """R-test: stale_quote_detector enter→decision_events, no_trade→no_trade_events."""

    def test_enter_path_writes_decision_events_row_with_correct_strategy_key(self):
        """Enter path: stale book hash + FOK arb inputs → decision_events row with strategy_key='stale_quote_detector'.

        Updated 2026-05-22: strategy reframed as FOK information-delay arbitrage.
        Enter now requires info_event_observed=True + p_after_lower_bound + stale_quote_price + edge>0.
        """
        from decimal import Decimal

        conn = _make_conn()
        candidate = StaleQuoteDetector()

        # FOK arb enter condition: InfoEvent known, p1=0.65, a0=0.50, book stale, depth present
        metrics = _make_metrics(
            info_event_observed=True,
            p_after_lower_bound=Decimal("0.65"),
            stale_quote_price=Decimal("0.50"),
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
        """Updated 2026-05-22: uses FOK arb enter fields."""
        from decimal import Decimal

        conn = _make_conn()
        candidate = StaleQuoteDetector()
        metrics = _make_metrics(
            info_event_observed=True,
            p_after_lower_bound=Decimal("0.65"),
            stale_quote_price=Decimal("0.50"),
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
        """Sanity: both paths produce non-None decisions and write exactly 1 row.

        Updated 2026-05-22: enter path uses FOK arb fields.
        """
        from decimal import Decimal

        conn = _make_conn()
        candidate = StaleQuoteDetector()

        # Path 1: enter — FOK arb inputs
        metrics_stale = _make_metrics(
            info_event_observed=True,
            p_after_lower_bound=Decimal("0.65"),
            stale_quote_price=Decimal("0.50"),
            raw_orderbook_hash_transition_delta_ms=None,
            depth_at_best_ask=5,
        )
        ctx1 = _make_context(conn, SimpleNamespace(metrics=metrics_stale))
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # Path 2: no_trade — data-gated (different observation_time to avoid PK collision)
        ctx2 = _make_context(
            conn, SimpleNamespace(metrics=_make_metrics(info_event_observed=False)),
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
# ResolutionWindowMaker relationship tests — typed SettlementOutcome contract
# Authority: STRATEGY_TAXONOMY_DIRECTIVE.md §2 + zeus_strategy_spec.md §19.2
# ---------------------------------------------------------------------------

def _make_analysis_source_published(
    yes_ask: str = "0.10",
    no_ask: str = "0.05",
    yes_token_id: str = "0xYES",
    no_token_id: str = "0xNO",
) -> SimpleNamespace:
    """Build analysis with SOURCE_PUBLISHED_VENUE_UNRESOLVED + ask prices wired."""
    return SimpleNamespace(
        metrics=_make_metrics(),
        settlement_outcome=SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
        yes_ask=Decimal(yes_ask),
        no_ask=Decimal(no_ask),
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


class TestResolutionWindowMakerRelationship:
    """R-tests: resolution_window_maker typed-SettlementOutcome contract.

    Cross-module invariant: SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED
    + ask prices + token IDs → DeterministicEdgeDecision with deterministic_profit > 0
    + decision_events shadow row; any missing input → no_trade + no_trade_events row.
    """

    # ── Enter path ────────────────────────────────────────────────────────────

    def test_enter_yes_writes_deterministic_edge_decision_and_decision_events_row(self):
        """R: SOURCE_PUBLISHED_VENUE_UNRESOLVED + profitable YES ask
           → DeterministicEdgeDecision(side='buy_yes', deterministic_profit>0)
           + decision_events shadow row with strategy_key='resolution_window_maker'.
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        # yes_ask=0.10 → Π_yes ≈ 1 − 0.10 − phi > 0; no_ask=0.50 → Π_no ≈ 0.5 − fee > 0
        # YES has higher profit
        analysis = _make_analysis_source_published(yes_ask="0.10", no_ask="0.50")
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(decision, DeterministicEdgeDecision), (
            f"Expected DeterministicEdgeDecision, got {type(decision).__name__}"
        )
        assert decision.outcome == "enter"
        assert decision.side == "buy_yes"
        assert decision.token_id == "0xYES"
        assert decision.proof_type == "source_known_venue_unresolved"
        assert decision.deterministic_profit > Decimal("0")
        assert decision.deterministic_payoff == Decimal("1")
        assert len(decision.proof_inputs_hash) == 64  # SHA-256 hex

        rows = conn.execute(
            "SELECT strategy_key, edge, side FROM decision_events WHERE market_slug=?",
            (ctx.natural_key[0],),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 shadow row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "resolution_window_maker"
        assert rows[0]["side"] == "buy_yes"
        assert rows[0]["edge"] is not None and rows[0]["edge"] > 0

    def test_enter_no_writes_deterministic_edge_decision_buy_no(self):
        """R: SOURCE_PUBLISHED_VENUE_UNRESOLVED + NO ask < YES ask
           → DeterministicEdgeDecision(side='buy_no') when Π_no > Π_yes > 0.
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        # no_ask=0.05 → Π_no ≈ 0.95 − fee; yes_ask=0.60 → Π_yes ≈ 0.40 − fee
        # NO has higher profit
        analysis = _make_analysis_source_published(yes_ask="0.60", no_ask="0.05")
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(decision, DeterministicEdgeDecision)
        assert decision.side == "buy_no"
        assert decision.token_id == "0xNO"
        assert decision.deterministic_profit > Decimal("0")

    def test_deterministic_profit_formula_matches_theorem(self):
        """R: deterministic_profit == 1 − executable_price − fee (payoff theorem §2)."""
        from src.strategy.fees import phi as fee_phi, venue_fee_rate
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = _make_analysis_source_published(yes_ask="0.15", no_ask="0.80")
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert isinstance(decision, DeterministicEdgeDecision)
        fee_rate = venue_fee_rate()
        expected_fee = fee_phi(Decimal("1"), decision.executable_price, fee_rate)
        expected_profit = Decimal("1") - decision.executable_price - expected_fee
        assert decision.fee == expected_fee, f"fee mismatch: {decision.fee} != {expected_fee}"
        assert decision.deterministic_profit == expected_profit, (
            f"profit mismatch: {decision.deterministic_profit} != {expected_profit}"
        )

    # ── No-trade: typed outcome absent (data-gated) ───────────────────────────

    def test_absent_settlement_outcome_data_gates(self):
        """R: analysis has NO settlement_outcome → no_trade(RESOLUTION_TYPED_OUTCOME_UNAVAILABLE)
           + no_trade_events shadow row.
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        # No settlement_outcome attribute at all
        analysis = SimpleNamespace(metrics=_make_metrics())
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE

        rows = conn.execute(
            "SELECT reason, schema_compatibility, reason_detail FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE.value
        assert rows[0]["schema_compatibility"] == "current"
        assert "candidate_strategy_key=resolution_window_maker" in rows[0]["reason_detail"]

    def test_wrong_settlement_outcome_type_data_gates(self):
        """R: settlement_outcome is a raw string (not typed SettlementOutcome)
           → no_trade(RESOLUTION_TYPED_OUTCOME_UNAVAILABLE).
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            settlement_outcome="SOURCE_PUBLISHED_VENUE_UNRESOLVED",  # raw string, not enum
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE

    def test_ask_prices_absent_data_gates(self):
        """R: typed outcome present but yes_ask / no_ask absent → data-gated no_trade."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            settlement_outcome=SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
            # yes_ask / no_ask / token IDs intentionally absent
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE

    # ── No-trade: wrong settlement outcome value ──────────────────────────────

    def test_unresolved_outcome_writes_no_trade_resolution_disputed(self):
        """R: SettlementOutcome.UNRESOLVED → no_trade(RESOLUTION_DISPUTED)."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            settlement_outcome=SettlementOutcome.UNRESOLVED,
            yes_ask=Decimal("0.10"),
            no_ask=Decimal("0.05"),
            yes_token_id="0xYES",
            no_token_id="0xNO",
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_DISPUTED

        rows = conn.execute(
            "SELECT reason FROM no_trade_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["reason"] == NoTradeReason.RESOLUTION_DISPUTED.value

    def test_venue_resolved_win_outcome_writes_no_trade_resolution_disputed(self):
        """R: SettlementOutcome.VENUE_RESOLVED_WIN → no_trade(RESOLUTION_DISPUTED).
           Venue already settled; no discount window exists.
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = SimpleNamespace(
            metrics=_make_metrics(),
            settlement_outcome=SettlementOutcome.VENUE_RESOLVED_WIN,
            yes_ask=Decimal("0.10"),
            no_ask=Decimal("0.05"),
            yes_token_id="0xYES",
            no_token_id="0xNO",
        )
        ctx = _make_context(conn, analysis)

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_DISPUTED

    # ── No-trade: neither leg profitable ─────────────────────────────────────

    def test_no_profitable_leg_writes_no_trade(self, monkeypatch):
        """R: SOURCE_PUBLISHED_VENUE_UNRESOLVED but fee exhausts margin
           → no_trade(RESOLUTION_DISPUTED) — neither Π > 0.

        Note: with the standard phi formula (fee = p*(1-p)*rate), fee → 0 as p → 1,
        so realistic asks always yield Π > 0.  This test uses a patched venue_fee_rate
        returning 2.0 (200% fee rate) to exercise the guard branch.
        """
        conn = _make_conn()
        candidate = ResolutionWindowMaker()
        analysis = _make_analysis_source_published(yes_ask="0.50", no_ask="0.50")
        ctx = _make_context(conn, analysis)

        import src.strategy.candidates.resolution_window_maker as _rwm_mod
        monkeypatch.setattr(_rwm_mod, "venue_fee_rate", lambda: Decimal("2.0"))

        decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.RESOLUTION_DISPUTED

        rows = conn.execute("SELECT reason FROM no_trade_events").fetchall()
        assert len(rows) == 1

    # ── Neither path silently drops ───────────────────────────────────────────

    def test_neither_path_silently_drops(self):
        """R: one enter + one no_trade → exactly 1 row in each events table."""
        conn = _make_conn()
        candidate = ResolutionWindowMaker()

        # Enter path: typed outcome wired, profitable ask
        ctx1 = _make_context(conn, _make_analysis_source_published(yes_ask="0.10", no_ask="0.80"))
        d1 = candidate.evaluate(context=ctx1, conn=conn, decision_time=_DECISION_TIME)
        assert d1 is not None

        # No-trade path: absent typed outcome (data-gated)
        ctx2 = _make_context(
            conn,
            SimpleNamespace(metrics=_make_metrics()),
            observation_time="2026-06-15T11:00:00+00:00",
        )
        d2 = candidate.evaluate(context=ctx2, conn=conn, decision_time=_DECISION_TIME)
        assert d2 is not None

        assert conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 1

    # ── Metadata / registry ───────────────────────────────────────────────────

    def test_registry_is_not_runtime_live(self):
        assert get_strategy_profile("resolution_window_maker").is_runtime_live() is False

    def test_candidate_metadata_executable_alpha_true(self):
        assert ResolutionWindowMaker().metadata.executable_alpha is True
