# Created: 2026-07-08
"""Golden-row tests for src.reconcile.chain_truth's snapshot contract,
including the fill-dedup CTE dedup behaviour (fill_dedup.py's core claim:
a bare SUM over-counts a fill re-observed across lifecycle revisions)."""
from __future__ import annotations

from src.reconcile.chain_truth import load_chain_truth_snapshot
from tests.reconcile.conftest import insert_order_fact, insert_trade_fact


def test_no_facts_returns_default_command_facts(trades_conn, forecasts_conn):
    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-missing")
    assert facts.canonical_filled_size == 0.0
    assert facts.has_fills is False
    assert facts.heartbeat_cancel_suspected is False


def test_dedups_multi_revision_fill_to_one_canonical_row(trades_conn, forecasts_conn):
    # Same real fill (trade_id=t1) observed at 3 lifecycle stages. A bare
    # SUM(filled_size) would triple-count to 30; the canonical CTE must
    # collapse to the single CONFIRMED/highest-local_sequence row (10).
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-1",
        command_id="cmd-1", state="MATCHED", filled_size="10", local_sequence=1,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=2, trade_id="t1", venue_order_id="vo-1",
        command_id="cmd-1", state="MINED", filled_size="10", local_sequence=2,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=3, trade_id="t1", venue_order_id="vo-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10", local_sequence=3,
    )
    trades_conn.commit()

    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-1")
    assert facts.canonical_filled_size == 10.0
    assert facts.has_fills is True


def test_sums_across_distinct_trade_ids(trades_conn, forecasts_conn):
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="6", local_sequence=1,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=2, trade_id="t2", venue_order_id="vo-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="4", local_sequence=1,
    )
    trades_conn.commit()

    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-1")
    assert facts.canonical_filled_size == 10.0


def test_heartbeat_cancel_suspected_flag(trades_conn, forecasts_conn):
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-1", command_id="cmd-1",
        state="HEARTBEAT_CANCEL_SUSPECTED", source="WS_USER", local_sequence=1,
    )
    trades_conn.commit()

    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-1")
    assert facts.heartbeat_cancel_suspected is True


def test_ws_state_stale_vs_rest_when_rest_observation_is_newer(trades_conn, forecasts_conn):
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-1", command_id="cmd-1",
        state="LIVE", source="WS_USER", observed_at="2026-07-04T00:00:10+00:00",
        local_sequence=1,
    )
    insert_order_fact(
        trades_conn, fact_id=2, venue_order_id="vo-1", command_id="cmd-1",
        state="CANCEL_CONFIRMED", source="REST", observed_at="2026-07-04T00:05:00+00:00",
        local_sequence=2,
    )
    trades_conn.commit()

    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-1")
    # Latest order fact overall is REST (higher local_sequence), so the WS
    # row is not the freshest observation and this should read as fresh.
    assert facts.latest_rest_order_state == "CANCEL_CONFIRMED"
    assert facts.ws_state_stale_vs_rest is False


def test_ws_state_stale_true_when_ws_is_latest_and_rest_is_newer_by_timestamp(trades_conn, forecasts_conn):
    # WS fact has the higher local_sequence (so it's "latest_order_*"), but a
    # REST/DATA_API observation with a LATER observed_at timestamp exists --
    # the WS stream fell behind a fresher REST read.
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-1", command_id="cmd-1",
        state="CANCEL_CONFIRMED", source="REST", observed_at="2026-07-04T00:10:00+00:00",
        local_sequence=1,
    )
    insert_order_fact(
        trades_conn, fact_id=2, venue_order_id="vo-1", command_id="cmd-1",
        state="LIVE", source="WS_USER", observed_at="2026-07-04T00:00:10+00:00",
        local_sequence=2,
    )
    trades_conn.commit()

    snapshot = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})

    facts = snapshot.command_facts("cmd-1")
    assert facts.latest_order_source == "WS_USER"
    assert facts.ws_state_stale_vs_rest is True
