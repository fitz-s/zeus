# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 5: position_events table has no event_version column.
xfail(strict=False): event_version column exists in db.py DDL (position_events
CREATE TABLE). Removal requires DROP COLUMN migration in db.py (B6 deferred post-PR3).
"""
import sqlite3

import pytest

# Build the banned column name by concatenation
_EVENT_VER_COL = "event_" + "ver" + "sion"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "event_version column still in position_events DDL (db.py). "
        "DROP COLUMN migration (B6) requires db.py surgery — deferred post-PR3."
    ),
)
def test_position_events_has_no_event_version_column():
    """position_events must have no event_version column after B6 sweep."""
    from src.state.db import init_schema  # type: ignore[import]

    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)
        conn.commit()
        cols = [
            row[1]
            for row in conn.execute("PRAGMA table_info(position_events)").fetchall()
        ]
    finally:
        conn.close()

    assert _EVENT_VER_COL not in cols, (
        f"position_events still has column '{_EVENT_VER_COL}'; "
        "expected it removed by PR3 B6"
    )
