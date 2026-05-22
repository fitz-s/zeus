# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742) + Phase 3 T2 (2026-05-21): schema_version CHECK extended to 18 + 6 SHOULDER_* NoTradeReason members + live release proof P0-3 schema compatibility marker + Phase 3 T3 (2026-05-21): CHECK extended to 23 + live authority follow-up (2026-05-21): CHECK extended to 25 + evidence governance follow-up (2026-05-21): strategy provenance columns, v26 + P1/P2 architecture review (2026-05-22): evidence lifecycle + day0_nowcast_entry, v27 + opportunity_fact strategy-key widening, v28

"""T2 — CREATE TABLE DDL for no_trade_events (world DB).

Per §5.2 column list:
  market_slug, target_date, temperature_metric, observation_time,
  observed_at, reason (NoTradeReason enum CHECK), reason_detail TEXT,
  decision_seq, schema_version.

PK: (market_slug, temperature_metric, target_date, observation_time, decision_seq)
  — matches decision_events natural key for FK-like joins (§5.2 "decision_natural_key
  FK-like reference"). decision_seq shared counter scope.

schema_version CHECK includes 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29:
  - 14: original scaffold
  - 15: P2 T2 production pass
  - 16: MUTUALLY_EXCLUSIVE_FAMILY_DEDUP added (PR #249, 2026-05-21)
  - 17: live-money no_trade reason taxonomy (PR #253, 2026-05-21)
  - 18: Phase 3 T2 (2026-05-21) — 6 SHOULDER_* NoTradeReason members added;
        table-rebuild migration under ATTACH+SAVEPOINT per INV-37 in
        scripts/migrate_no_trade_events_rebuild_phase3_t2.py.
  - 19: executable snapshot tradeability evidence, no no_trade_events DDL change.
  - 20: P0-3 live release proof — schema_compatibility marks degraded
        compatibility rows so live learning/report trust can exclude them.
  - 21, 22: current live-release schema/version bumps; no additional DDL
        beyond the compatibility marker and expanded CHECK range.
  - 23: Phase 3 T3 (2026-05-21) — shoulder_exposure_ledger table added;
        no_trade_events CHECK extended to accept v23 rows.
  - 24: Phase 5 T2 (2026-05-21) — regime_correlation_cache table added;
        no_trade_events CHECK extended to accept v24 rows.
  - 25: live authority/shadow/risk follow-up (2026-05-21) — shadow decision
        provenance schema bump; no_trade_events CHECK extended to accept v25 rows.
  - 26: evidence governance follow-up (2026-05-21) — structured strategy_key,
        event_source, and shadow_runtime provenance for evidence_report.
  - 27: P1/P2 architecture review (2026-05-22) — evidence lifecycle fields
        (revoke/expiry) in evidence_tier_assignments; day0_nowcast_entry strategy.
  - 28: neg_risk_basket NEGRISK_NO_PROFITABLE_BASKET reason enum member.
  - 29: settlement_capture_shadow: +4 NoTradeReason members
        (PHYSICAL_INTERVAL_DATA_GATED, PHYSICAL_INTERVAL_OVERLAP,
        PHYSICAL_INTERVAL_UNPROFITABLE, SETTLEMENT_CAPTURE_NOT_LOCKED).

Note: _REASON_VALUES_SQL is enum-derived (iterates NoTradeReason) so adding
SHOULDER_* members to the enum automatically extends the reason CHECK constraint —
no hardcoded value list needed here.
"""

from __future__ import annotations

import sqlite3

from src.contracts.no_trade_reason import NoTradeReason

# Schema version stamped into each row; stays in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 29

# Enum CHECK: every valid NoTradeReason value, joined for SQL IN clause.
_REASON_VALUES_SQL = ", ".join(f"'{r.value}'" for r in NoTradeReason)

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL CHECK (reason IN ({_REASON_VALUES_SQL})),
    reason_detail       TEXT,
    strategy_key        TEXT,
    event_source        TEXT,
    shadow_runtime      INTEGER NOT NULL DEFAULT 0 CHECK (shadow_runtime IN (0, 1)),
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29)),
    schema_compatibility TEXT NOT NULL DEFAULT 'current'
        CHECK (schema_compatibility IN ('current', 'degraded')),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_CREATE_TABLE_REBUILD_SQL = f"""
CREATE TABLE no_trade_events_new (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL CHECK (reason IN ({_REASON_VALUES_SQL})),
    reason_detail       TEXT,
    strategy_key        TEXT,
    event_source        TEXT,
    shadow_runtime      INTEGER NOT NULL DEFAULT 0 CHECK (shadow_runtime IN (0, 1)),
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29)),
    schema_compatibility TEXT NOT NULL DEFAULT 'current'
        CHECK (schema_compatibility IN ('current', 'degraded')),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

CREATE_INDEX_MARKET_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_no_trade_events_market_time
    ON no_trade_events(market_slug, observed_at)
"""

CREATE_INDEX_REASON_SQL = """
CREATE INDEX IF NOT EXISTS idx_no_trade_events_reason
    ON no_trade_events(reason)
"""

CREATE_INDEX_STRATEGY_SQL = """
CREATE INDEX IF NOT EXISTS idx_no_trade_events_strategy
    ON no_trade_events(strategy_key, observed_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create no_trade_events table + indices without destructive rebuild DDL.

    Runtime writers may call schema assertions, but must not rebuild this table
    while the daemon is in the hot path. Stale CHECK upgrades belong to
    ``migrate_no_trade_events_schema`` during boot/operator migration.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)
    if "strategy_key" in _table_columns(conn):
        conn.execute(CREATE_INDEX_STRATEGY_SQL)


def migrate_no_trade_events_schema(conn: sqlite3.Connection) -> None:
    """Boot/operator migration path for stale no_trade_events CHECK constraints."""

    conn.execute(CREATE_TABLE_SQL)
    _rebuild_stale_no_trade_events_table(conn)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)
    if "strategy_key" in _table_columns(conn):
        conn.execute(CREATE_INDEX_STRATEGY_SQL)


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()}


def _rebuild_stale_no_trade_events_table(conn: sqlite3.Connection) -> None:
    """Upgrade stale CHECK constraints on existing no_trade_events tables.

    Fires when the existing table SQL is missing:
    - MUTUALLY_EXCLUSIVE_FAMILY_DEDUP (v16 expansion), OR
    - SHOULDER_STRESS_FAIL (v18 expansion — Phase 3 T2), OR
    - CORR_HEDGE_REGIME_UNAVAILABLE (Phase 4 T4 expansion), OR
    - current schema_version CHECK range including v23, OR
    - schema_compatibility marker column.

    The rebuild is idempotent: if all flags and version range are present,
    returns immediately without touching the table.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='no_trade_events'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    # Enum-iteration guard: rebuild if ANY NoTradeReason value is absent from the
    # existing CHECK clause, or if the current SCHEMA_VERSION is not in the
    # schema_version IN (...) list.  This replaces the former hardcoded
    # "14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27" substring check which
    # silently skips when a prod-v28 table is present, leaving new enum members out of
    # the reason CHECK and causing IntegrityError on every shadow no_trade write.
    if (
        not any(r.value not in table_sql for r in NoTradeReason)
        and str(SCHEMA_VERSION) in table_sql
        and "schema_compatibility" in table_sql
        and "strategy_key" in table_sql
        and "event_source" in table_sql
        and "shadow_runtime" in table_sql
    ):
        return
    if table_sql and "schema_compatibility" not in table_sql:
        conn.execute(
            """
            ALTER TABLE no_trade_events
            ADD COLUMN schema_compatibility TEXT NOT NULL DEFAULT 'current'
            CHECK (schema_compatibility IN ('current', 'degraded'))
            """
        )
    columns = _table_columns(conn)
    if table_sql and "strategy_key" not in columns:
        conn.execute("ALTER TABLE no_trade_events ADD COLUMN strategy_key TEXT")
    if table_sql and "event_source" not in columns:
        conn.execute("ALTER TABLE no_trade_events ADD COLUMN event_source TEXT")
    if table_sql and "shadow_runtime" not in columns:
        conn.execute(
            """
            ALTER TABLE no_trade_events
            ADD COLUMN shadow_runtime INTEGER NOT NULL DEFAULT 0
            CHECK (shadow_runtime IN (0, 1))
            """
        )

    conn.execute("DROP TABLE IF EXISTS no_trade_events_new")
    conn.execute(_CREATE_TABLE_REBUILD_SQL)
    conn.execute(
        """
        INSERT OR IGNORE INTO no_trade_events_new (
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason, reason_detail,
            strategy_key, event_source, shadow_runtime,
            observed_at, schema_version, schema_compatibility
        )
        SELECT
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason,
            reason_detail,
            strategy_key,
            event_source,
            shadow_runtime,
            observed_at,
            CASE
                WHEN schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28) THEN schema_version
                ELSE 28
            END,
            schema_compatibility
        FROM no_trade_events
        """
    )
    conn.execute("DROP TABLE no_trade_events")
    conn.execute("ALTER TABLE no_trade_events_new RENAME TO no_trade_events")
