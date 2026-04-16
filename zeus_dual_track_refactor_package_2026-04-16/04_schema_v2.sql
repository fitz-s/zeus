-- Zeus dual-track world-data schema v2
-- Purpose:
--   1) prevent high/low contamination
--   2) preserve explicit physical quantity identity
--   3) allow runtime fallbacks without polluting training
--
-- This is a migration skeleton, not a drop-in production migration.

BEGIN;

CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (
    snapshot_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    issue_time_utc TEXT,
    available_at TEXT,
    fetch_time TEXT,
    lead_day INTEGER NOT NULL,
    lead_hours REAL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    physical_quantity TEXT NOT NULL,
    aggregation_contract TEXT NOT NULL,
    observation_field TEXT NOT NULL CHECK (observation_field IN ('high_temp','low_temp')),
    source_family TEXT NOT NULL,
    data_version TEXT NOT NULL,
    geometry_version TEXT NOT NULL,
    lead_day_anchor TEXT NOT NULL DEFAULT 'issue_utc.date()',
    causality_status TEXT NOT NULL CHECK (
        causality_status IN (
            'OK',
            'N/A_CAUSAL_DAY_ALREADY_STARTED',
            'N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON',
            'MISSING_RAW',
            'MISSING_EXTRACT'
        )
    ),
    training_allowed INTEGER NOT NULL CHECK (training_allowed IN (0,1)),
    boundary_policy TEXT,
    boundary_ambiguous INTEGER CHECK (boundary_ambiguous IN (0,1) OR boundary_ambiguous IS NULL),
    ambiguous_member_count INTEGER,
    manifest_hash TEXT,
    member_count INTEGER NOT NULL,
    members_json TEXT NOT NULL,
    spread REAL,
    is_bimodal INTEGER NOT NULL DEFAULT 0 CHECK (is_bimodal IN (0,1)),
    p_raw_json TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED')),
    raw_snapshot_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(city, target_date, temperature_metric, issue_time_utc, data_version, aggregation_contract)
);

CREATE INDEX IF NOT EXISTS idx_ensemble_snapshots_v2_lookup
ON ensemble_snapshots_v2(city, target_date, temperature_metric, available_at);

CREATE TABLE IF NOT EXISTS calibration_pairs_v2 (
    pair_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES ensemble_snapshots_v2(snapshot_id),
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    physical_quantity TEXT NOT NULL,
    observation_field TEXT NOT NULL CHECK (observation_field IN ('high_temp','low_temp')),
    source_family TEXT NOT NULL,
    data_version TEXT NOT NULL,
    range_label TEXT NOT NULL,
    p_raw REAL NOT NULL CHECK (p_raw >= 0.0 AND p_raw <= 1.0),
    outcome INTEGER NOT NULL CHECK (outcome IN (0,1)),
    settlement_value REAL NOT NULL,
    lead_days INTEGER NOT NULL,
    season TEXT NOT NULL,
    cluster TEXT NOT NULL,
    decision_group_id TEXT NOT NULL,
    bin_source TEXT NOT NULL,
    input_space TEXT NOT NULL DEFAULT 'raw_probability',
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED')),
    created_at TEXT NOT NULL,
    UNIQUE(snapshot_id, range_label)
);

CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_bucket
ON calibration_pairs_v2(temperature_metric, cluster, season, bin_source, authority);

CREATE TABLE IF NOT EXISTS platt_models_v2 (
    model_id TEXT PRIMARY KEY,
    bucket_key TEXT NOT NULL UNIQUE,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    physical_quantity TEXT NOT NULL,
    data_version TEXT NOT NULL,
    cluster TEXT NOT NULL,
    season TEXT NOT NULL,
    bin_source TEXT NOT NULL,
    input_space TEXT NOT NULL,
    param_A REAL NOT NULL,
    param_B REAL NOT NULL,
    param_C REAL NOT NULL DEFAULT 0.0,
    bootstrap_params_json TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    brier_insample REAL,
    fitted_at TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'VERIFIED'
        CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    UNIQUE(
        temperature_metric,
        physical_quantity,
        data_version,
        cluster,
        season,
        bin_source,
        input_space,
        is_active
    )
);

CREATE INDEX IF NOT EXISTS idx_platt_models_v2_lookup
ON platt_models_v2(temperature_metric, cluster, season, is_active);

CREATE TABLE IF NOT EXISTS ensemble_coverage_v2 (
    city TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    lead_day INTEGER NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    data_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'OK',
            'MISSING_RAW',
            'MISSING_EXTRACT',
            'N/A_CAUSAL_DAY_ALREADY_STARTED',
            'N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON',
            'REJECTED_BOUNDARY_AMBIGUOUS',
            'REJECTED_MEMBER_COUNT',
            'REJECTED_GRIB_INTEGRITY'
        )
    ),
    details_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (city, issue_date, target_date, lead_day, temperature_metric, data_version)
);

-- Observation / Day0 substrate extensions
-- Note: SQLite ALTER TABLE support is limited; use defensive migration wrappers in Python.
-- These statements are illustrative; production code should catch duplicate-column failures.

-- observation_instants:
--   add running_min so low Day0 can be causal and monotone in its own state space
ALTER TABLE observation_instants ADD COLUMN running_min REAL;

-- day0_residual_fact:
--   low track needs downside, not just upside
ALTER TABLE day0_residual_fact ADD COLUMN running_min REAL;
ALTER TABLE day0_residual_fact ADD COLUMN residual_downside REAL;
ALTER TABLE day0_residual_fact ADD COLUMN has_downside INTEGER
    CHECK (has_downside IN (0,1) OR has_downside IS NULL);

COMMIT;
