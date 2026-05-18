# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE2_PHASE_CRITIC.md
#                  MAJ-1 boot-wire dispatch brief
"""Antibody tests: F109 consolidator boot-wire invariants.

Four contracts under test:
  1. NO-OP on healthy DB (no duplicates) → scanned_tokens == 0
  2. Duplicate-seeded DB → consolidator voids oldest; migration pre-flight passes
  3. Consolidator failure (consolidate raises) → boot continues (logged WARNING only)
  4. Karachi-safe: single-row token → consolidator NO-OPs
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import uuid
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Minimal schema helpers
# ---------------------------------------------------------------------------

_POSITION_CURRENT_DDL = """
CREATE TABLE IF NOT EXISTS position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    trade_id TEXT,
    market_id TEXT,
    city TEXT,
    cluster TEXT,
    target_date TEXT,
    bin_label TEXT,
    direction TEXT,
    unit TEXT,
    size_usd REAL,
    shares REAL,
    cost_basis_usd REAL,
    entry_price REAL,
    p_posterior REAL,
    last_monitor_prob REAL,
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL DEFAULT 'opening_inertia',
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL DEFAULT 'high'
);
"""

_POSITION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    phase_before TEXT,
    phase_after TEXT,
    strategy_key TEXT,
    source_module TEXT,
    payload_json TEXT,
    env TEXT
);
"""

_COLLATERAL_DDL = """
CREATE TABLE IF NOT EXISTS collateral_ledger_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pusd_balance_micro INTEGER NOT NULL DEFAULT 0,
    pusd_allowance_micro INTEGER NOT NULL DEFAULT 0,
    usdc_e_legacy_balance_micro INTEGER NOT NULL DEFAULT 0,
    ctf_token_balances_json TEXT NOT NULL,
    ctf_token_allowances_json TEXT NOT NULL DEFAULT '{}',
    reserved_pusd_for_buys_micro INTEGER NOT NULL DEFAULT 0,
    reserved_tokens_for_sells_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL,
    authority_tier TEXT NOT NULL,
    raw_balance_payload_hash TEXT
);
"""


def _make_trade_db(tmp_path, *, chain_balances: dict | None = None) -> sqlite3.Connection:
    """Return a fresh in-memory-style SQLite connection with F109-relevant tables."""
    db = tmp_path / f"trade_{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(_POSITION_CURRENT_DDL + _POSITION_EVENTS_DDL + _COLLATERAL_DDL)

    if chain_balances is not None:
        conn.execute(
            """
            INSERT INTO collateral_ledger_snapshots
                (ctf_token_balances_json, captured_at, authority_tier)
            VALUES (?, '2026-05-17T00:00:00+00:00', 'CHAIN')
            """,
            (json.dumps({k: int(v * 1_000_000) for k, v in chain_balances.items()}),),
        )
    conn.commit()
    return conn


def _insert_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    token_id: str,
    phase: str,
    shares: float,
    occurred_at: str = "2026-05-17T10:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current
            (position_id, phase, token_id, shares, cost_basis_usd, updated_at,
             strategy_key, temperature_metric)
        VALUES (?, ?, ?, ?, 0.0, ?, 'opening_inertia', 'high')
        """,
        (position_id, phase, token_id, shares, occurred_at),
    )
    conn.execute(
        """
        INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, env)
        VALUES (?, ?, 1, 1, 'POSITION_OPEN_INTENT', ?, 'live')
        """,
        (str(uuid.uuid4()), position_id, occurred_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: NO-OP on healthy (no duplicates) DB
# ---------------------------------------------------------------------------

def test_consolidator_boot_noop_on_empty(tmp_path):
    """Consolidator invoked on a fresh DB with no duplicates → scanned_tokens == 0."""
    from src.state.position_duplicate_consolidator import consolidate

    conn = _make_trade_db(tmp_path)
    report = consolidate(conn)
    conn.close()

    assert report["scanned_tokens"] == 0, (
        f"expected no tokens scanned on empty DB, got {report['scanned_tokens']}"
    )
    assert report["voided_positions"] == []
    assert report["divergent_tokens"] == []


def test_consolidator_boot_noop_on_single_row(tmp_path):
    """Consolidator with one row per token → NO-OP (scanned_tokens == 0)."""
    from src.state.position_duplicate_consolidator import consolidate

    token = "tok_single_" + uuid.uuid4().hex[:8]
    conn = _make_trade_db(tmp_path, chain_balances={token: 6.0})
    _insert_position(conn, position_id=str(uuid.uuid4()), token_id=token,
                     phase="pending_exit", shares=6.0)

    report = consolidate(conn)
    conn.close()

    assert report["scanned_tokens"] == 0, (
        "single-row token must not be scanned by HAVING COUNT(*) > 1 filter"
    )


# ---------------------------------------------------------------------------
# Test 2: Duplicate-seeded DB — consolidator runs; migration pre-flight passes
# ---------------------------------------------------------------------------

def test_consolidator_boot_resolves_duplicates_then_migration_passes(tmp_path):
    """With seeded duplicate rows, consolidator voids oldest; migration index installs."""
    from src.state.position_duplicate_consolidator import consolidate

    token = "tok_dup_" + uuid.uuid4().hex[:8]
    # Chain has 6 shares; DB has 12 (overbooked by 6).
    conn = _make_trade_db(tmp_path, chain_balances={token: 6.0})

    pos_old = str(uuid.uuid4())
    pos_new = str(uuid.uuid4())
    _insert_position(conn, position_id=pos_old, token_id=token, phase="pending_exit",
                     shares=6.0, occurred_at="2026-05-17T10:00:00+00:00")
    _insert_position(conn, position_id=pos_new, token_id=token, phase="pending_exit",
                     shares=6.0, occurred_at="2026-05-17T11:00:00+00:00")

    # Pre-consolidation: 2 open-phase rows for the same token.
    dup_count_before = conn.execute(
        "SELECT COUNT(*) FROM position_current WHERE token_id=? AND phase='pending_exit'",
        (token,),
    ).fetchone()[0]
    assert dup_count_before == 2

    report = consolidate(conn)

    assert report["scanned_tokens"] == 1, "one duplicate token should be scanned"
    assert len(report["voided_positions"]) == 1, "one position should be voided"
    assert pos_old in report["voided_positions"], "oldest position should be voided"
    assert report["overbook_tokens"] == [token]

    # After consolidation: 1 open-phase row remains.
    dup_count_after = conn.execute(
        "SELECT COUNT(*) FROM position_current WHERE token_id=? AND phase='pending_exit'",
        (token,),
    ).fetchone()[0]
    assert dup_count_after == 1, (
        f"after consolidation, expected 1 open-phase row, got {dup_count_after}"
    )

    # Voided position has phase='voided' and shares=0.
    voided_row = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id=?", (pos_old,)
    ).fetchone()
    assert voided_row["phase"] == "voided"
    assert voided_row["shares"] == 0.0

    # Migration pre-flight must now pass (no duplicates remain).
    _mod = importlib.import_module(
        "scripts.migrations.202605_position_current_idempotent_open_per_token"
    )
    # Should not raise — pre-flight passes and index is created.
    _mod.up(conn)
    conn.commit()

    index_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_position_current_open_per_token'"
    ).fetchone()
    assert index_exists is not None, "migration should have installed the UNIQUE INDEX"

    conn.close()


# ---------------------------------------------------------------------------
# Test 3: Consolidator failure → boot continues (logged WARNING, does not raise)
# ---------------------------------------------------------------------------

def test_f109_consolidator_boot_failure_does_not_block_daemon(tmp_path, caplog):
    """If consolidate() raises, _run_f109_consolidator logs WARNING and returns.

    The daemon boot must NOT be blocked; _run_f109_consolidator is failure-tolerant.
    The warning is emitted to the module logger (name "zeus" or "src.main");
    we capture at root level to be logger-name agnostic.
    """
    import src.main as main_mod

    boom_msg = "synthetic_consolidator_failure_boom"

    # Patch the lazy import target: inside _run_f109_consolidator, the call
    # is `from src.state.db import get_trade_connection` which resolves at
    # call time. Patch at the db module level so both the lazy import and any
    # pre-bound reference see the same mock.
    with patch(
        "src.state.db.get_trade_connection",
        side_effect=RuntimeError(boom_msg),
    ):
        with caplog.at_level(logging.WARNING):
            # Must not raise.
            main_mod._run_f109_consolidator()

    assert any(boom_msg in r.message for r in caplog.records), (
        f"expected WARNING log containing '{boom_msg}'; "
        f"records={[(r.name, r.message) for r in caplog.records]}"
    )
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "expected at least one WARNING-level record"


# ---------------------------------------------------------------------------
# Test 4: Karachi-safe — single-row position (c30f28a5-d4e style) → NO-OP
# ---------------------------------------------------------------------------

def test_consolidator_karachi_single_row_noop(tmp_path):
    """Karachi trade c30f28a5-d4e is a single-row position.

    The HAVING COUNT(*) > 1 filter skips it completely.
    This test uses a synthetic Karachi-style position: one row, one token,
    active phase. Consolidator must return scanned_tokens == 0.
    """
    from src.state.position_duplicate_consolidator import consolidate

    # Karachi-style: one token, one open-phase row, chain agrees.
    karachi_token = "c30f28a5d4e" + uuid.uuid4().hex[:4]  # mocked token suffix
    conn = _make_trade_db(tmp_path, chain_balances={karachi_token: 6.0})
    _insert_position(
        conn,
        position_id="c30f28a5-d4e-" + uuid.uuid4().hex[:4],
        token_id=karachi_token,
        phase="active",
        shares=6.0,
        occurred_at="2026-05-17T08:00:00+00:00",
    )

    report = consolidate(conn)
    conn.close()

    assert report["scanned_tokens"] == 0, (
        "Karachi single-row position must be skipped by HAVING COUNT(*) > 1 filter; "
        f"got scanned_tokens={report['scanned_tokens']}"
    )
    assert report["voided_positions"] == []
