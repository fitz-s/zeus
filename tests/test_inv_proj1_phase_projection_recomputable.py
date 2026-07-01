# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §5 (INV-PROJ-1);
#   consult round-3 (thread 6a42bc3d). Exercises scripts/dev/replay_position_phase.py drift detector.

"""INV-PROJ-1 antibody: the position_current.phase projection is recomputable from the event log.

Proves the replay-diff detector (find_phase_projection_drift) correctly (a) passes a consistent
projection, (b) flags a position whose materialized phase was written bypassing the event log —
the multi-writer drift class the atlas §7D single-owner fix removes. Uses minimal fixture tables so
the test pins the detection LOGIC, independent of the full schema's CHECK constraints.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev" / "replay_position_phase.py"
_spec = importlib.util.spec_from_file_location("replay_position_phase", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
find_phase_projection_drift = _mod.find_phase_projection_drift


def _fixture_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE position_current (position_id TEXT PRIMARY KEY, phase TEXT);
        CREATE TABLE position_events (
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            phase_after TEXT
        );
        """
    )
    return conn


def test_consistent_projection_has_no_drift() -> None:
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P1', 'active')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [("P1", 1, "pending_entry"), ("P1", 2, "active"), ("P1", 3, None)],  # 3 = MONITOR_REFRESHED, null
    )
    assert find_phase_projection_drift(conn) == []


def test_bypassing_writer_drift_is_detected() -> None:
    # Latest phase-setting event says 'active', but a writer set position_current directly to 'voided'.
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P2', 'voided')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [("P2", 1, "pending_entry"), ("P2", 2, "active"), ("P2", 3, None)],
    )
    drift = find_phase_projection_drift(conn)
    assert len(drift) == 1
    assert drift[0]["position_id"] == "P2"
    assert drift[0]["stored_phase"] == "voided"
    assert drift[0]["event_phase_after"] == "active"


def test_trailing_null_phase_events_do_not_falsely_flag() -> None:
    # The latest *phase-setting* event matches; later null-phase events (monitor refreshes) are skipped.
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P3', 'settled')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [("P3", 1, "active"), ("P3", 2, "settled"), ("P3", 3, None), ("P3", 4, None)],
    )
    assert find_phase_projection_drift(conn) == []


def test_position_without_events_is_not_flagged() -> None:
    # No phase-setting event to compare against — the JOIN excludes it (reported separately if needed).
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P4', 'pending_entry')")
    assert find_phase_projection_drift(conn) == []
