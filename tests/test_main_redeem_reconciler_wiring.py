# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR-I.5 wiring + architecture/invariants.md NEGRISK-MISROUTE-GUARD
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody test — _redeem_reconciler_cycle() actually calls reconcile_pending_redeems
#          end-to-end (Karachi auto-recovery path).
# Reuse: Run when modifying _redeem_reconciler_cycle() in src/main.py or reconcile_pending_redeems().
"""Antibody test: _redeem_reconciler_cycle() wiring to reconcile_pending_redeems.

Verifies that the PR-I.5 wiring is in place: the scheduler function is no longer
a stub but actually invokes reconcile_pending_redeems(web3, conn), which runs the
Karachi antibody guard.

Antibody contract (sed-flip verifiable):
  W1: negRisk market + REDEEM_TX_HASHED + confirmed tx to Standard CTF
      → _redeem_reconciler_cycle() drives state to REDEEM_OPERATOR_REQUIRED,
         tx_hash cleared to NULL, error_payload contains REDEEM_NEGRISK_MISROUTED.

Sed-flip verification: patching the negRisk guard condition in settlement_commands.py
(e.g. `is_negrisk_market and tx_to == POLYGON_CTF_ADDRESS.lower()` → `False`) must
turn this test RED.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
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

NOW = datetime(2026, 5, 19, 8, 26, 0, tzinfo=timezone.utc)
_NEGRISK_CONDITION_ID = "0xkarachi_c8c220f5" + "a" * 46
_TX_HASH_KARACHI = "0x0c85d94640d33f38" + "0" * 46


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def world_tmp_db():
    """Temp world DB with neg_risk=1 for the Karachi condition."""
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
            "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, neg_risk) "
            "VALUES (?, ?, 1)",
            ("snap-karachi", _NEGRISK_CONDITION_ID),
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
def trade_conn():
    """In-memory trade connection with full settlement schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_tx_hashed_command(conn, condition_id: str, tx_hash: str) -> str:
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
        "UPDATE settlement_commands SET state = ?, tx_hash = ? WHERE command_id = ?",
        (SettlementState.REDEEM_TX_HASHED.value, tx_hash, cmd_id),
    )
    conn.commit()
    return cmd_id


_PAYOUT_REDEMPTION_TOPIC = (
    "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
)


def _make_receipt(tx_hash: str, status: int, to_address: str) -> dict:
    """Build a minimal receipt dict with a PayoutRedemption log from `to_address`.

    The logs-based antibody guard (4th iteration) inspects logs[*].address
    for the PayoutRedemption topic, not receipt.to.
    """
    return {
        "status": status,
        "to": to_address,
        "blockNumber": 100,
        "block_number": 100,
        "logs": [
            {
                "address": to_address,
                "topics": [_PAYOUT_REDEMPTION_TOPIC, "0x" + "cc" * 32],
                "data": "0x",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Test W1: full end-to-end cycle wiring (Karachi auto-recovery)
# ---------------------------------------------------------------------------

def test_reconciler_cycle_drives_negrisk_misroute_to_operator_required(
    trade_conn, world_tmp_db, monkeypatch
):
    """W1 (primary antibody): _redeem_reconciler_cycle() wired — Karachi negRisk misroute
    reaches REDEEM_OPERATOR_REQUIRED via the reconciler scheduler function.

    If this test PASSES but the reconciler is a stub, it means the stub accidentally
    returned before reaching reconcile_pending_redeems; run with -v to see log output.
    If the sed-flip of the negRisk guard turns this RED, the antibody is live.
    """
    import pathlib
    import src.execution.settlement_commands as sc
    import src.main as main_mod

    # Seed the REDEEM_TX_HASHED row (Karachi tx to Standard CTF)
    cmd_id = _insert_tx_hashed_command(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_KARACHI)

    # Patch world DB path for negRisk lookup
    world_path = pathlib.Path(world_tmp_db)
    monkeypatch.setattr("src.execution.settlement_commands.ZEUS_WORLD_DB_PATH", world_path, raising=False)
    import src.state.db as _db_mod
    monkeypatch.setattr(_db_mod, "ZEUS_WORLD_DB_PATH", world_path)

    # Patch get_mode → "live"
    monkeypatch.setattr("src.main.get_mode", lambda: "live")

    # Patch acquire_lock → always acquired
    @contextmanager
    def _fake_lock(name):
        yield True

    monkeypatch.setattr("src.main.acquire_lock", _fake_lock, raising=False)
    # Also patch the import inside the function
    import src.data.dual_run_lock as _lock_mod
    monkeypatch.setattr(_lock_mod, "acquire_lock", _fake_lock)

    # Patch get_trade_connection → wrapper that delegates but silences close()
    # so our post-call assertions can still read state.
    class _NoCloseConn:
        """Proxy that forwards everything except close() to the real conn."""
        def __init__(self, real):
            self._real = real
        def close(self):
            pass  # no-op — keep connection alive for assertions
        def __getattr__(self, name):
            return getattr(self._real, name)

    wrapped_conn = _NoCloseConn(trade_conn)
    monkeypatch.setattr("src.main.get_trade_connection", lambda **_kw: wrapped_conn, raising=False)
    import src.state.db as db_mod
    monkeypatch.setattr(db_mod, "get_trade_connection", lambda **_kw: wrapped_conn)

    # Patch web3.Web3 — fake that returns a SimpleNamespace with receipt for our tx
    receipt = _make_receipt(_TX_HASH_KARACHI, status=1, to_address=POLYGON_CTF_ADDRESS)
    fake_eth = SimpleNamespace(
        get_transaction_receipt=lambda _h: receipt,
        block_number=105,
    )
    fake_w3_instance = SimpleNamespace(eth=fake_eth)

    class FakeHTTPProvider:
        def __init__(self, *a, **kw):
            pass

    class FakeWeb3:
        HTTPProvider = FakeHTTPProvider
        def __init__(self, *a, **kw):
            pass
        @property
        def eth(self):
            return fake_eth

    _web3_mod = pytest.importorskip("web3")
    monkeypatch.setattr(_web3_mod, "Web3", FakeWeb3)

    # Patch _write_scheduler_health to suppress filesystem side-effects
    monkeypatch.setattr("src.main._write_scheduler_health", lambda *a, **kw: None)

    # Run the full cycle
    main_mod._redeem_reconciler_cycle()

    # W1: DB state must be REDEEM_OPERATOR_REQUIRED
    row = trade_conn.execute(
        "SELECT state, tx_hash, error_payload FROM settlement_commands WHERE command_id = ?",
        (cmd_id,),
    ).fetchone()
    assert row is not None, "W1 FAIL: row not found after reconciler cycle"
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value, (
        f"W1 FAIL: state={row['state']!r}, expected REDEEM_OPERATOR_REQUIRED. "
        "Reconciler may still be a stub — check that it calls reconcile_pending_redeems."
    )
    assert row["tx_hash"] is None, (
        f"W1 FAIL: tx_hash={row['tx_hash']!r}, expected NULL (must be cleared for reseat)"
    )
    import json
    payload = json.loads(row["error_payload"]) if row["error_payload"] else {}
    assert payload.get("errorCode") == "REDEEM_NEGRISK_MISROUTED", (
        f"W1 FAIL: errorCode={payload.get('errorCode')!r}, expected REDEEM_NEGRISK_MISROUTED"
    )
