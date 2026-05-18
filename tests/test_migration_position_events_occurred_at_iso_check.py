# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis:
#   docs/operations/task_2026-05-16_post_pr126_audit/RUN_16_track_F_position_lifecycle_correctness.md (F107)
#   scripts/migrations/202605_position_events_occurred_at_iso_check.py
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F107 antibody — verify scripts/migrations/202605_position_events_occurred_at_iso_check.py
#   idempotently backfills the 3 'unknown_entered_at' sentinel rows on a synthetic
#   in-memory database that mirrors the live (pre-migration) position_events schema.
#   Live execution against state/zeus_trades.db is OPERATOR-ONLY: this test exists so
#   the operator can run the migration with confidence the up(conn) path is correct.
# Reuse: Run on every PR touching position_events schema, the migration script,
#   or the F23 migration runner contract.

"""F107 antibody: position_events occurred_at CHECK + sentinel backfill migration.

The live `state/zeus_trades.db` has 3 sentinel rows whose occurred_at='unknown_entered_at':
- c30f28a5-d4e (Karachi, live capital in day0_window) — sequence_no=3, CHAIN_SYNCED
- bf0a16f5-f95 — sequence_no=3, CHAIN_SYNCED
- 6d8abfb4-b87 — sequence_no=3, CHAIN_SYNCED

The migration in scripts/migrations/202605_position_events_occurred_at_iso_check.py:
1. Adds CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE') to the column
2. Backfills each sentinel with the occurred_at of the immediately-following
   ENTRY_ORDER_FILLED event for the same position
3. Recreates the append-only triggers
4. Is idempotent — re-running on an already-migrated DB returns early

This test exercises the migration end-to-end with an in-memory SQLite DB containing
3 synthetic sentinel rows mirroring the live shape.

OPERATOR PROCEDURE (live execution — NOT run by this test):
  1. Stop com.zeus.live-trading and com.zeus.riskguard-live daemons.
  2. Verify no other writer holds state/zeus_trades.db: lsof state/zeus_trades.db.
  3. Run: python -c "from scripts.migrations.<...> import up;
                     import sqlite3; c=sqlite3.connect('state/zeus_trades.db');
                     up(c); c.close()"
     OR via the F23 migration runner if registered.
  4. Verify post-state:
       sqlite3 state/zeus_trades.db
         "SELECT COUNT(*) FROM position_events WHERE occurred_at='unknown_entered_at'"
       -> 0
  5. Restart daemons.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


# Script filename starts with digits, so direct package import is impossible.
def _import_migration_module():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = (
        repo_root
        / "scripts"
        / "migrations"
        / "202605_position_events_occurred_at_iso_check.py"
    )
    spec = importlib.util.spec_from_file_location("_f107_migr", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


migration = _import_migration_module()


# Pre-migration DDL — mirrors live state/zeus_trades.db.position_events (no CHECK
# on occurred_at, env column with DEFAULT 'live'). Sourced from sqlite_master
# inspection 2026-05-18 verbatim.
PRE_MIGRATION_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'CHAIN_QUARANTINED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED'
    )),
    occurred_at TEXT NOT NULL,
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry', 'active', 'day0_window', 'pending_exit',
        'economically_closed', 'settled', 'voided', 'quarantined', 'admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry', 'active', 'day0_window', 'pending_exit',
        'economically_closed', 'settled', 'voided', 'quarantined', 'admin_closed'
    )),
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia'
    )),
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    env TEXT NOT NULL DEFAULT 'live',
    UNIQUE(position_id, sequence_no)
);
"""


def _make_event_row(
    *,
    event_id: str,
    position_id: str,
    sequence_no: int,
    event_type: str,
    occurred_at: str,
) -> tuple:
    """Build a single position_events tuple matching the 19-column INSERT order."""
    return (
        event_id,
        position_id,
        1,             # event_version
        sequence_no,
        event_type,
        occurred_at,
        None,          # phase_before
        None,          # phase_after
        "settlement_capture",
        None, None, None, None, None, None, None,  # decision_id..venue_status
        "test",        # source_module
        "{}",          # payload_json
        "test",        # env
    )


def _seed_three_sentinels(conn: sqlite3.Connection) -> None:
    """Mirror the live shape: 3 positions, each with CHAIN_SYNCED sentinel followed by ENTRY_ORDER_FILLED."""
    rows = [
        # Position c30f28a5-d4e — Karachi
        _make_event_row(
            event_id="evt-karachi-1", position_id="c30f28a5-d4e",
            sequence_no=1, event_type="POSITION_OPEN_INTENT",
            occurred_at="2026-05-16T00:32:00+00:00",
        ),
        _make_event_row(
            event_id="evt-karachi-2", position_id="c30f28a5-d4e",
            sequence_no=2, event_type="ENTRY_ORDER_POSTED",
            occurred_at="2026-05-16T00:35:00+00:00",
        ),
        _make_event_row(
            event_id="evt-karachi-3", position_id="c30f28a5-d4e",
            sequence_no=3, event_type="CHAIN_SYNCED",
            occurred_at="unknown_entered_at",  # SENTINEL
        ),
        _make_event_row(
            event_id="evt-karachi-4", position_id="c30f28a5-d4e",
            sequence_no=4, event_type="ENTRY_ORDER_FILLED",
            occurred_at="2026-05-16T06:40:21.097343+00:00",
        ),
        # Position bf0a16f5-f95
        _make_event_row(
            event_id="evt-bf0a-1", position_id="bf0a16f5-f95",
            sequence_no=1, event_type="POSITION_OPEN_INTENT",
            occurred_at="2026-05-17T08:00:00+00:00",
        ),
        _make_event_row(
            event_id="evt-bf0a-2", position_id="bf0a16f5-f95",
            sequence_no=2, event_type="ENTRY_ORDER_POSTED",
            occurred_at="2026-05-17T08:01:00+00:00",
        ),
        _make_event_row(
            event_id="evt-bf0a-3", position_id="bf0a16f5-f95",
            sequence_no=3, event_type="CHAIN_SYNCED",
            occurred_at="unknown_entered_at",  # SENTINEL
        ),
        _make_event_row(
            event_id="evt-bf0a-4", position_id="bf0a16f5-f95",
            sequence_no=4, event_type="ENTRY_ORDER_FILLED",
            occurred_at="2026-05-17T10:11:52.337500+00:00",
        ),
        # Position 6d8abfb4-b87
        _make_event_row(
            event_id="evt-6d8a-1", position_id="6d8abfb4-b87",
            sequence_no=1, event_type="POSITION_OPEN_INTENT",
            occurred_at="2026-05-17T11:00:00+00:00",
        ),
        _make_event_row(
            event_id="evt-6d8a-2", position_id="6d8abfb4-b87",
            sequence_no=2, event_type="ENTRY_ORDER_POSTED",
            occurred_at="2026-05-17T11:01:00+00:00",
        ),
        _make_event_row(
            event_id="evt-6d8a-3", position_id="6d8abfb4-b87",
            sequence_no=3, event_type="CHAIN_SYNCED",
            occurred_at="unknown_entered_at",  # SENTINEL
        ),
        _make_event_row(
            event_id="evt-6d8a-4", position_id="6d8abfb4-b87",
            sequence_no=4, event_type="ENTRY_ORDER_FILLED",
            occurred_at="2026-05-17T12:41:38.355482+00:00",
        ),
    ]
    insert_sql = (
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    for r in rows:
        conn.execute(insert_sql, r)
    conn.commit()


@pytest.fixture
def seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(PRE_MIGRATION_DDL)
    _seed_three_sentinels(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Probe 1: migration backfills the 3 sentinels with the ENTRY_ORDER_FILLED time
# ---------------------------------------------------------------------------

def test_migration_backfills_three_sentinels(seeded_conn: sqlite3.Connection) -> None:
    """F107 Probe 1: after up(conn), 0 sentinels remain and each is replaced by
    the occurred_at of the next ENTRY_ORDER_FILLED for that position."""
    # Pre-state assertion: exactly 3 sentinels
    n_before = seeded_conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE occurred_at = 'unknown_entered_at'"
    ).fetchone()[0]
    assert n_before == 3, f"fixture should have 3 sentinels, got {n_before}"

    migration.up(seeded_conn)

    # Post-state: zero sentinels
    n_after = seeded_conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE occurred_at = 'unknown_entered_at'"
    ).fetchone()[0]
    assert n_after == 0, f"all sentinels should be backfilled, got {n_after} remaining"

    # Each former sentinel now carries the next ENTRY_ORDER_FILLED's occurred_at
    expected = {
        "c30f28a5-d4e": "2026-05-16T06:40:21.097343+00:00",
        "bf0a16f5-f95": "2026-05-17T10:11:52.337500+00:00",
        "6d8abfb4-b87": "2026-05-17T12:41:38.355482+00:00",
    }
    for position_id, expected_ts in expected.items():
        row = seeded_conn.execute(
            "SELECT occurred_at FROM position_events "
            "WHERE position_id = ? AND event_type = 'CHAIN_SYNCED'",
            (position_id,),
        ).fetchone()
        assert row is not None, f"{position_id} CHAIN_SYNCED row missing post-migration"
        assert row[0] == expected_ts, (
            f"{position_id}: occurred_at backfill should be {expected_ts}, got {row[0]}"
        )


# ---------------------------------------------------------------------------
# Probe 2: idempotency — re-running up() on already-migrated DB is a no-op
# ---------------------------------------------------------------------------

def test_migration_is_idempotent(seeded_conn: sqlite3.Connection) -> None:
    """F107 Probe 2: re-running up(conn) on an already-migrated DB is a no-op
    (_is_already_applied returns True)."""
    migration.up(seeded_conn)
    row_count_after_first = seeded_conn.execute(
        "SELECT COUNT(*) FROM position_events"
    ).fetchone()[0]

    # Second run — must short-circuit, not rebuild
    migration.up(seeded_conn)
    row_count_after_second = seeded_conn.execute(
        "SELECT COUNT(*) FROM position_events"
    ).fetchone()[0]
    assert row_count_after_first == row_count_after_second, (
        f"idempotent re-run should not change row count: "
        f"{row_count_after_first} != {row_count_after_second}"
    )

    # CHECK constraint must still be active
    n_sentinel = seeded_conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE occurred_at = 'unknown_entered_at'"
    ).fetchone()[0]
    assert n_sentinel == 0, "idempotent re-run should keep sentinels at zero"


# ---------------------------------------------------------------------------
# Probe 3: post-migration schema rejects new sentinel writes (CHECK constraint active)
# ---------------------------------------------------------------------------

def test_post_migration_check_rejects_sentinel(seeded_conn: sqlite3.Connection) -> None:
    """F107 Probe 3: post-migration CHECK constraint must reject any future
    INSERT with occurred_at = 'unknown_entered_at'. 'QUARANTINE' is the only
    legal non-ISO literal value."""
    migration.up(seeded_conn)

    # ISO-shaped occurred_at must pass
    seeded_conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _make_event_row(
            event_id="evt-new-iso",
            position_id="c30f28a5-d4e",
            sequence_no=5,
            event_type="MONITOR_REFRESHED",
            occurred_at="2026-05-18T00:00:00+00:00",
        ),
    )

    # 'QUARANTINE' literal must pass
    seeded_conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _make_event_row(
            event_id="evt-new-quar",
            position_id="c30f28a5-d4e",
            sequence_no=6,
            event_type="CHAIN_QUARANTINED",
            occurred_at="QUARANTINE",
        ),
    )

    # 'unknown_entered_at' sentinel must be rejected by CHECK
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _make_event_row(
                event_id="evt-new-sentinel",
                position_id="c30f28a5-d4e",
                sequence_no=7,
                event_type="CHAIN_SYNCED",
                occurred_at="unknown_entered_at",
            ),
        )


# ---------------------------------------------------------------------------
# Probe 4: append-only triggers are recreated on the new table
# ---------------------------------------------------------------------------

def test_post_migration_append_only_triggers_active(seeded_conn: sqlite3.Connection) -> None:
    """F107 Probe 4: trg_position_events_no_update and trg_position_events_no_delete
    must be active post-migration (recreated by step 4 of up())."""
    migration.up(seeded_conn)

    triggers = {
        row[0]
        for row in seeded_conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='position_events'"
        )
    }
    assert "trg_position_events_no_update" in triggers, (
        "append-only UPDATE trigger missing post-migration"
    )
    assert "trg_position_events_no_delete" in triggers, (
        "append-only DELETE trigger missing post-migration"
    )

    # Functional check: UPDATE must fail
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            "UPDATE position_events SET occurred_at='2099-01-01T00:00:00+00:00' "
            "WHERE event_id='evt-karachi-3'"
        )
    # DELETE must fail
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute("DELETE FROM position_events WHERE event_id='evt-karachi-3'")
