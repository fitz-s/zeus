# Created: 2026-05-31
# Last reused or audited: 2026-06-25
# Authority basis: PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md (v2, critic-resolved) +
#   GOAL #36 expanded (continuous entry+exit, evidence-gated). RED-first relationship tests for the
#   continuous re-decision contract. These pin the cache (P1) + cheap-screen/enqueue (P2) API BEFORE
#   any live-core (event_reactor_adapter / reactor) edit. Submit-safe semantics; no direct orders.
"""Relationship tests R1/R2/R6/R7 for continuous re-decision (src.events.continuous_redecision).

The defect (commit 00b73fbbce): decisions are one-shot per FORECAST_SNAPSHOT_READY; price-move events
hard-reject NO_DIRECT_STALE_TRADE → between forecast cycles the system never re-evaluates. The fix:
each reactor cycle CHEAP-SCREENS every live (family, market) pair against a CACHED belief × a FRESH
price and ENQUEUES a synthetic re-decision event only when edge clears the bar (the full cert/kernel
then runs through the existing pending path). The cheap screen must NOT run the bootstrap-MC kernel
(p_posterior is the kernel's OUTPUT — it is read from the cache).

These are RELATIONSHIP tests: they assert the property that holds when a cached belief flows into the
screen against a price — not a single function's return. They are expected RED until
src.events.continuous_redecision is authored.
"""
from __future__ import annotations

import sqlite3
import inspect
from datetime import datetime

import pytest

from src.events.event_store import EventStore
from src.events.opportunity_event import make_opportunity_event
from src.state.db import init_schema

# Intentionally import the not-yet-authored module: RED until P1+P2 land.
cr = pytest.importorskip(
    "src.events.continuous_redecision",
    reason="continuous_redecision module not yet authored (P1+P2) — relationship contract is RED",
)


def _mem_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # The cache uses the canonical probability_trace_fact columns the screen reads.
    cr.ensure_belief_cache_schema(conn)
    return conn


def _event_for_refutation(*, causal_snapshot_id: str = "snap-1", price_marker: str = "same"):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Shanghai|2026-06-19|low|run-1",
        source="cycle-test",
        observed_at="2026-06-18T00:00:00+00:00",
        available_at="2026-06-18T00:00:00+00:00",
        received_at="2026-06-18T00:00:01+00:00",
        causal_snapshot_id=causal_snapshot_id,
        payload={
            "city": "Shanghai",
            "target_date": "2026-06-19",
            "metric": "low",
            "source_run_id": "run-1",
            "price_marker": price_marker,
        },
        priority=50,
    )


def _insert_terminal_no_value_regret(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    causal_snapshot_id: str = "snap-1",
) -> None:
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', 'TRADE_SCORE_NON_POSITIVE', 'NO_EDGE',
                  '2026-06-18T00:00:10+00:00', 'Shanghai', '2026-06-19', 'low',
                  'family-shanghai-low', ?, '2026-06-18T00:00:10+00:00', 1)
        """,
        ("regret-" + event_id, event_id, causal_snapshot_id),
    )


def test_recent_no_value_refutation_suppresses_same_evidence_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    prior = _event_for_refutation()
    store.insert_or_ignore(prior)
    _insert_terminal_no_value_regret(conn, event_id=prior.event_id)

    same = _event_for_refutation()
    assert cr.recent_no_value_event_refutation(
        conn,
        same,
        decision_time=datetime.fromisoformat("2026-06-18T00:05:00+00:00"),
    ) is not None

    fresh_payload = _event_for_refutation(causal_snapshot_id="snap-2", price_marker="changed")
    assert cr.recent_no_value_event_refutation(
        conn,
        fresh_payload,
        decision_time=datetime.fromisoformat("2026-06-18T00:05:00+00:00"),
    ) is None


def test_recent_no_value_refutation_ignores_operational_duplicate_summary():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    prior = _event_for_refutation()
    store.insert_or_ignore(prior)
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  '2026-06-18T00:00:10+00:00', 'Shanghai', '2026-06-19', 'low',
                  'family-shanghai-low', ?, '2026-06-18T00:00:10+00:00', 1)
        """,
        (
            "regret-" + prior.event_id,
            prior.event_id,
            (
                "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 other=22; "
                "best_rejected=24C buy_yes missing_reason="
                "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:condition_id=0xabc"
            ),
            prior.causal_snapshot_id,
        ),
    )

    same = _event_for_refutation()
    assert cr.recent_no_value_event_refutation(
        conn,
        same,
        decision_time=datetime.fromisoformat("2026-06-18T00:05:00+00:00"),
    ) is None


def _cache_yes_belief(conn, *, p_posterior_yes: float, recorded_at: str, snapshot_id: str = "snap1"):
    """Cache a 2-bin belief where the YES side of bin 'b30' has p_posterior_yes."""
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id=snapshot_id,
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        # p_posterior is YES-prob per bin; YES-prob of b30.
        p_posterior_vec=[0.001, p_posterior_yes],
        recorded_at=recorded_at,
    )


def test_latest_belief_reader_uses_bounded_sql_latest_window():
    conn = _mem_world()
    _cache_yes_belief(
        conn,
        p_posterior_yes=0.10,
        recorded_at="2026-06-01T11:00:00+00:00",
        snapshot_id="old-snap",
    )
    _cache_yes_belief(
        conn,
        p_posterior_yes=0.90,
        recorded_at="2026-06-01T11:30:00+00:00",
        snapshot_id="new-snap",
    )

    beliefs = cr._all_latest_beliefs(conn, scan_limit=1)

    assert len(beliefs) == 1
    assert beliefs[0].snapshot_id == "new-snap"
    src = inspect.getsource(cr._all_latest_beliefs)
    assert "ORDER BY recorded_at DESC" in src
    assert "LIMIT ?" in src
    assert "sorted(" not in src


def test_latest_belief_reader_filters_forecast_only_inadmissible_families():
    conn = _mem_world()
    _cache_yes_belief(
        conn,
        p_posterior_yes=0.99,
        recorded_at="2026-06-01T11:30:00+00:00",
        snapshot_id="closed-snap",
    )
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-02|high",
        city="Wuhan",
        target_date="2026-06-02",
        temperature_metric="high",
        snapshot_id="open-snap",
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, 0.99],
        recorded_at="2026-06-01T11:35:00+00:00",
    )

    beliefs = cr._all_latest_beliefs(
        conn,
        decision_time="2026-06-01T13:00:00+00:00",
        forecast_only_admissible=True,
    )

    assert {belief.target_date for belief in beliefs} == {"2026-06-02"}


def test_executable_price_reader_ignores_closed_or_non_active_snapshot_rows():
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            orderbook_top_bid REAL,
            orderbook_top_ask REAL,
            freshness_deadline TEXT,
            captured_at TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            outcome_label TEXT,
            enable_orderbook INTEGER,
            active INTEGER,
            closed INTEGER,
            accepting_orders INTEGER
        )
        """
    )
    trade.executemany(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, orderbook_top_bid, orderbook_top_ask,
            freshness_deadline, captured_at, selected_outcome_token_id,
            yes_token_id, no_token_id, outcome_label, enable_orderbook,
            active, closed, accepting_orders
        ) VALUES (?, 'cid-1', ?, ?, '2026-06-01T14:00:00+00:00', ?, 'yes-1',
                  'yes-1', 'no-1', 'YES', ?, ?, ?, ?)
        """,
        [
            ("closed-latest", 0.39, 0.40, "2026-06-01T13:00:00+00:00", 1, 0, 1, 0),
            ("open-older", 0.59, 0.60, "2026-06-01T12:59:00+00:00", 1, 1, 0, 1),
        ],
    )

    quotes = cr.read_freshest_executable_prices(trade, condition_ids={"cid-1"})

    assert quotes[("cid-1", "buy_yes")].price == 0.60


def test_executable_price_reader_preserves_native_min_tick_size():
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            orderbook_top_bid REAL,
            orderbook_top_ask REAL,
            freshness_deadline TEXT,
            captured_at TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            outcome_label TEXT,
            min_tick_size TEXT,
            enable_orderbook INTEGER,
            active INTEGER,
            closed INTEGER,
            accepting_orders INTEGER
        )
        """
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, orderbook_top_bid, orderbook_top_ask,
            freshness_deadline, captured_at, selected_outcome_token_id,
            yes_token_id, no_token_id, outcome_label, min_tick_size,
            enable_orderbook, active, closed, accepting_orders
        ) VALUES ('tail-yes', 'cid-1', 0.001, 0.002,
                  '2026-06-01T14:00:00+00:00',
                  '2026-06-01T13:00:00+00:00', 'yes-1',
                  'yes-1', 'no-1', 'YES', '0.001', 1, 1, 0, 1)
        """
    )

    quotes = cr.read_freshest_executable_prices(trade, condition_ids={"cid-1"})

    assert quotes[("cid-1", "buy_yes")].price == 0.002
    assert quotes[("cid-1", "buy_yes")].tick_size == pytest.approx(0.001)


def test_executable_price_reader_uses_fresh_feasibility_quote_when_snapshot_stale():
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            orderbook_top_bid REAL,
            orderbook_top_ask REAL,
            freshness_deadline TEXT,
            captured_at TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            outcome_label TEXT,
            min_tick_size TEXT,
            enable_orderbook INTEGER,
            active INTEGER,
            closed INTEGER,
            accepting_orders INTEGER
        )
        """
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, orderbook_top_bid, orderbook_top_ask,
            freshness_deadline, captured_at, selected_outcome_token_id,
            yes_token_id, no_token_id, outcome_label, min_tick_size,
            enable_orderbook, active, closed, accepting_orders
        ) VALUES ('stale-no', 'cid-1', 0.70, 0.71,
                  '2026-06-01T12:00:00+00:00',
                  '2026-06-01T11:59:00+00:00', 'no-1',
                  'yes-1', 'no-1', 'NO', '0.001', 1, 1, 0, 1)
        """
    )
    trade.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            outcome_label TEXT,
            direction TEXT,
            quote_seen_at TEXT,
            created_at TEXT,
            best_bid_before REAL,
            best_ask_before REAL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            condition_id, token_id, outcome_label, direction, quote_seen_at,
            created_at, best_bid_before, best_ask_before
        ) VALUES ('cid-1', 'no-1', 'NO', 'buy_no',
                  '2026-06-01T13:00:00+00:00',
                  '2026-06-01T13:00:02+00:00', 0.74, 0.75)
        """
    )

    asks = cr.read_freshest_executable_prices(trade, condition_ids={"cid-1"})
    bids = cr.read_freshest_resting_best_bids(trade, condition_ids={"cid-1"})

    assert asks[("cid-1", "buy_no")].price == pytest.approx(0.75)
    assert asks[("cid-1", "buy_no")].freshness_deadline == "2026-06-01T13:01:30+00:00"
    assert bids[("cid-1", "buy_no")].price == pytest.approx(0.74)
    assert bids[("cid-1", "buy_no")].freshness_deadline == "2026-06-01T13:01:30+00:00"


def test_feasibility_quote_overlay_rejects_crossed_books():
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            outcome_label TEXT,
            direction TEXT,
            quote_seen_at TEXT,
            created_at TEXT,
            best_bid_before REAL,
            best_ask_before REAL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            condition_id, token_id, outcome_label, direction, quote_seen_at,
            created_at, best_bid_before, best_ask_before
        ) VALUES ('cid-1', 'no-1', 'NO', 'buy_no',
                  '2026-06-01T13:00:00+00:00',
                  '2026-06-01T13:00:02+00:00', 0.80, 0.75)
        """
    )

    assert cr.read_freshest_executable_prices(trade, condition_ids={"cid-1"}) == {}
    assert cr.read_freshest_resting_best_bids(trade, condition_ids={"cid-1"}) == {}


# ---------------------------------------------------------------------------
# R1 — entry: cached belief + open market + FRESH price, edge > min → enqueue
# ---------------------------------------------------------------------------
def test_R1_fresh_price_positive_edge_enqueues_redecision():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    # Market underprices YES: best YES cost 0.70 → edge = 0.99 - 0.70 - cost_fee ≈ +0.27.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",  # before freshness_deadline → FRESH
        price_lookup=price_lookup,
        min_edge=0.01,
    )
    keys = {(e.family_id, e.bin_label, e.direction) for e in enqueued}
    assert ("Wuhan|2026-06-01|high", "b30", "buy_yes") in keys
    assert all(e.event_type == "EDLI_REDECISION_PENDING" for e in enqueued)


def test_entry_redecision_requires_spine_members_on_latest_posterior_cycle():
    """Cheap q/price edge is not enough; the full q-kernel needs same-cycle raw-model members."""

    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    beliefs = cr._all_latest_beliefs(conn)
    redecisions = [
        cr.EnqueuedRedecision(
            family_id="Wuhan|2026-06-01|high",
            bin_label="b30",
            direction="buy_yes",
            edge=0.20,
        )
    ]
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT
        )
        """
    )
    forecasts.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            forecast_value_c REAL
        )
        """
    )
    forecasts.executemany(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, runtime_layer
        ) VALUES (?, 'Wuhan', '2026-06-01', 'high', ?, ?, ?, 'live')
        """,
        [
            (1, "2026-05-30T00:00:00+00:00", "2026-05-30T01:00:00+00:00", "2026-05-30T01:05:00+00:00"),
            (2, "2026-05-31T06:00:00+00:00", "2026-05-31T07:00:00+00:00", "2026-05-31T07:05:00+00:00"),
        ],
    )
    forecasts.executemany(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time, source_available_at, forecast_value_c
        ) VALUES (?, 'Wuhan', '2026-06-01', 'high', ?, ?, ?)
        """,
        [
            ("older-a", "2026-05-30T00:00:00+00:00", "2026-05-30T01:00:00+00:00", 31.0),
            ("older-b", "2026-05-30T06:00:00+00:00", "2026-05-30T07:00:00+00:00", 32.0),
            ("older-c", "2026-05-30T12:00:00+00:00", "2026-05-30T13:00:00+00:00", 33.0),
            ("latest-a", "2026-05-31T00:00:00+00:00", "2026-05-31T01:00:00+00:00", 31.0),
            ("latest-b", "2026-05-31T06:00:00+00:00", "2026-05-31T07:00:00+00:00", 32.0),
        ],
    )

    assert cr.filter_redecisions_with_spine_members(
        forecasts,
        redecisions,
        beliefs=beliefs,
        decision_time="2026-05-31T08:00:00+00:00",
    ) == []

    forecasts.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time, source_available_at, forecast_value_c
        ) VALUES ('latest-c', 'Wuhan', '2026-06-01', 'high',
                  '2026-05-31T12:00:00+00:00', '2026-05-31T07:30:00+00:00', 33.0)
        """
    )

    assert cr.filter_redecisions_with_spine_members(
        forecasts,
        redecisions,
        beliefs=beliefs,
        decision_time="2026-05-31T08:00:00+00:00",
    ) == redecisions


def test_entry_redecision_reader_treats_18z_runtime_layer_live_as_live():
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, runtime_layer
        ) VALUES (1, 'Wuhan', '2026-06-01', 'high',
                  '2026-05-31T18:00:00+00:00',
                  '2026-05-31T19:00:00+00:00',
                  '2026-05-31T19:05:00+00:00',
                  'live')
        """
    )

    assert cr._latest_posterior_source_cycle_for_family(
        forecasts,
        city="Wuhan",
        target_date="2026-06-01",
        metric="high",
        decision_time="2026-05-31T20:00:00+00:00",
    ) == "2026-05-31T18:00:00+00:00"


def test_entry_redecision_latest_cycle_ignores_deprecated_aifs_residue():
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT,
            source_id TEXT,
            posterior_method TEXT
        )
        """
    )
    forecasts.executemany(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, runtime_layer,
            source_id, posterior_method
        ) VALUES (?, 'Wuhan', '2026-06-01', 'high', ?, ?, ?, 'live', ?, ?)
        """,
        [
            (
                1,
                "2026-05-31T12:00:00+00:00",
                "2026-05-31T13:00:00+00:00",
                "2026-05-31T13:05:00+00:00",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                "the_path_bayes_precision_fusion",
            ),
            (
                2,
                "2026-05-31T18:00:00+00:00",
                "2026-05-31T19:00:00+00:00",
                "2026-05-31T19:05:00+00:00",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            ),
        ],
    )

    assert cr._latest_posterior_source_cycle_for_family(
        forecasts,
        city="Wuhan",
        target_date="2026-06-01",
        metric="high",
        decision_time="2026-05-31T20:00:00+00:00",
    ) == "2026-05-31T12:00:00+00:00"


def test_entry_redecision_latest_cycle_accepts_live_bpf_with_non_source_method():
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT,
            source_id TEXT,
            posterior_method TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, runtime_layer,
            source_id, posterior_method
        ) VALUES (1, 'Wuhan', '2026-06-01', 'high',
                  '2026-05-31T18:00:00+00:00',
                  '2026-05-31T19:00:00+00:00',
                  '2026-05-31T19:05:00+00:00',
                  'live',
                  'openmeteo_ecmwf_ifs9_bayes_fusion',
                  'the_path_bayes_precision_fusion')
        """
    )

    assert cr._latest_posterior_source_cycle_for_family(
        forecasts,
        city="Wuhan",
        target_date="2026-06-01",
        metric="high",
        decision_time="2026-05-31T20:00:00+00:00",
    ) == "2026-05-31T18:00:00+00:00"


def test_entry_redecision_ignores_non_live_posterior_cycle():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    beliefs = cr._all_latest_beliefs(conn)
    redecisions = [
        cr.EnqueuedRedecision(
            family_id="Wuhan|2026-06-01|high",
            bin_label="b30",
            direction="buy_yes",
            edge=0.20,
        )
    ]
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT
        )
        """
    )
    forecasts.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            forecast_value_c REAL
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, runtime_layer
        ) VALUES (1, 'Wuhan', '2026-06-01', 'high',
                  '2026-05-31T00:00:00+00:00',
                  '2026-05-31T01:00:00+00:00',
                  '2026-05-31T01:05:00+00:00',
                  NULL)
        """
    )
    forecasts.executemany(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time, source_available_at, forecast_value_c
        ) VALUES (?, 'Wuhan', '2026-06-01', 'high', ?, ?, ?)
        """,
        [
            ("latest-a", "2026-05-31T00:00:00+00:00", "2026-05-31T01:00:00+00:00", 31.0),
            ("latest-b", "2026-05-31T06:00:00+00:00", "2026-05-31T07:00:00+00:00", 32.0),
            ("latest-c", "2026-05-31T12:00:00+00:00", "2026-05-31T07:30:00+00:00", 33.0),
        ],
    )

    assert cr.filter_redecisions_with_spine_members(
        forecasts,
        redecisions,
        beliefs=beliefs,
        decision_time="2026-05-31T08:00:00+00:00",
    ) == []


def test_R1b_buy_no_positive_edge_enqueues_redecision():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.05, recorded_at="2026-05-31T00:00:00+00:00")
    # YES-prob is 0.05, so NO-prob is 0.95. NO cost 0.70 leaves strong edge.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
    )
    keys = {(e.family_id, e.bin_label, e.direction) for e in enqueued}
    assert ("Wuhan|2026-06-01|high", "b30", "buy_no") in keys
    assert all(e.event_type == "EDLI_REDECISION_PENDING" for e in enqueued)


# ---------------------------------------------------------------------------
# R2 — sub-edge: price move too small → NO enqueue, NO regret
# ---------------------------------------------------------------------------
def test_R2_sub_edge_does_not_enqueue():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.72, recorded_at="2026-05-31T00:00:00+00:00")
    # YES cost 0.71 → edge ≈ +0.01 minus fee < min_edge → no action.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.71, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=price_lookup, min_edge=0.05
    )
    assert enqueued == []


def test_entry_screen_uses_conservative_q_lcb_not_point_posterior():
    conn = _mem_world()
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id="snap1",
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, 0.99],
        q_lcb_yes_vec=[0.001, 0.60],
        q_lcb_no_vec=[0.999, 0.01],
        recorded_at="2026-05-31T00:00:00+00:00",
    )
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }

    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
    )

    assert enqueued == [], "point posterior edge is not confirmed trading value without q_lcb edge"


def test_entry_screen_requires_robust_c95_value_not_raw_top_quote_edge():
    conn = _mem_world()
    c95 = cr._entry_screen_c95_cost(0.50)
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id="snap1",
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, c95 + 0.005],
        q_lcb_yes_vec=[0.001, c95 + 0.005],
        q_lcb_no_vec=[0.999, 0.01],
        recorded_at="2026-05-31T00:00:00+00:00",
    )
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.50,
            freshness_deadline="2026-05-31T01:00:00+00:00",
        ),
    }

    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
    ) == []

    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id="snap2",
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, c95 + 0.02],
        q_lcb_yes_vec=[0.001, c95 + 0.02],
        q_lcb_no_vec=[0.999, 0.01],
        recorded_at="2026-05-31T00:10:00+00:00",
    )
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
    )


def test_entry_screen_uses_native_tick_for_low_price_yes_tail():
    conn = _mem_world()
    cr.cache_belief(
        conn,
        family_id="Kuala Lumpur|2026-06-26|high",
        city="Kuala Lumpur",
        target_date="2026-06-26",
        snapshot_id="tail-snap",
        calibrator_model_hash="identity",
        bin_labels=["28C"],
        p_posterior_vec=[0.006],
        q_lcb_yes_vec=[0.006],
        q_lcb_no_vec=[0.994],
        recorded_at="2026-06-25T11:30:00+00:00",
    )
    price_lookup = {
        ("Kuala Lumpur|2026-06-26|high", "28C", "buy_yes"): cr.PriceQuote(
            price=0.002,
            freshness_deadline="2026-06-25T11:45:00+00:00",
            tick_size=0.001,
        ),
    }

    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-25T11:35:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.002,
    )

    assert [(e.bin_label, e.direction) for e in enqueued] == [("28C", "buy_yes")]
    assert cr._entry_screen_robust_trade_score(
        q_posterior=0.006,
        q_lcb_5pct=0.006,
        price=0.002,
        tick_size=0.01,
    ) < 0.0


def test_latest_beliefs_dedupe_dynamic_family_hash_by_stable_market_identity():
    conn = _mem_world()
    label = "Will the lowest temperature in Shanghai be 24°C on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_old_hash",
        city="Shanghai",
        target_date="2026-06-19",
        snapshot_id="old-snap",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.99],
        q_lcb_yes_vec=[0.99],
        q_lcb_no_vec=[0.01],
        recorded_at="2026-06-17T23:00:00+00:00",
    )
    cr.cache_belief(
        conn,
        family_id="edli_family_new_hash",
        city="Shanghai",
        target_date="2026-06-19",
        snapshot_id="new-snap",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.10],
        q_lcb_yes_vec=[0.10],
        q_lcb_no_vec=[0.05],
        recorded_at="2026-06-17T23:10:00+00:00",
    )
    stale_old_hash_price = {
        ("edli_family_old_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.20,
            freshness_deadline="2026-06-17T23:30:00+00:00",
        ),
    }

    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T23:20:00+00:00",
        price_lookup=stale_old_hash_price,
        min_edge=0.01,
    ) == []


def test_entry_screen_blocks_after_recent_full_economics_negative_until_price_improves():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00")
    key = ("Wuhan|2026-06-01|high", "b30", "buy_yes")
    rejection = {
        key: cr.FullEconomicsReject(
            execution_price=cr._all_in_cost(0.70),
            q_lcb_5pct=0.90,
            trade_score=-0.01,
            created_at="2026-05-31T00:20:00+00:00",
        )
    }

    same_price = {
        key: cr.PriceQuote(price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=same_price,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    ) == []

    improved_price = {
        key: cr.PriceQuote(price=0.67, freshness_deadline="2026-05-31T01:00:00+00:00"),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=improved_price,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    )
    assert len(enqueued) == 1


def test_entry_screen_backoff_compares_all_in_cost_basis():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00")
    key = ("Wuhan|2026-06-01|high", "b30", "buy_yes")
    rejection = {
        key: cr.FullEconomicsReject(
            execution_price=0.7105,
            q_lcb_5pct=0.90,
            trade_score=-0.01,
            created_at="2026-05-31T00:20:00+00:00",
        )
    }

    one_tick_raw_improvement = {
        key: cr.PriceQuote(price=0.69, freshness_deadline="2026-05-31T01:00:00+00:00"),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=one_tick_raw_improvement,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    ) == []


def test_entry_screen_backoff_uses_stable_market_key_when_family_id_changes():
    conn = _mem_world()
    label = "Will the lowest temperature in Shanghai be 24°C on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_new_hash",
        city="Shanghai",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-new",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.90],
        q_lcb_yes_vec=[0.795],
        q_lcb_no_vec=[0.01],
        recorded_at="2026-06-17T22:32:00+00:00",
    )
    stable_key = ("Shanghai", "2026-06-19", "low", label, "buy_yes")
    rejection = {
        stable_key: cr.FullEconomicsReject(
            execution_price=cr._all_in_cost(0.34),
            q_lcb_5pct=0.795,
            trade_score=-0.14,
            created_at="2026-06-17T22:31:00+00:00",
        )
    }

    same_economics = {
        ("edli_family_new_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.34,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=same_economics,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    ) == []

    improved_economics = {
        ("edli_family_new_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.30,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=improved_economics,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    )


def test_recent_full_economics_rejections_publish_stable_market_key():
    conn = _mem_world()
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    label = "Will the lowest temperature in Shanghai be 24°C on June 19?"
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_lcb_5pct,
            trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_old_hash",
            "Shanghai",
            "2026-06-19",
            "low",
            label,
            "buy_yes",
            "TRADE_SCORE",
            "TRADE_SCORE_NON_POSITIVE",
            0.34,
            0.795,
            -0.14,
            "2026-06-17T22:31:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    stable_key = ("Shanghai", "2026-06-19", "low", label, "buy_yes")
    legacy_key = ("edli_family_old_hash", label, "buy_yes")
    assert stable_key in rejections
    assert legacy_key in rejections
    assert rejections[stable_key].trade_score == -0.14
    assert rejections[stable_key].rejection_reason == "TRADE_SCORE_NON_POSITIVE"
    assert ("family", "Shanghai", "2026-06-19", "low") in rejections


def test_recent_full_economics_rejections_skip_impossible_q_lcb_rows():
    conn = _mem_world()
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_live REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    label = "Will the lowest temperature in Shanghai be 24°C on June 19?"
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_live,
            q_lcb_5pct, trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_old_hash",
            "Shanghai",
            "2026-06-19",
            "low",
            label,
            "buy_yes",
            "TRADE_SCORE",
            "TRADE_SCORE_NON_POSITIVE",
            0.34,
            0.20839375296228654,
            0.7953671556018411,
            -0.14,
            "2026-06-17T22:31:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    assert rejections == {}


def test_all_candidates_rejected_row_is_family_backoff_not_candidate_backoff():
    conn = _mem_world()
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_lcb_5pct,
            trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_london_hash",
            "London",
            "2026-06-18",
            "low",
            None,
            None,
            "TRADE_SCORE",
            "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22; best_rejected=17C buy_yes",
            None,
            None,
            None,
            "2026-06-17T22:31:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    assert ("family", "London", "2026-06-18", "low") in rejections
    assert ("edli_family_london_hash", "", "") not in rejections
    assert not any(
        len(key) == 5 and key[:3] == ("London", "2026-06-18", "low")
        for key in rejections
    )


def test_operational_all_candidates_rejected_row_is_not_family_no_value_backoff():
    conn = _mem_world()
    label = "Will the highest temperature in Milan be 37°C on June 27?"
    cr.cache_belief(
        conn,
        family_id="edli_family_milan_hash",
        city="Milan",
        target_date="2026-06-27",
        temperature_metric="high",
        snapshot_id="snap-milan",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.25],
        q_lcb_yes_vec=[0.14],
        q_lcb_no_vec=[0.60],
        recorded_at="2026-06-26T13:00:00+00:00",
    )
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_lcb_5pct,
            trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_milan_hash",
            "Milan",
            "2026-06-27",
            "high",
            None,
            None,
            "TRADE_SCORE",
            (
                "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 other=22; "
                "best_rejected=Will the highest temperature in Milan be 37°C on June 27? "
                "buy_yes reason_class=other missing_reason="
                "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:condition_id=0xabc"
            ),
            None,
            None,
            None,
            "2026-06-26T13:01:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    assert ("family", "Milan", "2026-06-27", "high") not in rejections
    price_lookup = {
        ("edli_family_milan_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.10,
            freshness_deadline="2026-06-26T13:10:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-26T13:02:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
        recent_full_economics_rejections=rejections,
    )


def test_candidate_rejected_row_is_candidate_backoff_not_family_backoff():
    conn = _mem_world()
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_lcb_5pct,
            trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_shanghai_hash",
            "Shanghai",
            "2026-06-25",
            "high",
            "Will the highest temperature in Shanghai be 25°C on June 25?",
            "buy_yes",
            "TRADE_SCORE",
            "EVENT_BOUND_CANDIDATE_REJECTED:OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:position_id=held-1:candidate_id=candidate-buy-yes",
            0.6712,
            0.9616,
            0.4327,
            "2026-06-25T04:19:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    stable_key = (
        "Shanghai",
        "2026-06-25",
        "high",
        "Will the highest temperature in Shanghai be 25°C on June 25?",
        "buy_yes",
    )
    assert stable_key in rejections
    assert ("edli_family_shanghai_hash", stable_key[3], "buy_yes") in rejections
    assert ("family", "Shanghai", "2026-06-25", "high") not in rejections
    assert rejections[stable_key].execution_price == 0.6712
    assert rejections[stable_key].q_lcb_5pct == 0.9616
    assert rejections[stable_key].trade_score == 0.4327


def test_certificate_build_failure_without_family_id_is_family_backoff():
    conn = _mem_world()
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_lcb_5pct,
            trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None,
            "Hong Kong",
            "2026-06-19",
            "low",
            None,
            None,
            "EXECUTOR_EXPRESSIBILITY",
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:max_parent_source_available_at after decision_time",
            None,
            None,
            None,
            "2026-06-18T06:00:51+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)

    assert ("family", "Hong Kong", "2026-06-19", "low") in rejections
    assert not any(len(key) == 3 for key in rejections)
    assert not any(
        len(key) == 5 and key[:3] == ("Hong Kong", "2026-06-19", "low")
        for key in rejections
    )


def test_entry_screen_blocks_family_after_certificate_build_failure_cooldown():
    conn = _mem_world()
    label = "Will the lowest temperature in Hong Kong be 27°C or lower on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_hong_kong_low_hash",
        city="Hong Kong",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-hk",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.90],
        q_lcb_yes_vec=[0.86],
        q_lcb_no_vec=[0.01],
        recorded_at="2026-06-18T06:01:00+00:00",
    )
    family_key = ("family", "Hong Kong", "2026-06-19", "low")
    rejections = {
        family_key: cr.FullEconomicsReject(
            execution_price=None,
            q_lcb_5pct=None,
            trade_score=None,
            created_at="2026-06-18T06:00:51+00:00",
            rejection_reason=(
                "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:"
                "max_parent_source_available_at after decision_time"
            ),
        )
    }
    price_lookup = {
        ("edli_family_hong_kong_low_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.50,
            freshness_deadline="2026-06-18T06:40:00+00:00",
        ),
    }

    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-18T06:02:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
        recent_full_economics_rejections=rejections,
    ) == []
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-18T06:31:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
        recent_full_economics_rejections=rejections,
    )


def test_execution_quality_rejection_cools_entry_until_economics_improve():
    conn = _mem_world()
    label = "Will the highest temperature in Shenzhen be 35°C on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_shenzhen_hash",
        city="Shenzhen",
        target_date="2026-06-19",
        temperature_metric="high",
        snapshot_id="snap-shenzhen",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.12],
        q_lcb_yes_vec=[0.08],
        q_lcb_no_vec=[0.867],
        recorded_at="2026-06-17T22:32:00+00:00",
    )
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_live REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_live,
            q_lcb_5pct, trade_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_shenzhen_hash",
            "Shenzhen",
            "2026-06-19",
            "high",
            label,
            "buy_no",
            "EXECUTION_RECEIPT",
            "TAKER_QUALITY_PROOF_NOT_PASSED:edge=0.01:incremental_profit=0:confidence=0.99",
            0.75,
            0.869,
            0.867,
            0.116,
            "2026-06-17T22:31:00+00:00",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)
    stable_key = ("Shenzhen", "2026-06-19", "high", label, "buy_no")
    assert stable_key in rejections
    same_economics = {
        ("edli_family_shenzhen_hash", label, "buy_no"): cr.PriceQuote(
            price=0.74,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=same_economics,
        min_edge=0.01,
        recent_full_economics_rejections=rejections,
    ) == []

    improved_price = {
        ("edli_family_shenzhen_hash", label, "buy_no"): cr.PriceQuote(
            price=0.70,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=improved_price,
        min_edge=0.01,
        recent_full_economics_rejections=rejections,
    )


def test_recapture_edge_reversal_cools_entry_until_economics_improve():
    conn = _mem_world()
    label = "Will the highest temperature in Wellington be 9°C on June 27?"
    cr.cache_belief(
        conn,
        family_id="edli_family_wellington_hash",
        city="Wellington",
        target_date="2026-06-27",
        temperature_metric="high",
        snapshot_id="snap-wellington",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.0455121228],
        q_lcb_yes_vec=[0.0004764563],
        q_lcb_no_vec=[0.90],
        recorded_at="2026-06-26T10:39:00+00:00",
    )
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            c_fee_adjusted REAL,
            q_live REAL,
            q_lcb_5pct REAL,
            trade_score REAL,
            created_at TEXT,
            executable_snapshot_id TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            family_id, city, target_date, metric, bin_label, direction,
            rejection_stage, rejection_reason, c_fee_adjusted, q_live,
            q_lcb_5pct, trade_score, created_at, executable_snapshot_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edli_family_wellington_hash",
            "Wellington",
            "2026-06-27",
            "high",
            label,
            "buy_yes",
            "TRADE_SCORE",
            "SUBMIT_ABORTED_EDGE_REVERSED:recaptured robust marginal utility nonpositive",
            0.005,
            0.0455121228,
            0.0004764563,
            -0.0055224416,
            "2026-06-26T10:39:51+00:00",
            "ems2-stale-book",
        ),
    )

    rejections = cr.read_recent_full_economics_rejections(conn, lookback_hours=24 * 365)
    stable_key = ("Wellington", "2026-06-27", "high", label, "buy_yes")
    assert stable_key in rejections
    same_economics = {
        ("edli_family_wellington_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.005,
            freshness_deadline="2026-06-26T10:50:00+00:00",
            tick_size=0.001,
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-26T10:40:00+00:00",
        price_lookup=same_economics,
        min_edge=-0.01,
        recent_full_economics_rejections=rejections,
    ) == []

    improved_belief = {
        stable_key: cr.FullEconomicsReject(
            execution_price=0.010,
            q_lcb_5pct=0.0001,
            trade_score=-0.0055224416,
            created_at="2026-06-26T10:39:51+00:00",
            rejection_reason="SUBMIT_ABORTED_EDGE_REVERSED:old",
        )
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-26T10:40:00+00:00",
        price_lookup=same_economics,
        min_edge=-0.01,
        recent_full_economics_rejections=improved_belief,
    )


def test_entry_screen_blocks_fdr_refuted_candidate_despite_positive_trade_score():
    conn = _mem_world()
    label = "Will the lowest temperature in Paris be 22°C on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_paris_hash",
        city="Paris",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-paris",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.20],
        q_lcb_yes_vec=[0.10],
        q_lcb_no_vec=[0.78],
        recorded_at="2026-06-17T22:32:00+00:00",
    )
    stable_key = ("Paris", "2026-06-19", "low", label, "buy_no")
    rejection = {
        stable_key: cr.FullEconomicsReject(
            execution_price=0.60,
            q_lcb_5pct=0.78,
            trade_score=0.018,
            created_at="2026-06-17T22:31:00+00:00",
            rejection_reason="FDR_REJECTED",
        )
    }

    same_economics = {
        ("edli_family_paris_hash", label, "buy_no"): cr.PriceQuote(
            price=0.60,
            freshness_deadline="2026-06-17T23:30:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=same_economics,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    ) == []


def test_entry_screen_family_refutation_cooldown_blocks_family_then_releases():
    conn = _mem_world()
    label = "Will the lowest temperature in Paris be 22°C on June 19?"
    cr.cache_belief(
        conn,
        family_id="edli_family_paris_hash",
        city="Paris",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-paris",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.20],
        q_lcb_yes_vec=[0.10],
        q_lcb_no_vec=[0.78],
        recorded_at="2026-06-17T22:32:00+00:00",
    )
    family_key = ("family", "Paris", "2026-06-19", "low")
    rejection = {
        family_key: cr.FullEconomicsReject(
            execution_price=None,
            q_lcb_5pct=None,
            trade_score=0.0,
            created_at="2026-06-17T22:31:00+00:00",
            rejection_reason="EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22",
        )
    }
    price = {
        ("edli_family_paris_hash", label, "buy_no"): cr.PriceQuote(
            price=0.60,
            freshness_deadline="2026-06-17T23:30:00+00:00",
        ),
    }

    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:32:30+00:00",
        price_lookup=price,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    ) == []
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T23:01:01+00:00",
        price_lookup=price,
        min_edge=0.01,
        recent_full_economics_rejections=rejection,
    )


def test_entry_screen_acted_state_uses_stable_market_key_when_family_id_changes():
    conn = _mem_world()
    label = "Will the lowest temperature in Shanghai be 24°C on June 19?"
    acted: dict = {}
    cr.cache_belief(
        conn,
        family_id="edli_family_old_hash",
        city="Shanghai",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-old",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.90],
        q_lcb_yes_vec=[0.795],
        q_lcb_no_vec=[0.01],
        recorded_at="2026-06-17T22:30:00+00:00",
    )
    first_price = {
        ("edli_family_old_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.34,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:31:00+00:00",
        price_lookup=first_price,
        min_edge=0.01,
        acted_state=acted,
    )

    cr.cache_belief(
        conn,
        family_id="edli_family_new_hash",
        city="Shanghai",
        target_date="2026-06-19",
        temperature_metric="low",
        snapshot_id="snap-new",
        calibrator_model_hash="identity",
        bin_labels=[label],
        p_posterior_vec=[0.90],
        q_lcb_yes_vec=[0.795],
        q_lcb_no_vec=[0.01],
        recorded_at="2026-06-17T22:32:00+00:00",
    )
    same_price = {
        ("edli_family_new_hash", label, "buy_yes"): cr.PriceQuote(
            price=0.34,
            freshness_deadline="2026-06-17T23:00:00+00:00",
        ),
    }
    assert cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-06-17T22:33:00+00:00",
        price_lookup=same_price,
        min_edge=0.01,
        acted_state=acted,
    ) == []


def test_live_writer_belief_without_q_lcb_does_not_confirm_entry_value():
    conn = _mem_world()
    cr.write_belief_row(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id="snap1",
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, 0.99],
        recorded_at="2026-05-31T00:00:00+00:00",
    )
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }

    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",
        price_lookup=price_lookup,
        min_edge=0.01,
    )

    assert enqueued == []


# ---------------------------------------------------------------------------
# R6 — continuity (one-shot killer): cycle 2 price improves → second enqueue
# ---------------------------------------------------------------------------
def test_R6_second_cycle_price_improvement_reenqueues():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00")
    acted: dict = {}  # in-memory dedup state, held across cycles (the reactor owns this live).
    # Cycle 1: YES cost 0.85 clears the robust c95 screen → enqueue.
    q1 = {("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(price=0.85, freshness_deadline="2026-05-31T01:00:00+00:00")}
    e1 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=q1, min_edge=0.01, acted_state=acted)
    assert len(e1) == 1
    # Cycle 2: price improves to 0.80 → edge ≈ +0.09, materially better → re-enqueue (NOT deduped away).
    q2 = {("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(price=0.80, freshness_deadline="2026-05-31T02:00:00+00:00")}
    e2 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:30:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert len(e2) == 1, "price improved past delta → continuous re-decision must fire again (one-shot killed)"
    # Cycle 3: price unchanged (same edge) → deduped away (NOT a re-fire on noise).
    e3 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:40:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert e3 == [], "unchanged edge must not re-fire (act-once-per-edge)"


def test_R6_buy_no_second_cycle_price_improvement_reenqueues():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.10, recorded_at="2026-05-31T00:00:00+00:00")
    acted: dict = {}
    q1 = {("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(price=0.87, freshness_deadline="2026-05-31T01:00:00+00:00")}
    e1 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=q1, min_edge=0.01, acted_state=acted)
    assert len(e1) == 1
    q2 = {("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(price=0.78, freshness_deadline="2026-05-31T02:00:00+00:00")}
    e2 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:30:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert len(e2) == 1
    e3 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:40:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert e3 == []


# ---------------------------------------------------------------------------
# R7 — stale price (critic SEV-1): cached belief + STALE price → NO enqueue
# ---------------------------------------------------------------------------
def test_R7_stale_price_does_not_enqueue_phantom_edge():
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    # Price looks like positive edge BUT its freshness_deadline is in the past at decision_time.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T00:10:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",  # AFTER freshness_deadline → STALE
        price_lookup=price_lookup,
        min_edge=0.01,
    )
    assert enqueued == [], "stale price must not produce a phantom-edge re-decision"


# ---------------------------------------------------------------------------
# R3 — no cached belief → nothing enqueued (provenance: no belief = no decision)
# ---------------------------------------------------------------------------
def test_R3_no_belief_enqueues_nothing():
    conn = _mem_world()  # empty cache
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_yes"): cr.PriceQuote(
            price=0.50, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=price_lookup, min_edge=0.01
    )
    assert enqueued == []


# ---------------------------------------------------------------------------
# R8 — exit fires on BELIEF reversal (evidence), NOT on a bare price move
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not hasattr(cr, "screen_exit"),
    reason="screen_exit deleted in W3 (#133); references removed API — rewrite pending",
)
def test_R8_exit_on_belief_reversal_not_price_noise():
    conn = _mem_world()
    # Entered a NO position on bin b30 when belief said NO=0.90.
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")

    # Case A — belief later collapses to NO=0.60 (Δ0.30, material) → exit (reversal).
    _cache_yes_belief(conn, p_posterior_yes=0.60, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    exit_a = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_yes",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert exit_a is not None and exit_a.reason == "BELIEF_EDGE_REVERSAL"

    # Case B — belief barely moves to NO=0.86 (Δ0.04, price-noise scale) → HOLD (no exit).
    conn2 = _mem_world()
    _cache_yes_belief(conn2, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_yes_belief(conn2, p_posterior_yes=0.86, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    exit_b = cr.screen_exit(
        conn2, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_yes",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert exit_b is None, "a non-material belief move (price-noise scale) must NOT trigger exit"
