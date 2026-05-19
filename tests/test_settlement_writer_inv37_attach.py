# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P2
#                  architecture/db_table_ownership.yaml (INV-37 explicit)
"""
Relationship test R-1.4: INV-37 ATTACH+SAVEPOINT compliance.

SCAFFOLD — test body not yet implemented (xfail pending implementation).

RELATIONSHIP INVARIANT (cross-module):
    When write_settlement_v2_with_era_provenance() writes a settlement involving
    both forecasts.db (settlements_v2) and world.db (uma_resolution / era_watermark),
    it MUST:
      1. Obtain the connection via get_forecasts_connection_with_world() — which
         executes ATTACH DATABASE 'zeus-world.db' AS world
      2. Execute SAVEPOINT era_dispatch before any writes
      3. Execute RELEASE SAVEPOINT era_dispatch on success
      4. Execute ROLLBACK TO SAVEPOINT era_dispatch on exception
      5. NEVER open either DB via bare sqlite3.connect()

    This is INV-37 (explicit, not generic). Violation raises Inv37Violation.

TEST APPROACH (SCAFFOLD — body is docstring only):
    Monkey-patch sqlite3.connect to capture all connection calls.
    Assert that no bare sqlite3.connect() call is made during
    write_settlement_v2_with_era_provenance() execution.

    Also mock get_forecasts_connection_with_world() to capture connection calls,
    asserting:
      - get_forecasts_connection_with_world() is called exactly once
      - ATTACH DATABASE appears in the SQL issued (or is implicit in the mock)
      - SAVEPOINT era_dispatch is issued via conn.execute()
      - Either RELEASE SAVEPOINT or ROLLBACK TO SAVEPOINT is issued

    The test uses a synthetic settlement dict and ERA_BASIS_UMA_OO_V2 basis.
"""
import sqlite3
from unittest import mock

import pytest

# SCAFFOLD: import will succeed after implementation
# from src.state.settlement_writers import write_settlement_v2_with_era_provenance, ERA_BASIS_UMA_OO_V2
# from src.state.db import get_forecasts_connection_with_world


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_4_settlement_write_uses_attach_savepoint():
    """R-1.4: write_settlement_v2_with_era_provenance() must use ATTACH+SAVEPOINT
    (via get_forecasts_connection_with_world()), never bare sqlite3.connect().

    Assertion contract:
      1. sqlite3.connect() is NOT called directly during the write
      2. get_forecasts_connection_with_world() IS called
      3. SAVEPOINT era_dispatch is issued on the connection
      4. Either RELEASE SAVEPOINT or ROLLBACK TO SAVEPOINT is issued
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_4_exception_triggers_savepoint_rollback():
    """R-1.4 (error path): when an exception occurs inside write_settlement_v2_with_era_provenance(),
    ROLLBACK TO SAVEPOINT era_dispatch must be issued before re-raising the exception.
    No partial writes must remain in the DB (atomicity guarantee).
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_4_bare_sqlite_connect_raises_inv37_violation():
    """R-1.4 (guard test): attempting to call sqlite3.connect() directly on
    zeus-forecasts.db or zeus-world.db raises Inv37Violation (per src/state/db.py).
    This test verifies the guard is active, not just the settlement writer behaviour.
    """
    ...
