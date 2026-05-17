# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: OPS_FORENSICS.md §F8 + PLAN.md WAVE-4 §F8
"""Antibody test: position_events.occurred_at CHECK constraint.

Verifies:
  1. Migration applies cleanly and is idempotent.
  2. INSERT with non-ISO occurred_at raises sqlite3.IntegrityError post-migration.
  3. INSERT with valid ISO timestamp succeeds.
  4. INSERT with 'QUARANTINE' literal succeeds.
  5. Sentinel backfill: unknown_entered_at rows are replaced with ISO timestamps.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MIGRATION_PATH = REPO_ROOT / "scripts" / "migrations" / "202605_position_events_occurred_at_iso_check.py"

# Minimal position_events DDL without the CHECK (pre-migration state)
_PRE_MIGRATION_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT','ENTRY_ORDER_POSTED','ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED','ENTRY_ORDER_REJECTED','DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED','CHAIN_SIZE_CORRECTED','CHAIN_QUARANTINED',
        'MONITOR_REFRESHED','EXIT_INTENT','EXIT_ORDER_POSTED','EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED','EXIT_ORDER_REJECTED','SETTLED','ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED'
    )),
    occurred_at TEXT NOT NULL,
    phase_before TEXT,
    phase_after TEXT,
    strategy_key TEXT NOT NULL DEFAULT 'settlement_capture',
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL DEFAULT 'test',
    payload_json TEXT NOT NULL DEFAULT '{}',
    env TEXT NOT NULL DEFAULT 'live',
    UNIQUE(position_id, sequence_no)
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_f8", MIGRATION_PATH)
    mod = types.ModuleType("mig_f8")
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _ins(event_id, position_id, seq, event_type, occurred_at):
    return (
        "INSERT INTO position_events "
        "(event_id, position_id, event_version, sequence_no, event_type, occurred_at, "
        "strategy_key, source_module, payload_json, env) VALUES "
        f"('{event_id}','{position_id}',1,{seq},'{event_type}','{occurred_at}',"
        "'settlement_capture','test','{}','live')"
    )


def _make_db_with_sentinels() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_PRE_MIGRATION_DDL)
    conn.execute(_ins("e1", "pos-clean", 1, "POSITION_OPEN_INTENT", "2026-05-17T10:00:00+00:00"))
    conn.execute(_ins("e2", "pos-sentinel-a", 1, "CHAIN_SYNCED", "unknown_entered_at"))
    conn.execute(_ins("e3", "pos-sentinel-a", 2, "ENTRY_ORDER_FILLED", "2026-05-17T11:00:00+00:00"))
    conn.execute(_ins("e4", "pos-sentinel-b", 1, "CHAIN_SYNCED", "unknown_entered_at"))
    # pos-sentinel-b has no following ENTRY_ORDER_FILLED — fallback to QUARANTINE
    conn.commit()
    return conn


class TestPositionEventsCheckConstraint:

    def test_migration_applies_and_is_idempotent(self):
        mod = _load_migration()
        conn = _make_db_with_sentinels()
        mod.up(conn)
        # Idempotent: second call must not raise
        mod.up(conn)

    def test_non_iso_insert_raises_after_migration(self):
        mod = _load_migration()
        conn = _make_db_with_sentinels()
        mod.up(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_ins("bad1", "pos-new", 1, "POSITION_OPEN_INTENT", "unknown_entered_at"))

    def test_iso_insert_succeeds_after_migration(self):
        mod = _load_migration()
        conn = _make_db_with_sentinels()
        mod.up(conn)

        conn.execute(_ins("ok1", "pos-new", 1, "POSITION_OPEN_INTENT", "2026-05-17T12:00:00+00:00"))

    def test_quarantine_literal_insert_succeeds_after_migration(self):
        mod = _load_migration()
        conn = _make_db_with_sentinels()
        mod.up(conn)

        conn.execute(_ins("q1", "pos-new", 1, "CHAIN_QUARANTINED", "QUARANTINE"))

    def test_sentinel_rows_backfilled(self):
        mod = _load_migration()
        conn = _make_db_with_sentinels()
        mod.up(conn)

        remaining = conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE occurred_at='unknown_entered_at'"
        ).fetchone()[0]
        assert remaining == 0, f"Expected 0 sentinel rows, got {remaining}"

        # pos-sentinel-a should have ENTRY_ORDER_FILLED timestamp
        ts_a = conn.execute(
            "SELECT occurred_at FROM position_events WHERE event_id='e2'"
        ).fetchone()[0]
        assert ts_a == "2026-05-17T11:00:00+00:00"

        # pos-sentinel-b has no following fill — fallback to QUARANTINE
        ts_b = conn.execute(
            "SELECT occurred_at FROM position_events WHERE event_id='e4'"
        ).fetchone()[0]
        assert ts_b == "QUARANTINE"
