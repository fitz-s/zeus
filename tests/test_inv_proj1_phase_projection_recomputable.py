# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §5 (INV-PROJ-1);
#   consult round-3 (thread 6a42bc3d). Exercises scripts/dev/replay_position_phase.py drift detector.

"""INV-PROJ-1 antibody: the position_current.phase projection is event-sourced.

Proves the replay-diff detector (find_phase_projection_drift): (a) passes a phase produced by an event,
(b) FLAGS a phase no event ever produced (a writer that bypassed the event log), and — the correctness
case that broke the naive first cut — (c) does NOT flag a terminal phase (voided) that a real event
produced, even when later observational MONITOR_REFRESHED events carry a stale phase_after=active. Uses
minimal fixture tables so the test pins the detection LOGIC independent of full-schema CHECKs.
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


def test_event_sourced_phase_has_no_drift() -> None:
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P1', 'active')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [("P1", 1, "pending_entry"), ("P1", 2, "active"), ("P1", 3, None)],
    )
    assert find_phase_projection_drift(conn) == []


def test_unsourced_phase_is_detected() -> None:
    # Stored 'voided' but no event ever produced 'voided' — a writer bypassed the event log.
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


def test_terminal_phase_then_stale_monitor_noise_is_not_drift() -> None:
    # Reproduces live position edli...a2: ADMIN_VOIDED produced 'voided', then MONITOR_REFRESHED events
    # carry a stale phase_after='active'. Stored 'voided' IS event-sourced (the ADMIN_VOIDED) → NOT drift.
    # The naive "latest event phase_after" check wrongly flagged this; the event-sourcing check must not.
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P3', 'voided')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [
            ("P3", 1, "pending_entry"),
            ("P3", 2, "active"),
            ("P3", 3, "voided"),   # ADMIN_VOIDED — the authoritative terminal transition
            ("P3", 4, "active"),   # stale MONITOR_REFRESHED noise after the void
            ("P3", 5, "active"),
        ],
    )
    assert find_phase_projection_drift(conn) == []


def test_economically_closed_then_quarantine_noise_is_not_drift() -> None:
    # Reproduces live position 8e2710ed: EXIT_ORDER_FILLED produced 'economically_closed', then
    # REVIEW_REQUIRED/MONITOR carry 'quarantined'. A5 authority keeps stored 'economically_closed' — sourced.
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P4', 'economically_closed')")
    conn.executemany(
        "INSERT INTO position_events VALUES (?, ?, ?)",
        [
            ("P4", 1, "active"),
            ("P4", 2, "pending_exit"),
            ("P4", 3, "economically_closed"),
            ("P4", 4, "quarantined"),
            ("P4", 5, "quarantined"),
        ],
    )
    assert find_phase_projection_drift(conn) == []


def test_position_without_events_is_not_flagged() -> None:
    conn = _fixture_db()
    conn.execute("INSERT INTO position_current VALUES ('P5', 'pending_entry')")
    assert find_phase_projection_drift(conn) == []
