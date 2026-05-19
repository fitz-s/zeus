# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.2 (Path D natural-key schema, v3)
# SCAFFOLD: migration script — production execution pending T1 production pass
"""Create decision_events + AFTER INSERT TRIGGER (backstop) + indices on world DB.

Idempotent (IF NOT EXISTS).  Does NOT bump SCHEMA_VERSION (src/state/db.py owns).

v3 changes from v2:
- PK simplified to 5 components: (market_slug, temperature_metric, target_date,
  observation_time, decision_seq) — condition_id DROPPED from PK (nullable pre-discovery).
- Column rename: market_id → market_slug; decision_group_id → decision_event_id.
- Trigger strategy: Option β (writer-side hash) + backstop-only trigger.
  Trigger fires ONLY on NULL decision_event_id, populating sentinel value
  'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'. Compliant writers compute the hash
  via decision_event_id_v1_hash() before INSERT — trigger is dormant.
- NO SQLite UDF binding required (Option β eliminates the 4→7 kwarg mismatch).
"""

# Natural-key PK (5 components); condition_id is nullable enrichment (not PK).
# decision_event_id: deid_v1_ namespace — DISTINCT from dgid_v1_ (calibration).
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS decision_events (
    -- Natural key (PK) — 5 components; condition_id excluded (nullable pre-discovery)
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,

    -- Enrichment-only (nullable; NOT in PK)
    condition_id        TEXT,

    -- Audit-only derived hash — writer computes via decision_event_id_v1_hash()
    -- Trigger backstop fires on NULL, setting sentinel (Option β per ultraplan §4.2.2)
    decision_event_id   TEXT,

    decision_time       TEXT NOT NULL,

    -- Identity
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,

    -- Probability outputs (live-only; NULL for backfill)
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,

    -- DecisionSourceContext — PR 3 (5 fields)
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL CHECK (
        polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback')
    ),

    -- DecisionSourceContext — PR 6 (8 fields)
    -- Nullable to allow phase0_backfill rows; Python enforces NOT NULL for source='live_decision'
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,

    -- Provenance
    schema_version INTEGER NOT NULL CHECK (schema_version IN (12, 13)),
    source         TEXT NOT NULL CHECK (source IN ('phase0_backfill', 'live_decision')),

    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)"""

# AFTER INSERT TRIGGER — backstop only (Option β).
# Fires WHEN NEW.decision_event_id IS NULL (writer bypassed hash computation).
# Compliant writers never trigger this; sentinel surfaces as anomaly in audit.
_CREATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS decision_events_event_id_backstop
AFTER INSERT ON decision_events
FOR EACH ROW
WHEN NEW.decision_event_id IS NULL
BEGIN
    UPDATE decision_events
       SET decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
     WHERE market_slug = NEW.market_slug
       AND temperature_metric = NEW.temperature_metric
       AND target_date = NEW.target_date
       AND observation_time = NEW.observation_time
       AND decision_seq = NEW.decision_seq;
END"""

_CREATE_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_decision_events_slug_date ON decision_events(market_slug, target_date)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_strategy ON decision_events(strategy_key, decision_time)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_event_id ON decision_events(decision_event_id)",
]


def main() -> None:
    """Apply decision_events CREATE TABLE, AFTER INSERT TRIGGER, and 3 indices to world DB.

    Idempotent (IF NOT EXISTS). Does NOT bump SCHEMA_VERSION — src/state/db.py owns that
    via init_schema(). This script is a standalone migration path for production deployments
    where the DB already exists and init_schema() must not be re-run in full.
    """
    import sqlite3

    # Import the canonical world DB path (avoids hardcoding)
    import sys
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415

    db_path = ZEUS_WORLD_DB_PATH
    print(f"Applying decision_events schema to: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_TABLE)
        print("  CREATE TABLE decision_events: OK")

        conn.execute(_CREATE_TRIGGER)
        print("  CREATE TRIGGER decision_events_event_id_backstop: OK")

        for stmt in _CREATE_INDICES:
            conn.execute(stmt)
        print(f"  CREATE INDEX x{len(_CREATE_INDICES)}: OK")

        conn.commit()
        print("Done. decision_events schema applied idempotently.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
