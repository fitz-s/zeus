# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742)

"""T2 — CREATE TABLE DDL for no_trade_events (world DB).

Per §5.2 column list:
  market_slug, target_date, temperature_metric, observation_time,
  observed_at, reason (NoTradeReason enum CHECK), reason_detail TEXT,
  decision_seq, schema_version.

PK: (market_slug, temperature_metric, target_date, observation_time, decision_seq)
  — matches decision_events natural key for FK-like joins (§5.2 "decision_natural_key
  FK-like reference"). decision_seq shared counter scope.

schema_version CHECK includes 14 and 15 (production pass bumps 14→15).

SCAFFOLD — ensure_table wiring into db.py init_schema happens at T2 production pass.
"""

from __future__ import annotations

import sqlite3

from src.contracts.no_trade_reason import NoTradeReason

# Schema version stamped into each row; stays in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 16

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
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16)),
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
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16)),
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


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create no_trade_events table + indices if they do not exist.

    Idempotent (IF NOT EXISTS). Called from two paths:
      1. db.py init_schema (daemon boot, world DB) — wired at T2 production pass
      2. scripts/migrate_no_trade_events_create_2026_05_21.py — operator one-shot migration
    """
    conn.execute(CREATE_TABLE_SQL)
    _rebuild_stale_no_trade_events_table(conn)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)


def _rebuild_stale_no_trade_events_table(conn: sqlite3.Connection) -> None:
    """Upgrade stale CHECK constraints on existing no_trade_events tables."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='no_trade_events'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if (
        NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP.value in table_sql
        and "14, 15, 16" in table_sql
    ):
        return

    conn.execute("DROP TABLE IF EXISTS no_trade_events_new")
    conn.execute(_CREATE_TABLE_REBUILD_SQL)
    conn.execute(
        """
        INSERT OR IGNORE INTO no_trade_events_new (
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason, reason_detail,
            observed_at, schema_version
        )
        SELECT
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason,
            reason_detail,
            observed_at,
            CASE
                WHEN schema_version IN (14, 15, 16) THEN schema_version
                ELSE 16
            END
        FROM no_trade_events
        """
    )
    conn.execute("DROP TABLE no_trade_events")
    conn.execute("ALTER TABLE no_trade_events_new RENAME TO no_trade_events")
