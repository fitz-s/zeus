# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 (T2 Day0Nowcast)
"""Migration: create day0_horizon_platt_fits and day0_nowcast_runs on zeus-forecasts.db.

SCHEMA_FORECASTS_VERSION: 3 → 4 (T2 Day0Nowcast tables).

Idempotent: uses CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS
+ CREATE TRIGGER IF NOT EXISTS. Safe to re-run after partial failure.

TARGET_DB = "forecasts" — migration runner applies this to zeus-forecasts.db only.
"""

TARGET_DB = "forecasts"


def up(conn):
    """Create day0_horizon_platt_fits and day0_nowcast_runs tables.

    day0_horizon_platt_fits: one row per HorizonPlattFit execution (fit_run_id PK).
    day0_nowcast_runs: one row per Day0HighNowcastSignal evaluation.

    CHECK (schema_version IN (3, 4)) allows both pre-bump and post-bump rows
    during migration window (mirrors T1 pattern).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day0_horizon_platt_fits (
            fit_run_id          TEXT PRIMARY KEY,
            fit_version         TEXT NOT NULL,
            alpha               REAL NOT NULL,
            beta                REAL NOT NULL,
            gamma_morning       REAL NOT NULL,
            gamma_afternoon     REAL NOT NULL,
            gamma_post_peak     REAL NOT NULL,
            delta               REAL NOT NULL,
            epsilon             REAL NOT NULL,
            fit_date            TEXT,
            n_obs               INTEGER NOT NULL,
            sample_period_start TEXT,
            sample_period_end   TEXT,
            schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
            source              TEXT NOT NULL CHECK (source IN ('live_fit', 'replay_fit'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS day0_nowcast_runs (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            target_date         TEXT NOT NULL,
            observation_time    TEXT NOT NULL,
            run_seq             INTEGER NOT NULL,
            nowcast_event_id    TEXT,
            fit_run_id          TEXT NOT NULL
                REFERENCES day0_horizon_platt_fits(fit_run_id),
            p_nowcast_json      TEXT,
            p_now_raw_json      TEXT,
            hours_remaining     REAL NOT NULL,
            daypart             TEXT NOT NULL
                CHECK (daypart IN ('pre_sunrise','morning','afternoon','post_peak')),
            schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
            source              TEXT NOT NULL CHECK (source IN ('live_nowcast', 'replay')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_slug_date
            ON day0_nowcast_runs(market_slug, target_date)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_event_id
            ON day0_nowcast_runs(nowcast_event_id)
    """)

    # AFTER INSERT backstop: stamp sentinel if writer bypassed nei computation.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_day0_nowcast_runs_nei_backstop
        AFTER INSERT ON day0_nowcast_runs
        WHEN NEW.nowcast_event_id IS NULL
        BEGIN
            UPDATE day0_nowcast_runs
            SET nowcast_event_id = 'nei_v1_BACKSTOP_NULL_WRITER_BYPASS'
            WHERE market_slug        = NEW.market_slug
              AND temperature_metric = NEW.temperature_metric
              AND target_date        = NEW.target_date
              AND observation_time   = NEW.observation_time
              AND run_seq            = NEW.run_seq;
        END
    """)

    # Bump schema version to 4
    conn.execute("PRAGMA user_version = 4")
