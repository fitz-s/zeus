# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P2
#                  architecture/db_table_ownership.yaml (INV-37 explicit)
"""
Relationship test R-1.4: INV-37 ATTACH+SAVEPOINT compliance.

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
"""
import sqlite3
from contextlib import contextmanager
from datetime import date
from unittest import mock

import pytest

from src.state.settlement_writers import ERA_BASIS_UMA_OO_V2, write_settlement_v2_with_era_provenance


def _minimal_settlement() -> dict:
    return {
        "city": "TestCity",
        "target_date": "2024-07-15",
        "temperature_metric": "max",
        "market_slug": "test-market",
        "winning_bin": "[25,30)",
        "settlement_value": 27.5,
        "settlement_source": "wu_icao",
        "settled_at": "2024-07-15T12:00:00Z",
        "authority": "VERIFIED",
        "provenance": {"writer": "test"},
        "recorded_at": "2024-07-15T12:00:00Z",
        "condition_id": None,
    }


def test_r1_4_settlement_write_uses_attach_savepoint():
    """R-1.4: write_settlement_v2_with_era_provenance() must use ATTACH+SAVEPOINT
    (via get_forecasts_connection_with_world()), never bare sqlite3.connect().

    Assertion contract:
      1. sqlite3.connect() is NOT called directly during the write
      2. get_forecasts_connection_with_world() IS called
      3. SAVEPOINT era_dispatch is issued on the connection
      4. Either RELEASE SAVEPOINT or ROLLBACK TO SAVEPOINT is issued
    """
    executed_sql = []

    fake_conn = mock.MagicMock()

    def _record_execute(sql, *args, **kwargs):
        executed_sql.append(sql.strip())
        return mock.MagicMock()

    fake_conn.execute.side_effect = _record_execute
    fake_conn.__enter__ = lambda s: fake_conn
    fake_conn.__exit__ = mock.MagicMock(return_value=False)

    @contextmanager
    def _fake_get_conn():
        yield fake_conn

    settlement = _minimal_settlement()

    with mock.patch("src.state.db.get_forecasts_connection_with_world", _fake_get_conn), \
         mock.patch("src.state.db.log_settlement_v2", return_value={"status": "written"}) as mock_log, \
         mock.patch("sqlite3.connect") as mock_sqlite_connect:

        write_settlement_v2_with_era_provenance(settlement, ERA_BASIS_UMA_OO_V2)

        # CRITICAL: bare sqlite3.connect() must NOT be called
        mock_sqlite_connect.assert_not_called()

        # log_settlement_v2 (i.e. the writer) must be called
        mock_log.assert_called_once()

    # SAVEPOINT era_dispatch must be issued
    savepoint_sqls = [s for s in executed_sql if "SAVEPOINT" in s.upper() and "era_dispatch" in s]
    assert len(savepoint_sqls) >= 1, f"Expected SAVEPOINT era_dispatch in SQL, got: {executed_sql}"

    # RELEASE (success path) must also be issued
    release_sqls = [s for s in executed_sql if "RELEASE" in s.upper() and "era_dispatch" in s]
    assert len(release_sqls) >= 1, f"Expected RELEASE SAVEPOINT era_dispatch in SQL, got: {executed_sql}"


def test_r1_4_exception_triggers_savepoint_rollback():
    """R-1.4 (error path): when an exception occurs inside write_settlement_v2_with_era_provenance(),
    ROLLBACK TO SAVEPOINT era_dispatch must be issued before re-raising the exception.
    No partial writes must remain in the DB (atomicity guarantee).
    """
    executed_sql = []

    fake_conn = mock.MagicMock()

    def _record_execute(sql, *args, **kwargs):
        executed_sql.append(sql.strip())
        return mock.MagicMock()

    fake_conn.execute.side_effect = _record_execute
    fake_conn.__enter__ = lambda s: fake_conn
    fake_conn.__exit__ = mock.MagicMock(return_value=False)

    @contextmanager
    def _fake_get_conn():
        yield fake_conn

    settlement = _minimal_settlement()

    with mock.patch("src.state.db.get_forecasts_connection_with_world", _fake_get_conn), \
         mock.patch("src.state.db.log_settlement_v2", side_effect=RuntimeError("simulated write failure")):

        with pytest.raises(RuntimeError, match="simulated write failure"):
            write_settlement_v2_with_era_provenance(settlement, ERA_BASIS_UMA_OO_V2)

    # ROLLBACK TO SAVEPOINT must be issued on error path
    rollback_sqls = [s for s in executed_sql if "ROLLBACK" in s.upper() and "era_dispatch" in s]
    assert len(rollback_sqls) >= 1, f"Expected ROLLBACK TO SAVEPOINT era_dispatch in SQL, got: {executed_sql}"


def test_r1_4_caller_provided_conn_skips_savepoint():
    """R-1.4 (caller-conn path): when conn is provided by caller, no SAVEPOINT
    is added — the caller owns the transaction boundary. get_forecasts_connection_with_world()
    must NOT be called in this path.
    """
    executed_sql = []

    fake_conn = mock.MagicMock()

    def _record_execute(sql, *args, **kwargs):
        executed_sql.append(sql.strip())
        return mock.MagicMock()

    fake_conn.execute.side_effect = _record_execute

    settlement = _minimal_settlement()

    with mock.patch("src.state.db.get_forecasts_connection_with_world") as mock_get_conn, \
         mock.patch("src.state.db.log_settlement_v2", return_value={"status": "written"}):

        write_settlement_v2_with_era_provenance(settlement, ERA_BASIS_UMA_OO_V2, conn=fake_conn)

        # When conn is provided, get_forecasts_connection_with_world must NOT be called
        mock_get_conn.assert_not_called()

    # No SAVEPOINT should appear when caller owns the transaction
    savepoint_sqls = [s for s in executed_sql if "SAVEPOINT" in s.upper()]
    assert len(savepoint_sqls) == 0, (
        f"Expected no SAVEPOINT when conn is caller-provided, got: {savepoint_sqls}"
    )
