-- Zeus Data Improvement Foundation Plus
-- Purpose:
--   1) unify probability trace truth
--   2) move calibration to decision-group aware accounting
--   3) add OOS model-eval lineage
--   4) create Day0 residual training fact surface
--   5) persist selection-family hypothesis testing
--   6) persist data-driven city correlation estimates
--
-- SQLite-compatible. Additive only.

BEGIN;

CREATE TABLE IF NOT EXISTS probability_trace_fact (
    trace_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    decision_snapshot_id TEXT,
    candidate_id TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    mode TEXT,
    strategy_key TEXT,
    entry_method TEXT,
    selected_method TEXT,
    model_family TEXT,
    calibration_bucket_key TEXT,
    input_space TEXT,
    lead_hours REAL,
    forecast_available_at TEXT,
    p_raw_json TEXT NOT NULL,
    p_cal_json TEXT,
    p_market_json TEXT,
    p_posterior_json TEXT,
    alpha REAL,
    agreement TEXT,
    calibration_version TEXT,
    fusion_version TEXT,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_probability_trace_city_target
ON probability_trace_fact(city, target_date, recorded_at);

CREATE TABLE IF NOT EXISTS calibration_decision_group (
    group_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    forecast_available_at TEXT NOT NULL,
    cluster TEXT NOT NULL,
    season TEXT NOT NULL,
    lead_days REAL NOT NULL,
    settlement_value REAL,
    winning_range_label TEXT,
    bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
    n_pair_rows INTEGER NOT NULL,
    n_positive_rows INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calibration_decision_group_key
ON calibration_decision_group(city, target_date, forecast_available_at);

CREATE INDEX IF NOT EXISTS idx_calibration_decision_group_bucket
ON calibration_decision_group(cluster, season, lead_days);

CREATE TABLE IF NOT EXISTS model_eval_run (
    run_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    task_name TEXT NOT NULL,
    data_source TEXT NOT NULL,
    split_method TEXT NOT NULL,
    train_start TEXT,
    train_end TEXT,
    test_start TEXT,
    test_end TEXT,
    scorer_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created', 'running', 'completed', 'failed')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS model_eval_point (
    point_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    point_type TEXT NOT NULL CHECK (point_type IN ('calibration_group', 'trade_decision', 'day0_observation', 'forecast_error')),
    reference_id TEXT NOT NULL,
    city TEXT,
    target_date TEXT,
    bucket_key TEXT,
    lead_days REAL,
    y_true REAL,
    p_raw REAL,
    p_cal REAL,
    p_post REAL,
    log_loss REAL,
    brier REAL,
    crps REAL,
    ece_bin TEXT,
    meta_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES model_eval_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_model_eval_point_run
ON model_eval_point(run_id, point_type, city, target_date);

CREATE TABLE IF NOT EXISTS selection_family_fact (
    family_id TEXT PRIMARY KEY,
    cycle_mode TEXT NOT NULL,
    decision_snapshot_id TEXT,
    city TEXT,
    target_date TEXT,
    strategy_key TEXT,
    discovery_mode TEXT,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS selection_hypothesis_fact (
    hypothesis_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    decision_id TEXT,
    candidate_id TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    range_label TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
    p_value REAL,
    q_value REAL,
    ci_lower REAL,
    ci_upper REAL,
    edge REAL,
    tested INTEGER NOT NULL DEFAULT 1 CHECK (tested IN (0, 1)),
    passed_prefilter INTEGER NOT NULL DEFAULT 0 CHECK (passed_prefilter IN (0, 1)),
    selected_post_fdr INTEGER NOT NULL DEFAULT 0 CHECK (selected_post_fdr IN (0, 1)),
    rejection_stage TEXT,
    recorded_at TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES selection_family_fact(family_id)
);

CREATE INDEX IF NOT EXISTS idx_selection_hypothesis_family
ON selection_hypothesis_fact(family_id, selected_post_fdr, p_value);

CREATE TABLE IF NOT EXISTS day0_residual_fact (
    fact_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    source TEXT NOT NULL,
    local_timestamp TEXT NOT NULL,
    local_hour REAL,
    temp_current REAL,
    running_max REAL,
    delta_rate_per_h REAL,
    daylight_progress REAL,
    obs_age_minutes REAL,
    post_peak_confidence REAL,
    ens_q50_remaining REAL,
    ens_q90_remaining REAL,
    ens_spread REAL,
    settlement_value REAL,
    residual_upside REAL,
    has_upside INTEGER CHECK (has_upside IN (0, 1)),
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_day0_residual_city_ts
ON day0_residual_fact(city, target_date, local_timestamp);

CREATE TABLE IF NOT EXISTS city_correlation_estimate (
    as_of TEXT NOT NULL,
    method TEXT NOT NULL,
    domain TEXT NOT NULL CHECK (domain IN ('settlement_anomaly', 'forecast_error')),
    source TEXT,
    lead_days REAL,
    city_a TEXT NOT NULL,
    city_b TEXT NOT NULL,
    estimate REAL NOT NULL,
    effective_n INTEGER NOT NULL,
    shrinkage_lambda REAL,
    meta_json TEXT NOT NULL,
    PRIMARY KEY (as_of, method, domain, source, lead_days, city_a, city_b)
);

CREATE INDEX IF NOT EXISTS idx_city_correlation_estimate_domain
ON city_correlation_estimate(domain, source, lead_days, city_a, city_b);

CREATE VIEW IF NOT EXISTS vw_probability_trace_completeness AS
SELECT
    COUNT(*) AS trace_rows,
    SUM(CASE WHEN p_raw_json IS NOT NULL AND trim(p_raw_json) <> '' THEN 1 ELSE 0 END) AS with_p_raw_json,
    SUM(CASE WHEN p_cal_json IS NOT NULL AND trim(p_cal_json) <> '' THEN 1 ELSE 0 END) AS with_p_cal_json,
    SUM(CASE WHEN p_market_json IS NOT NULL AND trim(p_market_json) <> '' THEN 1 ELSE 0 END) AS with_p_market_json,
    SUM(CASE WHEN p_posterior_json IS NOT NULL AND trim(p_posterior_json) <> '' THEN 1 ELSE 0 END) AS with_p_posterior_json
FROM probability_trace_fact;

CREATE VIEW IF NOT EXISTS vw_calibration_bucket_health AS
SELECT
    cluster || '_' || season AS bucket_key,
    cluster,
    season,
    COUNT(*) AS decision_groups,
    AVG(n_pair_rows) AS avg_pair_rows_per_group,
    SUM(n_positive_rows) AS positive_rows,
    MIN(lead_days) AS min_lead_days,
    MAX(lead_days) AS max_lead_days
FROM calibration_decision_group
GROUP BY cluster, season;

COMMIT;
