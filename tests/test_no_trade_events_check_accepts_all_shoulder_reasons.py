# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md §"NoTradeReason additions"
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: C1 antibody — no_trade_events table CHECK constraint accepts all SHOULDER_* NoTradeReason values
# Reuse: requires table-rebuild migration (migrate_no_trade_events_rebuild_phase3_t2.py) to have run first

"""C1 antibody: no_trade_events table accepts all SHOULDER_* NoTradeReason values.

C1 per plan §2 T2: after the table-rebuild migration, the no_trade_events
CHECK constraint must accept all 6 new SHOULDER_* members.

Structural probe: verify the CHECK constraint SQL (as stored in sqlite_master)
includes each SHOULDER_* member string value. This test catches the failure
mode where the migration runs but the expanded enum-derived CHECK is not applied.

Production-pass test (test_c1_*_accepts_shoulder_write) is SCAFFOLD-skipped
until the table-rebuild migration runs. The structural probe
(test_c1_check_constraint_includes_shoulder_strings) runs immediately against
the in-memory init_schema DB to confirm enum-derived SQL expansion.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.contracts.no_trade_reason import NoTradeReason


# ---------------------------------------------------------------------------
# C1-structural: CHECK constraint SQL includes SHOULDER_* strings
# ---------------------------------------------------------------------------

def test_c1_check_constraint_includes_all_shoulder_reason_strings():
    """C1: no_trade_events CREATE TABLE SQL (sqlite_master) includes all 6
    SHOULDER_* reason value strings in the CHECK clause.

    This probe verifies the enum-derived _REASON_VALUES_SQL expansion works
    correctly for newly added SHOULDER_* members. Runs against init_schema
    :memory: DB — no migration needed for this structural check.
    """
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='no_trade_events'"
    ).fetchone()
    assert row is not None, "no_trade_events table not found in init_schema DB"

    table_sql = row[0]
    shoulder_members = [m for m in NoTradeReason if m.name.startswith("SHOULDER_")]
    assert len(shoulder_members) == 6, (
        f"Expected 6 SHOULDER_* members, got {len(shoulder_members)}"
    )

    missing_in_check = []
    for member in shoulder_members:
        if member.value not in table_sql:
            missing_in_check.append(member.value)

    assert not missing_in_check, (
        f"SHOULDER_* reason values missing from no_trade_events CHECK SQL: "
        f"{missing_in_check}\nFull SQL:\n{table_sql}"
    )


def test_c1_all_no_trade_reason_values_in_check_constraint():
    """C1: All NoTradeReason values (not just SHOULDER_*) appear in CHECK SQL.

    Regression guard: ensures the _REASON_VALUES_SQL generation in
    no_trade_events_schema.py iterates ALL enum members, not a static list.
    """
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='no_trade_events'"
    ).fetchone()
    assert row is not None
    table_sql = row[0]

    missing = [m.value for m in NoTradeReason if m.value not in table_sql]
    assert not missing, (
        f"NoTradeReason values missing from CHECK SQL: {missing}"
    )


# ---------------------------------------------------------------------------
# C1-write: actual INSERT with SHOULDER_* reason accepted by CHECK
# (SCAFFOLD-skipped: requires rebuild migration to expand existing DB's CHECK)
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason="SCAFFOLD — requires table-rebuild migration (Phase 3 T2 production pass) "
    "to expand existing zeus-world.db CHECK constraint. "
    "C1 structural probe above verifies expansion via init_schema :memory: DB."
)
def test_c1_insert_shoulder_stress_fail_accepted():
    """C1: INSERT with reason=SHOULDER_STRESS_FAIL accepted after rebuild migration."""
    pass


@pytest.mark.skip(
    reason="SCAFFOLD — requires table-rebuild migration (Phase 3 T2 production pass)"
)
def test_c1_insert_all_six_shoulder_reasons_accepted():
    """C1: All 6 SHOULDER_* reason values survive INSERT → CHECK on rebuilt table."""
    pass
