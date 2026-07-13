-- Zeus architecture kernel migration
-- Introduces canonical event + projection tables and schema-level semantic constraints.
-- SQLite-compatible.

CREATE TABLE IF NOT EXISTS position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    -- 2026-07-12 T5 MIGRATION (docs/rebuild/quarantine_excision_2026-07-11.md,
    -- scripts/migrations/2026_07_quarantine_phase_retirement.py): the retired
    -- chain-quarantine event type (formerly between CHAIN_SIZE_CORRECTED and
    -- MONITOR_REFRESHED below) is dropped from this CHECK; a historical row
    -- still carrying it is rewritten to REVIEW_REQUIRED by that migration
    -- (already a valid member below). No quoted retired literal in this
    -- comment — see tests/test_architecture_contracts.py's INV-07 SQL/Python
    -- consistency antibody, which naively comma-splits the CHECK list text
    -- and would otherwise misparse a mid-list comment as a phantom member.
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'EXIT_RETRY_RELEASED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED',
        'VENUE_POSITION_OBSERVED',
        'REVIEW_REQUIRED'
    )),
    -- 2026-07-11: 'QUARANTINE' sentinel literal removed from this CHECK — a
    -- state word inside a timestamp type (docs/rebuild/quarantine_excision_2026-07-11.md
    -- §T7). Evidence: zero live position_events rows carry occurred_at='QUARANTINE'
    -- (state/zeus_trades.db, 2026-07-11) and no src/ writer ever sets it; the
    -- literal only ever appeared as a last-resort fallback inside the historical,
    -- already-applied scripts/migrations/202605_position_events_occurred_at_iso_check.py
    -- (kept verbatim as history — it built its own self-contained DDL, independent
    -- of this kernel file, and never hit that fallback branch on live data).
    occurred_at TEXT NOT NULL
        CHECK (occurred_at LIKE '____-__-__T%'),
    -- 2026-07-12 T5 MIGRATION (docs/rebuild/quarantine_excision_2026-07-11.md,
    -- scripts/migrations/2026_07_quarantine_phase_retirement.py): the retired
    -- quarantine phase literal is dropped from phase_before/phase_after/phase
    -- (all three below) here. A legacy row is rewritten to the position's
    -- true phase by that migration (REPLACEMENT PHASE LAW). Comment kept
    -- OUTSIDE the parenthesized IN(...) lists — see
    -- tests/test_architecture_contracts.py's INV-07 antibody, which naively
    -- comma-splits the CHECK list text.
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
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
        'admin_closed'
    )),
    strategy_key TEXT NOT NULL,
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest')),
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
);

CREATE TRIGGER IF NOT EXISTS trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN
    SELECT RAISE(FAIL, 'position_events.env is required');
END;

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
    -- 2026-07-12 T5 MIGRATION: see the phase_before comment above — same
    -- retired-literal drop, same rationale, applies to this phase CHECK too.
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
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
    last_monitor_prob_is_fresh INTEGER,
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    last_monitor_market_price_is_fresh INTEGER,
    last_monitor_best_bid REAL,
    last_monitor_best_ask REAL,
    last_monitor_market_vig REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL,
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    -- PR D0b (Finding D0/D2-wire, Part-2 audit, 2026-05-27): durable
    -- authority projection. NULL-default so columns are additive on
    -- legacy DBs via ALTER TABLE ADD COLUMN. Downstream training gates
    -- and crash-recovery loaders consult these fields to distinguish
    -- balance-only recovery from trade-verified fill.
    fill_authority TEXT,
    recovery_authority TEXT,
    chain_shares REAL,
    -- F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
    -- economics columns. Balance-only rescue writes the chain aggregate
    -- here instead of mutating entry_price / cost_basis_usd / size_usd /
    -- shares. Additive on legacy DBs via
    -- _ensure_position_current_authority_columns.
    chain_avg_price REAL,
    chain_cost_basis_usd REAL,
    chain_seen_at TEXT,
    chain_absence_at TEXT,
    -- BUG #128 (SEV1, 2026-06-02): durable realized-P&L projection. Nullable
    -- columns persist close economics through the canonical write path so a
    -- filled+settled order leaves a queryable P&L record (GOAL#36 post-fill
    -- correctness). NULL on open/legacy rows; populated at economic-close /
    -- settlement. Additive on legacy DBs via
    -- _ensure_position_current_authority_columns.
    realized_pnl_usd REAL,
    exit_price REAL,
    settlement_price REAL,
    settled_at TEXT,
    exit_reason TEXT,
    -- Canonical-column parity (2026-06-12): these were added to
    -- CANONICAL_POSITION_CURRENT_COLUMNS by later packets but never to this
    -- kernel DDL, so a FRESH kernel DB failed assert_canonical_transaction_schema
    -- while legacy DBs were healed by the ALTER backfill. entry_ci_width is the
    -- entry-time q CI width; exit_retry_count / next_exit_retry_at are the K3
    -- exit-retry backoff projection (task #45). Nullable / zero-default so the
    -- ALTER path on legacy DBs stays equivalent.
    entry_ci_width REAL,
    exit_retry_count INTEGER,
    next_exit_retry_at TEXT
);

CREATE TABLE IF NOT EXISTS strategy_health (
    strategy_key TEXT NOT NULL,
    as_of TEXT NOT NULL,
    open_exposure_usd REAL NOT NULL DEFAULT 0,
    settled_trades_30d INTEGER NOT NULL DEFAULT 0,
    realized_pnl_30d REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    win_rate_30d REAL,
    brier_30d REAL,
    fill_rate_14d REAL,
    edge_trend_30d REAL,
    risk_level TEXT,
    execution_decay_flag INTEGER NOT NULL DEFAULT 0 CHECK (execution_decay_flag IN (0, 1)),
    edge_compression_flag INTEGER NOT NULL DEFAULT 0 CHECK (edge_compression_flag IN (0, 1)),
    PRIMARY KEY (strategy_key, as_of)
);

CREATE TABLE IF NOT EXISTS risk_actions (
    action_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
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

-- B070: control_overrides is an event-sourced projection.
-- control_overrides_history is the canonical append-only log; the
-- control_overrides VIEW projects the latest recorded_at per override_id.
--
-- DO NOT add writes that bypass control_overrides_history.
-- DO NOT remove control_overrides_history: the VIEW depends on it. Removing
--   the history table breaks every override read (riskguard, control_plane).
--   See git log e6dd214 ("refactor: remove 2 dead audit tables") for the
--   prior incident where this history table was cleaned up as 'dead' and had
--   to be reimplemented as B070.
CREATE TABLE IF NOT EXISTS control_overrides_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('strategy', 'global', 'position')),
    target_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    value TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    precedence INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'expire', 'migrated', 'revoke')),
    recorded_at TEXT NOT NULL
);

-- Index covers the `MAX(history_id) WHERE override_id = ?` lookup used by the
-- VIEW and by expire_control_override.
CREATE INDEX IF NOT EXISTS idx_control_overrides_history_id_time
    ON control_overrides_history(override_id, history_id DESC);

CREATE TRIGGER IF NOT EXISTS control_overrides_history_no_update
BEFORE UPDATE ON control_overrides_history
BEGIN
    SELECT RAISE(ABORT, 'control_overrides_history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS control_overrides_history_no_delete
BEFORE DELETE ON control_overrides_history
BEGIN
    SELECT RAISE(ABORT, 'control_overrides_history is append-only');
END;

-- VIEW orders by `history_id` (AUTOINCREMENT, strictly monotone per writer)
-- rather than `recorded_at` (wall-clock, microsecond-resolution, vulnerable
-- to ties and clock skew). `recorded_at` is retained as an observability
-- field but is not load-bearing for ordering.
CREATE VIEW IF NOT EXISTS control_overrides AS
SELECT override_id, target_type, target_key, action_type, value,
       issued_by, issued_at, effective_until, reason, precedence
FROM control_overrides_history h1
WHERE history_id = (
    SELECT MAX(history_id)
    FROM control_overrides_history h2
    WHERE h2.override_id = h1.override_id
);

-- B071: token_suppression is an event-sourced projection (mirrors B070).
-- token_suppression_history is the canonical append-only log; the
-- token_suppression_current VIEW projects the latest row (by history_id,
-- AUTOINCREMENT) per token_id.
--
-- DO NOT add writes that bypass token_suppression_history.
-- DO NOT remove token_suppression_history: the VIEW depends on it.
-- See B070 (control_overrides_history) for the pattern rationale.
CREATE TABLE IF NOT EXISTS token_suppression_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    operation TEXT NOT NULL DEFAULT 'record' CHECK (operation IN ('record', 'migrated')),
    recorded_at TEXT NOT NULL
);

-- Index covers MAX(history_id) WHERE token_id = ? lookup used by the VIEW.
CREATE INDEX IF NOT EXISTS idx_token_suppression_history_id_time
    ON token_suppression_history(token_id, history_id DESC);

CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_update
BEFORE UPDATE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_delete
BEFORE DELETE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END;

-- VIEW orders by history_id (AUTOINCREMENT, strictly monotone per writer)
-- rather than created_at (wall-clock, vulnerable to ties and clock skew).
CREATE VIEW IF NOT EXISTS token_suppression_current AS
SELECT token_id, condition_id, suppression_reason, source_module,
       created_at, updated_at, evidence_json
FROM token_suppression_history h1
WHERE history_id = (
    SELECT MAX(history_id)
    FROM token_suppression_history h2
    WHERE h2.token_id = h1.token_id
);

-- Legacy token_suppression table kept for backward-compat until migration runs.
-- After migration (scripts/migrate_b071_token_suppression_to_history.py --apply
-- with ZEUS_DESTRUCTIVE_CONFIRMED=1), this table is DROPped and the name
-- token_suppression becomes an alias VIEW for token_suppression_current.
CREATE TABLE IF NOT EXISTS token_suppression (
    token_id TEXT PRIMARY KEY,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_token_suppression_reason
    ON token_suppression(suppression_reason, updated_at);

CREATE TABLE IF NOT EXISTS opportunity_fact (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    city TEXT,
    target_date TEXT,
    range_label TEXT,
    direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
    strategy_key TEXT,
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
    -- OBS-AUTHORITY-FOUNDATION (2026-05-23): FK to the settlement-day
    -- observation authority row captured at decision time. NULL for legacy
    -- rows and non-settlement-day candidates (no day0/settlement obs fetched).
    -- Idempotent ALTER in log_opportunity_fact backfills production trade DBs
    -- whose opportunity_fact predates this column.
    observation_authority_id TEXT,
    -- OBS-AUTHORITY-FOUNDATION FIX-2 (2026-05-23): per-edge day0 observation-lock
    -- classification payload (JSON). day0_truth_classification + observed
    -- high/low + candidate bin bounds + settlement_capture eligibility. This is
    -- what makes "is this day0 edge observation-locked, forecast-upside, or
    -- wrong?" answerable per opportunity row. NULL for non-day0/non-HIGH rows.
    day0_context_json TEXT,
    recorded_at TEXT NOT NULL
);

-- NOTE: settlement_day_observation_authority (OBS-AUTHORITY-FOUNDATION
-- 2026-05-23) is a TRADE-CLASS table — its DDL lives in db.py _TRADE_CLASS_DDL
-- and is created only by init_schema_trade_only on zeus_trades.db (colocated
-- with opportunity_fact's runtime write target). It is intentionally NOT
-- created here so init_schema (world) does not pollute zeus-world.db with it.

CREATE TABLE IF NOT EXISTS execution_fact (
    intent_id TEXT PRIMARY KEY,
    position_id TEXT,
    decision_id TEXT,
    order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
    strategy_key TEXT,
    posted_at TEXT,
    filled_at TEXT,
    voided_at TEXT,
    submitted_price REAL,
    fill_price REAL,
    shares REAL,
    fill_quality REAL,
    latency_seconds REAL,
    venue_status TEXT,
    terminal_exec_status TEXT,
    -- F7: FK to venue_commands.command_id — column added by 202605 migration batch.
    command_id TEXT
);

CREATE TABLE IF NOT EXISTS outcome_fact (
    position_id TEXT PRIMARY KEY,
    strategy_key TEXT,
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
