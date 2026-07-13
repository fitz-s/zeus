# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2-a;
#   scripts/backfill_ctf_token_registry.py
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: dry-run-vs-apply antibody for the ctf_token_registry backfill, across
#   its three durable scan sources (venue_commands, position_current, executable
#   market snapshots) with the documented first_source priority.

"""Tests for scripts/backfill_ctf_token_registry.py."""

from __future__ import annotations

import sqlite3

from scripts.backfill_ctf_token_registry import run_backfill
from src.state.ctf_token_registry import get_token_registry_row
from src.state.db import init_schema_trade_only


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema_trade_only(conn)
    return conn


def _insert_snapshot(conn, *, snapshot_id, condition_id, yes_token_id, no_token_id, captured_at):
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, gamma_market_id, event_id, condition_id, question_id,
            yes_token_id, no_token_id, enable_orderbook, active, closed,
            min_tick_size, min_order_size, fee_details_json, token_map_json,
            neg_risk, orderbook_top_bid, orderbook_top_ask, orderbook_depth_json,
            raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
            authority_tier, captured_at, freshness_deadline
        ) VALUES (?, 'gm1', 'ev1', ?, 'q1', ?, ?, 1, 1, 0,
                   '0.01', '5', '{}', '{}', 0, '0.5', '0.5', '{}',
                   'h1', 'h2', 'h3', 'GAMMA', ?, ?)
        """,
        (snapshot_id, condition_id, yes_token_id, no_token_id, captured_at, captured_at),
    )


def _insert_venue_command(conn, *, command_id, snapshot_id, token_id, created_at):
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            state, created_at, updated_at
        ) VALUES (?, ?, 'env1', 'pos1', 'dec1', ?, 'ENTRY', 'market1', ?, 'BUY', 1.0, 0.5,
                  'ACKED', ?, ?)
        """,
        (command_id, snapshot_id, command_id, token_id, created_at, created_at),
    )


def _insert_position(conn, *, position_id, token_id, no_token_id, condition_id, updated_at):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, updated_at, temperature_metric,
            token_id, no_token_id, condition_id
        ) VALUES (?, 'active', 'strat1', ?, 'high', ?, ?, ?)
        """,
        (position_id, updated_at, token_id, no_token_id, condition_id),
    )


def test_dry_run_scans_without_writing():
    conn = _make_conn()
    _insert_snapshot(
        conn, snapshot_id="snap1", condition_id="cond1",
        yes_token_id="tokY", no_token_id="tokN", captured_at="t0",
    )
    _insert_venue_command(conn, command_id="cmd1", snapshot_id="snap1", token_id="tokY", created_at="t1")
    _insert_position(
        conn, position_id="pos1", token_id="tokY", no_token_id="tokN",
        condition_id="cond1", updated_at="t2",
    )

    summary = run_backfill(conn, apply=False)

    assert summary["apply"] is False
    assert summary["scanned_by_source"]["zeus_command"] == 1
    assert summary["scanned_by_source"]["attributed_fill"] == 2  # both legs
    assert summary["scanned_by_source"]["market_topology"] == 2  # yes + no
    assert summary["rows_inserted"] is None
    # init_schema_trade_only always creates the table; dry-run just never writes to it.
    count = conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0]
    assert count == 0


def test_apply_writes_rows_with_documented_source_priority():
    """Scan order (packet-specified): venue_commands, position_current, topology.

    tokY is seen by ALL THREE sources -- first_source must be zeus_command
    (the FIRST source scanned), not whichever source happened to run last.
    """
    conn = _make_conn()
    _insert_snapshot(
        conn, snapshot_id="snap1", condition_id="cond1",
        yes_token_id="tokY", no_token_id="tokN", captured_at="t0",
    )
    _insert_venue_command(conn, command_id="cmd1", snapshot_id="snap1", token_id="tokY", created_at="t1")
    _insert_position(
        conn, position_id="pos1", token_id="tokY", no_token_id="tokN",
        condition_id="cond1", updated_at="t2",
    )

    summary = run_backfill(conn, apply=True)

    assert summary["apply"] is True
    assert summary["rows_inserted"] == 2  # tokY (via zeus_command first), tokN (via attributed_fill first)

    tok_y = get_token_registry_row(conn, token_id="tokY")
    assert tok_y is not None
    assert tok_y.first_source == "zeus_command"
    assert tok_y.condition_id == "cond1"

    tok_n = get_token_registry_row(conn, token_id="tokN")
    assert tok_n is not None
    # tokN is never in venue_commands in this fixture -- first source that
    # observes it is position_current (attributed_fill).
    assert tok_n.first_source == "attributed_fill"


def test_apply_is_idempotent_on_rerun():
    """Rerunning the backfill never inserts a second row for an already-known token.

    Fixture note: tokY is scanned TWICE per run (once via venue_commands.token_id,
    once via the topology yes_token_id leg), so a rerun reports 3 confirmations
    (tokY x2 + tokN x1) — the point under test is rows_inserted==0 and the table
    stays at exactly 2 physical rows, not the exact confirm-count arithmetic.
    """
    conn = _make_conn()
    _insert_venue_command(conn, command_id="cmd1", snapshot_id="snap1", token_id="tokY", created_at="t1")
    _insert_snapshot(
        conn, snapshot_id="snap1", condition_id="cond1",
        yes_token_id="tokY", no_token_id="tokN", captured_at="t0",
    )

    first = run_backfill(conn, apply=True)
    second = run_backfill(conn, apply=True)

    assert first["rows_inserted"] == 2  # tokY (zeus_command), tokN (market_topology)
    # rerun: both tokens already registered -- confirmed, never re-inserted.
    assert second["rows_inserted"] == 0
    assert second["rows_confirmed"] == 3
    count = conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0]
    assert count == 2
