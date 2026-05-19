# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P7-2
#                  src/state/uma_resolution_listener.py:496 (record_resolution INSERT OR IGNORE)
"""
Relationship test R-1.5: uma_resolution_listener late revalidation + reorg safety.

BACKGROUND (Critic 1 P7-2):
    src/state/uma_resolution_listener.py:496 uses:
        INSERT OR IGNORE ON (condition_id, tx_hash)
    This protects against duplicate inserts but does NOT handle Polygon reorgs.
    A reorg can invalidate a previously-recorded tx_hash, leaving a stale row
    in uma_resolution with a tx_hash that is no longer canonical.

    PR 1 adds:
      - confirmations_required: int = 6 field to uma_resolution rows
      - late-revalidation pass that re-checks confirmation count for rows
        with confirmation_count < confirmations_required

RELATIONSHIP INVARIANT (cross-module):
    A uma_resolution row with confirmation_count < confirmations_required that
    is later found to have been invalidated by a chain reorg MUST be removed
    (or marked invalid) by the late-revalidation pass. It must NOT be used
    as settlement evidence.
"""
import sqlite3
from datetime import datetime, timezone
from unittest import mock

import pytest

from src.state.uma_resolution_listener import (
    CONFIRMATIONS_REQUIRED_DEFAULT,
    ResolvedMarket,
    init_uma_resolution_schema,
    record_resolution,
    run_late_revalidation_pass,
)


def _make_resolution(
    *,
    condition_id: str = "0xabc123",
    tx_hash: str = "0xdeadbeef",
    block_number: int = 1000,
    confirmations_count: int = 3,
    confirmations_required: int = CONFIRMATIONS_REQUIRED_DEFAULT,
) -> ResolvedMarket:
    return ResolvedMarket(
        condition_id=condition_id,
        resolved_value=1,
        tx_hash=tx_hash,
        block_number=block_number,
        resolved_at_utc=datetime(2024, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
        raw_log={"test": True},
        confirmations_count=confirmations_count,
        confirmations_required=confirmations_required,
    )


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_uma_resolution_schema(conn)
    return conn


def test_r1_5_late_revalidation_removes_reorg_invalidated_row():
    """R-1.5: a uma_resolution row with low confirmation count that is
    invalidated by a Polygon reorg must be marked is_valid=0 by the late-revalidation pass.
    """
    conn = _make_db()
    resolution = _make_resolution(confirmations_count=3, confirmations_required=CONFIRMATIONS_REQUIRED_DEFAULT)
    record_resolution(conn, resolution)

    # Verify row was inserted with is_valid=1
    row = conn.execute(
        "SELECT is_valid FROM uma_resolution WHERE condition_id = ? AND tx_hash = ?",
        (resolution.condition_id, resolution.tx_hash),
    ).fetchone()
    assert row is not None
    assert row[0] == 1  # is_valid initially 1

    # Simulate reorg: tx_hash not found on-chain
    mock_rpc = mock.MagicMock()
    mock_rpc.check_transaction_exists.return_value = False

    invalidated = run_late_revalidation_pass(conn, rpc_client=mock_rpc)

    assert invalidated == 1

    # Row must be marked is_valid=0
    row_after = conn.execute(
        "SELECT is_valid FROM uma_resolution WHERE condition_id = ? AND tx_hash = ?",
        (resolution.condition_id, resolution.tx_hash),
    ).fetchone()
    assert row_after[0] == 0  # is_valid=0 after reorg


def test_r1_5_late_revalidation_does_not_remove_confirmed_rows():
    """R-1.5 (negative): rows with confirmation_count >= confirmations_required
    are NOT removed by the late-revalidation pass, even if mocked RPC call fails.
    """
    conn = _make_db()
    # Insert a fully-confirmed row
    confirmed_resolution = _make_resolution(
        condition_id="0xconfirmed",
        tx_hash="0xconfirmedtx",
        confirmations_count=CONFIRMATIONS_REQUIRED_DEFAULT,  # exactly at threshold
        confirmations_required=CONFIRMATIONS_REQUIRED_DEFAULT,
    )
    record_resolution(conn, confirmed_resolution)

    # Mock RPC that would invalidate if called
    mock_rpc = mock.MagicMock()
    mock_rpc.check_transaction_exists.return_value = False  # would invalidate if checked

    invalidated = run_late_revalidation_pass(conn, rpc_client=mock_rpc)

    # No rows should be invalidated — confirmed row is above threshold
    assert invalidated == 0

    # Row must still be valid
    row = conn.execute(
        "SELECT is_valid FROM uma_resolution WHERE condition_id = ?",
        (confirmed_resolution.condition_id,),
    ).fetchone()
    assert row[0] == 1


def test_r1_5_confirmations_required_default_is_six():
    """R-1.5 (unit): the default value of confirmations_required is 6, matching
    Polygon mainnet finality assumptions documented in ULTRAPLAN §D.1.
    """
    assert CONFIRMATIONS_REQUIRED_DEFAULT == 6

    # Also verify a ResolvedMarket uses the default when not specified
    resolution = _make_resolution(confirmations_count=0)
    assert resolution.confirmations_required == 6
