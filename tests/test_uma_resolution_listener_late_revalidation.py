# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P7-2
#                  src/state/uma_resolution_listener.py:496 (record_resolution INSERT OR IGNORE)
"""
Relationship test R-1.5: uma_resolution_listener late revalidation + reorg safety.

SCAFFOLD — test body not yet implemented (xfail pending implementation).

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

TEST (SCAFFOLD — body is docstring only):

R-1.5 (late revalidation removes reorg-invalidated rows):
    Setup:
      - Insert a synthetic uma_resolution row with
        confirmation_count = 3, confirmations_required = 6, tx_hash = '0x...'
      - Simulate a chain reorg: mock the eth_call / RPC that would confirm
        this tx_hash, returning "tx not found" (invalidated by reorg)

    Execute:
      - Run the late-revalidation pass from uma_resolution_listener

    Assert:
      - The row with tx_hash = '0x...' is either removed or marked
        is_valid = False in the uma_resolution table
      - A settlement that relied on this row would NOT be counted as confirmed
      - The late-revalidation pass does NOT remove rows with
        confirmation_count >= confirmations_required (those are safe)
"""
import pytest

# SCAFFOLD: import will succeed after implementation
# from src.state.uma_resolution_listener import run_late_revalidation_pass, record_resolution


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_5_late_revalidation_removes_reorg_invalidated_row():
    """R-1.5: a uma_resolution row with low confirmation count that is
    invalidated by a Polygon reorg must be removed by the late-revalidation pass.
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_5_late_revalidation_does_not_remove_confirmed_rows():
    """R-1.5 (negative): rows with confirmation_count >= confirmations_required
    are NOT removed by the late-revalidation pass, even if mocked RPC call fails.
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_5_confirmations_required_default_is_six():
    """R-1.5 (unit): the default value of confirmations_required is 6, matching
    Polygon mainnet finality assumptions documented in ULTRAPLAN §D.1.
    """
    ...
