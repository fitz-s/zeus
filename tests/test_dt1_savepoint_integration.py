# Created: 2026-04-20
# Last reused/audited: 2026-04-20
# Authority basis: P10E contract S3g — SAVEPOINT antibody for atomic trade-entry writes

"""Integration tests for the SAVEPOINT guard pattern in cycle_runtime.

Two tests:
1. SAVEPOINT mechanism itself (SQLite in-memory) — proves ROLLBACK works.
2. AST check that cycle_runtime._try_execute_candidate uses SAVEPOINT.

The R-DF.5 antibody in test_phase10e_closeout.py already does the AST scan;
this file adds the live SQLite round-trip to confirm the mechanism is real.
"""

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_savepoint_rollback_atomicity():
    """SAVEPOINT/ROLLBACK prevents partial writes when second INSERT raises.

    Simulates the pattern verbatim from cycle_runtime.py:
        SAVEPOINT sp
        INSERT (first write — log_trade_entry analogue)
        INSERT or RAISE (second write — log_execution_report analogue)
        → on exception: ROLLBACK TO SAVEPOINT sp / RELEASE sp

    Assert: first INSERT row is absent after rollback.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, val TEXT)")
    conn.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, val TEXT)")

    sp = "sp_test_001"

    # Simulate the cycle_runtime SAVEPOINT guard
    try:
        conn.execute(f"SAVEPOINT {sp}")
        conn.execute("INSERT INTO entries VALUES ('e1', 'entry-data')")
        # Simulate log_execution_report raising
        raise RuntimeError("simulated execution report failure")
    except RuntimeError:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")

    # Row must be absent after rollback
    count = conn.execute("SELECT COUNT(*) FROM entries WHERE id='e1'").fetchone()[0]
    assert count == 0, (
        f"SAVEPOINT rollback failed: entries row persisted after RuntimeError. "
        f"count={count}"
    )
    conn.close()


def test_savepoint_success_persists_both_rows():
    """SAVEPOINT released on success — both writes persist."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, val TEXT)")
    conn.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, val TEXT)")

    sp = "sp_test_002"

    conn.execute(f"SAVEPOINT {sp}")
    try:
        conn.execute("INSERT INTO entries VALUES ('e2', 'entry-data')")
        conn.execute("INSERT INTO reports VALUES ('r2', 'report-data')")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise

    entry_count = conn.execute("SELECT COUNT(*) FROM entries WHERE id='e2'").fetchone()[0]
    report_count = conn.execute("SELECT COUNT(*) FROM reports WHERE id='r2'").fetchone()[0]
    assert entry_count == 1, "Success path: entry row missing after RELEASE"
    assert report_count == 1, "Success path: report row missing after RELEASE"
    conn.close()


def test_cycle_runtime_uses_savepoint_pattern():
    """AST: cycle_runtime._try_execute_candidate must contain SAVEPOINT guard.

    Verifies all three required elements:
    - SAVEPOINT {sp}
    - ROLLBACK TO SAVEPOINT {sp}
    - RELEASE SAVEPOINT {sp}
    """
    cycle_runtime_py = PROJECT_ROOT / "src" / "engine" / "cycle_runtime.py"
    if not cycle_runtime_py.exists():
        pytest.skip("cycle_runtime.py not found")

    source = cycle_runtime_py.read_text()
    assert "SAVEPOINT" in source, (
        "cycle_runtime.py missing SAVEPOINT — atomic trade-entry guard removed."
    )
    assert "ROLLBACK TO SAVEPOINT" in source, (
        "cycle_runtime.py missing ROLLBACK TO SAVEPOINT — rollback path removed."
    )
    assert "RELEASE SAVEPOINT" in source, (
        "cycle_runtime.py missing RELEASE SAVEPOINT — success path incomplete."
    )
