# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.2 (Path D natural-key schema)
# SCAFFOLD: migration script — production execution pending T1 production pass
"""Create decision_events + AFTER INSERT TRIGGER + indices on world DB.

Idempotent (IF NOT EXISTS).  Does NOT bump SCHEMA_VERSION (src/state/db.py).
TRIGGER requires 'decision_group_id_v1' UDF bound via connection.create_function()
— see §4.2.2 Option α.  UDF arg mapping is ambiguity #1 for wave-critic round 2.
"""

# Natural-key PK; decision_group_id audit-only (trigger-populated)
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS decision_events (
    market_id             TEXT NOT NULL,
    condition_id          TEXT NOT NULL,
    temperature_metric    TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    target_date           TEXT NOT NULL,
    observation_time      TEXT NOT NULL,
    decision_seq          INTEGER NOT NULL,
    decision_group_id     TEXT,
    decision_time         TEXT NOT NULL,
    outcome               TEXT NOT NULL,
    side                  TEXT NOT NULL,
    strategy_key          TEXT NOT NULL,
    cycle_id              TEXT,
    cycle_iteration       INTEGER,
    p_posterior           REAL,
    edge                  REAL,
    target_size_usd       REAL,
    target_price          REAL,
    forecast_time         TEXT,
    provider_reported_time TEXT,
    observation_available_at TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL CHECK (
        polymarket_end_anchor_source IN ('gamma_explicit','f1_12z_fallback')),
    first_member_observed_time   TEXT NOT NULL,
    run_complete_time             TEXT NOT NULL,
    zeus_submit_intent_time       TEXT NOT NULL,
    venue_ack_time                TEXT NOT NULL,
    first_inclusion_block_time   TEXT,
    finality_confirmed_time      TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL CHECK (schema_version IN (12,13)),
    source         TEXT NOT NULL CHECK (source IN ('phase0_backfill','live_decision')),
    PRIMARY KEY (market_id, condition_id, temperature_metric,
                 target_date, observation_time, decision_seq)
)"""

# SCAFFOLD-PENDING §4.2.2: UDF arg mapping to decision_group_id_v1_hash is
# ambiguity #1 for wave-critic — 4-arg call does not match existing 7-kwarg sig.
_CREATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS decision_events_hash_after_insert
AFTER INSERT ON decision_events FOR EACH ROW
WHEN NEW.decision_group_id IS NULL
BEGIN
    UPDATE decision_events
       SET decision_group_id = decision_group_id_v1(
               NEW.strategy_key, NEW.market_id, NEW.target_date, NEW.observation_time)
     WHERE market_id=NEW.market_id AND condition_id=NEW.condition_id
       AND temperature_metric=NEW.temperature_metric AND target_date=NEW.target_date
       AND observation_time=NEW.observation_time AND decision_seq=NEW.decision_seq;
END"""

_CREATE_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_decision_events_market_date ON decision_events(market_id, target_date)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_strategy ON decision_events(strategy_key, decision_time)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_hash ON decision_events(decision_group_id)",
]


def main() -> None:
    """SCAFFOLD — production body pending T1 production pass."""
    raise NotImplementedError("SCAFFOLD — pending T1 production")


if __name__ == "__main__":
    main()
