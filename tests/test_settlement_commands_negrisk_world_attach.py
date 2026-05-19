# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: INV-37 ATTACH guard + PR #192 bot comment thread 1
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody test — ATTACH world guard in submit_redeem() with plain trade conn.
# Reuse: Run when modifying submit_redeem(), ATTACH guard, or connection helpers.
"""Antibody test: submit_redeem() ATTACHes world schema when caller passes a plain
trade connection (no world attached), so the world.executable_market_snapshots
snapshot lookup succeeds rather than raising OperationalError and routing to
REDEEM_NEGRISK_FACT_MISSING.

Root cause (PR #192 bot P1): src/main.py's redeem scheduler opens
get_trade_connection(write_class="live") — plain trade conn, no world attached.
Passing that conn to submit_redeem() caused OperationalError: "no such table:
world.executable_market_snapshots", swallowed into REDEEM_NEGRISK_FACT_MISSING,
meaning every autonomous negRisk redeem was parked at REDEEM_OPERATOR_REQUIRED.

Fix: submit_redeem() lines 393-405 guard-ATTACH world before the snapshot query.

Antibody contracts (sed-flip verifiable):
  B1: plain trade conn (no world ATTACH) → submit_redeem negRisk market →
      adapter.redeem called (no REDEEM_NEGRISK_FACT_MISSING fallthrough).
  B2: sed-flip guard-ATTACH lines → B1 goes RED (REDEEM_NEGRISK_FACT_MISSING raised).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.state.db import init_schema


NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_CONDITION_ID = "0xdeadbeef" + "a" * 56


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def plain_trade_conn():
    """Plain trade connection with NO world schema attached — mirrors main.py scheduler."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    # Deliberately do NOT attach world — this is what the bug exhibited.
    yield db
    db.close()


@pytest.fixture()
def world_db():
    """In-memory world DB with the executable_market_snapshots table."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'GAMMA',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self):
        self.calls = []

    def redeem(self, condition_id, *, index_sets=None, neg_risk=False, amount_per_slot=None, **_kw):
        self.calls.append({
            "condition_id": condition_id,
            "index_sets": index_sets,
            "neg_risk": neg_risk,
            "amount_per_slot": amount_per_slot,
        })
        return {"success": True, "tx_hash": "0xdeadbeef"}


def _insert_command(conn, condition_id: str) -> str:
    """Create a minimal pending redeem command via request_redeem."""
    from src.execution.settlement_commands import request_redeem
    cmd_id = request_redeem(
        condition_id,
        "USDC",
        market_id=condition_id,
        token_amounts={"yes-token-123": "1.5"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    return cmd_id


def _insert_world_snapshot(world_db, conn, condition_id: str) -> None:
    """Attach world_db as 'world' and insert a negRisk snapshot row."""
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world.executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'GAMMA',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO world.executable_market_snapshots
          (snapshot_id, condition_id, yes_token_id, no_token_id, neg_risk, captured_at, freshness_deadline)
        VALUES (?, ?, 'yes-token-123', 'no-token-456', 1, datetime('now'), datetime('now', '+1 day'))
        """,
        (f"snap-{condition_id}", condition_id),
    )
    conn.commit()
    # Detach so we can test that submit_redeem re-attaches on a PLAIN conn
    conn.execute("DETACH DATABASE world")
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_b1_plain_conn_attach_guard_routes_to_adapter(plain_trade_conn, monkeypatch):
    """B1: plain trade conn (no world) → submit_redeem ATTACHes world → adapter called.

    The ATTACH guard in submit_redeem() must transparently attach world before the
    world.executable_market_snapshots query so the negRisk snapshot lookup succeeds.
    """
    import src.execution.settlement_commands as sc

    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )
    monkeypatch.setattr(
        "src.execution.settlement_commands.require_pusd_redemption_allowed",
        lambda fx: fx,
    )
    from src.state.db import ZEUS_WORLD_DB_PATH

    # Build a temporary world DB file so ATTACH can succeed (can't ATTACH ':memory:' across conns)
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        world_tmp = f.name
    try:
        world_conn = sqlite3.connect(world_tmp)
        world_conn.execute(
            """
            CREATE TABLE executable_market_snapshots (
              snapshot_id TEXT PRIMARY KEY,
              condition_id TEXT NOT NULL,
              yes_token_id TEXT NOT NULL DEFAULT 'yes-token-123',
              no_token_id TEXT NOT NULL DEFAULT 'no-token-456',
              neg_risk INTEGER NOT NULL DEFAULT 1,
              captured_at TEXT NOT NULL DEFAULT (datetime('now')),
              freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
            )
            """
        )
        world_conn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?, ?, 1)",
            (f"snap-{_CONDITION_ID}", _CONDITION_ID),
        )
        world_conn.commit()
        world_conn.close()

        # Redirect ZEUS_WORLD_DB_PATH so the guard-ATTACH hits our temp file
        import pathlib
        monkeypatch.setattr("src.execution.settlement_commands.ZEUS_WORLD_DB_PATH", pathlib.Path(world_tmp), raising=False)
        # Also patch at the import site used in the guard
        import src.state.db as _db_mod
        original_path = _db_mod.ZEUS_WORLD_DB_PATH
        _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_tmp)

        cmd_id = _insert_command(plain_trade_conn, _CONDITION_ID)
        adapter = _FakeAdapter()
        ledger = SimpleNamespace()

        result = sc.submit_redeem(
            cmd_id,
            adapter,
            ledger,
            conn=plain_trade_conn,
        )

        # B1 contract: adapter must have been called (no REDEEM_NEGRISK_FACT_MISSING)
        assert adapter.calls, (
            "B1 FAIL: adapter.redeem was never called — ATTACH guard failed and "
            "negRisk snapshot lookup raised OperationalError, routing to "
            "REDEEM_NEGRISK_FACT_MISSING / REDEEM_OPERATOR_REQUIRED"
        )
        # Verify neg_risk=True was passed through
        assert adapter.calls[0]["neg_risk"] is True, (
            f"B1 FAIL: adapter called but neg_risk={adapter.calls[0]['neg_risk']!r}; "
            "expected True for negRisk market"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = original_path
        try:
            os.unlink(world_tmp)
        except OSError:
            pass
