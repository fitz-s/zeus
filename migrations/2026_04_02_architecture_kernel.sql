-- Zeus architecture kernel migration
-- Introduces canonical event + projection tables and schema-level semantic constraints.
-- SQLite-compatible.

CREATE TABLE IF NOT EXISTS position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'CHAIN_QUARANTINED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED'
    )),
    occurred_at TEXT NOT NULL,
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
);

CREATE TRIGGER IF NOT EXISTS trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;

CREATE TABLE IF NOT EXISTS position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    trade_id TEXT,
    market_id TEXT,
    city TEXT,
    cluster TEXT,
    target_date TEXT,
    bin_label TEXT,
    direction TEXT CHECK (direction IS NULL OR direction IN ('buy_yes', 'buy_no', 'unknown')),
    unit TEXT CHECK (unit IS NULL OR unit IN ('F', 'C')),
    size_usd REAL,
    shares REAL,
    cost_basis_usd REAL,
    entry_price REAL,
    p_posterior REAL,
    last_monitor_prob REAL,
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_actions (
    action_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    action_type TEXT NOT NULL CHECK (action_type IN (
        'gate',
        'allocation_multiplier',
        'threshold_multiplier',
        'exit_only'
    )),
    value TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('riskguard', 'manual', 'system')),
    precedence INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'revoked'))
);

CREATE TABLE IF NOT EXISTS control_overrides (
    override_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('strategy', 'global', 'position')),
    target_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    value TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    precedence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_fact (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    city TEXT,
    target_date TEXT,
    range_label TEXT,
    direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    discovery_mode TEXT,
    entry_method TEXT,
    snapshot_id TEXT,
    p_raw REAL,
    p_cal REAL,
    p_market REAL,
    alpha REAL,
    best_edge REAL,
    ci_width REAL,
    rejection_stage TEXT,
    rejection_reason_json TEXT,
    availability_status TEXT CHECK (availability_status IN (
        'ok',
        'missing',
        'stale',
        'rate_limited',
        'unavailable',
        'chain_unavailable'
    )),
    should_trade INTEGER NOT NULL CHECK (should_trade IN (0, 1)),
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_fact (
    intent_id TEXT PRIMARY KEY,
    position_id TEXT,
    decision_id TEXT,
    order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    posted_at TEXT,
    filled_at TEXT,
    voided_at TEXT,
    submitted_price REAL,
    fill_price REAL,
    shares REAL,
    fill_quality REAL,
    latency_seconds REAL,
    venue_status TEXT,
    terminal_exec_status TEXT
);

CREATE TABLE IF NOT EXISTS outcome_fact (
    position_id TEXT PRIMARY KEY,
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    entered_at TEXT,
    exited_at TEXT,
    settled_at TEXT,
    exit_reason TEXT,
    admin_exit_reason TEXT,
    decision_snapshot_id TEXT,
    pnl REAL,
    outcome INTEGER CHECK (outcome IN (0, 1)),
    hold_duration_hours REAL,
    monitor_count INTEGER,
    chain_corrections_count INTEGER
);

CREATE TABLE IF NOT EXISTS availability_fact (
    availability_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
    scope_key TEXT NOT NULL,
    failure_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
    details_json TEXT NOT NULL
);
