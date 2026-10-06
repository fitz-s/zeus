-- Live state/zeus_trades.db DDL (schema only, no rows) captured read-only 2026-10-06
-- for tests/test_repair_historical_position_economics_2026_10_06.py.

CREATE TABLE "position_events" (
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
    ,
        'POSITION_IDENTITY_SUPERSEDED',
        'POSITION_TOKEN_SPLIT_RECONSTRUCTED')),
    occurred_at TEXT NOT NULL
        CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE'),
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
        'quarantined',
        'admin_closed'
    )),
    strategy_key TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest','shadow')),
    payload_json TEXT NOT NULL, decision_law_id TEXT, position_origin TEXT,
    UNIQUE(position_id, sequence_no)
);

CREATE TABLE "position_current" (
    position_id TEXT PRIMARY KEY,
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
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT,
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low'))
, fill_authority TEXT, recovery_authority TEXT, chain_shares REAL, chain_seen_at TEXT, chain_absence_at TEXT, chain_avg_price REAL, chain_cost_basis_usd REAL, realized_pnl_usd REAL, exit_price REAL, settlement_price REAL, settled_at TEXT, exit_reason TEXT, entry_ci_width REAL, exit_retry_count INTEGER, next_exit_retry_at TEXT, last_monitor_prob_is_fresh INTEGER, last_monitor_market_price_is_fresh INTEGER, last_monitor_best_bid REAL, last_monitor_best_ask REAL, last_monitor_market_vig REAL, decision_law_id TEXT, position_origin TEXT);

CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            -- U1 (INV-NEW-E): every persisted venue command cites an
            -- executable-market snapshot. Freshness/tradability are enforced
            -- in src/state/venue_command_repo.py because they depend on now().
            snapshot_id TEXT NOT NULL,
            -- U2 (INV-NEW-F): every venue command cites a pre-side-effect
            -- submission provenance envelope.
            envelope_id TEXT NOT NULL,
            -- Identity
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            -- Order shape
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            -- Venue identity (NULL until first ACK)
            venue_order_id TEXT,
            -- Lifecycle
            state TEXT NOT NULL,
            last_event_id TEXT,
            -- Timestamps
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            -- Optional review
            review_required_reason TEXT
        , q_version TEXT);

CREATE TABLE venue_trade_facts (
          trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          trade_id TEXT NOT NULL,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN ('MATCHED','MINED','CONFIRMED','RETRYING','FAILED')),
          filled_size TEXT NOT NULL,
          fill_price TEXT NOT NULL,
          fee_paid_micro INTEGER,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (trade_id, local_sequence)
        );

CREATE TABLE venue_fill_cash_facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id         INTEGER NOT NULL,
    tx_hash          TEXT NOT NULL,
    wallet           TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('PROVEN', 'UNKNOWN')),
    reason           TEXT NOT NULL,
    block_number     INTEGER,
    block_hash       TEXT,
    finalized_number INTEGER,
    finalized_hash   TEXT,
    observed_at      TEXT NOT NULL,
    proof_hash       TEXT NOT NULL,
    proof_json       TEXT NOT NULL,
    UNIQUE (chain_id, tx_hash, wallet, proof_hash),
    CHECK (
        status = 'UNKNOWN'
        OR (
            typeof(block_number) = 'integer'
            AND typeof(finalized_number) = 'integer'
            AND block_number >= 0
            AND finalized_number >= block_number
            AND block_hash IS NOT NULL AND length(block_hash) = 66
            AND finalized_hash IS NOT NULL AND length(finalized_hash) = 66
        )
    )
);

CREATE TABLE "payout_observations" (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id        TEXT NOT NULL,
    outcome_index       INTEGER NOT NULL,
    payout_numerator    INTEGER,
    payout_denominator  INTEGER,
    state               TEXT NOT NULL CHECK (state IN (
        'UNKNOWN', 'UNRESOLVED', 'RESOLVED_ZERO', 'RESOLVED_NONZERO'
    )),
    block_number        INTEGER,
    block_hash          TEXT,
    observed_at         TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'chain_rpc',
    superseded_by       INTEGER REFERENCES "payout_observations"(id),
    CHECK (
        (state = 'UNKNOWN' AND (payout_numerator IS NULL OR payout_denominator IS NULL))
        OR (state = 'UNRESOLVED' AND payout_denominator = 0)
        OR (
            state IN ('RESOLVED_ZERO', 'RESOLVED_NONZERO')
            AND payout_denominator IS NOT NULL AND payout_denominator > 0
            AND payout_numerator IS NOT NULL
            AND (
                (state = 'RESOLVED_ZERO' AND payout_numerator = 0)
                OR (state = 'RESOLVED_NONZERO' AND payout_numerator > 0)
            )
        )
    )
);

CREATE TABLE executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          event_slug TEXT,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT,
          outcome_label TEXT CHECK (outcome_label IN ('YES','NO') OR outcome_label IS NULL),
          enable_orderbook INTEGER NOT NULL CHECK (enable_orderbook IN (0,1)),
          active INTEGER NOT NULL CHECK (active IN (0,1)),
          closed INTEGER NOT NULL CHECK (closed IN (0,1)),
          accepting_orders INTEGER CHECK (accepting_orders IN (0,1) OR accepting_orders IS NULL),
          market_start_at TEXT,
          market_end_at TEXT,
          market_close_at TEXT,
          sports_start_at TEXT,
          min_tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          fee_details_json TEXT NOT NULL,
          token_map_json TEXT NOT NULL,
          rfqe INTEGER CHECK (rfqe IN (0,1) OR rfqe IS NULL),
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          orderbook_top_bid TEXT NOT NULL,
          orderbook_top_ask TEXT NOT NULL,
          orderbook_depth_json TEXT NOT NULL,
          raw_gamma_payload_hash TEXT NOT NULL,
          raw_clob_market_info_hash TEXT NOT NULL,
          raw_orderbook_hash TEXT NOT NULL,
          authority_tier TEXT NOT NULL CHECK (authority_tier IN ('GAMMA','DATA','CLOB','CHAIN')),
          captured_at TEXT NOT NULL,
          freshness_deadline TEXT NOT NULL, wide_spread_display_substitution INTEGER NOT NULL DEFAULT 0 CHECK (wide_spread_display_substitution IN (0,1)), depth_at_best_ask INTEGER NOT NULL DEFAULT 0, tradeability_status_json TEXT NOT NULL DEFAULT '{}', capture_trigger TEXT,
          UNIQUE (snapshot_id)
        );

CREATE INDEX idx_position_events_entry_execution_occurred_at
    ON position_events(occurred_at DESC, event_type, strategy_key)
    WHERE event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_REJECTED',
        'ENTRY_ORDER_VOIDED'
    );

CREATE INDEX idx_position_events_position_partial_exit_sequence
    ON position_events(position_id, sequence_no, event_id)
    WHERE caused_by IN ('partial_exit_fill', 'partial_exit_economics_repair');

CREATE INDEX idx_position_events_position_phase_after_sequence
    ON position_events(position_id, phase_after, sequence_no DESC);

CREATE INDEX idx_position_events_position_type_sequence
    ON position_events(position_id, event_type, sequence_no DESC);

CREATE INDEX idx_position_events_settled_env_position_sequence
    ON position_events(env, position_id, sequence_no DESC)
    WHERE event_type = 'SETTLED';

CREATE INDEX idx_position_current_city_date_metric
    ON position_current(city, target_date, temperature_metric);

CREATE INDEX idx_position_current_no_token_id ON position_current(no_token_id);

CREATE INDEX idx_position_current_phase_quote
    ON position_current(phase, chain_state, chain_shares, token_id, no_token_id);

CREATE INDEX idx_position_current_token_id ON position_current(token_id);

CREATE INDEX idx_venue_commands_decision ON venue_commands(decision_id);

CREATE INDEX idx_venue_commands_envelope ON venue_commands(envelope_id);

CREATE INDEX idx_venue_commands_position ON venue_commands(position_id);

CREATE INDEX idx_venue_commands_snapshot ON venue_commands(snapshot_id);

CREATE INDEX idx_venue_commands_state ON venue_commands(state);

CREATE INDEX idx_venue_commands_token_intent
    ON venue_commands(token_id, intent_kind, updated_at DESC, created_at DESC);

CREATE INDEX idx_venue_commands_venue_order_intent
    ON venue_commands(venue_order_id, intent_kind, updated_at DESC, created_at DESC);

CREATE INDEX idx_trade_facts_command ON venue_trade_facts (command_id, observed_at);

CREATE INDEX idx_trade_facts_trade ON venue_trade_facts (trade_id, observed_at);

CREATE INDEX idx_venue_fill_cash_facts_chain_tx_wallet_time
    ON venue_fill_cash_facts(chain_id, tx_hash, wallet, observed_at, id);

CREATE INDEX idx_payout_observations_active_lookup
    ON payout_observations(condition_id, outcome_index, superseded_by);

CREATE INDEX idx_payout_observations_condition
    ON payout_observations(condition_id, outcome_index, id);

CREATE INDEX idx_snapshots_condition_captured
          ON executable_market_snapshots (condition_id, captured_at DESC);

CREATE INDEX idx_snapshots_no_token_captured
          ON executable_market_snapshots (no_token_id, captured_at DESC);

CREATE INDEX idx_snapshots_selected_token_captured
          ON executable_market_snapshots (selected_outcome_token_id, captured_at DESC);

CREATE INDEX idx_snapshots_yes_token_captured
          ON executable_market_snapshots (yes_token_id, captured_at DESC);

CREATE TRIGGER trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;

CREATE TRIGGER trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;

CREATE TRIGGER trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN
    SELECT RAISE(FAIL, 'position_events.env is required');
END;

CREATE TRIGGER venue_trade_facts_no_delete
        BEFORE DELETE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

CREATE TRIGGER venue_trade_facts_no_update
        BEFORE UPDATE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

CREATE TRIGGER venue_fill_cash_facts_no_delete
BEFORE DELETE ON venue_fill_cash_facts
BEGIN
    SELECT RAISE(ABORT, 'venue_fill_cash_facts rows are append-only');
END;

CREATE TRIGGER venue_fill_cash_facts_no_update
BEFORE UPDATE ON venue_fill_cash_facts
BEGIN
    SELECT RAISE(ABORT, 'venue_fill_cash_facts rows are append-only');
END;

CREATE TRIGGER payout_observations_guarded_update
BEFORE UPDATE ON payout_observations
FOR EACH ROW
WHEN NOT (
    OLD.superseded_by IS NULL
    AND NEW.superseded_by IS NOT NULL
    AND NEW.condition_id IS OLD.condition_id
    AND NEW.outcome_index IS OLD.outcome_index
    AND NEW.payout_numerator IS OLD.payout_numerator
    AND NEW.payout_denominator IS OLD.payout_denominator
    AND NEW.state IS OLD.state
    AND NEW.block_number IS OLD.block_number
    AND NEW.block_hash IS OLD.block_hash
    AND NEW.observed_at = OLD.observed_at
    AND NEW.source = OLD.source
)
BEGIN
    SELECT RAISE(ABORT, 'payout_observations rows are immutable except a one-time superseded_by transition');
END;

CREATE TRIGGER payout_observations_no_delete
BEFORE DELETE ON payout_observations
BEGIN
    SELECT RAISE(ABORT, 'payout_observations is append-only (delete forbidden)');
END;

CREATE TRIGGER no_delete_executable_market_snapshots
        BEFORE DELETE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;

CREATE TRIGGER no_update_executable_market_snapshots
        BEFORE UPDATE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
