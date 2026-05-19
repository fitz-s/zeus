# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR #192 Karachi root-cause + architecture/invariants.md NEGRISK-MISROUTE-GUARD
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody tests — negRisk-misroute reconcile-guard (Karachi c8c220f5 root-cause).
# Reuse: Run when modifying reconcile_pending_redeems(), _lookup_market_neg_risk_via_world_attach(),
#         or POLYGON_CTF_ADDRESS / POLYGON_NEGRISK_ADAPTER_ADDRESS routing in settlement_commands.py.
"""Antibody tests: negRisk-misroute reconcile-guard in reconcile_pending_redeems().

Root cause (Karachi tx 0x0c85d9...): A negRisk market redeem was submitted to Standard
CTF (POLYGON_CTF_ADDRESS) instead of POLYGON_NEGRISK_ADAPTER_ADDRESS.  The tx mined
with status=1 (success) but paid 0 USDC — ERC-1155 transfer to the wrong contract.
reconcile_pending_redeems() was marking it REDEEM_CONFIRMED despite the mis-route.

Fix (settlement_commands.py _lookup_market_neg_risk_via_world_attach + reconcile guard):
When a confirmed tx.to == POLYGON_CTF_ADDRESS for a negRisk market, reset to
REDEEM_OPERATOR_REQUIRED (tx_hash=NULL, error_payload=REDEEM_NEGRISK_MISROUTED) so
reseat_stub_deferred_rows_for_autonomous_retry promotes it back via the correct adapter.

Antibody contracts (sed-flip verifiable):
  C1: negRisk market + confirmed tx to Standard CTF → state reset to REDEEM_OPERATOR_REQUIRED,
      tx_hash cleared, error_payload contains REDEEM_NEGRISK_MISROUTED.
  C2: standard market + confirmed tx to Standard CTF → state REDEEM_CONFIRMED (not reset).
  C3: negRisk market + confirmed tx to NegRisk adapter → state REDEEM_CONFIRMED (not reset).
  C4: _lookup_market_neg_risk_via_world_attach returns False safely when world ATTACH fails.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.state.db import init_schema
from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
    request_redeem,
)
from src.venue.polymarket_v2_adapter import (
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
)


NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_NEGRISK_CONDITION_ID = "0xnegriskbeef" + "a" * 52
_STANDARD_CONDITION_ID = "0xstandardctf" + "b" * 51
_TX_HASH_MISROUTED = "0xmisrouted_tx" + "0" * 50
_TX_HASH_STANDARD = "0xstandard_tx" + "1" * 51
_TX_HASH_NEGRISK_OK = "0xnegrisk_ok_tx" + "2" * 49


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def trade_conn():
    """Plain in-memory trade connection with settlement schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


@pytest.fixture()
def world_tmp_db_negrisk():
    """Temporary world DB file with a negRisk=1 snapshot for _NEGRISK_CONDITION_ID."""
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
        # negRisk=1 entry for the misrouted condition
        wconn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?, ?, 1)",
            (f"snap-negrisk-{_NEGRISK_CONDITION_ID[:20]}", _NEGRISK_CONDITION_ID),
        )
        # negRisk=1 entry for the correctly-routed negRisk condition
        wconn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?, ?, 1)",
            (f"snap-negrisk-ok-{_NEGRISK_CONDITION_ID[:16]}", _NEGRISK_CONDITION_ID + "_ok"),
        )
        # negRisk=0 entry for the standard condition
        wconn.execute(
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) VALUES (?, ?, 0)",
            (f"snap-standard-{_STANDARD_CONDITION_ID[:20]}", _STANDARD_CONDITION_ID),
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
# Helpers
# ---------------------------------------------------------------------------

def _insert_tx_hashed_command(conn, condition_id: str, tx_hash: str) -> str:
    """Create a REDEEM_TX_HASHED command (simulates post-submit state)."""
    cmd_id = request_redeem(
        condition_id,
        "USDC",
        market_id=condition_id,
        token_amounts={"yes-token": "2.0"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.execute(
        """
        UPDATE settlement_commands
           SET state = ?,
               tx_hash = ?
         WHERE command_id = ?
        """,
        (SettlementState.REDEEM_TX_HASHED.value, tx_hash, cmd_id),
    )
    conn.commit()
    return cmd_id


def _make_web3(tx_hash: str, status: int, to_address: str) -> SimpleNamespace:
    """Minimal web3 mock: eth.get_transaction_receipt returns a receipt dict."""
    receipt = {
        "status": status,
        "to": to_address,
        "blockNumber": 100,
        "block_number": 100,
    }
    eth_mock = SimpleNamespace(
        get_transaction_receipt=lambda _tx: receipt,
        block_number=105,
    )
    return SimpleNamespace(eth=eth_mock)


def _read_command(conn, cmd_id: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM settlement_commands WHERE command_id = ?", (cmd_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_negrisk_market_with_standard_ctf_tx_resets_to_operator_required(
    trade_conn, world_tmp_db_negrisk, monkeypatch
):
    """C1 (primary antibody, Karachi case): negRisk market + confirmed tx to Standard CTF
    → reconcile resets to REDEEM_OPERATOR_REQUIRED, clears tx_hash, logs REDEEM_NEGRISK_MISROUTED.

    This is the exact scenario from Karachi: condition c8c220f5 is negRisk but tx 0x0c85d9…
    was sent to POLYGON_CTF_ADDRESS.  The fix must intercept the confirmed-but-wrong receipt
    and block REDEEM_CONFIRMED from being applied.
    """
    import pathlib
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(
        "src.execution.settlement_commands.ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_tmp_db_negrisk),
        raising=False,
    )
    import src.state.db as _db_mod
    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_tmp_db_negrisk)
    try:
        cmd_id = _insert_tx_hashed_command(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_MISROUTED)
        web3 = _make_web3(_TX_HASH_MISROUTED, status=1, to_address=POLYGON_CTF_ADDRESS)

        results = sc.reconcile_pending_redeems(web3, trade_conn)

        # C1: must have returned a result
        assert len(results) == 1, f"C1 FAIL: expected 1 result, got {len(results)}"
        result = results[0]

        # C1: result error payload must contain REDEEM_NEGRISK_MISROUTED
        assert result.error_payload, "C1 FAIL: error_payload is empty/None"
        assert result.error_payload.get("errorCode") == "REDEEM_NEGRISK_MISROUTED", (
            f"C1 FAIL: errorCode={result.error_payload.get('errorCode')!r}, "
            "expected REDEEM_NEGRISK_MISROUTED"
        )

        # C1: result state must be REDEEM_OPERATOR_REQUIRED
        assert result.state == SettlementState.REDEEM_OPERATOR_REQUIRED, (
            f"C1 FAIL: result.state={result.state!r}, expected REDEEM_OPERATOR_REQUIRED"
        )

        # C1: tx_hash must be None (cleared so reseat sees a clean row)
        assert result.tx_hash is None, (
            f"C1 FAIL: result.tx_hash={result.tx_hash!r}, expected None"
        )

        # C1: DB row must reflect the reset
        row = _read_command(trade_conn, cmd_id)
        assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value, (
            f"C1 FAIL: DB state={row['state']!r}, expected REDEEM_OPERATOR_REQUIRED"
        )
        assert row["tx_hash"] is None, (
            f"C1 FAIL: DB tx_hash={row['tx_hash']!r}, expected NULL"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig


def test_standard_market_with_standard_ctf_tx_marks_confirmed(
    trade_conn, world_tmp_db_negrisk, monkeypatch
):
    """C2 (negative control): standard CTF market + confirmed tx to Standard CTF
    → reconcile marks REDEEM_CONFIRMED (normal path, no reset).
    """
    import pathlib
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(
        "src.execution.settlement_commands.ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_tmp_db_negrisk),
        raising=False,
    )
    import src.state.db as _db_mod
    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_tmp_db_negrisk)
    try:
        cmd_id = _insert_tx_hashed_command(trade_conn, _STANDARD_CONDITION_ID, _TX_HASH_STANDARD)
        web3 = _make_web3(_TX_HASH_STANDARD, status=1, to_address=POLYGON_CTF_ADDRESS)

        results = sc.reconcile_pending_redeems(web3, trade_conn)

        assert len(results) == 1, f"C2 FAIL: expected 1 result, got {len(results)}"
        result = results[0]

        # C2: standard market must reach REDEEM_CONFIRMED, NOT REDEEM_OPERATOR_REQUIRED
        assert result.state == SettlementState.REDEEM_CONFIRMED, (
            f"C2 FAIL: result.state={result.state!r}, expected REDEEM_CONFIRMED"
        )

        row = _read_command(trade_conn, cmd_id)
        assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
            f"C2 FAIL: DB state={row['state']!r}, expected REDEEM_CONFIRMED"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig


def test_negrisk_market_with_negrisk_adapter_tx_marks_confirmed(
    trade_conn, world_tmp_db_negrisk, monkeypatch
):
    """C3 (positive control): negRisk market + confirmed tx to NegRisk adapter
    → reconcile marks REDEEM_CONFIRMED (correct-adapter path, no reset).
    """
    import pathlib
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(
        "src.execution.settlement_commands.ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_tmp_db_negrisk),
        raising=False,
    )
    import src.state.db as _db_mod
    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_tmp_db_negrisk)
    try:
        # Use the _NEGRISK_CONDITION_ID (which has neg_risk=1 in the world DB)
        cmd_id = _insert_tx_hashed_command(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_NEGRISK_OK)
        web3 = _make_web3(_TX_HASH_NEGRISK_OK, status=1, to_address=POLYGON_NEGRISK_ADAPTER_ADDRESS)

        results = sc.reconcile_pending_redeems(web3, trade_conn)

        assert len(results) == 1, f"C3 FAIL: expected 1 result, got {len(results)}"
        result = results[0]

        # C3: correctly-routed negRisk tx must reach REDEEM_CONFIRMED
        assert result.state == SettlementState.REDEEM_CONFIRMED, (
            f"C3 FAIL: result.state={result.state!r}, expected REDEEM_CONFIRMED"
        )

        row = _read_command(trade_conn, cmd_id)
        assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
            f"C3 FAIL: DB state={row['state']!r}, expected REDEEM_CONFIRMED"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig


def test_negrisk_lookup_world_attach_failure_returns_false_safely(trade_conn, monkeypatch):
    """C4 (fail-safe): _lookup_market_neg_risk_via_world_attach returns False safely when
    world DB path is missing/invalid, so reconcile falls through to REDEEM_CONFIRMED
    (fail-open on lookup errors — does not crash or suppress other results).
    """
    import pathlib
    import src.execution.settlement_commands as sc

    # Point to a non-existent path so ATTACH fails
    missing_path = pathlib.Path("/tmp/zeus_world_nonexistent_antibody_test.db")
    monkeypatch.setattr(
        "src.execution.settlement_commands.ZEUS_WORLD_DB_PATH",
        missing_path,
        raising=False,
    )
    import src.state.db as _db_mod
    orig = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = missing_path
    try:
        result = sc._lookup_market_neg_risk_via_world_attach(trade_conn, _NEGRISK_CONDITION_ID)
        # C4: must return False, not raise
        assert result is False, (
            f"C4 FAIL: _lookup_market_neg_risk_via_world_attach returned {result!r} "
            "on missing world DB; expected False (fail-safe)"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig
