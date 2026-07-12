# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-2 + scripts/migrations/2026_07_quarantine_phase_retirement.py.
"""Shared test fixture helper: downgrade a freshly-built position_current /
position_events pair back to the PRE-T5-migration CHECK shape.

T5 (docs/rebuild/quarantine_excision_2026-07-11.md, scripts/migrations/
2026_07_quarantine_phase_retirement.py) tightened the position_current.phase
and position_events.phase_before/phase_after/event_type CHECK constraints to
drop the retired 'quarantined' / 'CHAIN_QUARANTINED' literals. A handful of
tests deliberately simulate a pre-migration legacy row still carrying one of
those literals (the exact mixed-epoch bridge scenario
src.state.portfolio._normalize_runtime_lifecycle_state /
_normalize_runtime_chain_state and src.state.canonical_write's phase_before
preservation exist to handle until the migration runs against a real DB).
Call this right after ``init_schema`` / ``init_schema_trade_only`` /
``apply_architecture_kernel_schema`` on a throwaway connection to rebuild
just those two tables with the pre-migration CHECK, so such tests keep
exercising the SAME bridge behavior against a legacy-shaped row.
"""

from __future__ import annotations

import sqlite3

_LEGACY_DDL = """
DROP TABLE IF EXISTS position_events;
DROP TABLE IF EXISTS position_current;
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    trade_id TEXT, market_id TEXT, city TEXT, cluster TEXT, target_date TEXT,
    bin_label TEXT,
    direction TEXT CHECK (direction IS NULL OR direction IN ('buy_yes','buy_no','unknown')),
    unit TEXT CHECK (unit IS NULL OR unit IN ('F','C')),
    size_usd REAL, shares REAL, cost_basis_usd REAL, entry_price REAL,
    p_posterior REAL, entry_ci_width REAL, last_monitor_prob REAL,
    last_monitor_prob_is_fresh INTEGER, last_monitor_edge REAL,
    last_monitor_market_price REAL, last_monitor_market_price_is_fresh INTEGER,
    last_monitor_best_bid REAL, last_monitor_best_ask REAL, last_monitor_market_vig REAL,
    decision_snapshot_id TEXT, entry_method TEXT, strategy_key TEXT NOT NULL,
    edge_source TEXT, discovery_mode TEXT, chain_state TEXT, token_id TEXT,
    no_token_id TEXT, condition_id TEXT, order_id TEXT, order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    fill_authority TEXT, recovery_authority TEXT, chain_shares REAL,
    chain_avg_price REAL, chain_cost_basis_usd REAL, chain_seen_at TEXT,
    chain_absence_at TEXT, realized_pnl_usd REAL, exit_price REAL,
    settlement_price REAL, settled_at TEXT, exit_reason TEXT,
    exit_retry_count INTEGER, next_exit_retry_at TEXT
);
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT','ENTRY_ORDER_POSTED','ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED','ENTRY_ORDER_REJECTED','DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED','CHAIN_SIZE_CORRECTED','CHAIN_QUARANTINED',
        'MONITOR_REFRESHED','EXIT_INTENT','EXIT_ORDER_POSTED','EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED','EXIT_ORDER_REJECTED','EXIT_RETRY_RELEASED','SETTLED',
        'ADMIN_VOIDED','MANUAL_OVERRIDE_APPLIED','VENUE_POSITION_OBSERVED','REVIEW_REQUIRED'
    )),
    occurred_at TEXT NOT NULL CHECK (occurred_at LIKE '____-__-__T%'),
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    strategy_key TEXT NOT NULL, decision_id TEXT, snapshot_id TEXT, order_id TEXT,
    command_id TEXT, caused_by TEXT, idempotency_key TEXT UNIQUE, venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest')),
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
);
CREATE TRIGGER trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN SELECT RAISE(FAIL, 'position_events.env is required'); END;
CREATE TRIGGER trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN SELECT RAISE(FAIL, 'position_events is append-only'); END;
CREATE TRIGGER trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN SELECT RAISE(FAIL, 'position_events is append-only'); END;
"""


def downgrade_position_current_to_legacy_quarantine_check(conn: sqlite3.Connection) -> None:
    """Rebuild position_current + position_events with the pre-T5-migration
    CHECK (still permitting 'quarantined' / 'CHAIN_QUARANTINED'). Call on a
    throwaway connection immediately after schema init, before any data is
    inserted into either table."""
    conn.executescript(_LEGACY_DDL)
