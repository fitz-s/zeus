# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19.md P1-4
"""Antibody: reconcile_pending_redeems must respect batch cap and wall-clock budget.

P1-4 (codereview-may19.md): the outer SELECT in reconcile_pending_redeems had
no LIMIT. With N rows each needing a _lookup_market_neg_risk_authoritative call
(up to 5s Gamma HTTP), one reconcile tick could block for N×5s.

Fix:
  (a) LIMIT via ZEUS_REDEEM_RECONCILE_BATCH_CAP (default 50)
  (b) per-call CLOB result cache (condition_id → Optional[bool])
  (c) wall-clock budget via ZEUS_REDEEM_RECONCILE_BUDGET_S (default 60s)

Antibody contracts:
  C1: with cap=5 and 20 seeded rows, one reconcile call processes ≤ 5 rows.
  C2: budget-exceeded path emits a log line containing RECONCILE_REDEEM_BUDGET_EXCEEDED.
  C3: CLOB cache prevents repeated lookup for the same condition_id within one call
      — with 5 rows sharing the same condition, _lookup_market_neg_risk_authoritative
      is called at most once.

Sed-flip targets:
  C1: remove LIMIT from SELECT → 20 rows processed instead of ≤ cap
  C2: remove budget check loop → BUDGET_EXCEEDED never logged
  C3: remove cache dict → lookup called N times for same condition
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    ensure_settlement_schema_ready,
    reconcile_pending_redeems,
)
from src.state.db import init_schema

NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)
_COND = "0xcccc0000000000000000000000000000000000000000000000000000000000dd"


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    ensure_settlement_schema_ready(db)
    yield db
    db.close()


def _seed_rows(conn, n: int, condition_id: str = _COND) -> None:
    """Seed n TX_HASHED rows. Each row gets a unique condition_id derived from
    the base + row index to avoid the (condition_id, market_id, payout_asset)
    unique partial index violation."""
    for i in range(n):
        # Produce a unique condition_id: take first 62 hex chars of base + 2-digit index.
        base_hex = condition_id.lstrip("0x")[:62]
        unique_cond = "0x" + base_hex + f"{i:02x}"
        conn.execute(
            """INSERT INTO settlement_commands
               (command_id, state, condition_id, market_id, payout_asset, requested_at, tx_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"row-{i:04d}",
                SettlementState.REDEEM_TX_HASHED.value,
                unique_cond,
                unique_cond,
                "USDC",
                NOW.isoformat(),
                f"0x{'00' * 31}{i:02x}",
            ),
        )
    conn.commit()


def _null_web3():
    """web3 stub that returns None for every receipt (rows stay in TX_HASHED)."""
    w = MagicMock()
    w.eth.get_transaction_receipt.return_value = None
    w.eth.block_number = 87000012
    return w


def test_c1_batch_cap_limits_rows_processed(conn, monkeypatch):
    """C1: with BATCH_CAP=5 and 20 seeded rows, at most 5 are fetched."""
    _seed_rows(conn, 20)
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "5")
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BUDGET_S", "3600")

    import src.execution.settlement_commands as sc
    lookup_calls: list[str] = []

    def _fake_lookup(c, cid):  # noqa: ARG001
        lookup_calls.append(cid)
        return False  # standard CTF, no further checks

    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative", _fake_lookup)

    web3 = MagicMock()
    web3.eth.get_transaction_receipt.return_value = {
        "status": 1,
        "transactionHash": "0x00",
        "blockNumber": 87000000,
        "logs": [],
    }
    web3.eth.block_number = 87000012

    reconcile_pending_redeems(web3, conn)

    # Receipt was fetched for processed rows; cap=5 means at most 5 receipts tried
    call_count = web3.eth.get_transaction_receipt.call_count
    assert call_count <= 5, (
        f"C1 FAIL: expected ≤ 5 rows processed (cap=5), got {call_count}. "
        "LIMIT was not applied to the SELECT."
    )


def test_c2_budget_exceeded_logged(conn, monkeypatch, caplog):
    """C2: when the wall-clock budget is exceeded mid-loop, a BUDGET_EXCEEDED
    log line is emitted."""
    import logging
    _seed_rows(conn, 10)
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "50")
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BUDGET_S", "0")  # budget = 0s → exceeded immediately

    import src.execution.settlement_commands as sc
    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative",
                        lambda c, cid: False)

    web3 = MagicMock()
    web3.eth.get_transaction_receipt.return_value = {
        "status": 1,
        "transactionHash": "0x00",
        "blockNumber": 87000000,
        "logs": [],
    }
    web3.eth.block_number = 87000012

    with caplog.at_level(logging.WARNING, logger="src.execution.settlement_commands"):
        reconcile_pending_redeems(web3, conn)

    budget_logs = [r for r in caplog.records if "RECONCILE_REDEEM_BUDGET_EXCEEDED" in r.message]
    assert budget_logs, (
        "C2 FAIL: RECONCILE_REDEEM_BUDGET_EXCEEDED was not logged when budget=0s. "
        "Budget check may be missing from the loop."
    )


def test_c3_clob_cache_deduplicates_lookups(conn, monkeypatch):
    """C3: multiple rows with the same condition_id → _lookup_market_neg_risk_authoritative
    called at most once per unique condition_id (cache hit for subsequent rows).

    The unique partial index prevents multiple TX_HASHED rows with the same
    (condition_id, market_id, payout_asset). Use different market_ids to
    allow two rows sharing the same condition_id, which is the realistic
    scenario (e.g. same condition + different markets or multiple assets).
    """
    SAME_COND = "0xeeee0000000000000000000000000000000000000000000000000000000000ff"
    # Seed two rows with the same condition_id but different market_ids.
    # This bypasses the unique index (which keys on condition_id + market_id + payout_asset).
    for i in range(3):
        conn.execute(
            """INSERT INTO settlement_commands
               (command_id, state, condition_id, market_id, payout_asset, requested_at, tx_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"cache-row-{i}",
                SettlementState.REDEEM_TX_HASHED.value,
                SAME_COND,
                f"market-{i}",   # distinct market_id bypasses the unique index
                "USDC",
                NOW.isoformat(),
                f"0x{'ee' * 31}{i:02x}",
            ),
        )
    conn.commit()

    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "50")
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BUDGET_S", "3600")

    import src.execution.settlement_commands as sc
    lookup_calls: list[str] = []

    def _counting_lookup(c, cid):  # noqa: ARG001
        lookup_calls.append(cid)
        return False

    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative", _counting_lookup)

    web3 = MagicMock()
    web3.eth.get_transaction_receipt.return_value = {
        "status": 1,
        "transactionHash": "0x00",
        "blockNumber": 87000000,
        "logs": [],
    }
    web3.eth.block_number = 87000012

    reconcile_pending_redeems(web3, conn)

    same_cond_calls = [c for c in lookup_calls if c == SAME_COND]
    assert len(same_cond_calls) <= 1, (
        f"C3 FAIL: _lookup_market_neg_risk_authoritative called {len(same_cond_calls)} "
        f"times for the same condition_id (expected ≤ 1, cache not working)."
    )
