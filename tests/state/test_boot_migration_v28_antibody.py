# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: wave/deterministic-strategies-20260522 critic verdict CRITICAL-1 + CRITICAL-2
"""Antibody tests: prod-v28 DB migration correctness.

Regression class: fresh-DB tests miss CHECK-expansion failures because
they build the table from current DDL.  These tests START from a prior-version
table shape (v28 no_trade_events, v27/v28 evidence_tier_assignments) and verify
that the boot migration makes all new-reason INSERTs and schema_version=29
tribunal writes succeed without IntegrityError.

CRITICAL-1 guard (no_trade_events_schema.py): rebuild fires on any missing
NoTradeReason value OR missing SCHEMA_VERSION in the schema_version CHECK.

CRITICAL-2 guard (phase6_evidence_schema.py): rebuild fires when
SCHEMA_VERSION is absent from the evidence_tier_assignments CHECK list.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.state.schema.no_trade_events_schema import (
    migrate_no_trade_events_schema,
)
from src.state.schema.phase6_evidence_schema import ensure_tables


# ---------------------------------------------------------------------------
# Helpers — build prior-version table shapes
# ---------------------------------------------------------------------------

_NEW_V29_REASON_NAMES: frozenset[str] = frozenset({
    "PHYSICAL_INTERVAL_DATA_GATED",
    "PHYSICAL_INTERVAL_OVERLAP",
    "PHYSICAL_INTERVAL_UNPROFITABLE",
    "SETTLEMENT_CAPTURE_NOT_LOCKED",
    "PHYSICAL_ENVELOPE_UNWIRED",
    "SHOULDER_PHYSICAL_BOUND_NOT_EXCLUDES_TAIL",
    "RESOLUTION_TYPED_OUTCOME_UNAVAILABLE",
    "CENTER_PAIR_PARITY_BOOK_UNAVAILABLE",
    "CENTER_PAIR_PARITY_NO_EDGE",
})


def _v28_reason_values_sql() -> str:
    """IN clause for NoTradeReason members that existed at v28 (pre-wave)."""
    v28_members = [r for r in NoTradeReason if r.name not in _NEW_V29_REASON_NAMES]
    return ", ".join(f"'{r.value}'" for r in v28_members)


def _build_v28_no_trade_events(conn: sqlite3.Connection) -> None:
    """Create a no_trade_events table shaped like a prod-v28 DB.

    Uses the exact spacing/format the old code emitted so the rebuild guard
    is exercised realistically.  The old guard keyed on the substring
    ``"14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27"`` (with spaces)
    which IS present in a v28 CHECK (14, 15, …, 27, 28); the old guard therefore
    returned early, leaving new-reason values out of the CHECK.
    """
    v28_reasons = _v28_reason_values_sql()
    conn.execute(f"""
        CREATE TABLE no_trade_events (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL,
            target_date         TEXT NOT NULL,
            observation_time    TEXT NOT NULL,
            decision_seq        INTEGER NOT NULL,
            reason              TEXT NOT NULL CHECK (reason IN ({v28_reasons})),
            reason_detail       TEXT,
            strategy_key        TEXT,
            event_source        TEXT,
            shadow_runtime      INTEGER NOT NULL DEFAULT 0 CHECK (shadow_runtime IN (0, 1)),
            observed_at         TEXT NOT NULL,
            schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28)),
            schema_compatibility TEXT NOT NULL DEFAULT 'current'
                CHECK (schema_compatibility IN ('current', 'degraded')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)


def _build_v27_evidence_tier_assignments(conn: sqlite3.Connection) -> None:
    """Create evidence_tier_assignments shaped like a prod-v27 DB (CHECK only up to 27).

    Uses exact production spacing/quoting so the structural guard in
    _migrate_evidence_tier_assignments_schema fires (returns early = bug without
    the SCHEMA_VERSION check fix).  The key property: all structural columns are
    present so the old guard would return early, but schema_version=29 is absent
    from the CHECK list, causing IntegrityError on tribunal writes.
    """
    conn.execute("""
        CREATE TABLE evidence_tier_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT NOT NULL,
            tier INTEGER NOT NULL CHECK (tier IN (0, 1, 2, 3, 4, 5, 6, 7)),
            assigned_at TEXT NOT NULL, rationale TEXT, operator_ref TEXT, verdict_reason TEXT,
            schema_version INTEGER NOT NULL DEFAULT 27 CHECK (schema_version IN (25, 26, 27)),
            assignment_source TEXT NOT NULL DEFAULT "tribunal"
                CHECK (assignment_source IN ("tribunal", "operator_override", "migration")),
            verdict_kind TEXT NOT NULL DEFAULT "MIGRATION"
                CHECK (verdict_kind IN ("PROMOTE", "HOLD", "DEMOTE", "OPERATOR_OVERRIDE", "MIGRATION")),
            effective_from TEXT, effective_until TEXT,
            revoked_at TEXT, revoked_by TEXT, supersedes_assignment_id INTEGER
        )
    """)


# ---------------------------------------------------------------------------
# CRITICAL-1 antibody: v28 no_trade_events migration
# ---------------------------------------------------------------------------

class TestNoTradeEventsMigrationFromV28:
    """Boot migration from prod-v28-shaped table must expand the reason CHECK
    to include all 9 new v29 NoTradeReason members."""

    def _migrated_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        _build_v28_no_trade_events(conn)
        migrate_no_trade_events_schema(conn)
        return conn

    def test_new_reason_insert_succeeds_after_migration(self) -> None:
        """Each new v29 reason must be insertable after migration."""
        conn = self._migrated_conn()
        for name in _NEW_V29_REASON_NAMES:
            r = NoTradeReason[name]
            conn.execute(
                """
                INSERT INTO no_trade_events
                    (market_slug, temperature_metric, target_date,
                     observation_time, decision_seq, reason,
                     observed_at, schema_version)
                VALUES ('test-slug', 'HIGH', '2026-06-01',
                        '12:00', 1, ?, '2026-05-22T00:00:00', ?)
                """,
                (r.value, SCHEMA_VERSION),
            )
            # Clean up for next iteration (PK reuse)
            conn.execute("DELETE FROM no_trade_events")

    def test_schema_version_29_insert_accepted(self) -> None:
        """schema_version=29 rows must be accepted after migration."""
        conn = self._migrated_conn()
        r = NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED
        conn.execute(
            """
            INSERT INTO no_trade_events
                (market_slug, temperature_metric, target_date,
                 observation_time, decision_seq, reason,
                 observed_at, schema_version)
            VALUES ('slug', 'HIGH', '2026-06-01', '12:00', 1, ?, '2026-05-22T00:00:00', 29)
            """,
            (r.value,),
        )

    def test_old_reason_still_accepted_after_migration(self) -> None:
        """Pre-existing v28 reasons must still INSERT without error."""
        conn = self._migrated_conn()
        r = NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET
        conn.execute(
            """
            INSERT INTO no_trade_events
                (market_slug, temperature_metric, target_date,
                 observation_time, decision_seq, reason,
                 observed_at, schema_version)
            VALUES ('slug2', 'HIGH', '2026-06-01', '12:00', 2, ?, '2026-05-22T00:00:00', 28)
            """,
            (r.value,),
        )


# ---------------------------------------------------------------------------
# CRITICAL-2 antibody: v27/v28 evidence_tier_assignments migration
# ---------------------------------------------------------------------------

class TestEvidenceTierAssignmentsMigrationFromV27:
    """Boot migration from prod-v27/v28-shaped table must widen the
    schema_version CHECK to include 28 and 29."""

    def _migrated_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        _build_v27_evidence_tier_assignments(conn)
        # ensure_tables creates shadow_experiments + regret_decompositions and
        # calls _migrate_evidence_tier_assignments_schema on the existing table.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_experiments (
                experiment_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                started_at TEXT NOT NULL,
                closed_at TEXT,
                cohort_tag TEXT NOT NULL,
                immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0,1))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS regret_decompositions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                decision_event_id TEXT NOT NULL,
                total_regret_usd REAL NOT NULL,
                computed_at TEXT NOT NULL
            )
        """)
        # Call only the migration function directly to avoid re-creating the table
        from src.state.schema.phase6_evidence_schema import (
            _migrate_evidence_tier_assignments_schema,
        )
        _migrate_evidence_tier_assignments_schema(conn)
        return conn

    def test_schema_version_29_tribunal_write_succeeds(self) -> None:
        """evidence_tier_assignments INSERT at schema_version=29 must not IntegrityError."""
        conn = self._migrated_conn()
        conn.execute(
            """
            INSERT INTO evidence_tier_assignments
                (strategy_id, tier, assigned_at, rationale, schema_version,
                 assignment_source, verdict_kind)
            VALUES ('shoulder_impossible_tail_capture', 3, '2026-05-22T00:00:00',
                    'antibody test', 29, 'tribunal', 'PROMOTE')
            """
        )

    def test_schema_version_28_insert_still_accepted(self) -> None:
        """Rows at schema_version=28 (post-migration) must also be accepted."""
        conn = self._migrated_conn()
        conn.execute(
            """
            INSERT INTO evidence_tier_assignments
                (strategy_id, tier, assigned_at, rationale, schema_version,
                 assignment_source, verdict_kind)
            VALUES ('settlement_capture', 4, '2026-05-22T00:00:00',
                    'antibody test v28', 28, 'tribunal', 'HOLD')
            """
        )

    def test_old_v27_rows_migrated_successfully(self) -> None:
        """Rows that existed in the v27 table are accessible after migration."""
        # Pre-populate then migrate
        conn = sqlite3.connect(":memory:")
        _build_v27_evidence_tier_assignments(conn)
        conn.execute(
            """
            INSERT INTO evidence_tier_assignments
                (strategy_id, tier, assigned_at, schema_version,
                 assignment_source, verdict_kind)
            VALUES ('opening_inertia', 4, '2026-05-20T00:00:00', 27, 'tribunal', 'HOLD')
            """
        )
        from src.state.schema.phase6_evidence_schema import (
            _migrate_evidence_tier_assignments_schema,
        )
        _migrate_evidence_tier_assignments_schema(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM evidence_tier_assignments WHERE strategy_id='opening_inertia'"
        ).fetchone()[0]
        assert count == 1
