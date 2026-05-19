# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR #196 antibody — _lookup_market_neg_risk_authoritative data-dependency failure
"""Antibody tests: _lookup_market_neg_risk_authoritative three-tier fallback + fail-closed None.

Root cause (PR #196 data-dependency failure):
  _lookup_market_neg_risk_via_world_attach queried world.executable_market_snapshots but
  state/zeus-world.db had 0 rows for Karachi's condition_id.  Lookup returned False
  (not True) → antibody guard never fired → Karachi was marked REDEEM_CONFIRMED despite
  being a negRisk market mis-routed to Standard CTF.

Fix contract (_lookup_market_neg_risk_authoritative):
  Tier 1 — world.executable_market_snapshots via ATTACH.
  Tier 2 — main (zeus_trades.db) executable_market_snapshots.
  Tier 3 — Gamma CLOB API https://clob.polymarket.com/markets/{condition_id}.
  Fail-closed: returns None (NOT False) when all sources unavailable.

Caller contract (reconcile_pending_redeems):
  None → defer (continue, no terminal transition) rather than silently REDEEM_CONFIRMED.

Antibody contracts (sed-flip verifiable):
  T1: world.db has row → returns from world.db (Tier 1).
  T2: world.db empty, zeus_trades.db has row → returns from trades.db (Tier 2).
  T3: Both DBs empty, Gamma returns neg_risk=True → returns True (Tier 3).
  T4: All three sources fail → returns None (NOT False) (fail-closed).
  T5: Caller gets None → row stays REDEEM_TX_HASHED (no terminal transition).
      Sed-flip: comment out the None-guard continue → T5 goes RED (REDEEM_CONFIRMED wrongly).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.state.db import init_schema
from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
    request_redeem,
    _lookup_market_neg_risk_authoritative,
)
from src.venue.polymarket_v2_adapter import POLYGON_CTF_ADDRESS

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_KARACHI_CONDITION_ID = "0x8407ffe5522ed805c0c1f59727c729d5ee5a9232a232e1617e46d17b094afcac"
_TX_HASH = "0xdeadbeef" + "0" * 55


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _make_snapshots_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS executable_market_snapshots (
          snapshot_id    TEXT PRIMARY KEY,
          condition_id   TEXT NOT NULL,
          neg_risk       INTEGER NOT NULL DEFAULT 0,
          captured_at    TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_conn():
    """In-memory DB with settlement schema but NO snapshot rows."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    _make_snapshots_table(db)
    yield db
    db.close()


@pytest.fixture()
def world_db_with_negrisk():
    """Temp file world.db with a neg_risk=1 row for _KARACHI_CONDITION_ID."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        wconn = sqlite3.connect(path)
        wconn.execute(
            """
            CREATE TABLE executable_market_snapshots (
              snapshot_id TEXT PRIMARY KEY,
              condition_id TEXT NOT NULL,
              neg_risk INTEGER NOT NULL DEFAULT 0,
              captured_at TEXT NOT NULL DEFAULT (datetime('now')),
              freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
            )
            """
        )
        wconn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?,?,1)",
            ("snap-world-karachi", _KARACHI_CONDITION_ID),
        )
        wconn.commit()
        wconn.close()
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture()
def world_db_empty():
    """Temp file world.db with schema but 0 rows (Karachi production scenario)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        wconn = sqlite3.connect(path)
        wconn.execute(
            """
            CREATE TABLE executable_market_snapshots (
              snapshot_id TEXT PRIMARY KEY,
              condition_id TEXT NOT NULL,
              neg_risk INTEGER NOT NULL DEFAULT 0,
              captured_at TEXT NOT NULL DEFAULT (datetime('now')),
              freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
            )
            """
        )
        wconn.commit()
        wconn.close()
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_t1_world_db_hit_returns_from_world(world_db_with_negrisk, tmp_path):
    """T1: world.db has row → Tier 1 returns True; no Tier 2/3 needed."""
    import pathlib
    import src.state.db as _db_mod

    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_db_with_negrisk)
    try:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _make_snapshots_table(conn)  # trades.db table present but empty

        result = _lookup_market_neg_risk_authoritative(conn, _KARACHI_CONDITION_ID)

        assert result is True, (
            f"T1 FAIL: expected True (world.db hit), got {result!r}"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
        conn.close()


def test_t2_world_db_empty_trades_db_hit(world_db_empty, tmp_path):
    """T2: world.db empty, trades.db has row → Tier 2 returns True (Karachi fix path)."""
    import pathlib
    import src.state.db as _db_mod

    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_db_empty)
    try:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _make_snapshots_table(conn)
        conn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?,?,1)",
            ("snap-trades-karachi", _KARACHI_CONDITION_ID),
        )
        conn.commit()

        result = _lookup_market_neg_risk_authoritative(conn, _KARACHI_CONDITION_ID)

        assert result is True, (
            f"T2 FAIL: expected True (trades.db hit), got {result!r}"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
        conn.close()


def test_t3_both_dbs_empty_gamma_returns_true(world_db_empty):
    """T3: Both DBs empty, Gamma returns neg_risk=True → Tier 3 returns True."""
    import pathlib
    import src.state.db as _db_mod

    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_db_empty)
    try:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _make_snapshots_table(conn)  # no rows

        gamma_resp = MagicMock()
        gamma_resp.json.return_value = {"neg_risk": True, "condition_id": _KARACHI_CONDITION_ID}
        gamma_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=gamma_resp) as mock_get:
            result = _lookup_market_neg_risk_authoritative(conn, _KARACHI_CONDITION_ID)

        assert result is True, (
            f"T3 FAIL: expected True (Gamma hit), got {result!r}"
        )
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert _KARACHI_CONDITION_ID in call_url, (
            f"T3 FAIL: Gamma URL did not contain condition_id: {call_url!r}"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
        conn.close()


def test_t4_all_sources_fail_returns_none():
    """T4: All three sources fail → returns None (NOT False) — fail-closed contract."""
    import pathlib
    import src.state.db as _db_mod

    orig = _db_mod.ZEUS_WORLD_DB_PATH
    # Point to non-existent world.db so ATTACH fails
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path("/tmp/zeus_world_t4_nonexistent_12345.db")
    try:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _make_snapshots_table(conn)  # no rows

        with patch("httpx.get", side_effect=Exception("network error")):
            result = _lookup_market_neg_risk_authoritative(conn, _KARACHI_CONDITION_ID)

        # CRITICAL: must be None, not False — fail-closed, not fail-open
        assert result is None, (
            f"T4 FAIL: expected None (all sources failed, fail-closed), got {result!r}. "
            "Returning False here is the original bug — it bypasses the antibody guard."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
        conn.close()


def test_t5_none_result_defers_terminal_transition():
    """T5 (sed-flip verified): caller gets None → row stays REDEEM_TX_HASHED.

    Sed-flip contract: comment out the None-guard 'continue' in reconcile_pending_redeems
    and this test goes RED because the row is wrongly marked REDEEM_CONFIRMED.
    """
    import pathlib
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod

    orig = _db_mod.ZEUS_WORLD_DB_PATH
    # Non-existent world.db → ATTACH fails → Tier 1 miss
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path("/tmp/zeus_world_t5_nonexistent_12345.db")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_settlement_command_schema(conn)
    _make_snapshots_table(conn)  # no rows in trades.db either

    try:
        cmd_id = request_redeem(
            _KARACHI_CONDITION_ID,
            "USDC",
            market_id=_KARACHI_CONDITION_ID,
            token_amounts={"yes-token": "2.0"},
            winning_index_set='["2"]',
            conn=conn,
            requested_at=NOW,
        )
        conn.execute(
            "UPDATE settlement_commands SET state=?, tx_hash=? WHERE command_id=?",
            (SettlementState.REDEEM_TX_HASHED.value, _TX_HASH, cmd_id),
        )
        conn.commit()

        receipt = {"status": 1, "to": POLYGON_CTF_ADDRESS, "blockNumber": 100, "block_number": 100}
        eth_mock = SimpleNamespace(
            get_transaction_receipt=lambda _tx: receipt,
            block_number=105,
        )
        web3 = SimpleNamespace(eth=eth_mock)

        # All lookup sources return None (gamma also fails)
        with patch("httpx.get", side_effect=Exception("network error")):
            results = sc.reconcile_pending_redeems(web3, conn)

        # None path → deferred → no result emitted for this row
        assert len(results) == 0, (
            f"T5 FAIL: expected 0 results (deferred), got {len(results)}: {results}. "
            "If this fails after sed-flipping the None-guard, the antibody is working."
        )

        # Row must still be REDEEM_TX_HASHED — not REDEEM_CONFIRMED
        row = conn.execute(
            "SELECT state FROM settlement_commands WHERE command_id=?", (cmd_id,)
        ).fetchone()
        assert row["state"] == SettlementState.REDEEM_TX_HASHED.value, (
            f"T5 FAIL: DB state={row['state']!r}, expected REDEEM_TX_HASHED. "
            "Row must not advance to REDEEM_CONFIRMED when neg_risk lookup is unknown."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
        conn.close()
