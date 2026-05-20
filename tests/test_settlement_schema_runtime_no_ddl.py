# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19.md P1-2 (DDL out of hot path)
"""Antibody: hot-path settlement functions must not execute DDL.

P1-2 (codereview-may19.md): init_settlement_command_schema was called inside
request_redeem, submit_redeem, reconcile_pending_redeems, get_command, and
list_commands on every tick.  executescript() runs CREATE TABLE IF NOT EXISTS
plus idempotent ALTER TABLE — fine at boot, but creates schema-lock and
transaction-boundary risk on every hot-path call.

Fix: split into ensure_settlement_schema_ready (boot, runs DDL) and
assert_settlement_schema_ready (hot path, PRAGMA-only check).

Antibody contracts:
  C1: calling submit_redeem() on an already-initialized schema must NOT
      execute CREATE TABLE or ALTER TABLE statements.
  C2: calling reconcile_pending_redeems() on an already-initialized schema
      must NOT execute CREATE TABLE or ALTER TABLE statements.
  C3: calling get_command() / list_commands() must NOT execute DDL.
  C4: ensure_settlement_schema_ready() MAY execute DDL (boot path, not tested
      to be DDL-free; that would be an incorrect constraint).
  C5: assert_settlement_schema_ready() on a missing table raises
      SettlementSchemaNotReadyError, not silent pass-through.

Sed-flip target: revert hot-path calls back to init_settlement_command_schema
→ C1/C2/C3 fail (DDL detected in hot path).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.execution.settlement_commands import (
    SettlementSchemaNotReadyError,
    SettlementState,
    assert_settlement_schema_ready,
    ensure_settlement_schema_ready,
    get_command,
    list_commands,
    reconcile_pending_redeems,
    request_redeem,
)
from src.state.db import init_schema

NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)


class _DDLSnifferConn:
    """Wraps a real sqlite3 connection and records DDL statements."""

    def __init__(self, real_conn: sqlite3.Connection):
        self._conn = real_conn
        self.ddl_statements: list[str] = []

    def _is_ddl(self, sql: str) -> bool:
        s = sql.strip().upper()
        return s.startswith("CREATE ") or s.startswith("ALTER ") or s.startswith("DROP ")

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        if self._is_ddl(sql):
            self.ddl_statements.append(sql.strip()[:120])
        return self._conn.execute(sql, params)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        self.ddl_statements.append(f"[executescript] {sql[:60]}")
        return self._conn.executescript(sql)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    # Delegate attribute access for PRAGMA etc.
    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def initialized_conn():
    """A real :memory: connection with the settlement schema already created."""
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    init_schema(real)
    ensure_settlement_schema_ready(real)
    real.commit()
    yield real
    real.close()


def _fake_negrisk_lookup(monkeypatch):
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative",
                        lambda conn, cid: False)


def test_c1_submit_redeem_no_ddl(initialized_conn, monkeypatch):
    """C1: submit_redeem on an initialized schema must not emit DDL."""
    sniffer = _DDLSnifferConn(initialized_conn)

    # Seed a submittable row directly
    initialized_conn.execute(
        """INSERT INTO settlement_commands
           (command_id, state, condition_id, market_id, payout_asset, requested_at)
           VALUES ('sc-ddl-c1', ?, 'cond-ddl', 'cond-ddl', 'USDC', ?)""",
        (SettlementState.REDEEM_INTENT_CREATED.value, NOW.isoformat()),
    )
    initialized_conn.commit()

    # Patch cutover to allow and adapter to return a stub
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(sc, "redemption_decision",
                        lambda: MagicMock(allow_redemption=True, block_reason=None,
                                          state=MagicMock(value="LIVE")))
    monkeypatch.setattr(sc, "require_pusd_redemption_allowed", lambda x: x)

    fake_adapter = MagicMock()
    fake_adapter.redeem.return_value = {
        "success": False,
        "errorCode": "REDEEM_DEFERRED_TO_R1",
    }
    fake_ledger = MagicMock()

    from src.architecture.gate_runtime import check as _gate_check
    monkeypatch.setattr("src.architecture.gate_runtime.check", lambda *a, **kw: None)

    from src.execution.settlement_commands import submit_redeem
    submit_redeem("sc-ddl-c1", fake_adapter, fake_ledger, conn=sniffer)

    assert not sniffer.ddl_statements, (
        f"C1 FAIL: submit_redeem emitted DDL on initialized schema: "
        f"{sniffer.ddl_statements}"
    )


def test_c2_reconcile_no_ddl(initialized_conn, monkeypatch):
    """C2: reconcile_pending_redeems on initialized schema must not emit DDL."""
    sniffer = _DDLSnifferConn(initialized_conn)
    _fake_negrisk_lookup(monkeypatch)

    # Seed a TX_HASHED row
    initialized_conn.execute(
        """INSERT INTO settlement_commands
           (command_id, state, condition_id, market_id, payout_asset, requested_at, tx_hash)
           VALUES ('sc-ddl-c2', ?, 'cond-r2', 'cond-r2', 'USDC', ?, '0xdeadbeef')""",
        (SettlementState.REDEEM_TX_HASHED.value, NOW.isoformat()),
    )
    initialized_conn.commit()

    web3_stub = MagicMock()
    web3_stub.eth.get_transaction_receipt.return_value = {
        "status": 1,
        "transactionHash": "0xdeadbeef",
        "blockNumber": 87000000,
        "logs": [],
    }
    web3_stub.eth.block_number = 87000012

    reconcile_pending_redeems(web3_stub, sniffer)

    assert not sniffer.ddl_statements, (
        f"C2 FAIL: reconcile_pending_redeems emitted DDL: {sniffer.ddl_statements}"
    )


def test_c3_list_get_no_ddl(initialized_conn):
    """C3: get_command / list_commands must not emit DDL."""
    sniffer = _DDLSnifferConn(initialized_conn)

    list_commands(sniffer)

    assert not sniffer.ddl_statements, (
        f"C3 FAIL: list_commands emitted DDL: {sniffer.ddl_statements}"
    )


def test_c5_assert_raises_on_missing_schema():
    """C5: assert_settlement_schema_ready raises SettlementSchemaNotReadyError
    when called on a connection where the table has not been created."""
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    with pytest.raises(SettlementSchemaNotReadyError):
        assert_settlement_schema_ready(bare)
    bare.close()
