#!/usr/bin/env python3
# Lifecycle: created=2026-07-22; last_reviewed=2026-07-22; last_reused=2026-07-22
# Purpose: production-supported home for DB integrity checks reused by both the operator
#   safety-gate CLI (scripts/ops/db_safety_gates.py) and its antibody test
#   (tests/test_no_dangling_foreign_keys.py). Moved out of the test module so an operator
#   deployment that omits tests/ does not lose the gate it depends on.
"""DB integrity checks shared by the safety-gate preflight and its test suite."""
from __future__ import annotations

import sqlite3


def find_dangling_foreign_keys(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return (child_table, child_column, missing_parent_table) for every FK edge whose
    parent table is absent from the same schema. Empty list == healthy."""
    present = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }
    dangling: list[tuple[str, str, str]] = []
    for table in sorted(present):
        for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
            parent, child_col = fk[2], fk[3]
            if parent not in present:
                dangling.append((table, child_col, parent))
    return dangling
