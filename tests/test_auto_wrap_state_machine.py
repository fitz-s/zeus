# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: .omc/plans/2026-05-19-auto-wrap-post-redeem.md
"""State machine antibody tests for wrap_unwrap_commands.py.

Covers:
  SM1: enqueue_wrap_if_balance_above_threshold inserts WRAP_REQUESTED on first call.
  SM2: Idempotency gate — second call does NOT insert if non-terminal row exists.
  SM3: Threshold gate — balance ≤ threshold yields None (no insert).
  SM4: WRAP_REQUESTED → WRAP_APPROVE_TX_HASHED → WRAP_APPROVED → WRAP_TX_HASHED
       → WRAP_CONFIRMED full happy path.
  SM5: fail_wrap transitions to WRAP_FAILED (terminal).
  SM6: DB target is world (get_world_connection), not trades DB.
  SM7: reconcile_pending_wraps advances WRAP_TX_HASHED → WRAP_CONFIRMED and calls
       adapter.update_balance_allowance() on WRAP_CONFIRMED.
  SM8: reconcile_pending_wraps advances WRAP_APPROVE_TX_HASHED → WRAP_APPROVED.

Sed-flip antibodies embedded in SM2 (idempotency) and SM6 (DB target).
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


@pytest.fixture
def world_conn(tmp_path):
    """Provide an isolated in-memory-like SQLite connection in the world slot."""
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _fake_w3_with_balance(balance_micro: int):
    """Return a mock web3 instance that returns balance_micro from eth_call."""
    w3 = MagicMock()
    # balanceOf returns uint256 as big-endian 32 bytes.
    w3.eth.call.return_value = balance_micro.to_bytes(32, "big")
    return w3


def _fake_w3_with_receipt(tx_hash: str, *, status: int = 1, block_number: int = 12345):
    """Return a mock web3 instance that returns a successful receipt."""
    w3 = MagicMock()
    receipt = MagicMock()
    receipt.status = status
    receipt.blockNumber = block_number
    receipt.get.side_effect = lambda k, default=None: {
        "status": status,
        "blockNumber": block_number,
    }.get(k, default)
    w3.eth.get_transaction_receipt.return_value = receipt
    return w3


# ---------------------------------------------------------------------------
# SM1: enqueue inserts on first call when balance > threshold
# ---------------------------------------------------------------------------

def test_sm1_enqueue_inserts_on_first_call(world_conn):
    """SM1: enqueue_wrap_if_balance_above_threshold inserts WRAP_REQUESTED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        enqueue_wrap_if_balance_above_threshold,
        get_command,
    )

    w3 = _fake_w3_with_balance(500_000)  # $0.50 > default threshold $0.10
    safe_addr = "0xSafeAddress"

    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            safe_addr, w3, world_conn, threshold_micro=100_000
        )
    world_conn.commit()

    assert command_id is not None
    row = get_command(command_id, world_conn)
    assert row["state"] == WrapUnwrapState.WRAP_REQUESTED.value
    assert row["direction"] == "WRAP"
    assert row["amount_micro"] == 500_000


# ---------------------------------------------------------------------------
# SM2: idempotency — no double-insert
# ---------------------------------------------------------------------------

def test_sm2_idempotency_no_double_insert(world_conn):
    """SM2: Second call returns None when non-terminal WRAP row exists.

    Sed-flip: remove the idempotency gate check in enqueue_wrap_if_balance_above_threshold
    → this test turns RED (command_id2 would be non-None).
    """
    from src.execution.wrap_unwrap_commands import enqueue_wrap_if_balance_above_threshold

    w3 = _fake_w3_with_balance(500_000)
    safe_addr = "0xSafeAddress"

    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id1 = enqueue_wrap_if_balance_above_threshold(
            safe_addr, w3, world_conn, threshold_micro=100_000
        )
        world_conn.commit()
        # Second call — row already exists in WRAP_REQUESTED (non-terminal).
        command_id2 = enqueue_wrap_if_balance_above_threshold(
            safe_addr, w3, world_conn, threshold_micro=100_000
        )

    assert command_id1 is not None
    assert command_id2 is None, (
        "Idempotency gate failed: enqueue returned a second command_id when "
        "a non-terminal WRAP row already exists."
    )


# ---------------------------------------------------------------------------
# SM3: threshold gate
# ---------------------------------------------------------------------------

def test_sm3_threshold_gate_no_insert_below_threshold(world_conn):
    """SM3: balance < threshold → returns None, no row inserted."""
    from src.execution.wrap_unwrap_commands import enqueue_wrap_if_balance_above_threshold

    safe_addr = "0xSafeAddress"
    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=50_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            safe_addr, MagicMock(), world_conn, threshold_micro=100_000
        )

    assert command_id is None


def test_sm3_threshold_exact_boundary_no_insert(world_conn):
    """SM3 boundary: balance == threshold → no insert (strictly above required)."""
    from src.execution.wrap_unwrap_commands import enqueue_wrap_if_balance_above_threshold

    safe_addr = "0xSafeAddress"
    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=100_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            safe_addr, MagicMock(), world_conn, threshold_micro=100_000
        )

    assert command_id is None


# ---------------------------------------------------------------------------
# SM4: full happy path state transitions
# ---------------------------------------------------------------------------

def test_sm4_full_happy_path_state_transitions(world_conn):
    """SM4: WRAP_REQUESTED → APPROVE_TX_HASHED → APPROVED → TX_HASHED → CONFIRMED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        confirm_wrap,
        enqueue_wrap_if_balance_above_threshold,
        get_command,
        mark_wrap_approve_tx_hashed,
        mark_wrap_approved,
        mark_wrap_tx_hashed,
    )

    w3 = _fake_w3_with_balance(1_000_000)
    safe_addr = "0xSafeAddress"

    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            safe_addr, w3, world_conn, threshold_micro=100_000
        )
    world_conn.commit()
    assert get_command(command_id, world_conn)["state"] == WrapUnwrapState.WRAP_REQUESTED.value

    mark_wrap_approve_tx_hashed(command_id, "0x" + "a" * 64, conn=world_conn)
    assert get_command(command_id, world_conn)["state"] == WrapUnwrapState.WRAP_APPROVE_TX_HASHED.value
    assert get_command(command_id, world_conn)["tx_kind"] == "APPROVE"

    mark_wrap_approved(command_id, conn=world_conn)
    assert get_command(command_id, world_conn)["state"] == WrapUnwrapState.WRAP_APPROVED.value

    mark_wrap_tx_hashed(command_id, "0x" + "b" * 64, conn=world_conn)
    assert get_command(command_id, world_conn)["state"] == WrapUnwrapState.WRAP_TX_HASHED.value
    assert get_command(command_id, world_conn)["tx_kind"] == "WRAP"

    confirm_wrap(command_id, confirmation_count=1, conn=world_conn)
    final = get_command(command_id, world_conn)
    assert final["state"] == WrapUnwrapState.WRAP_CONFIRMED.value
    assert final["terminal_at"] is not None


# ---------------------------------------------------------------------------
# SM5: fail_wrap → WRAP_FAILED (terminal)
# ---------------------------------------------------------------------------

def test_sm5_fail_wrap_transitions_to_failed(world_conn):
    """SM5: fail_wrap() moves any WRAP row to WRAP_FAILED terminal state."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        enqueue_wrap_if_balance_above_threshold,
        fail_wrap,
        get_command,
    )

    w3 = _fake_w3_with_balance(1_000_000)
    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            "0xSafe", w3, world_conn, threshold_micro=100_000
        )
    world_conn.commit()

    fail_wrap(
        command_id,
        error_payload={"reason": "test_failure", "code": "WRAP_GAS_ESTIMATE_REVERTED"},
        conn=world_conn,
    )
    final = get_command(command_id, world_conn)
    assert final["state"] == WrapUnwrapState.WRAP_FAILED.value
    assert final["terminal_at"] is not None
    assert final["error_payload"] is not None


# ---------------------------------------------------------------------------
# SM6: DB target is world (get_world_connection)
# ---------------------------------------------------------------------------

def test_sm6_db_target_uses_world_connection():
    """SM6: _transition and _request use get_world_connection, not get_trade_connection_with_world.

    Sed-flip: change get_world_connection import in _transition/enqueue to
    get_trade_connection_with_world → this test turns RED.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "execution" / "wrap_unwrap_commands.py"
    ).read_text()

    # Must NOT import/use get_trade_connection_with_world
    assert "get_trade_connection_with_world" not in source, (
        "DB ownership bug: wrap_unwrap_commands.py imports/uses "
        "get_trade_connection_with_world (writes to trades DB). "
        "All wrap writes must use get_world_connection (world DB)."
    )

    # Must use get_world_connection
    assert "get_world_connection" in source, (
        "wrap_unwrap_commands.py must use get_world_connection for world DB access."
    )


# ---------------------------------------------------------------------------
# SM7: reconcile_pending_wraps advances WRAP_TX_HASHED → WRAP_CONFIRMED
#      and calls adapter.update_balance_allowance()
# ---------------------------------------------------------------------------

def test_sm7_reconcile_wrap_confirmed_calls_balance_refresh(world_conn):
    """SM7: reconcile_pending_wraps calls adapter.update_balance_allowance on WRAP_CONFIRMED.

    Sed-flip: remove the update_balance_allowance call from reconcile_pending_wraps
    → adapter.update_balance_allowance is never called (assertion fails).
    """
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        enqueue_wrap_if_balance_above_threshold,
        get_command,
        mark_wrap_approve_tx_hashed,
        mark_wrap_approved,
        mark_wrap_tx_hashed,
        reconcile_pending_wraps,
    )

    # Set up a WRAP_TX_HASHED row.
    w3_enqueue = _fake_w3_with_balance(1_000_000)
    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            "0xSafe", w3_enqueue, world_conn, threshold_micro=100_000
        )
    world_conn.commit()
    tx_hash_b = "0x" + "b" * 64
    mark_wrap_approve_tx_hashed(command_id, "0x" + "a" * 64, conn=world_conn)
    mark_wrap_approved(command_id, conn=world_conn)
    mark_wrap_tx_hashed(command_id, tx_hash_b, conn=world_conn)
    world_conn.commit()

    # Provide w3 with a successful receipt for the wrap tx.
    w3_reconcile = _fake_w3_with_receipt(tx_hash_b, status=1, block_number=99)
    adapter = MagicMock()

    results = reconcile_pending_wraps(w3_reconcile, adapter, world_conn)

    assert results, "reconcile_pending_wraps returned empty list"
    final = get_command(command_id, world_conn)
    assert final["state"] == WrapUnwrapState.WRAP_CONFIRMED.value, (
        f"Expected WRAP_CONFIRMED; got {final['state']}"
    )
    adapter.update_balance_allowance.assert_called_once(), (
        "update_balance_allowance must be called on WRAP_CONFIRMED to refresh CLOB ledger"
    )


# ---------------------------------------------------------------------------
# SM8: reconcile_pending_wraps advances WRAP_APPROVE_TX_HASHED → WRAP_APPROVED
# ---------------------------------------------------------------------------

def test_sm8_reconcile_approve_tx_hashed_to_approved(world_conn):
    """SM8: reconcile_pending_wraps advances WRAP_APPROVE_TX_HASHED to WRAP_APPROVED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        enqueue_wrap_if_balance_above_threshold,
        get_command,
        mark_wrap_approve_tx_hashed,
        reconcile_pending_wraps,
    )

    w3_enqueue = _fake_w3_with_balance(1_000_000)
    with patch("src.execution.wrap_unwrap_commands._read_usdce_balance", return_value=500_000):
        command_id = enqueue_wrap_if_balance_above_threshold(
            "0xSafe", w3_enqueue, world_conn, threshold_micro=100_000
        )
    world_conn.commit()
    approve_tx = "0x" + "a" * 64
    mark_wrap_approve_tx_hashed(command_id, approve_tx, conn=world_conn)
    world_conn.commit()

    w3_reconcile = _fake_w3_with_receipt(approve_tx, status=1, block_number=100)
    adapter = MagicMock()
    results = reconcile_pending_wraps(w3_reconcile, adapter, world_conn)

    assert results, "reconcile_pending_wraps returned empty"
    final = get_command(command_id, world_conn)
    assert final["state"] == WrapUnwrapState.WRAP_APPROVED.value, (
        f"Expected WRAP_APPROVED; got {final['state']}"
    )
    # update_balance_allowance is NOT called until WRAP_CONFIRMED.
    adapter.update_balance_allowance.assert_not_called()
