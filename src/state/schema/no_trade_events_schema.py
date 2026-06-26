# Created: 2026-05-20
# Last reused or audited: 2026-06-14
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742) + Phase 3 T2 (2026-05-21): schema_version CHECK extended to 18 + 6 SHOULDER_* NoTradeReason members + live release proof P0-3 schema compatibility marker + Phase 3 T3 (2026-05-21): CHECK extended to 23 + live authority follow-up (2026-05-21): CHECK extended to 25 + evidence governance follow-up (2026-05-21): strategy provenance columns, v26 + P1/P2 architecture review (2026-05-22): evidence lifecycle + day0_nowcast_entry, v27 + opportunity_fact strategy-key widening, v28 + 2026-05-23 review5.23 P0-2: unified schema version authority (import from src.state.db) + 2026-06-14 q-kernel rebuild Stage 0 (docs/rebuild/consult_build_spec.md:994-1033): 19 NULLABLE decision-receipt-spine columns appended (additive, observability-only — never gate)

"""T2 — schema DDL for the no_trade_events table (world DB).

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
  - 18: Phase 3 T2 (2026-05-21) — retired shoulder scaffold reason expansion
        later removed; current CHECK is enum-derived from live reasons.
  - 19: executable snapshot tradeability evidence, no no_trade_events DDL change.
  - 20: P0-3 live release proof — schema_compatibility marks degraded
        compatibility rows so live learning/report trust can exclude them.
  - 21, 22: current live-release schema/version bumps; no additional DDL
        beyond the compatibility marker and expanded CHECK range.
  - 23: Phase 3 T3 (2026-05-21) — shoulder_exposure_ledger table added;
        no_trade_events CHECK extended to accept v23 rows.
  - 24: Phase 5 T2 (2026-05-21) — regime_correlation_cache table added;
        no_trade_events CHECK extended to accept v24 rows.
  - 25: live authority/risk follow-up (2026-05-21) — provenance schema bump;
        no_trade_events CHECK extended to accept v25 rows.
  - 26: evidence governance follow-up (2026-05-21) — structured strategy_key
        and event_source provenance for evidence_report.
  - 27: P1/P2 architecture review (2026-05-22) — evidence lifecycle fields
        (revoke/expiry) in evidence_tier_assignments; day0_nowcast_entry strategy.
  - 28: neg_risk_basket NEGRISK_NO_PROFITABLE_BASKET reason enum member.
  - 29: settlement_capture physical-interval reasons: +4 NoTradeReason members
        (PHYSICAL_INTERVAL_DATA_GATED, PHYSICAL_INTERVAL_OVERLAP,
        PHYSICAL_INTERVAL_UNPROFITABLE, SETTLEMENT_CAPTURE_NOT_LOCKED).

Note: _REASON_VALUES_SQL is enum-derived (iterates NoTradeReason) so adding
SHOULDER_* members to the enum automatically extends the reason CHECK constraint —
no hardcoded value list needed here.
"""

from __future__ import annotations

import re
import sqlite3

from src.contracts.no_trade_reason import NoTradeReason
SCHEMA_VERSION = 55  # B2: frozen row-provenance value at #358 main bump; db.SCHEMA_VERSION counter cancelled

# Enum CHECK: every valid NoTradeReason value, joined for SQL IN clause.
_REASON_VALUES_SQL = ", ".join(f"'{r.value}'" for r in NoTradeReason)

# === Q-KERNEL REBUILD STAGE 0 — decision-receipt spine (2026-06-14) ==================
# 19 NULLABLE columns appended to no_trade_events so every CURRENT live candidate is
# reconstructable forecast -> q -> route -> size from the values the live path used
# (docs/rebuild/consult_build_spec.md:994-1033, field list :1008-1027). Column names are
# EXACTLY the spec field names; the dataclass that fills them is
# src/decision/decision_receipt.py (DecisionReceipt.to_row / RECEIPT_SPINE_COLUMNS — one
# vocabulary). ADDITIVE + observability-only: every column is NULLABLE with no DEFAULT and
# no CHECK, so existing writers that omit them are byte-unaffected and these columns can
# NEVER gate, size, or submit. Stage 0 populates only the fields the current path computes
# (q_source, mu_native, sigma_native, member/debiased envelope, rounding_rule, q_sum,
# applied_debias_native, debias_artifact_id, day0_observed_extreme_native, sizing_authority);
# the spine-only fields (predictive_distribution_id, q_band_basis, market_implied_q, route_id,
# payoff_vector_hash, edge_lcb, delta_u) stay NULL until each later stage wires its own.
# REAL = native-unit floats / probabilities; TEXT = ids / labels.
_RECEIPT_SPINE_COLUMNS_SQL = """    predictive_distribution_id   TEXT,
    q_source                     TEXT,
    mu_native                    REAL,
    sigma_native                 REAL,
    member_min_native            REAL,
    member_max_native            REAL,
    debiased_member_min_native   REAL,
    debiased_member_max_native   REAL,
    applied_debias_native        REAL,
    debias_artifact_id           TEXT,
    day0_observed_extreme_native REAL,
    rounding_rule                TEXT,
    q_sum                        REAL,
    q_band_basis                 TEXT,
    market_implied_q             REAL,
    route_id                     TEXT,
    payoff_vector_hash           TEXT,
    edge_lcb                     REAL,
    delta_u                      REAL,
    sizing_authority             TEXT"""

# (column_name, sql_type) pairs — the authority for ALTER-ADD migration of pre-Stage-0
# tables. Order matches _RECEIPT_SPINE_COLUMNS_SQL and the spec field list.
_RECEIPT_SPINE_COLUMN_DEFS: tuple[tuple[str, str], ...] = (
    ("predictive_distribution_id", "TEXT"),
    ("q_source", "TEXT"),
    ("mu_native", "REAL"),
    ("sigma_native", "REAL"),
    ("member_min_native", "REAL"),
    ("member_max_native", "REAL"),
    ("debiased_member_min_native", "REAL"),
    ("debiased_member_max_native", "REAL"),
    ("applied_debias_native", "REAL"),
    ("debias_artifact_id", "TEXT"),
    ("day0_observed_extreme_native", "REAL"),
    ("rounding_rule", "TEXT"),
    ("q_sum", "REAL"),
    ("q_band_basis", "TEXT"),
    ("market_implied_q", "REAL"),
    ("route_id", "TEXT"),
    ("payoff_vector_hash", "TEXT"),
    ("edge_lcb", "REAL"),
    ("delta_u", "REAL"),
    ("sizing_authority", "TEXT"),
)

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
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)),
    schema_compatibility TEXT NOT NULL DEFAULT 'current'
        CHECK (schema_compatibility IN ('current', 'degraded')),
{_RECEIPT_SPINE_COLUMNS_SQL},
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
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)),
    schema_compatibility TEXT NOT NULL DEFAULT 'current'
        CHECK (schema_compatibility IN ('current', 'degraded')),
{_RECEIPT_SPINE_COLUMNS_SQL},
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


def _ensure_receipt_spine_columns(conn: sqlite3.Connection) -> None:
    """Additively ADD the 19 Stage-0 decision-receipt-spine columns if absent.

    Every spine column is NULLABLE with no DEFAULT and no CHECK, so ALTER TABLE ADD COLUMN
    is non-destructive and requires no table rebuild — an existing no_trade_events table on
    a live world DB gains the columns in place, and rows written before Stage 0 simply carry
    NULL. Idempotent: only columns missing from PRAGMA table_info are added. This never
    touches data and can never gate (observability-only columns).
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_events'"
    ).fetchone():
        return
    existing = _table_columns(conn)
    for column_name, sql_type in _RECEIPT_SPINE_COLUMN_DEFS:
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE no_trade_events ADD COLUMN {column_name} {sql_type}"
            )


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create no_trade_events table + indices without destructive rebuild DDL.

    Runtime writers may call schema assertions, but must not rebuild this table
    while the daemon is in the hot path. Stale CHECK upgrades belong to
    ``migrate_no_trade_events_schema`` during boot/operator migration.
    """
    conn.execute(CREATE_TABLE_SQL)
    # Stage 0 (2026-06-14): additively backfill the receipt-spine columns onto a
    # pre-Stage-0 table created by an earlier CREATE_TABLE_SQL (IF NOT EXISTS is a no-op
    # when the table already exists, so the spine columns must be ALTER-added). NULLABLE +
    # no-default + no-CHECK -> non-destructive, in-place, no hot-path table rebuild.
    _ensure_receipt_spine_columns(conn)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)
    if "strategy_key" in _table_columns(conn):
        conn.execute(CREATE_INDEX_STRATEGY_SQL)


def migrate_no_trade_events_schema(conn: sqlite3.Connection) -> None:
    """Boot/operator migration path for stale no_trade_events CHECK constraints."""

    conn.execute(CREATE_TABLE_SQL)
    _rebuild_stale_no_trade_events_table(conn)
    # Stage 0 (2026-06-14): if the rebuild path returned early (table already current on the
    # reason/version axes) but predates Stage 0, the receipt-spine columns are still absent —
    # add them additively here. After a full rebuild the new table already has them, so this
    # is a no-op (idempotent on PRAGMA table_info).
    _ensure_receipt_spine_columns(conn)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)
    if "strategy_key" in _table_columns(conn):
        conn.execute(CREATE_INDEX_STRATEGY_SQL)


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()}


def _legacy_alter_table_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA legacy_alter_table").fetchone()
    return bool(row and int(row[0] or 0))


def _set_legacy_alter_table(conn: sqlite3.Connection, enabled: bool) -> None:
    conn.execute(f"PRAGMA legacy_alter_table = {'ON' if enabled else 'OFF'}")


def _schema_version_in_list(table_sql: str) -> set[int]:
    """Parse the schema_version IN (...) values from the table-definition SQL."""
    m = re.search(r"schema_version\s+INTEGER[^C]*CHECK\s*\(schema_version\s+IN\s*\(([^)]+)\)", table_sql)
    if not m:
        return set()
    return {int(v.strip()) for v in m.group(1).split(",") if v.strip().lstrip("-").isdigit()}


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
    # the reason CHECK and causing IntegrityError on every no_trade write.
    if (
        not any(r.value not in table_sql for r in NoTradeReason)
        and SCHEMA_VERSION in _schema_version_in_list(table_sql)
        and "schema_compatibility" in table_sql
        and "strategy_key" in table_sql
        and "event_source" in table_sql
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
    conn.execute("DROP TABLE IF EXISTS no_trade_events_new")
    conn.execute(_CREATE_TABLE_REBUILD_SQL)

    # Pre-migration row count: used to detect silent drops from INSERT OR IGNORE.
    _pre_count = conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0]

    # Stage 0 (2026-06-14): carry any receipt-spine columns that the SOURCE table already
    # has through the rebuild so a later CHECK-driven rebuild never DROPS spine data that
    # ensure_table's ALTER-ADD already populated. Columns absent on the source default to
    # NULL on no_trade_events_new (the rebuild table SQL defines all 19). Both lists stay in
    # the same order so the INSERT column list and the SELECT expressions line up.
    _src_columns = _table_columns(conn)
    _spine_present = [
        name for name, _type in _RECEIPT_SPINE_COLUMN_DEFS if name in _src_columns
    ]
    _spine_insert_cols = ("".join(f",\n            {name}" for name in _spine_present))
    _spine_select_cols = ("".join(f",\n            {name}" for name in _spine_present))

    conn.execute(
        f"""
        INSERT OR IGNORE INTO no_trade_events_new (
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason, reason_detail,
            strategy_key, event_source,
            observed_at, schema_version, schema_compatibility{_spine_insert_cols}
        )
        SELECT
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            reason,
            reason_detail,
            strategy_key,
            event_source,
            observed_at,
            CASE
                WHEN schema_version IN (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42) THEN schema_version
                ELSE 36
            END,
            schema_compatibility{_spine_select_cols}
        FROM no_trade_events
        """
    )

    # Post-migration row count: assert no rows were silently dropped by INSERT OR IGNORE.
    # A mismatch here means legacy rows violate the new CHECK constraint — investigate
    # before proceeding (do not silently lose provenance rows).
    _post_count = conn.execute("SELECT COUNT(*) FROM no_trade_events_new").fetchone()[0]
    if _post_count != _pre_count:
        raise RuntimeError(
            f"no_trade_events migration: {_pre_count} rows before INSERT OR IGNORE but only "
            f"{_post_count} rows transferred. {_pre_count - _post_count} rows were silently "
            "dropped — likely CHECK constraint violations on the new schema. "
            "Investigate before retrying migration."
        )

    conn.execute("DROP TABLE no_trade_events")
    _legacy_alter_was_enabled = _legacy_alter_table_enabled(conn)
    _set_legacy_alter_table(conn, True)
    try:
        conn.execute("ALTER TABLE no_trade_events_new RENAME TO no_trade_events")
    finally:
        _set_legacy_alter_table(conn, _legacy_alter_was_enabled)
