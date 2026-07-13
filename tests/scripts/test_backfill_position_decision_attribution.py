# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
#   adjudication §(c) (LX-E packet: position_decision_attribution rehome).
"""Tests for scripts/backfill_position_decision_attribution.py.

Conservative-by-design: only the EXACT position -> ENTRY command ->
edli_live_profit_audit.execution_command_id link is used. The ambiguous
(condition_id, direction) "latest row" inference used by the legacy settlement-time
bridge is NEVER consulted here — zero or multiple exact hashes both mark the
position UNATTRIBUTABLE with a named reason.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.backfill_position_decision_attribution import (
    REASON_AMBIGUOUS_MULTI_HASH,
    REASON_NO_AUDIT_ROW,
    REASON_NO_ENTRY_COMMAND,
    run_backfill,
)
from src.state.db import init_schema


def _make_trade_conn(tmp_path) -> sqlite3.Connection:
    path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(path)
    init_schema(conn)  # creates position_current + venue_commands (test-fixture shape)
    return conn


def _attach_world(conn: sqlite3.Connection, tmp_path, *, audit_rows: list[dict]) -> None:
    """Build a real world.db file with edli_live_profit_audit rows and ATTACH it.

    Bypasses the script's production ATTACH (which targets the real
    ZEUS_WORLD_DB_PATH) — the run_backfill entrypoint no-ops its own ATTACH when
    'world' is already present.
    """
    world_path = str(tmp_path / "world.db")
    wconn = sqlite3.connect(world_path)
    init_schema(wconn)
    for row in audit_rows:
        wconn.execute(
            """INSERT INTO edli_live_profit_audit
               (audit_id, event_id, aggregate_id, condition_id, token_id,
                execution_command_id, direction,
                expected_edge_source_certificate_hash, order_lifecycle_state,
                created_at, schema_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["audit_id"], f"evt-{row['audit_id']}", f"agg-{row['audit_id']}",
                row.get("condition_id", "cond-1"), row.get("token_id", "tok-1"),
                row["execution_command_id"], row.get("direction", "buy_no"),
                row.get("expected_edge_source_certificate_hash"),
                "FILLED", "2026-06-01T00:00:00Z", 1,
            ),
        )
    wconn.commit()
    wconn.close()
    conn.execute("ATTACH DATABASE ? AS world", (world_path,))


def _seed_position(conn: sqlite3.Connection, *, position_id: str) -> None:
    conn.execute(
        """INSERT INTO position_current
           (position_id, phase, strategy_key, condition_id, direction, entry_price,
            shares, cost_basis_usd, temperature_metric, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (position_id, "settled", "center_buy", "cond-1", "buy_no", 0.35, 10.0, 3.5,
         "high", "2026-06-01T00:00:00Z"),
    )


def _seed_entry_command(conn: sqlite3.Connection, *, position_id: str, command_id: str,
                         created_at: str = "2026-06-01T00:00:00Z") -> None:
    conn.execute(
        """INSERT INTO venue_commands
           (command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            state, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (command_id, "snap-1", "env-1", position_id, "dec-1", f"idem-{command_id}",
         "ENTRY", "mkt-1", "tok-1", "BUY", 10.0, 0.35, "INTENT_CREATED",
         created_at, created_at),
    )
    conn.commit()


def test_exact_single_hash_attributes(tmp_path):
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-1")
    _seed_entry_command(conn, position_id="pos-1", command_id="cmd-1")
    _attach_world(conn, tmp_path, audit_rows=[
        {"audit_id": "aud-1", "execution_command_id": "cmd-1",
         "expected_edge_source_certificate_hash": "cert-1"},
    ])

    stats = run_backfill(conn, apply=True)
    assert stats["attributed"] == 1
    assert stats["unattributable"] == 0

    row = conn.execute(
        "SELECT resolution, decision_certificate_hash, resolution_reason, command_id, source "
        "FROM position_decision_attribution WHERE position_id = 'pos-1'"
    ).fetchone()
    assert row[0] == "ATTRIBUTED"
    assert row[1] == "cert-1"
    assert row[2] == "exact_command_link"
    assert row[3] == "cmd-1"
    assert row[4] == "BACKFILL"


def test_no_entry_command_marks_unattributable(tmp_path):
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-2")
    _attach_world(conn, tmp_path, audit_rows=[])

    stats = run_backfill(conn, apply=True)
    assert stats["unattributable"] == 1
    assert stats["unattributable_by_reason"] == {REASON_NO_ENTRY_COMMAND: 1}

    row = conn.execute(
        "SELECT resolution, decision_certificate_hash, resolution_reason "
        "FROM position_decision_attribution WHERE position_id = 'pos-2'"
    ).fetchone()
    assert row[0] == "UNATTRIBUTABLE"
    assert row[1] is None
    assert row[2] == REASON_NO_ENTRY_COMMAND


def test_no_audit_row_for_command_marks_unattributable(tmp_path):
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-3")
    _seed_entry_command(conn, position_id="pos-3", command_id="cmd-3")
    _attach_world(conn, tmp_path, audit_rows=[])  # no audit row references cmd-3

    stats = run_backfill(conn, apply=True)
    assert stats["unattributable_by_reason"] == {REASON_NO_AUDIT_ROW: 1}


def test_ambiguous_multi_hash_never_guesses_latest_row(tmp_path):
    """The EXACT command-id join finds TWO distinct certificate hashes for the SAME
    command — the backfill must mark UNATTRIBUTABLE, never pick one (e.g. latest
    created_at), even though that guess is exactly what the legacy
    (condition_id, direction) bridge does at settlement time."""
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-4")
    _seed_entry_command(conn, position_id="pos-4", command_id="cmd-4")
    _attach_world(conn, tmp_path, audit_rows=[
        {"audit_id": "aud-4a", "execution_command_id": "cmd-4",
         "expected_edge_source_certificate_hash": "cert-a"},
        {"audit_id": "aud-4b", "execution_command_id": "cmd-4",
         "expected_edge_source_certificate_hash": "cert-b"},
    ])

    stats = run_backfill(conn, apply=True)
    assert stats["unattributable_by_reason"] == {REASON_AMBIGUOUS_MULTI_HASH: 1}
    row = conn.execute(
        "SELECT decision_certificate_hash FROM position_decision_attribution "
        "WHERE position_id = 'pos-4'"
    ).fetchone()
    assert row[0] is None


def test_dry_run_does_not_commit(tmp_path):
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-5")
    _seed_entry_command(conn, position_id="pos-5", command_id="cmd-5")
    _attach_world(conn, tmp_path, audit_rows=[
        {"audit_id": "aud-5", "execution_command_id": "cmd-5",
         "expected_edge_source_certificate_hash": "cert-5"},
    ])

    stats = run_backfill(conn, apply=False)
    assert stats["attributed"] == 1
    assert stats["applied"] is False

    # A FRESH read on the same (uncommitted, then rolled-back) connection sees no row.
    row = conn.execute(
        "SELECT 1 FROM position_decision_attribution WHERE position_id = 'pos-5'"
    ).fetchone()
    assert row is None


def test_already_attributed_position_skipped(tmp_path):
    """A position that already has an attribution row (e.g. from the live hook) is
    never reconsidered by the backfill — append-only law."""
    conn = _make_trade_conn(tmp_path)
    _seed_position(conn, position_id="pos-6")
    _seed_entry_command(conn, position_id="pos-6", command_id="cmd-6")
    _attach_world(conn, tmp_path, audit_rows=[
        {"audit_id": "aud-6", "execution_command_id": "cmd-6",
         "expected_edge_source_certificate_hash": "cert-6"},
    ])

    from src.state.venue_command_repo import record_position_decision_attribution

    record_position_decision_attribution(
        conn, position_id="pos-6", command_id="cmd-6",
        decision_certificate_hash="cert-live", intent_kind="ENTRY",
        created_at="2026-06-01T00:00:00Z",
    )
    conn.commit()

    stats = run_backfill(conn, apply=True)
    assert stats["considered"] == 0

    row = conn.execute(
        "SELECT decision_certificate_hash, source FROM position_decision_attribution "
        "WHERE position_id = 'pos-6'"
    ).fetchone()
    assert row[0] == "cert-live"
    assert row[1] == "LIVE_DECISION"
