"""Zeus database schema and connection management.

All tables enforce the 4-timestamp constraint where applicable.
Settlement truth = Polymarket settlement result (spec §1.3).
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from src.config import STATE_DIR, state_path, settings
from src.state.ledger import (
    CANONICAL_POSITION_EVENT_COLUMNS,
    apply_architecture_kernel_schema,
    append_event_and_project,
    append_many_and_project,
)
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS


ZEUS_DB_PATH = STATE_DIR / "zeus.db"  # LEGACY — remove after Phase 4
ZEUS_SHARED_DB_PATH = STATE_DIR / "zeus-shared.db"  # Shared world data (settlements, calibration, ENS)
RISK_DB_PATH = state_path("risk_state.db")  # Per-process: paper vs live isolation


def _zeus_trade_db_path(mode: str | None = None) -> Path:
    """Physical path for mode-specific trade database.
    Paper and live trade data live in different files.
    Cross-mode reads are unconstructable — different files."""
    mode = mode or os.environ.get("ZEUS_MODE", settings.mode)
    return STATE_DIR / f"zeus-{mode}.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Low-level connection with standard pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_trade_connection(mode: str | None = None) -> sqlite3.Connection:
    """Mode-isolated trade DB. Paper gets zeus-paper.db, live gets zeus-live.db."""
    return _connect(_zeus_trade_db_path(mode))


def get_shared_connection() -> sqlite3.Connection:
    """Shared world data DB (settlements, calibration, ENS)."""
    return _connect(ZEUS_SHARED_DB_PATH)


def get_trade_connection_with_shared(mode: str | None = None) -> sqlite3.Connection:
    """Trade connection with shared DB ATTACHed for cross-DB joins."""
    conn = get_trade_connection(mode)
    conn.execute("ATTACH DATABASE ? AS shared", (str(ZEUS_SHARED_DB_PATH),))
    return conn


logger = logging.getLogger(__name__)
CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"
CANONICAL_POSITION_SETTLED_DETAIL_FIELDS = (
    "contract_version",
    "winning_bin",
    "position_bin",
    "won",
    "outcome",
    "p_posterior",
    "exit_price",
    "pnl",
    "exit_reason",
)
AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS = (
    "trade_id",
    "city",
    "target_date",
    "range_label",
    "direction",
    "p_posterior",
    "outcome",
    "pnl",
    "settled_at",
)
OPEN_EXPOSURE_PHASES = (
    "pending_entry",
    "active",
    "day0_window",
    "pending_exit",
)
TERMINAL_TRADE_DECISION_STATUSES = frozenset(
    {
        "exited",
        "settled",
        "voided",
        "admin_closed",
        "unresolved_ghost",
    }
)
PORTFOLIO_LOADER_PHASE_TO_RUNTIME_STATE = {
    "pending_entry": "pending_tracked",
    "active": "entered",
    "day0_window": "day0_window",
    "pending_exit": "pending_exit",
    "economically_closed": "economically_closed",
    "settled": "settled",
    "voided": "voided",
    "quarantined": "quarantined",
    "admin_closed": "admin_closed",
}
DEFAULT_CONTROL_OVERRIDE_PRECEDENCE = 100


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or ZEUS_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create all Zeus tables. Idempotent."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    conn.executescript("""
        -- Inherited from Rainstorm: settlement outcomes
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            UNIQUE(city, target_date)
        );

        -- Inherited: IEM ASOS, NOAA GHCND, Meteostat, WU PWS
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            station_id TEXT,
            fetched_at TEXT,
            UNIQUE(city, target_date, source)
        );

        -- Inherited: market structure and token IDs
        CREATE TABLE IF NOT EXISTS market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            outcome TEXT,
            created_at TEXT,
            UNIQUE(market_slug, condition_id)
        );

        -- Inherited: historical prices for baseline backtesting
        -- city/target_date/range_label carried over from Rainstorm for bin mapping
        CREATE TABLE IF NOT EXISTS token_price_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            price REAL NOT NULL,
            volume REAL,
            bid REAL,
            ask REAL,
            spread REAL,
            source_timestamp TEXT,
            timestamp TEXT NOT NULL
        );

        -- Zeus core: ENS snapshots with 4-timestamp constraint
        -- Spec §9.2: issue_time, valid_time, available_at, fetch_time
        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            issue_time TEXT NOT NULL,
            valid_time TEXT NOT NULL,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            lead_hours REAL NOT NULL,
            members_json TEXT NOT NULL,
            p_raw_json TEXT,
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT NOT NULL,
            data_version TEXT NOT NULL DEFAULT 'v1',
            UNIQUE(city, target_date, issue_time, data_version)
        );

        -- Calibration: raw → calibrated probability pairs
        CREATE TABLE IF NOT EXISTS calibration_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            range_label TEXT NOT NULL,
            p_raw REAL NOT NULL,
            outcome INTEGER NOT NULL,
            lead_days REAL NOT NULL,
            season TEXT NOT NULL,
            cluster TEXT NOT NULL,
            forecast_available_at TEXT NOT NULL,
            settlement_value REAL
        );

        -- Platt model parameters per bucket
        CREATE TABLE IF NOT EXISTS platt_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_key TEXT NOT NULL UNIQUE,
            param_A REAL NOT NULL,
            param_B REAL NOT NULL,
            param_C REAL NOT NULL DEFAULT 0.0,
            bootstrap_params_json TEXT NOT NULL,
            n_samples INTEGER NOT NULL,
            brier_insample REAL,
            fitted_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            input_space TEXT NOT NULL DEFAULT 'raw_probability'
        );

        -- Trade decisions with full audit trail
        CREATE TABLE IF NOT EXISTS trade_decisions (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            bin_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            size_usd REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id),
            calibration_model_version TEXT,
            p_raw REAL NOT NULL,
            p_calibrated REAL,
            p_posterior REAL NOT NULL,
            edge REAL NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            kelly_fraction REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filled_at TEXT,
            fill_price REAL,
            runtime_trade_id TEXT,
            order_id TEXT,
            order_status_text TEXT,
            order_posted_at TEXT,
            entered_at_ts TEXT,
            chain_state TEXT,
            -- Attribution fields (CLAUDE.md: mandatory on every trade)
            strategy TEXT,
            edge_source TEXT,
            bin_type TEXT,
            discovery_mode TEXT,
            market_hours_open REAL,
            fill_quality REAL,
            entry_method TEXT,
            selected_method TEXT,
            applied_validations_json TEXT,
            exit_trigger TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            exit_divergence_score REAL DEFAULT 0.0,
            exit_market_velocity_1h REAL DEFAULT 0.0,
            exit_forward_edge REAL DEFAULT 0.0,
            -- Phase 2 Domain Object Snapshots (JSON flattened blobs)
            settlement_semantics_json TEXT,
            epistemic_context_json TEXT,
            edge_context_json TEXT,
            -- Phase 3: Shadow Proof True Attribution
            entry_alpha_usd REAL DEFAULT 0.0,
            execution_slippage_usd REAL DEFAULT 0.0,
            exit_timing_usd REAL DEFAULT 0.0,
            risk_throttling_usd REAL DEFAULT 0.0,
            settlement_edge_usd REAL DEFAULT 0.0
        );

        -- Shadow signals for pre-trading validation
        CREATE TABLE IF NOT EXISTS shadow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            decision_snapshot_id TEXT,
            p_raw_json TEXT NOT NULL,
            p_cal_json TEXT,
            edges_json TEXT,
            lead_hours REAL NOT NULL
        );

        -- Append-only trade chronicle
        CREATE TABLE IF NOT EXISTS chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL
        );

        -- Durable stage-level runtime spine for position lifecycle events
        CREATE TABLE IF NOT EXISTS position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            runtime_trade_id TEXT NOT NULL,
            position_state TEXT,
            order_id TEXT,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            market_id TEXT,
            bin_label TEXT,
            direction TEXT,
            strategy TEXT,
            edge_source TEXT,
            source TEXT NOT NULL DEFAULT 'runtime',
            details_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            env TEXT NOT NULL DEFAULT 'paper'
        );

        -- Derived health view for PnL and edge compression
        CREATE TABLE IF NOT EXISTS strategy_health (
            strategy_key TEXT NOT NULL CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
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

        -- Decision chain: every cycle's artifacts (Blueprint v2 §3)
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(timestamp);

        -- ETL tables: Rainstorm data validated and imported

        -- Ladder backfill: 5 models × 7 leads per settlement
        CREATE TABLE IF NOT EXISTS forecast_skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            lead_days INTEGER NOT NULL,
            forecast_temp REAL NOT NULL,
            actual_temp REAL NOT NULL,
            error REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            season TEXT NOT NULL,
            available_at TEXT NOT NULL,
            UNIQUE(city, target_date, source, lead_days)
        );

        -- Per-model bias correction
        CREATE TABLE IF NOT EXISTS model_bias (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            source TEXT NOT NULL,
            bias REAL NOT NULL,
            mae REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            discount_factor REAL DEFAULT 0.7,
            UNIQUE(city, season, source)
        );

        -- Token price history with market timing
        CREATE TABLE IF NOT EXISTS market_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            token_id TEXT NOT NULL,
            price REAL NOT NULL,
            recorded_at TEXT NOT NULL,
            hours_since_open REAL,
            hours_to_resolution REAL,
            UNIQUE(token_id, recorded_at)
        );

        -- DST-safe hourly observation timeline
        CREATE TABLE IF NOT EXISTS observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone_name TEXT NOT NULL,
            local_hour REAL,
            local_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
            time_basis TEXT NOT NULL,
            temp_current REAL,
            running_max REAL,
            delta_rate_per_h REAL,
            temp_unit TEXT NOT NULL,
            station_id TEXT,
            observation_count INTEGER,
            raw_response TEXT,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            UNIQUE(city, source, utc_timestamp)
        );

        -- Legacy compatibility table derived from observation_instants.
        -- New time-sensitive logic must prefer observation_instants/diurnal tables.
        CREATE TABLE IF NOT EXISTS hourly_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            obs_date TEXT NOT NULL,
            obs_hour INTEGER NOT NULL,
            temp REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(city, obs_date, obs_hour, source)
        );

        -- Daily sunrise/sunset context for Day0 and DST-aware timing
        CREATE TABLE IF NOT EXISTS solar_daily (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            sunrise_local TEXT NOT NULL,
            sunset_local TEXT NOT NULL,
            sunrise_utc TEXT NOT NULL,
            sunset_utc TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL,
            UNIQUE(city, target_date)
        );

        -- Diurnal temperature curves per city×season
        CREATE TABLE IF NOT EXISTS diurnal_curves (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            hour INTEGER NOT NULL,
            avg_temp REAL NOT NULL,
            std_temp REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            p_high_set REAL,
            UNIQUE(city, season, hour)
        );

        CREATE TABLE IF NOT EXISTS diurnal_peak_prob (
            city TEXT NOT NULL,
            month INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            p_high_set REAL NOT NULL,
            n_obs INTEGER NOT NULL,
            UNIQUE(city, month, hour)
        );

        -- Historical forecast values (5 NWP models)
        CREATE TABLE IF NOT EXISTS historical_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_high REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            lead_days INTEGER,
            available_at TEXT,
            UNIQUE(city, target_date, source, lead_days)
        );

        -- Model skill summary per city×season
        CREATE TABLE IF NOT EXISTS model_skill (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            source TEXT NOT NULL,
            mae REAL NOT NULL,
            bias REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season, source)
        );

        -- Day-over-day temperature persistence
        CREATE TABLE IF NOT EXISTS temp_persistence (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            delta_bucket TEXT NOT NULL,
            frequency REAL NOT NULL,
            avg_next_day_reversion REAL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season, delta_bucket)
        );

        -- Create indexes for common query patterns
        CREATE INDEX IF NOT EXISTS idx_settlements_city_date
            ON settlements(city, target_date);
        CREATE INDEX IF NOT EXISTS idx_observations_city_date
            ON observations(city, target_date, source);
        CREATE INDEX IF NOT EXISTS idx_observation_instants_city_date
            ON observation_instants(city, target_date, utc_timestamp);
        CREATE INDEX IF NOT EXISTS idx_observation_instants_source
            ON observation_instants(source, city, target_date);
        CREATE INDEX IF NOT EXISTS idx_token_price_token
            ON token_price_log(token_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_market_events_slug
            ON market_events(market_slug);
        CREATE INDEX IF NOT EXISTS idx_ensemble_city_date
            ON ensemble_snapshots(city, target_date, available_at);
        CREATE INDEX IF NOT EXISTS idx_calibration_bucket
            ON calibration_pairs(cluster, season);

        -- Replay engine results
        CREATE TABLE IF NOT EXISTS replay_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            settlement_value REAL,
            winning_bin TEXT,
            replay_direction TEXT,
            replay_edge REAL,
            replay_p_posterior REAL,
            replay_size_usd REAL,
            replay_should_trade INTEGER,
            replay_rejection_stage TEXT,
            actual_direction TEXT,
            actual_edge REAL,
            actual_should_trade INTEGER,
            replay_pnl REAL,
            actual_pnl REAL,
            overrides_json TEXT,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_replay_run
            ON replay_results(replay_run_id);
    """)
    
    # Safe Schema evolution for phase 3 attribution
    for col in ["entry_alpha_usd", "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd", "settlement_edge_usd"]:
        try:
            conn.execute(f"ALTER TABLE trade_decisions ADD COLUMN {col} REAL DEFAULT 0.0;")
        except sqlite3.OperationalError:
            pass

    try:
        conn.execute("ALTER TABLE platt_models ADD COLUMN input_space TEXT NOT NULL DEFAULT 'raw_probability';")
    except sqlite3.OperationalError:
        pass

    # Provenance: env column on trade-facing tables (Decision 2)
    # Existing rows default to 'paper' — all historical data is from paper trading.
    _env_tables = ["trade_decisions", "chronicle", "decision_log", "position_events"]
    for table in _env_tables:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN env TEXT NOT NULL DEFAULT 'paper';")
        except sqlite3.OperationalError:
            pass  # Column already exists
            
    try:
        conn.execute("ALTER TABLE trade_decisions ADD COLUMN edge_source TEXT;")
    except sqlite3.OperationalError:
        pass

    # Backfill missing trade_decisions attribution / snapshot columns on older DBs.
    for ddl in [
        "ALTER TABLE trade_decisions ADD COLUMN runtime_trade_id TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_id TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_status_text TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_posted_at TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN entered_at_ts TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN chain_state TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN bin_type TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN discovery_mode TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN market_hours_open REAL;",
        "ALTER TABLE trade_decisions ADD COLUMN fill_quality REAL;",
        "ALTER TABLE trade_decisions ADD COLUMN strategy TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN entry_method TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN selected_method TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN applied_validations_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_trigger TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_reason TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN admin_exit_reason TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_divergence_score REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_market_velocity_1h REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_forward_edge REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN settlement_semantics_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN epistemic_context_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN edge_context_json TEXT;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    try:
        conn.execute("ALTER TABLE shadow_signals ADD COLUMN decision_snapshot_id TEXT;")
    except sqlite3.OperationalError:
        pass

    _ensure_runtime_bootstrap_support_tables(conn)

    if own_conn:
        conn.commit()
        conn.close()


def _ensure_runtime_bootstrap_support_tables(conn: sqlite3.Connection) -> None:
    """Ensure legacy runtime bootstrap can coexist with canonical support tables."""
    event_columns = _table_columns(conn, "position_events")
    legacy_present = bool(event_columns) and set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(event_columns)
    canonical_present = bool(event_columns) and set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(event_columns)

    if legacy_present and not canonical_present:
        if not _table_exists(conn, "position_events_legacy"):
            conn.execute("ALTER TABLE position_events RENAME TO position_events_legacy")
        else:
            raise RuntimeError(
                "legacy position_events collision unresolved: both legacy and alternate legacy tables exist"
            )
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_position_events_legacy_trade_ts ON position_events_legacy(runtime_trade_id, timestamp)"
            )
        except sqlite3.OperationalError:
            pass

    apply_architecture_kernel_schema(conn)

LEGACY_RUNTIME_POSITION_EVENT_COLUMNS = (
    "runtime_trade_id",
    "position_state",
    "strategy",
    "source",
    "details_json",
    "timestamp",
    "env",
)


def _legacy_position_events_table(conn: sqlite3.Connection) -> str:
    for table in ("position_events_legacy", "position_events"):
        columns = _table_columns(conn, table)
        if columns and set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(columns):
            return table
    return ""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _canonical_position_surface_available(conn: sqlite3.Connection) -> bool:
    event_columns = _table_columns(conn, "position_events")
    current_columns = _table_columns(conn, "position_current")
    return (
        bool(event_columns)
        and bool(current_columns)
        and set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(event_columns)
        and set(CANONICAL_POSITION_CURRENT_COLUMNS).issubset(current_columns)
    )


def _missing_canonical_position_tables(conn: sqlite3.Connection) -> list[str]:
    missing: list[str] = []
    if not _table_exists(conn, "position_events"):
        missing.append("position_events")
    if not _table_exists(conn, "position_current"):
        missing.append("position_current")
    return missing


def _legacy_runtime_position_event_schema_available(conn: sqlite3.Connection) -> bool:
    legacy_table = _legacy_position_events_table(conn)
    event_columns = _table_columns(conn, legacy_table) if legacy_table else set()
    return (
        bool(event_columns)
        and set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(event_columns)
    )


def _assert_legacy_runtime_position_event_schema(conn: sqlite3.Connection) -> None:
    legacy_table = _legacy_position_events_table(conn)
    event_columns = _table_columns(conn, legacy_table) if legacy_table else set()
    if not event_columns:
        raise RuntimeError("legacy runtime position_events schema not installed")
    if not set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(event_columns):
        raise RuntimeError("legacy runtime position_events schema not installed")


def backfill_open_legacy_paper_positions(
    conn: sqlite3.Connection | None,
    positions: Iterable[object],
    *,
    source_module: str = "scripts.backfill_open_positions_canonical",
) -> dict:
    positions = list(positions)
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "seeded_trade_ids": [],
            "seeded_count": 0,
        }
    if not _canonical_position_surface_available(conn):
        return {
            "status": "skipped_missing_canonical_tables",
            "missing_tables": _missing_canonical_position_tables(conn),
            "seeded_trade_ids": [],
            "seeded_count": 0,
        }

    from src.engine.lifecycle_events import build_entry_canonical_write, canonical_phase_for_position

    seeded_trade_ids: list[str] = []
    skipped_existing_trade_ids: list[str] = []
    skipped_non_open_trade_ids: list[str] = []
    skipped_non_paper_trade_ids: list[str] = []

    for position in positions:
        trade_id = str(getattr(position, "trade_id", "") or "").strip()
        if not trade_id:
            raise ValueError("cannot backfill canonical open position without trade_id")

        env = str(getattr(position, "env", "") or "paper").strip()
        if env != "paper":
            skipped_non_paper_trade_ids.append(trade_id)
            continue

        canonical_phase = canonical_phase_for_position(position)
        if canonical_phase not in OPEN_EXPOSURE_PHASES:
            skipped_non_open_trade_ids.append(trade_id)
            continue

        event_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
                (trade_id,),
            ).fetchone()[0]
        )
        projection_row = conn.execute(
            """
            SELECT 1
            FROM position_current
            WHERE position_id = ? OR trade_id = ?
            LIMIT 1
            """,
            (trade_id, trade_id),
        ).fetchone()
        projection_exists = projection_row is not None
        if bool(event_count) != projection_exists:
            raise RuntimeError(
                f"partial canonical state blocks open-position backfill for {trade_id}"
            )
        if event_count and projection_exists:
            skipped_existing_trade_ids.append(trade_id)
            continue

        events, projection = build_entry_canonical_write(
            position,
            decision_id=None,
            source_module=source_module,
        )
        append_many_and_project(conn, events, projection)
        seeded_trade_ids.append(trade_id)

    status = "seeded" if seeded_trade_ids else "seeded_empty"
    return {
        "status": status,
        "candidate_count": len(positions),
        "seeded_trade_ids": seeded_trade_ids,
        "seeded_count": len(seeded_trade_ids),
        "skipped_existing_trade_ids": skipped_existing_trade_ids,
        "skipped_existing_count": len(skipped_existing_trade_ids),
        "skipped_non_open_trade_ids": skipped_non_open_trade_ids,
        "skipped_non_open_count": len(skipped_non_open_trade_ids),
        "skipped_non_paper_trade_ids": skipped_non_paper_trade_ids,
        "skipped_non_paper_count": len(skipped_non_paper_trade_ids),
    }

def record_shadow_attribution_trade(
    conn: sqlite3.Connection,
    trade_id: str,
    market_id: str,
    bin_label: str,
    direction: str,
    size_usd: float,
    price: float,
    p_raw: float,
    p_posterior: float,
    edge: float,
    edge_source: str,
    timestamp: str,
    settlement_json: str = "",
    epistemic_json: str = "",
    edge_context_json: str = "",
    # New Phase 3 Variables passed when completing loops
    intended_size_usd: float = 0.0,
    filled_price: float = 0.0,
    settlement_prob: float = 0.0,
    final_pnl_usd: float = 0.0,
    is_early_exit: bool = False
) -> None:
    """Phase 3 Shadow Attribution: Persist truly split advantage metrics."""
    
    # Mathematical Splitting calculations
    # 1. execution_slippage: intended vs filled price
    slippage_usd = 0.0
    if filled_price > 0 and price > 0:
        slippage_usd = (size_usd / price) * filled_price - size_usd
        
    # 2. entry_alpha: actual theoretical expected jump vs market immediately
    entry_alpha_usd = size_usd * edge
    
    # 3. exit_timing: did we secure value or get stopped false?
    exit_timing_usd = final_pnl_usd if is_early_exit else 0.0
    
    # 4. risk_throttling: capital shielded from saturation
    throttling_usd = (intended_size_usd - size_usd) * edge
    
    # 5. settlement_edge: the pure outcome movement
    settlement_edge_usd = final_pnl_usd if not is_early_exit else 0.0

    conn.execute("""
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp, 
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, 
            status, edge_source, 
            settlement_semantics_json, epistemic_context_json, edge_context_json,
            entry_alpha_usd, execution_slippage_usd, exit_timing_usd, risk_throttling_usd, settlement_edge_usd
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        market_id, bin_label, direction, size_usd, price, timestamp,
        p_raw, p_posterior, edge, 0.0, 0.0, 0.0,
        "filled", edge_source,
        settlement_json, epistemic_json, edge_context_json,
        entry_alpha_usd, slippage_usd, exit_timing_usd, throttling_usd, settlement_edge_usd
    ))



def log_microstructure(conn, token_id: str, city: str, target_date: str, range_label: str,
                       price: float, volume: float, bid: float, ask: float, spread: float, source_timestamp: str):
    """Log microstructure snapshot (Spec injection point 7)."""
    try:
        conn.execute("""
            INSERT INTO token_price_log
            (token_id, city, target_date, range_label, price, volume, bid, ask, spread, source_timestamp, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
        """, (token_id, city, target_date, range_label, price, volume, bid, ask, spread, source_timestamp))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log microstructure: %s', e)


def log_shadow_signal(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    timestamp: str,
    decision_snapshot_id: str,
    p_raw_json: str,
    p_cal_json: str,
    edges_json: str,
    lead_hours: float,
) -> None:
    try:
        conn.execute(
            """
            INSERT INTO shadow_signals
            (city, target_date, timestamp, decision_snapshot_id, p_raw_json, p_cal_json, edges_json, lead_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (city, target_date, timestamp, decision_snapshot_id, p_raw_json, p_cal_json, edges_json, lead_hours),
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to log shadow signal: %s", e)


def _bin_type_for_label(label: str) -> str:
    lower = (label or "").lower()
    if "or below" in lower:
        return "shoulder_low"
    if "or higher" in lower or "or above" in lower:
        return "shoulder_high"
    return "center"


def _coerce_snapshot_fk(value) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_opportunity_availability_status(value: str) -> str:
    status = str(value or "").strip().upper()
    if not status:
        return "ok"
    mapping = {
        "OK": "ok",
        "MISSING": "missing",
        "DATA_MISSING": "missing",
        "DATA_STALE": "stale",
        "STALE": "stale",
        "RATE_LIMITED": "rate_limited",
        "UNAVAILABLE": "unavailable",
        "DATA_UNAVAILABLE": "unavailable",
        "CHAIN_UNAVAILABLE": "chain_unavailable",
    }
    return mapping.get(status, "unavailable")


def _candidate_city_name(candidate) -> str:
    city = getattr(candidate, "city", "")
    return str(getattr(city, "name", city) or "")


def _opportunity_fact_candidate_id(candidate) -> str:
    event_id = str(getattr(candidate, "event_id", "") or "").strip()
    if event_id:
        return event_id
    slug = str(getattr(candidate, "slug", "") or "").strip()
    if slug:
        return slug
    city_name = _candidate_city_name(candidate)
    target_date = str(getattr(candidate, "target_date", "") or "").strip()
    if city_name and target_date:
        return f"{city_name}:{target_date}"
    return ""


def _decision_vector_value(decision, attr_name: str) -> float | None:
    edge = getattr(decision, "edge", None)
    vector = getattr(decision, attr_name, None)
    if edge is None or vector is None:
        return None
    try:
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
    except TypeError:
        return None
    label = str(getattr(getattr(edge, "bin", None), "label", "") or "")
    bin_labels = []
    try:
        bin_labels = list(getattr(decision, "bin_labels", []) or [])
    except TypeError:
        bin_labels = []
    if not label or not bin_labels:
        return None
    try:
        idx = bin_labels.index(label)
    except ValueError:
        return None
    if idx >= len(values):
        return None
    try:
        probability = float(values[idx])
    except (TypeError, ValueError):
        return None
    if getattr(edge, "direction", "") == "buy_no":
        probability = 1.0 - probability
    return probability


def log_opportunity_fact(
    conn: sqlite3.Connection | None,
    *,
    candidate,
    decision,
    should_trade: bool,
    rejection_stage: str,
    rejection_reasons: list[str] | None,
    recorded_at: str,
) -> dict:
    if conn is None:
        logger.info("Opportunity fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "opportunity_fact"}
    if not _table_exists(conn, "opportunity_fact"):
        logger.info("Opportunity fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "opportunity_fact"}

    edge = getattr(decision, "edge", None)
    direction = str(getattr(edge, "direction", "") or "unknown")
    if direction not in {"buy_yes", "buy_no", "unknown"}:
        direction = "unknown"
    range_label = str(getattr(getattr(edge, "bin", None), "label", "") or "")
    strategy_key = str(getattr(decision, "strategy_key", "") or "").strip() or None
    snapshot_id = str(getattr(decision, "decision_snapshot_id", "") or "").strip() or None
    p_raw = _decision_vector_value(decision, "p_raw")
    p_cal = _decision_vector_value(decision, "p_cal")
    p_market = _decision_vector_value(decision, "p_market")
    if p_cal is None and edge is not None:
        try:
            p_cal = float(getattr(edge, "p_model", None))
        except (TypeError, ValueError):
            p_cal = None
    if p_market is None and edge is not None:
        try:
            p_market = float(getattr(edge, "p_market", None))
        except (TypeError, ValueError):
            p_market = None
    best_edge = None
    ci_width = None
    alpha = getattr(decision, "alpha", None)
    if edge is not None:
        try:
            best_edge = float(getattr(edge, "edge", None))
        except (TypeError, ValueError):
            best_edge = None
        try:
            ci_width = max(0.0, float(edge.ci_upper) - float(edge.ci_lower))
        except (TypeError, ValueError, AttributeError):
            ci_width = None
    try:
        alpha = float(alpha) if alpha not in (None, "") else None
    except (TypeError, ValueError):
        alpha = None
    rejection_reason_json = None
    if rejection_reasons:
        rejection_reason_json = json.dumps(list(rejection_reasons), ensure_ascii=False)

    conn.execute(
        """
        INSERT OR REPLACE INTO opportunity_fact (
            decision_id,
            candidate_id,
            city,
            target_date,
            range_label,
            direction,
            strategy_key,
            discovery_mode,
            entry_method,
            snapshot_id,
            p_raw,
            p_cal,
            p_market,
            alpha,
            best_edge,
            ci_width,
            rejection_stage,
            rejection_reason_json,
            availability_status,
            should_trade,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(getattr(decision, "decision_id", "") or ""),
            _opportunity_fact_candidate_id(candidate) or None,
            _candidate_city_name(candidate) or None,
            str(getattr(candidate, "target_date", "") or "") or None,
            range_label or None,
            direction,
            strategy_key,
            str(getattr(candidate, "discovery_mode", "") or "") or None,
            str(
                getattr(decision, "selected_method", "")
                or getattr(decision, "entry_method", "")
                or ""
            )
            or None,
            snapshot_id,
            p_raw,
            p_cal,
            p_market,
            alpha,
            best_edge,
            ci_width,
            str(rejection_stage or "") or None,
            rejection_reason_json,
            _normalize_opportunity_availability_status(getattr(decision, "availability_status", "")),
            int(bool(should_trade)),
            recorded_at,
        ),
    )
    return {"status": "written", "table": "opportunity_fact"}


def log_availability_fact(
    conn: sqlite3.Connection | None,
    *,
    availability_id: str,
    scope_type: str,
    scope_key: str,
    failure_type: str,
    started_at: str,
    impact: str,
    details: dict | None = None,
    ended_at: str | None = None,
) -> dict:
    if conn is None:
        logger.info("Availability fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "availability_fact"}
    if not _table_exists(conn, "availability_fact"):
        logger.info("Availability fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "availability_fact"}

    normalized_scope_type = scope_type if scope_type in {"cycle", "candidate", "city_target", "order", "chain"} else "candidate"
    normalized_impact = impact if impact in {"skip", "degrade", "retry", "block"} else "skip"
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT OR REPLACE INTO availability_fact (
            availability_id,
            scope_type,
            scope_key,
            failure_type,
            started_at,
            ended_at,
            impact,
            details_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            availability_id,
            normalized_scope_type,
            scope_key,
            failure_type,
            started_at,
            ended_at,
            normalized_impact,
            payload,
        ),
    )
    return {"status": "written", "table": "availability_fact"}


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _execution_intent_id(*, trade_id: str, order_role: str, explicit_intent_id: str | None = None) -> str:
    if explicit_intent_id:
        return explicit_intent_id
    return f"{trade_id}:{order_role}"


def log_execution_fact(
    conn: sqlite3.Connection | None,
    *,
    intent_id: str,
    position_id: str,
    order_role: str,
    decision_id: str | None = None,
    strategy_key: str | None = None,
    posted_at: str | None = None,
    filled_at: str | None = None,
    voided_at: str | None = None,
    submitted_price: float | None = None,
    fill_price: float | None = None,
    shares: float | None = None,
    fill_quality: float | None = None,
    latency_seconds: float | None = None,
    venue_status: str | None = None,
    terminal_exec_status: str | None = None,
) -> dict:
    if conn is None:
        logger.info("Execution fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "execution_fact"}
    if not _table_exists(conn, "execution_fact"):
        logger.info("Execution fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "execution_fact"}

    if order_role not in {"entry", "exit"}:
        raise ValueError(f"execution_fact order_role must be entry/exit, got {order_role!r}")

    current = conn.execute(
        """
        SELECT posted_at, filled_at, voided_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status, decision_id, strategy_key
        FROM execution_fact
        WHERE intent_id = ?
        """,
        (intent_id,),
    ).fetchone()

    stored_posted_at = posted_at or (current["posted_at"] if current else None)
    stored_filled_at = filled_at or (current["filled_at"] if current else None)
    stored_voided_at = voided_at or (current["voided_at"] if current else None)
    stored_submitted_price = submitted_price if submitted_price is not None else (current["submitted_price"] if current else None)
    stored_fill_price = fill_price if fill_price is not None else (current["fill_price"] if current else None)
    stored_shares = shares if shares is not None else (current["shares"] if current else None)
    stored_fill_quality = fill_quality if fill_quality is not None else (current["fill_quality"] if current else None)
    stored_venue_status = venue_status if venue_status not in (None, "") else (current["venue_status"] if current else None)
    stored_terminal_status = terminal_exec_status if terminal_exec_status not in (None, "") else (current["terminal_exec_status"] if current else None)
    stored_decision_id = decision_id if decision_id not in (None, "") else (current["decision_id"] if current else None)
    stored_strategy_key = strategy_key if strategy_key not in (None, "") else (current["strategy_key"] if current else None)

    if latency_seconds is None and stored_posted_at and stored_filled_at:
        posted_dt = _parse_iso_timestamp(stored_posted_at)
        filled_dt = _parse_iso_timestamp(stored_filled_at)
        if posted_dt is not None and filled_dt is not None:
            latency_seconds = max(0.0, (filled_dt - posted_dt).total_seconds())
    stored_latency_seconds = latency_seconds if latency_seconds is not None else (current["latency_seconds"] if current else None)

    conn.execute(
        """
        INSERT OR REPLACE INTO execution_fact (
            intent_id,
            position_id,
            decision_id,
            order_role,
            strategy_key,
            posted_at,
            filled_at,
            voided_at,
            submitted_price,
            fill_price,
            shares,
            fill_quality,
            latency_seconds,
            venue_status,
            terminal_exec_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            position_id,
            stored_decision_id,
            order_role,
            stored_strategy_key,
            stored_posted_at,
            stored_filled_at,
            stored_voided_at,
            stored_submitted_price,
            stored_fill_price,
            stored_shares,
            stored_fill_quality,
            stored_latency_seconds,
            stored_venue_status,
            stored_terminal_status,
        ),
    )
    return {"status": "written", "table": "execution_fact"}


def _hours_between(started_at: str | None, ended_at: str | None) -> float | None:
    start_dt = _parse_iso_timestamp(started_at)
    end_dt = _parse_iso_timestamp(ended_at)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)


def log_outcome_fact(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
    strategy_key: str | None = None,
    entered_at: str | None = None,
    exited_at: str | None = None,
    settled_at: str | None = None,
    exit_reason: str | None = None,
    admin_exit_reason: str | None = None,
    decision_snapshot_id: str | None = None,
    pnl: float | None = None,
    outcome: int | None = None,
    hold_duration_hours: float | None = None,
    monitor_count: int | None = None,
    chain_corrections_count: int | None = None,
) -> dict:
    if conn is None:
        logger.info("Outcome fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "outcome_fact"}
    if not _table_exists(conn, "outcome_fact"):
        logger.info("Outcome fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "outcome_fact"}

    current = conn.execute(
        """
        SELECT entered_at, exited_at, settled_at, exit_reason, admin_exit_reason, decision_snapshot_id,
               pnl, outcome, hold_duration_hours, monitor_count, chain_corrections_count, strategy_key
        FROM outcome_fact
        WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()

    stored_entered_at = entered_at if entered_at not in (None, "") else (current["entered_at"] if current else None)
    stored_exited_at = exited_at if exited_at not in (None, "") else (current["exited_at"] if current else None)
    stored_settled_at = settled_at if settled_at not in (None, "") else (current["settled_at"] if current else None)
    stored_exit_reason = exit_reason if exit_reason not in (None, "") else (current["exit_reason"] if current else None)
    stored_admin_exit_reason = admin_exit_reason if admin_exit_reason not in (None, "") else (current["admin_exit_reason"] if current else None)
    stored_snapshot = decision_snapshot_id if decision_snapshot_id not in (None, "") else (current["decision_snapshot_id"] if current else None)
    stored_pnl = pnl if pnl is not None else (current["pnl"] if current else None)
    stored_outcome = outcome if outcome is not None else (current["outcome"] if current else None)
    stored_monitor_count = monitor_count if monitor_count is not None else (current["monitor_count"] if current else 0)
    stored_chain_corrections = chain_corrections_count if chain_corrections_count is not None else (current["chain_corrections_count"] if current else 0)
    stored_strategy_key = strategy_key if strategy_key not in (None, "") else (current["strategy_key"] if current else None)

    if hold_duration_hours is None:
        hold_duration_hours = _hours_between(
            stored_entered_at,
            stored_exited_at or stored_settled_at,
        )
    stored_hold_hours = hold_duration_hours if hold_duration_hours is not None else (current["hold_duration_hours"] if current else None)

    conn.execute(
        """
        INSERT OR REPLACE INTO outcome_fact (
            position_id,
            strategy_key,
            entered_at,
            exited_at,
            settled_at,
            exit_reason,
            admin_exit_reason,
            decision_snapshot_id,
            pnl,
            outcome,
            hold_duration_hours,
            monitor_count,
            chain_corrections_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id,
            stored_strategy_key,
            stored_entered_at,
            stored_exited_at,
            stored_settled_at,
            stored_exit_reason,
            stored_admin_exit_reason,
            stored_snapshot,
            stored_pnl,
            stored_outcome,
            stored_hold_hours,
            stored_monitor_count,
            stored_chain_corrections,
        ),
    )
    return {"status": "written", "table": "outcome_fact"}

def log_trade_entry(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Log explicitly at entry for replay reconstruction."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    env = getattr(pos, "env", None) or settings.mode
    status = "pending_tracked" if getattr(pos, "state", "") == "pending_tracked" else "entered"
    timestamp = getattr(pos, "order_posted_at", "") if status == "pending_tracked" else getattr(pos, "entered_at", "")
    filled_at = getattr(pos, "entered_at", None) if status == "entered" else None
    fill_price = getattr(pos, "entry_price", None) if status == "entered" else None
    if _table_exists(conn, "trade_decisions"):
        try:
            values = (
                pos.market_id,
                pos.bin_label,
                pos.direction,
                pos.size_usd,
                pos.entry_price,
                timestamp,
                _coerce_snapshot_fk(getattr(pos, "decision_snapshot_id", None)),
                getattr(pos, "calibration_version", "") or None,
                pos.p_posterior,
                pos.p_posterior,
                pos.edge,
                pos.p_posterior - (pos.entry_ci_width / 2) if pos.entry_ci_width else 0.0,
                pos.p_posterior + (pos.entry_ci_width / 2) if pos.entry_ci_width else 0.0,
                0.0,
                status,
                filled_at,
                fill_price,
                getattr(pos, "trade_id", ""),
                getattr(pos, "order_id", ""),
                getattr(pos, "order_status", ""),
                getattr(pos, "order_posted_at", ""),
                getattr(pos, "entered_at", ""),
                getattr(pos, "chain_state", ""),
                getattr(pos, "strategy", ""),
                pos.edge_source,
                _bin_type_for_label(pos.bin_label),
                env,
                getattr(pos, "discovery_mode", ""),
                getattr(pos, "market_hours_open", 0.0),
                getattr(pos, "fill_quality", 0.0),
                getattr(pos, "entry_method", ""),
                getattr(pos, "selected_method", ""),
                json.dumps(getattr(pos, "applied_validations", []) or []),
                getattr(pos, "settlement_semantics_json", None),
                getattr(pos, "epistemic_context_json", None),
                getattr(pos, "edge_context_json", None),
            )
            placeholders = ", ".join(["?"] * len(values))
            conn.execute(f"""
                INSERT INTO trade_decisions (
                    market_id, bin_label, direction, size_usd, price, timestamp,
                    forecast_snapshot_id, calibration_model_version,
                    p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                    status, filled_at, fill_price, runtime_trade_id, order_id, order_status_text, order_posted_at, entered_at_ts, chain_state,
                    strategy, edge_source, bin_type, env, discovery_mode, market_hours_open,
                    fill_quality, entry_method, selected_method, applied_validations_json,
                    settlement_semantics_json, epistemic_context_json, edge_context_json
                )
                VALUES ({placeholders})
            """, values)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('Failed to log trade entry: %s', e)

    if _legacy_runtime_position_event_schema_available(conn):
        log_position_event(
            conn,
            "POSITION_ENTRY_RECORDED",
            pos,
            details={
                "status": status,
                "fill_price": fill_price,
                "submitted_price": getattr(pos, "entry_price", None),
                "shares": getattr(pos, "shares", 0.0),
                "chain_state": getattr(pos, "chain_state", ""),
                "entry_method": getattr(pos, "entry_method", ""),
                "selected_method": getattr(pos, "selected_method", ""),
            },
            timestamp=timestamp or None,
            source="trade_decisions",
        )
    elif not _canonical_position_surface_available(conn):
        log_position_event(
            conn,
            "POSITION_ENTRY_RECORDED",
            pos,
            details={
                "status": status,
                "fill_price": fill_price,
                "submitted_price": getattr(pos, "entry_price", None),
                "shares": getattr(pos, "shares", 0.0),
                "chain_state": getattr(pos, "chain_state", ""),
                "entry_method": getattr(pos, "entry_method", ""),
                "selected_method": getattr(pos, "selected_method", ""),
            },
            timestamp=timestamp or None,
            source="trade_decisions",
        )


def log_execution_report(conn: sqlite3.Connection, pos, result, *, decision_id: str | None = None) -> None:
    """Append an execution telemetry event tied to the runtime trade."""
    legacy_runtime_events_available = _legacy_runtime_position_event_schema_available(conn)
    if not legacy_runtime_events_available:
        if not _canonical_position_surface_available(conn):
            _assert_legacy_runtime_position_event_schema(conn)
    if not getattr(pos, "trade_id", ""):
        return
    submitted_price = getattr(result, "submitted_price", None)
    fill_price = getattr(result, "fill_price", None)
    fill_quality = None
    if fill_price not in (None, 0) and submitted_price not in (None, 0):
        try:
            fill_quality = (float(fill_price) - float(submitted_price)) / float(submitted_price)
        except (TypeError, ValueError, ZeroDivisionError):
            fill_quality = None
    if fill_quality is None:
        fill_quality = getattr(pos, "fill_quality", None)

    details = {
        "status": getattr(result, "status", ""),
        "reason": getattr(result, "reason", None),
        "submitted_price": submitted_price,
        "fill_price": fill_price,
        "shares": getattr(result, "shares", None),
        "timeout_seconds": getattr(result, "timeout_seconds", None),
        "fill_quality": fill_quality,
        "order_status": getattr(pos, "order_status", ""),
    }
    status = getattr(result, "status", "")
    order_role = str(getattr(result, "order_role", "") or "entry")
    event_timestamp = (
        getattr(result, "filled_at", None)
        or getattr(pos, "order_posted_at", None)
        or datetime.now(timezone.utc).isoformat()
    )
    if status == "filled":
        event_type = "ORDER_FILLED"
    elif status in {"rejected", "cancelled", "canceled"}:
        event_type = "ORDER_REJECTED"
    else:
        event_type = "ORDER_ATTEMPTED"
    terminal_exec_status = status or None
    voided_at = event_timestamp if status in {"rejected", "cancelled", "canceled"} else None
    posted_at = (
        getattr(pos, "order_posted_at", None)
        or getattr(result, "filled_at", None)
        or event_timestamp
    )
    log_execution_fact(
        conn,
        intent_id=_execution_intent_id(
            trade_id=getattr(pos, "trade_id", ""),
            order_role=order_role,
            explicit_intent_id=getattr(result, "intent_id", None),
        ),
        position_id=getattr(pos, "trade_id", ""),
        decision_id=decision_id,
        order_role=order_role,
        strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
        posted_at=posted_at,
        filled_at=getattr(result, "filled_at", None) if status == "filled" else None,
        voided_at=voided_at,
        submitted_price=submitted_price,
        fill_price=fill_price,
        shares=getattr(result, "shares", None),
        fill_quality=fill_quality,
        venue_status=str(getattr(result, "venue_status", "") or getattr(pos, "order_status", "") or status or "") or None,
        terminal_exec_status=terminal_exec_status,
    )
    if legacy_runtime_events_available:
        log_position_event(
            conn,
            event_type,
            pos,
            details=details,
            timestamp=event_timestamp,
            source="execution",
        )


def log_settlement_event(
    conn: sqlite3.Connection,
    pos,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    exited_at_override: str | None = None,
) -> None:
    """Append a durable settlement event for learning/risk consumers."""
    legacy_runtime_events_available = _legacy_runtime_position_event_schema_available(conn)
    if not legacy_runtime_events_available:
        if not _canonical_position_surface_available(conn):
            _assert_legacy_runtime_position_event_schema(conn)
    settled_at = getattr(pos, "last_exit_at", None)
    entered_at = getattr(pos, "entered_at", None) or getattr(pos, "day0_entered_at", None)
    log_outcome_fact(
        conn,
        position_id=getattr(pos, "trade_id", ""),
        strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
        entered_at=entered_at,
        exited_at=exited_at_override,
        settled_at=settled_at,
        exit_reason=getattr(pos, "exit_reason", None),
        admin_exit_reason=getattr(pos, "admin_exit_reason", None),
        decision_snapshot_id=getattr(pos, "decision_snapshot_id", None),
        pnl=getattr(pos, "pnl", None),
        outcome=outcome,
        monitor_count=int(getattr(pos, "monitor_count", 0) or 0),
        chain_corrections_count=int(getattr(pos, "chain_corrections_count", 0) or 0),
    )
    if legacy_runtime_events_available:
        log_position_event(
            conn,
            "POSITION_SETTLED",
            pos,
            details=_canonical_position_settled_payload(
                pos,
                winning_bin=winning_bin,
                won=won,
                outcome=outcome,
            ),
            timestamp=settled_at,
            source="settlement",
        )


def log_trade_exit(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Update or insert exit fill evidence."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        from datetime import datetime
        env = getattr(pos, "env", None) or settings.mode
        status = "voided" if getattr(pos, "state", "") == "voided" else "exited"
        values = (
            pos.market_id, pos.bin_label, pos.direction, pos.size_usd, pos.entry_price, pos.last_exit_at or datetime.utcnow().isoformat(),
            getattr(pos, "decision_snapshot_id", None) or None,
            getattr(pos, "calibration_version", "") or None,
            pos.p_posterior, pos.p_posterior, pos.edge, 0.0, 0.0, 0.0,
            status, getattr(pos, "strategy", ""), pos.edge_source, _bin_type_for_label(pos.bin_label), env, pos.last_exit_at, pos.exit_price, getattr(pos, 'pnl', 0.0),
            getattr(pos, "trade_id", ""),
            getattr(pos, "order_id", ""),
            getattr(pos, "order_status", ""),
            getattr(pos, "order_posted_at", ""),
            getattr(pos, "entered_at", ""),
            getattr(pos, "chain_state", ""),
            getattr(pos, "discovery_mode", ""),
            getattr(pos, "market_hours_open", 0.0),
            getattr(pos, "fill_quality", 0.0),
            getattr(pos, "entry_method", ""),
            getattr(pos, "selected_method", ""),
            json.dumps(getattr(pos, "applied_validations", []) or []),
            getattr(pos, "exit_trigger", ""),
            getattr(pos, "exit_reason", ""),
            getattr(pos, "admin_exit_reason", ""),
            getattr(pos, "exit_divergence_score", 0.0),
            getattr(pos, "exit_market_velocity_1h", 0.0),
            getattr(pos, "exit_forward_edge", 0.0),
            getattr(pos, "settlement_semantics_json", None),
            getattr(pos, "epistemic_context_json", None),
            getattr(pos, "edge_context_json", None),
        )
        placeholders = ", ".join(["?"] * len(values))
        conn.execute(f"""
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                forecast_snapshot_id, calibration_model_version,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, strategy, edge_source, bin_type, env, filled_at, fill_price, settlement_edge_usd,
                runtime_trade_id, order_id, order_status_text, order_posted_at, entered_at_ts, chain_state,
                discovery_mode, market_hours_open, fill_quality,
                entry_method, selected_method, applied_validations_json,
                exit_trigger, exit_reason, admin_exit_reason,
                exit_divergence_score, exit_market_velocity_1h, exit_forward_edge,
                settlement_semantics_json, epistemic_context_json, edge_context_json
            )
            VALUES ({placeholders})
        """, values)
        log_position_event(
            conn,
            "POSITION_EXIT_RECORDED",
            pos,
            details={
                "status": status,
                "exit_price": getattr(pos, "exit_price", None),
                "pnl": getattr(pos, "pnl", None),
                "exit_trigger": getattr(pos, "exit_trigger", ""),
                "exit_reason": getattr(pos, "exit_reason", ""),
                "admin_exit_reason": getattr(pos, "admin_exit_reason", ""),
            },
            timestamp=getattr(pos, "last_exit_at", None),
            source="trade_decisions",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log trade exit: %s', e)


def update_trade_lifecycle(conn: sqlite3.Connection, pos) -> None:
    """Update the lifecycle state of the latest DB row for a runtime trade."""
    runtime_trade_id = getattr(pos, "trade_id", "")
    if not runtime_trade_id:
        return

    row = conn.execute(
        """
        SELECT trade_id FROM trade_decisions
        WHERE runtime_trade_id = ?
        ORDER BY trade_id DESC
        LIMIT 1
        """,
        (runtime_trade_id,),
    ).fetchone()
    if row is None:
        return

    status = getattr(pos, "state", "") or "entered"
    timestamp = (
        getattr(pos, "day0_entered_at", "") if status == "day0_window" else ""
    ) or getattr(pos, "entered_at", "") or getattr(pos, "order_posted_at", "")
    filled_at = getattr(pos, "entered_at", "") if status in {"entered", "day0_window"} else None
    fill_price = getattr(pos, "entry_price", None) if status in {"entered", "day0_window"} else None
    entry_order_id = getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", "")
    order_id = getattr(pos, "order_id", "") or entry_order_id
    conn.execute(
        """
        UPDATE trade_decisions
        SET status = ?,
            timestamp = COALESCE(NULLIF(?, ''), timestamp),
            filled_at = COALESCE(?, filled_at),
            fill_price = COALESCE(?, fill_price),
            fill_quality = COALESCE(?, fill_quality),
            order_id = COALESCE(NULLIF(?, ''), order_id),
            order_status_text = COALESCE(NULLIF(?, ''), order_status_text),
            order_posted_at = COALESCE(NULLIF(?, ''), order_posted_at),
            entered_at_ts = COALESCE(NULLIF(?, ''), entered_at_ts),
            chain_state = COALESCE(NULLIF(?, ''), chain_state)
        WHERE trade_id = ?
        """,
        (
            status,
            timestamp,
            filled_at,
            fill_price,
            getattr(pos, "fill_quality", None),
            order_id,
            getattr(pos, "order_status", ""),
            getattr(pos, "order_posted_at", ""),
            getattr(pos, "entered_at", ""),
            getattr(pos, "chain_state", ""),
            row["trade_id"],
        ),
    )

    log_position_event(
        conn,
        "POSITION_LIFECYCLE_UPDATED",
        pos,
        details={
            "status": status,
            "filled_at": filled_at,
            "fill_price": fill_price,
            "entry_order_id": entry_order_id,
            "entry_fill_verified": getattr(pos, "entry_fill_verified", False),
            "order_status": getattr(pos, "order_status", ""),
            "chain_state": getattr(pos, "chain_state", ""),
            "day0_entered_at": getattr(pos, "day0_entered_at", ""),
        },
        timestamp=timestamp or None,
        source="trade_decisions",
    )


def _decode_position_event_rows(rows) -> list[dict]:
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        results.append(item)
    return results


def _is_missing_settlement_value(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _coerce_settlement_float(value) -> Optional[float]:
    if _is_missing_settlement_value(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_settlement_int(value) -> Optional[int]:
    if _is_missing_settlement_value(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _canonical_position_settled_payload(pos, *, winning_bin: str, won: bool, outcome: int) -> dict:
    return {
        "contract_version": CANONICAL_POSITION_SETTLED_CONTRACT_VERSION,
        "winning_bin": winning_bin,
        "position_bin": getattr(pos, "bin_label", ""),
        "won": bool(won),
        "outcome": int(outcome),
        "p_posterior": getattr(pos, "p_posterior", None),
        "exit_price": getattr(pos, "exit_price", None),
        "pnl": getattr(pos, "pnl", None),
        "exit_reason": getattr(pos, "exit_reason", ""),
    }


def _normalize_position_settlement_event(event: dict) -> Optional[dict]:
    details = dict(event.get("details") or {})
    contract_missing_fields = [
        field
        for field in CANONICAL_POSITION_SETTLED_DETAIL_FIELDS
        if _is_missing_settlement_value(details.get(field))
    ]
    normalized = {
        "trade_id": str(event.get("runtime_trade_id") or ""),
        "city": str(event.get("city") or ""),
        "target_date": str(event.get("target_date") or ""),
        "range_label": str(event.get("bin_label") or ""),
        "direction": str(event.get("direction") or ""),
        "p_posterior": _coerce_settlement_float(details.get("p_posterior")),
        "outcome": _coerce_settlement_int(details.get("outcome")),
        "pnl": _coerce_settlement_float(details.get("pnl")),
        "decision_snapshot_id": str(event.get("decision_snapshot_id") or ""),
        "edge_source": str(event.get("edge_source") or ""),
        "strategy": str(event.get("strategy") or ""),
        "settled_at": str(event.get("timestamp") or ""),
        "winning_bin": details.get("winning_bin"),
        "position_bin": details.get("position_bin") or event.get("bin_label"),
        "won": details.get("won"),
        "exit_price": _coerce_settlement_float(details.get("exit_price")),
        "exit_reason": str(details.get("exit_reason") or ""),
        "source": "position_events",
        "authority_level": "durable_event",
        "contract_version": str(
            details.get("contract_version") or CANONICAL_POSITION_SETTLED_CONTRACT_VERSION
        ),
    }
    missing_required = [
        field
        for field in AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS
        if _is_missing_settlement_value(normalized.get(field))
    ]
    if missing_required:
        normalized.update({
            "is_degraded": True,
            "degraded_reason": f"missing_required_fields:{','.join(missing_required)}",
            "contract_missing_fields": contract_missing_fields,
            "canonical_payload_complete": not contract_missing_fields,
            "learning_snapshot_ready": bool(normalized["decision_snapshot_id"]),
            "metric_ready": False,
            "authority_level": "durable_event_malformed",
            "required_missing_fields": missing_required,
        })
        return normalized

    degraded_reasons: list[str] = []
    if contract_missing_fields:
        degraded_reasons.append(
            f"missing_payload_fields:{','.join(contract_missing_fields)}"
        )
    if not normalized["decision_snapshot_id"]:
        degraded_reasons.append("missing_decision_snapshot_id")
    normalized.update({
        "is_degraded": bool(degraded_reasons),
        "degraded_reason": "; ".join(degraded_reasons),
        "contract_missing_fields": contract_missing_fields,
        "canonical_payload_complete": not contract_missing_fields,
        "learning_snapshot_ready": bool(normalized["decision_snapshot_id"]),
        "metric_ready": True,
        "required_missing_fields": [],
    })
    return normalized


def query_position_events(conn: sqlite3.Connection, runtime_trade_id: str, limit: int = 50) -> list[dict]:
    """Load recent durable position events for one runtime trade."""
    _assert_legacy_runtime_position_event_schema(conn)
    legacy_table = _legacy_position_events_table(conn)
    rows = conn.execute(
        f"""
        SELECT event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
               city, target_date, market_id, bin_label, direction, strategy, edge_source,
               source, details_json, timestamp, env
        FROM {legacy_table}
        WHERE runtime_trade_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (runtime_trade_id, limit),
    ).fetchall()
    return _decode_position_event_rows(rows)


def query_settlement_events(
    conn: sqlite3.Connection,
    limit: int | None = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
    env: str | None = None,
    not_before: str | None = None,
) -> list[dict]:
    """Load recent canonical settlement stage events from the durable event spine."""
    _assert_legacy_runtime_position_event_schema(conn)
    legacy_table = _legacy_position_events_table(conn)
    filters = ["event_type = 'POSITION_SETTLED'"]
    query_env = settings.mode if env is None else env
    params: list[object] = []
    if query_env:
        filters.append("env = ?")
        params.append(query_env)
    if city is not None:
        filters.append("city = ?")
        params.append(city)
    if target_date is not None:
        filters.append("target_date = ?")
        params.append(target_date)
    if not_before is not None:
        filters.append("timestamp >= ?")
        params.append(not_before)
    query = f"""
        SELECT event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
               city, target_date, market_id, bin_label, direction, strategy, edge_source,
               source, details_json, timestamp, env
        FROM {legacy_table}
        WHERE {' AND '.join(filters)}
        ORDER BY id DESC
        """
    if limit is not None:
        query += "\n        LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return _decode_position_event_rows(rows)


def query_authoritative_settlement_rows(
    conn: sqlite3.Connection,
    limit: int | None = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
    env: str | None = None,
    not_before: str | None = None,
) -> list[dict]:
    """Prefer stage-level settlement events, then fall back to legacy decision_log blobs."""
    stage_events = query_settlement_events(
        conn,
        limit=limit,
        city=city,
        target_date=target_date,
        env=env,
        not_before=not_before,
    )
    normalized_stage = [
        normalized
        for event in stage_events
        if (normalized := _normalize_position_settlement_event(event)) is not None
    ]
    if normalized_stage:
        return normalized_stage[:limit] if limit is not None else normalized_stage

    from src.state.decision_chain import query_legacy_settlement_records
    legacy_rows = query_legacy_settlement_records(
        conn,
        limit=limit,
        city=city,
        target_date=target_date,
        env=env,
        not_before=not_before,
    )
    return legacy_rows[:limit] if limit is not None else legacy_rows


def query_authoritative_settlement_source(conn: sqlite3.Connection) -> str:
    """Report which settlement source is currently authoritative for readers."""
    rows = query_authoritative_settlement_rows(conn, limit=1)
    if not rows:
        return "none"
    return str(rows[0].get("source") or "none")


def refresh_strategy_health(
    conn: sqlite3.Connection | None,
    *,
    as_of: str | None = None,
) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "strategy_health",
            "rows_written": 0,
        }
    if not _table_exists(conn, "strategy_health"):
        return {
            "status": "skipped_missing_table",
            "table": "strategy_health",
            "rows_written": 0,
        }

    required_tables = ("position_current",)
    optional_tables = ("outcome_fact", "execution_fact", "risk_actions")
    missing_required_tables = [table for table in required_tables if not _table_exists(conn, table)]
    missing_optional_tables = [table for table in optional_tables if not _table_exists(conn, table)]
    refresh_time = as_of or datetime.now(timezone.utc).isoformat()
    if missing_required_tables:
        return {
            "status": "skipped_missing_inputs",
            "table": "strategy_health",
            "rows_written": 0,
            "as_of": refresh_time,
            "missing_required_tables": missing_required_tables,
            "missing_optional_tables": missing_optional_tables,
            "omitted_fields": [
                "risk_level",
                "brier_30d",
                "edge_trend_30d",
            ],
        }

    position_rows = conn.execute(
        f"""
        SELECT
            strategy_key,
            SUM(COALESCE(size_usd, 0.0)) AS open_exposure_usd,
            SUM(
                CASE
                    WHEN shares IS NOT NULL
                     AND last_monitor_market_price IS NOT NULL
                     AND cost_basis_usd IS NOT NULL
                    THEN (shares * last_monitor_market_price) - cost_basis_usd
                    ELSE 0.0
                END
            ) AS unrealized_pnl
        FROM position_current
        WHERE phase IN ({", ".join("?" for _ in OPEN_EXPOSURE_PHASES)})
        GROUP BY strategy_key
        """,
        OPEN_EXPOSURE_PHASES,
    ).fetchall()
    position_metrics = {
        str(row["strategy_key"]): {
            "open_exposure_usd": round(float(row["open_exposure_usd"] or 0.0), 2),
            "unrealized_pnl": round(float(row["unrealized_pnl"] or 0.0), 2),
        }
        for row in position_rows
    }

    settled_cutoff = _shift_iso_timestamp(refresh_time, days=30)
    settlement_metrics: dict[str, dict] = {}
    if "outcome_fact" not in missing_optional_tables:
        settlement_rows = conn.execute(
            """
            SELECT
                strategy_key,
                COUNT(*) AS settled_trades_30d,
                SUM(COALESCE(pnl, 0.0)) AS realized_pnl_30d,
                SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS wins
            FROM outcome_fact
            WHERE settled_at IS NOT NULL
              AND settled_at >= ?
            GROUP BY strategy_key
            """,
            (settled_cutoff,),
        ).fetchall()
        for row in settlement_rows:
            trade_count = int(row["settled_trades_30d"] or 0)
            settlement_metrics[str(row["strategy_key"])] = {
                "settled_trades_30d": trade_count,
                "realized_pnl_30d": round(float(row["realized_pnl_30d"] or 0.0), 2),
                "win_rate_30d": round(float(row["wins"] or 0) / trade_count, 4) if trade_count else None,
            }

    execution_cutoff = _shift_iso_timestamp(refresh_time, days=14)
    execution_metrics: dict[str, dict] = {}
    if "execution_fact" not in missing_optional_tables:
        execution_rows = conn.execute(
            """
            SELECT
                strategy_key,
                SUM(CASE WHEN terminal_exec_status = 'filled' THEN 1 ELSE 0 END) AS filled,
                SUM(CASE WHEN terminal_exec_status IN ('rejected', 'cancelled', 'canceled') THEN 1 ELSE 0 END) AS rejected
            FROM execution_fact
            WHERE order_role = 'entry'
              AND COALESCE(filled_at, voided_at, posted_at) IS NOT NULL
              AND COALESCE(filled_at, voided_at, posted_at) >= ?
            GROUP BY strategy_key
            """,
            (execution_cutoff,),
        ).fetchall()
        for row in execution_rows:
            filled = int(row["filled"] or 0)
            rejected = int(row["rejected"] or 0)
            observed = filled + rejected
            fill_rate = round(filled / observed, 4) if observed else None
            execution_metrics[str(row["strategy_key"])] = {
                "fill_rate_14d": fill_rate,
                "execution_decay_flag": int(fill_rate is not None and observed >= 10 and fill_rate < 0.3),
            }

    risk_action_metrics: dict[str, dict] = {}
    if "risk_actions" not in missing_optional_tables:
        risk_action_rows = conn.execute(
            """
            SELECT strategy_key, action_type, reason
            FROM risk_actions
            WHERE status = 'active'
              AND (effective_until IS NULL OR effective_until > ?)
              AND issued_at <= ?
            """,
            (refresh_time, refresh_time),
        ).fetchall()
        for row in risk_action_rows:
            strategy_key = str(row["strategy_key"] or "")
            if not strategy_key:
                continue
            bucket = risk_action_metrics.setdefault(
                strategy_key,
                {
                    "edge_compression_flag": 0,
                    "execution_decay_flag": 0,
                },
            )
            reason = str(row["reason"] or "")
            if "edge_compression" in reason:
                bucket["edge_compression_flag"] = 1
            if "execution_decay(" in reason:
                bucket["execution_decay_flag"] = 1

    strategy_keys = set(position_metrics)
    strategy_keys.update(settlement_metrics)
    strategy_keys.update(execution_metrics)
    strategy_keys.update(risk_action_metrics)

    conn.execute("DELETE FROM strategy_health")
    rows_written = 0
    for strategy_key in sorted(strategy_keys):
        position_bucket = position_metrics.get(strategy_key, {})
        settlement_bucket = settlement_metrics.get(strategy_key, {})
        execution_bucket = execution_metrics.get(strategy_key, {})
        action_bucket = risk_action_metrics.get(strategy_key, {})
        conn.execute(
            """
            INSERT INTO strategy_health (
                strategy_key,
                as_of,
                open_exposure_usd,
                settled_trades_30d,
                realized_pnl_30d,
                unrealized_pnl,
                win_rate_30d,
                brier_30d,
                fill_rate_14d,
                edge_trend_30d,
                risk_level,
                execution_decay_flag,
                edge_compression_flag
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                strategy_key,
                refresh_time,
                float(position_bucket.get("open_exposure_usd", 0.0)),
                int(settlement_bucket.get("settled_trades_30d", 0)),
                float(settlement_bucket.get("realized_pnl_30d", 0.0)),
                float(position_bucket.get("unrealized_pnl", 0.0)),
                settlement_bucket.get("win_rate_30d"),
                execution_bucket.get("fill_rate_14d"),
                int(
                    max(
                        int(execution_bucket.get("execution_decay_flag", 0)),
                        int(action_bucket.get("execution_decay_flag", 0)),
                    )
                ),
                int(action_bucket.get("edge_compression_flag", 0)),
            ),
        )
        rows_written += 1
    refresh_status = "refreshed" if rows_written else "refreshed_empty"
    return {
        "status": refresh_status,
        "table": "strategy_health",
        "rows_written": rows_written,
        "as_of": refresh_time,
        "missing_required_tables": missing_required_tables,
        "missing_optional_tables": missing_optional_tables,
        "omitted_fields": [
            "risk_level",
            "brier_30d",
            "edge_trend_30d",
        ],
    }


def query_strategy_health_snapshot(
    conn: sqlite3.Connection | None,
    *,
    now: str | None = None,
    max_age_seconds: int = 300,
) -> dict:
    snapshot_time = now or datetime.now(timezone.utc).isoformat()
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
    if not _table_exists(conn, "strategy_health"):
        return {
            "status": "missing_table",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
    rows = conn.execute(
        """
        SELECT sh.*
        FROM strategy_health sh
        JOIN (
            SELECT strategy_key, MAX(as_of) AS latest_as_of
            FROM strategy_health
            GROUP BY strategy_key
        ) latest
          ON latest.strategy_key = sh.strategy_key
         AND latest.latest_as_of = sh.as_of
        ORDER BY sh.strategy_key
        """
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }

    snapshot_dt = _parse_iso_timestamp(snapshot_time)
    stale_strategy_keys: list[str] = []
    by_strategy: dict[str, dict] = {}
    for row in rows:
        strategy_key = str(row["strategy_key"])
        as_of_raw = str(row["as_of"] or "")
        row_as_of = _parse_iso_timestamp(as_of_raw)
        age_seconds = None
        if snapshot_dt is not None and row_as_of is not None:
            age_seconds = max(0.0, (snapshot_dt - row_as_of).total_seconds())
        if age_seconds is None or age_seconds > max_age_seconds:
            stale_strategy_keys.append(strategy_key)
        by_strategy[strategy_key] = {
            key: row[key]
            for key in row.keys()
        }
        by_strategy[strategy_key]["age_seconds"] = age_seconds

    return {
        "status": "stale" if stale_strategy_keys else "fresh",
        "table": "strategy_health",
        "as_of": max(str(row["as_of"] or "") for row in rows),
        "by_strategy": by_strategy,
        "stale_strategy_keys": stale_strategy_keys,
        "max_age_seconds": max_age_seconds,
    }


def query_position_current_status_view(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "position_current",
            "positions": [],
            "strategy_open_counts": {},
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }
    if not _table_exists(conn, "position_current"):
        return {
            "status": "missing_table",
            "table": "position_current",
            "positions": [],
            "strategy_open_counts": {},
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }

    rows = conn.execute(
        """
        SELECT position_id, phase, trade_id, city, bin_label, direction,
               size_usd, shares, cost_basis_usd, entry_price,
               strategy_key, chain_state, order_status,
               decision_snapshot_id, last_monitor_market_price
        FROM position_current
        ORDER BY updated_at DESC, position_id
        """
    ).fetchall()
    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    latest_trade_statuses = _latest_trade_decision_status_by_trade_id(conn, trade_ids)

    positions: list[dict] = []
    strategy_open_counts: dict[str, int] = {}
    chain_state_counts: dict[str, int] = {}
    exit_state_counts: dict[str, int] = {}
    total_exposure_usd = 0.0
    total_unrealized_pnl = 0.0
    unverified_entries = 0
    day0_positions = 0

    for row in rows:
        phase = str(row["phase"] or "")
        if phase not in OPEN_EXPOSURE_PHASES:
            continue
        trade_id = str(row["trade_id"] or row["position_id"] or "")
        latest_trade_status = latest_trade_statuses.get(trade_id, "")
        if latest_trade_status in TERMINAL_TRADE_DECISION_STATUSES:
            continue
        hints = transitional_hints.get(trade_id, {})
        chain_state = str(row["chain_state"] or "unknown")
        exit_state = str(hints.get("exit_state") or "none")
        entry_fill_verified = bool(hints.get("entry_fill_verified", False))
        admin_exit_reason = str(hints.get("admin_exit_reason") or "")
        day0_entered_at = str(hints.get("day0_entered_at") or "")
        shares = float(row["shares"] or 0.0)
        mark_price = row["last_monitor_market_price"]
        cost_basis_usd = row["cost_basis_usd"]
        unrealized_pnl = 0.0
        if shares and mark_price is not None and cost_basis_usd is not None:
            unrealized_pnl = round((shares * float(mark_price)) - float(cost_basis_usd), 2)

        positions.append(
            {
                "trade_id": trade_id,
                "city": str(row["city"] or ""),
                "direction": str(row["direction"] or ""),
                "strategy": str(row["strategy_key"] or ""),
                "state": phase,
                "chain_state": chain_state,
                "exit_state": exit_state,
                "entry_fill_verified": entry_fill_verified,
                "admin_exit_reason": admin_exit_reason,
                "size_usd": float(row["size_usd"] or 0.0),
                "shares": shares,
                "entry_price": row["entry_price"],
                "edge": None,
                "bin_label": str(row["bin_label"] or ""),
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "day0_entered_at": day0_entered_at,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
            }
        )

        strategy_key = str(row["strategy_key"] or "unclassified")
        strategy_open_counts[strategy_key] = strategy_open_counts.get(strategy_key, 0) + 1
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
        exit_state_counts[exit_state] = exit_state_counts.get(exit_state, 0) + 1
        total_exposure_usd += float(row["size_usd"] or 0.0)
        total_unrealized_pnl += unrealized_pnl
        if not entry_fill_verified:
            unverified_entries += 1
        if phase == "day0_window":
            day0_positions += 1

    return {
        "status": "ok",
        "table": "position_current",
        "positions": positions,
        "strategy_open_counts": strategy_open_counts,
        "open_positions": len(positions),
        "total_exposure_usd": round(total_exposure_usd, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
        "chain_state_counts": chain_state_counts,
        "exit_state_counts": exit_state_counts,
        "unverified_entries": unverified_entries,
        "day0_positions": day0_positions,
    }


def query_portfolio_loader_view(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "position_current",
            "positions": [],
        }
    if not _table_exists(conn, "position_current"):
        return {
            "status": "missing_table",
            "table": "position_current",
            "positions": [],
        }

    rows = conn.execute(
        """
        SELECT position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
               direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
               last_monitor_prob, last_monitor_edge, last_monitor_market_price,
               decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
               chain_state, order_id, order_status, updated_at
        FROM position_current
        ORDER BY updated_at DESC, position_id
        """
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "table": "position_current",
            "positions": [],
        }

    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    latest_trade_statuses = _latest_trade_decision_status_by_trade_id(conn, trade_ids)
    legacy_latest_by_trade: dict[str, datetime] = {}
    legacy_table = _legacy_position_events_table(conn)
    if legacy_table:
        placeholders = ", ".join("?" for _ in trade_ids)
        legacy_rows = conn.execute(
            f"""
            SELECT runtime_trade_id, MAX(timestamp) AS latest_timestamp
            FROM {legacy_table}
            WHERE runtime_trade_id IN ({placeholders})
            GROUP BY runtime_trade_id
            """,
            trade_ids,
        ).fetchall()
        for legacy_row in legacy_rows:
            trade_id = str(legacy_row["runtime_trade_id"] or "")
            parsed = _parse_iso_timestamp(str(legacy_row["latest_timestamp"] or ""))
            if trade_id and parsed is not None:
                legacy_latest_by_trade[trade_id] = parsed

    current_mode = os.environ.get("ZEUS_MODE", settings.mode)
    positions: list[dict] = []
    stale_trade_ids: list[str] = []
    for row in rows:
        trade_id = str(row["trade_id"] or row["position_id"] or "")
        phase = str(row["phase"] or "")
        latest_trade_status = latest_trade_statuses.get(trade_id, "")
        hints = transitional_hints.get(trade_id, {})
        position_env = str(hints.get("env") or current_mode)
        if position_env != current_mode:
            continue
        projection_updated_at = _parse_iso_timestamp(str(row["updated_at"] or ""))
        latest_legacy = legacy_latest_by_trade.get(trade_id)
        if projection_updated_at is not None and latest_legacy is not None and latest_legacy > projection_updated_at:
            stale_trade_ids.append(trade_id)
            continue
        runtime_state = PORTFOLIO_LOADER_PHASE_TO_RUNTIME_STATE.get(phase, phase)
        terminal_runtime_state = _terminal_runtime_state_for_trade_decision_status(latest_trade_status)
        if terminal_runtime_state:
            runtime_state = terminal_runtime_state
        positions.append(
            {
                "trade_id": trade_id,
                "market_id": str(row["market_id"] or ""),
                "city": str(row["city"] or ""),
                "cluster": str(row["cluster"] or ""),
                "target_date": str(row["target_date"] or ""),
                "bin_label": str(row["bin_label"] or ""),
                "direction": str(row["direction"] or "unknown"),
                "unit": str(row["unit"] or "F"),
                "size_usd": float(row["size_usd"] or 0.0),
                "shares": float(row["shares"] or 0.0),
                "cost_basis_usd": float(row["cost_basis_usd"] or 0.0),
                "entry_price": float(row["entry_price"] or 0.0),
                "p_posterior": float(row["p_posterior"] or 0.0),
                "last_monitor_prob": float(row["last_monitor_prob"] or 0.0),
                "last_monitor_edge": float(row["last_monitor_edge"] or 0.0),
                "last_monitor_market_price": row["last_monitor_market_price"],
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "entry_method": str(row["entry_method"] or ""),
                "strategy_key": str(row["strategy_key"] or ""),
                "strategy": str(row["strategy_key"] or ""),
                "edge_source": str(row["edge_source"] or row["strategy_key"] or ""),
                "discovery_mode": str(row["discovery_mode"] or ""),
                "chain_state": str(row["chain_state"] or "unknown"),
                "order_id": str(row["order_id"] or ""),
                "order_status": str(row["order_status"] or ""),
                "state": runtime_state,
                "env": position_env,
                "entered_at": str(hints.get("entered_at") or row["updated_at"] or ""),
                "day0_entered_at": str(hints.get("day0_entered_at") or ""),
                "exit_state": str(hints.get("exit_state") or ""),
                "admin_exit_reason": str(hints.get("admin_exit_reason") or ""),
                "entry_fill_verified": bool(hints.get("entry_fill_verified", False)),
            }
        )
    if not positions:
        return {
            "status": "stale_legacy_fallback" if stale_trade_ids else "empty",
            "table": "position_current",
            "positions": [],
            "stale_trade_ids": stale_trade_ids,
        }
    if stale_trade_ids:
        return {
            "status": "stale_legacy_fallback",
            "table": "position_current",
            "positions": [],
            "stale_trade_ids": stale_trade_ids,
        }
    return {
        "status": "ok",
        "table": "position_current",
        "positions": positions,
    }


def upsert_control_override(
    conn: sqlite3.Connection | None,
    *,
    override_id: str,
    target_type: str,
    target_key: str,
    action_type: str,
    value: str,
    issued_by: str,
    issued_at: str,
    reason: str,
    effective_until: str | None = None,
    precedence: int = DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides"}
    if not _table_exists(conn, "control_overrides"):
        return {"status": "skipped_missing_table", "table": "control_overrides"}
    conn.execute(
        """
        INSERT OR REPLACE INTO control_overrides (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            override_id,
            target_type,
            target_key,
            action_type,
            value,
            issued_by,
            issued_at,
            effective_until,
            reason,
            precedence,
        ),
    )
    return {"status": "written", "table": "control_overrides", "override_id": override_id}


def expire_control_override(
    conn: sqlite3.Connection | None,
    *,
    override_id: str,
    expired_at: str,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides", "expired_count": 0}
    if not _table_exists(conn, "control_overrides"):
        return {"status": "skipped_missing_table", "table": "control_overrides", "expired_count": 0}
    cur = conn.execute(
        """
        UPDATE control_overrides
        SET effective_until = ?
        WHERE override_id = ?
          AND (effective_until IS NULL OR effective_until > ?)
        """,
        (expired_at, override_id, expired_at),
    )
    return {
        "status": "expired" if cur.rowcount else "noop",
        "table": "control_overrides",
        "expired_count": int(cur.rowcount or 0),
        "override_id": override_id,
    }


def query_control_override_state(
    conn: sqlite3.Connection | None,
    *,
    now: str | None = None,
) -> dict:
    current_time = now or datetime.now(timezone.utc).isoformat()
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "entries_paused": False,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    if not _table_exists(conn, "control_overrides"):
        return {
            "status": "missing_table",
            "entries_paused": False,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    rows = conn.execute(
        """
        SELECT override_id, target_type, target_key, action_type, value, precedence, issued_at
        FROM control_overrides
        WHERE target_type IN ('global', 'strategy')
          AND issued_at <= ?
          AND (effective_until IS NULL OR effective_until > ?)
        ORDER BY precedence DESC, issued_at DESC, override_id DESC
        """,
        (current_time, current_time),
    ).fetchall()
    entries_paused = False
    edge_threshold_multiplier = 1.0
    strategy_gates: dict[str, bool] = {}
    seen_strategy_gate: set[str] = set()
    global_gate_seen = False
    global_threshold_seen = False
    for row in rows:
        target_type = str(row["target_type"] or "")
        target_key = str(row["target_key"] or "")
        action_type = str(row["action_type"] or "")
        value = str(row["value"] or "")
        if target_type == "global" and target_key == "entries" and action_type == "gate" and not global_gate_seen:
            entries_paused = _parse_boolish_text(value)
            global_gate_seen = True
            continue
        if target_type == "global" and target_key == "entries" and action_type == "threshold_multiplier" and not global_threshold_seen:
            try:
                edge_threshold_multiplier = max(1.0, float(value))
            except (TypeError, ValueError):
                edge_threshold_multiplier = 1.0
            global_threshold_seen = True
            continue
        if target_type == "strategy" and action_type == "gate" and target_key and target_key not in seen_strategy_gate:
            strategy_gates[target_key] = not _parse_boolish_text(value)
            seen_strategy_gate.add(target_key)
    return {
        "status": "ok",
        "entries_paused": entries_paused,
        "edge_threshold_multiplier": edge_threshold_multiplier,
        "strategy_gates": strategy_gates,
    }


def _shift_iso_timestamp(timestamp: str, *, days: int) -> str:
    parsed = _parse_iso_timestamp(timestamp)
    if parsed is None:
        return timestamp
    return (parsed - timedelta(days=days)).isoformat()


def _parse_boolish_text(raw: str) -> bool:
    text = str(raw).strip().lower()
    return text in {"1", "true", "yes", "on", "enabled", "gate"}


def _latest_trade_decision_status_by_trade_id(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, str]:
    if not trade_ids or not _table_exists(conn, "trade_decisions"):
        return {}
    placeholders = ", ".join("?" for _ in trade_ids)

    def _query_latest_status_rows(target_conn: sqlite3.Connection) -> dict[str, str]:
        rows = target_conn.execute(
            f"""
            WITH latest AS (
                SELECT runtime_trade_id, MAX(trade_id) AS latest_trade_id
                FROM trade_decisions
                WHERE runtime_trade_id IN ({placeholders})
                  AND runtime_trade_id IS NOT NULL
                GROUP BY runtime_trade_id
            )
            SELECT td.runtime_trade_id, td.status
            FROM latest
            JOIN trade_decisions td ON td.trade_id = latest.latest_trade_id
            """,
            trade_ids,
        ).fetchall()
        return {
            str(row["runtime_trade_id"] or ""): str(row["status"] or "")
            for row in rows
            if str(row["runtime_trade_id"] or "")
        }

    statuses = _query_latest_status_rows(conn)
    try:
        db_list = conn.execute("PRAGMA database_list").fetchall()
        main_path = next((str(row[2]) for row in db_list if row[1] == "main"), "")
    except sqlite3.OperationalError:
        main_path = ""

    if ZEUS_DB_PATH.exists() and str(ZEUS_DB_PATH) != main_path:
        legacy_conn = sqlite3.connect(str(ZEUS_DB_PATH))
        legacy_conn.row_factory = sqlite3.Row
        try:
            if _table_exists(legacy_conn, "trade_decisions"):
                legacy_statuses = _query_latest_status_rows(legacy_conn)
                for trade_id, legacy_status in legacy_statuses.items():
                    if statuses.get(trade_id) not in TERMINAL_TRADE_DECISION_STATUSES and legacy_status in TERMINAL_TRADE_DECISION_STATUSES:
                        statuses[trade_id] = legacy_status
        finally:
            legacy_conn.close()

    return statuses


def _terminal_runtime_state_for_trade_decision_status(status: str) -> str | None:
    normalized = str(status or "").strip().lower()
    if normalized == "exited":
        return "economically_closed"
    if normalized in {"settled", "voided", "admin_closed"}:
        return normalized
    if normalized == "unresolved_ghost":
        return "admin_closed"
    return None


def _query_transitional_position_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, dict]:
    if not trade_ids:
        return {}
    columns = _table_columns(conn, "position_events")
    placeholders = ", ".join("?" for _ in trade_ids)
    if {"runtime_trade_id", "details_json", "timestamp"}.issubset(columns):
        legacy_table = _legacy_position_events_table(conn) or "position_events"
        rows = conn.execute(
            f"""
            SELECT runtime_trade_id AS trade_key, event_type, details_json AS payload, env, timestamp AS occurred_at
            FROM {legacy_table}
            WHERE runtime_trade_id IN ({placeholders})
            ORDER BY timestamp DESC, id DESC
            """,
            trade_ids,
        ).fetchall()
    elif {"position_id", "payload_json", "occurred_at"}.issubset(columns):
        rows = conn.execute(
            f"""
            SELECT position_id AS trade_key, event_type, payload_json AS payload, occurred_at
            FROM position_events
            WHERE position_id IN ({placeholders})
            ORDER BY occurred_at DESC, sequence_no DESC
            """,
            trade_ids,
        ).fetchall()
    else:
        return {}
    hints: dict[str, dict] = {}
    for row in rows:
        trade_id = str(row["trade_key"] or "")
        if not trade_id:
            continue
        bucket = hints.setdefault(trade_id, {})
        try:
            details = json.loads(row["payload"] or "{}")
        except Exception:
            details = {}
        if "entry_fill_verified" not in bucket and "entry_fill_verified" in details:
            bucket["entry_fill_verified"] = bool(details.get("entry_fill_verified"))
        if "admin_exit_reason" not in bucket and details.get("admin_exit_reason"):
            bucket["admin_exit_reason"] = str(details.get("admin_exit_reason"))
        if "day0_entered_at" not in bucket and details.get("day0_entered_at"):
            bucket["day0_entered_at"] = str(details.get("day0_entered_at"))
        occurred_at = str(row["occurred_at"] or "")
        if (
            "order_posted_at" not in bucket
            and row["event_type"] in {"POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED"}
            and occurred_at
        ):
            bucket["order_posted_at"] = occurred_at
        if (
            "entered_at" not in bucket
            and row["event_type"] == "ENTRY_ORDER_FILLED"
            and occurred_at
        ):
            bucket["entered_at"] = occurred_at
        if "exit_state" not in bucket:
            status = details.get("status")
            if status not in (None, ""):
                bucket["exit_state"] = str(status)
        if "env" not in bucket:
            env_value = None
            if "env" in row.keys():
                env_value = row["env"]
            if env_value not in (None, ""):
                bucket["env"] = str(env_value)
    return hints


def query_p4_fact_smoke_summary(conn: sqlite3.Connection) -> dict:
    missing_tables = [
        table
        for table in ("opportunity_fact", "availability_fact", "execution_fact", "outcome_fact")
        if not _table_exists(conn, table)
    ]
    summary = {
        "missing_tables": missing_tables,
        "opportunity": {"total": 0, "trade_eligible": 0, "no_trade": 0, "availability_tagged": 0},
        "availability": {"total": 0, "failure_types": {}},
        "execution": {"total": 0, "terminal_status_counts": {}, "avg_fill_quality": None},
        "outcome": {"total": 0, "wins": 0, "pnl_total": 0.0},
        "separation": {
            "opportunity_loss_without_availability": 0,
            "availability_failures": 0,
            "execution_vs_outcome_gap": 0,
        },
    }

    if "opportunity_fact" not in missing_tables:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN should_trade = 1 THEN 1 ELSE 0 END) AS trade_eligible,
                SUM(CASE WHEN should_trade = 0 THEN 1 ELSE 0 END) AS no_trade,
                SUM(CASE WHEN availability_status IS NOT NULL AND availability_status != 'ok' THEN 1 ELSE 0 END) AS availability_tagged,
                SUM(CASE WHEN should_trade = 0 AND (availability_status IS NULL OR availability_status = 'ok') THEN 1 ELSE 0 END) AS no_availability_loss
            FROM opportunity_fact
            """
        ).fetchone()
        summary["opportunity"] = {
            "total": int(row["total"] or 0),
            "trade_eligible": int(row["trade_eligible"] or 0),
            "no_trade": int(row["no_trade"] or 0),
            "availability_tagged": int(row["availability_tagged"] or 0),
        }
        summary["separation"]["opportunity_loss_without_availability"] = int(row["no_availability_loss"] or 0)

    if "availability_fact" not in missing_tables:
        rows = conn.execute(
            "SELECT failure_type, COUNT(*) AS n FROM availability_fact GROUP BY failure_type"
        ).fetchall()
        failure_types = {str(r["failure_type"]): int(r["n"]) for r in rows}
        summary["availability"] = {
            "total": sum(failure_types.values()),
            "failure_types": failure_types,
        }
        summary["separation"]["availability_failures"] = summary["availability"]["total"]

    if "execution_fact" not in missing_tables:
        rows = conn.execute(
            "SELECT terminal_exec_status, COUNT(*) AS n FROM execution_fact GROUP BY terminal_exec_status"
        ).fetchall()
        status_counts = {str(r["terminal_exec_status"] or ""): int(r["n"]) for r in rows}
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, AVG(fill_quality) AS avg_fill_quality
            FROM execution_fact
            """
        ).fetchone()
        summary["execution"] = {
            "total": int(row["total"] or 0),
            "terminal_status_counts": status_counts,
            "avg_fill_quality": float(row["avg_fill_quality"]) if row["avg_fill_quality"] is not None else None,
        }

    if "outcome_fact" not in missing_tables:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(COALESCE(pnl, 0.0)) AS pnl_total
            FROM outcome_fact
            """
        ).fetchone()
        summary["outcome"] = {
            "total": int(row["total"] or 0),
            "wins": int(row["wins"] or 0),
            "pnl_total": float(row["pnl_total"] or 0.0),
        }
    summary["separation"]["execution_vs_outcome_gap"] = max(
        0,
        summary["execution"]["total"] - summary["outcome"]["total"],
    )
    return summary


def query_execution_event_summary(
    conn: sqlite3.Connection,
    *,
    env: str | None = None,
    limit: int | None = 500,
    not_before: str | None = None,
) -> dict:
    _assert_legacy_runtime_position_event_schema(conn)
    legacy_table = _legacy_position_events_table(conn)
    query_env = settings.mode if env is None else env
    filters = [
        "env = ?",
        """event_type IN (
            'ORDER_ATTEMPTED', 'ORDER_FILLED', 'ORDER_REJECTED',
            'EXIT_ORDER_ATTEMPTED', 'EXIT_ORDER_FILLED',
            'EXIT_RETRY_SCHEDULED', 'EXIT_BACKOFF_EXHAUSTED',
            'EXIT_FILL_CHECK_FAILED', 'EXIT_FILL_CHECKED',
            'EXIT_FILL_CONFIRMED', 'EXIT_RETRY_RELEASED'
          )""",
    ]
    params: list[object] = [query_env]
    if not_before is not None:
        filters.append("timestamp >= ?")
        params.append(not_before)
    query = f"""
        SELECT event_type, strategy
        FROM {legacy_table}
        WHERE {' AND '.join(filters)}
        ORDER BY id DESC
        """
    if limit is not None:
        query += "\n        LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()

    def _blank() -> dict:
        return {
            "entry_attempted": 0,
            "entry_filled": 0,
            "entry_rejected": 0,
            "exit_attempted": 0,
            "exit_filled": 0,
            "exit_retry_scheduled": 0,
            "exit_backoff_exhausted": 0,
            "exit_fill_check_failed": 0,
            "exit_fill_checked": 0,
            "exit_fill_confirmed": 0,
            "exit_retry_released": 0,
        }

    overall = _blank()
    by_strategy: dict[str, dict] = {}

    mapping = {
        "ORDER_ATTEMPTED": "entry_attempted",
        "ORDER_FILLED": "entry_filled",
        "ORDER_REJECTED": "entry_rejected",
        "EXIT_ORDER_ATTEMPTED": "exit_attempted",
        "EXIT_ORDER_FILLED": "exit_filled",
        "EXIT_RETRY_SCHEDULED": "exit_retry_scheduled",
        "EXIT_BACKOFF_EXHAUSTED": "exit_backoff_exhausted",
        "EXIT_FILL_CHECK_FAILED": "exit_fill_check_failed",
        "EXIT_FILL_CHECKED": "exit_fill_checked",
        "EXIT_FILL_CONFIRMED": "exit_fill_confirmed",
        "EXIT_RETRY_RELEASED": "exit_retry_released",
    }

    for row in rows:
        event_type = str(row["event_type"])
        counter_key = mapping.get(event_type)
        if counter_key is None:
            continue
        overall[counter_key] += 1
        strategy = str(row["strategy"] or "unclassified")
        bucket = by_strategy.setdefault(strategy, _blank())
        bucket[counter_key] += 1

    return {
        "event_sample_size": len(rows),
        "overall": overall,
        "by_strategy": by_strategy,
    }


def log_position_event(
    conn: sqlite3.Connection,
    event_type: str,
    pos,
    *,
    details: dict | None = None,
    timestamp: str | None = None,
    source: str = "runtime",
    order_id: str | None = None,
    position_state: str | None = None,
) -> None:
    """Append a stage-level position event without changing open-position authority."""
    _assert_legacy_runtime_position_event_schema(conn)
    legacy_table = _legacy_position_events_table(conn)
    runtime_trade_id = getattr(pos, "trade_id", "")
    if not runtime_trade_id:
        return

    env = getattr(pos, "env", None) or settings.mode
    event_timestamp = (
        timestamp
        or getattr(pos, "last_exit_at", "")
        or getattr(pos, "entered_at", "")
        or getattr(pos, "order_posted_at", "")
        or datetime.now(timezone.utc).isoformat()
    )
    payload = details or {}
    event_order_id = (
        order_id
        or getattr(pos, "order_id", "")
        or getattr(pos, "entry_order_id", "")
        or getattr(pos, "last_exit_order_id", "")
        or None
    )
    conn.execute(
        f"""
        INSERT INTO {legacy_table} (
            event_type,
            runtime_trade_id,
            position_state,
            order_id,
            decision_snapshot_id,
            city,
            target_date,
            market_id,
            bin_label,
            direction,
            strategy,
            edge_source,
            source,
            details_json,
            timestamp,
            env
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            runtime_trade_id,
            position_state if position_state is not None else (getattr(pos, "state", "") or None),
            event_order_id,
            getattr(pos, "decision_snapshot_id", "") or None,
            getattr(pos, "city", "") or None,
            getattr(pos, "target_date", "") or None,
            getattr(pos, "market_id", "") or None,
            getattr(pos, "bin_label", "") or None,
            getattr(pos, "direction", "") or None,
            getattr(pos, "strategy", "") or None,
            getattr(pos, "edge_source", "") or None,
            source,
            json.dumps(payload, default=str),
            event_timestamp,
            env,
        ),
    )


def log_exit_lifecycle_event(
    conn: sqlite3.Connection,
    pos,
    *,
    event_type: str,
    reason: str = "",
    error: str = "",
    status: str = "",
    order_id: str | None = None,
    details: dict | None = None,
    timestamp: str | None = None,
) -> None:
    """Append sell-side lifecycle telemetry without changing exit authority."""
    payload = {
        "status": status or getattr(pos, "exit_state", ""),
        "exit_reason": getattr(pos, "exit_reason", "") or reason,
        "error": error or getattr(pos, "last_exit_error", ""),
        "retry_count": getattr(pos, "exit_retry_count", 0),
        "next_retry_at": getattr(pos, "next_exit_retry_at", ""),
        "last_exit_order_id": getattr(pos, "last_exit_order_id", ""),
    }
    if details:
        payload.update(details)
    if event_type in {
        "EXIT_ORDER_POSTED",
        "EXIT_ORDER_ATTEMPTED",
        "EXIT_ORDER_FILLED",
        "EXIT_ORDER_REJECTED",
        "EXIT_ORDER_VOIDED",
        "EXIT_RETRY_SCHEDULED",
        "EXIT_BACKOFF_EXHAUSTED",
        "EXIT_FILL_CHECKED",
        "EXIT_FILL_CONFIRMED",
    }:
        terminal_exec_status = None
        voided_at = None
        filled_at = None
        if event_type == "EXIT_ORDER_FILLED":
            terminal_exec_status = "filled"
            filled_at = timestamp or getattr(pos, "last_exit_at", None) or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_RETRY_SCHEDULED", "EXIT_BACKOFF_EXHAUSTED", "EXIT_ORDER_REJECTED", "EXIT_ORDER_VOIDED"}:
            terminal_exec_status = str(payload.get("status") or getattr(pos, "exit_state", "") or "rejected")
            voided_at = timestamp or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_ORDER_ATTEMPTED", "EXIT_ORDER_POSTED", "EXIT_FILL_CHECKED", "EXIT_FILL_CONFIRMED"}:
            terminal_exec_status = str(payload.get("status") or status or "pending")
        posted_at = (
            timestamp
            or getattr(pos, "last_exit_at", None)
            or getattr(pos, "entered_at", None)
            or datetime.now(timezone.utc).isoformat()
        )
        submitted_price = None
        sell_result = payload.get("sell_result")
        if isinstance(sell_result, dict):
            submitted_price = sell_result.get("submitted_price")
        if submitted_price in (None, "") and event_type in {"EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED"}:
            submitted_price = payload.get("current_market_price")
        log_execution_fact(
            conn,
            intent_id=_execution_intent_id(
                trade_id=getattr(pos, "trade_id", ""),
                order_role="exit",
                explicit_intent_id=f"{getattr(pos, 'trade_id', '')}:exit",
            ),
            position_id=getattr(pos, "trade_id", ""),
            order_role="exit",
            strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
            posted_at=posted_at if event_type in {"EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED", "EXIT_FILL_CHECKED", "EXIT_FILL_CONFIRMED"} else None,
            filled_at=filled_at,
            voided_at=voided_at,
            submitted_price=submitted_price,
            fill_price=payload.get("fill_price"),
            shares=payload.get("shares") if payload.get("shares") is not None else getattr(pos, "effective_shares", getattr(pos, "shares", None)),
            fill_quality=None,
            venue_status=str(payload.get("status") or status or "") or None,
            terminal_exec_status=terminal_exec_status,
        )
    log_position_event(
        conn,
        event_type,
        pos,
        details=payload,
        timestamp=timestamp,
        source="exit_lifecycle",
        order_id=order_id,
    )


def log_exit_retry_event(
    conn: sqlite3.Connection,
    pos,
    *,
    reason: str,
    error: str = "",
    timestamp: str | None = None,
) -> None:
    """Append retry/backoff telemetry after exit retry state is updated."""
    event_type = "EXIT_BACKOFF_EXHAUSTED" if getattr(pos, "exit_state", "") == "backoff_exhausted" else "EXIT_RETRY_SCHEDULED"
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        reason=reason,
        error=error,
        timestamp=timestamp,
    )


def log_pending_exit_status_event(
    conn: sqlite3.Connection,
    pos,
    *,
    status: str,
    timestamp: str | None = None,
) -> None:
    """Append fill-check telemetry for an already placed exit order."""
    event_type = "EXIT_FILL_CONFIRMED" if status in {"MATCHED", "FILLED"} else "EXIT_FILL_CHECKED"
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        status=status,
        timestamp=timestamp,
    )


def log_exit_attempt_event(
    conn: sqlite3.Connection,
    pos,
    *,
    order_id: str,
    status: str,
    current_market_price: float,
    best_bid: float | None,
    shares: float,
    details: dict | None = None,
    timestamp: str | None = None,
) -> None:
    """Append sell-order attempt telemetry at placement time."""
    payload = {
        "status": status,
        "current_market_price": current_market_price,
        "best_bid": best_bid,
        "shares": shares,
    }
    if details:
        payload.update(details)
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_ATTEMPTED",
        status=status,
        order_id=order_id,
        details=payload,
        timestamp=timestamp,
    )


def log_exit_fill_event(
    conn: sqlite3.Connection,
    pos,
    *,
    order_id: str,
    fill_price: float,
    current_market_price: float,
    best_bid: float | None,
    timestamp: str | None = None,
) -> None:
    """Append terminal sell-fill telemetry for live exits."""
    payload = {
        "status": "FILLED",
        "fill_price": fill_price,
        "current_market_price": current_market_price,
        "best_bid": best_bid,
        "shares": getattr(pos, "effective_shares", getattr(pos, "shares", None)),
    }
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_FILLED",
        status="FILLED",
        order_id=order_id,
        details=payload,
        timestamp=timestamp,
    )


def log_exit_fill_check_error_event(
    conn: sqlite3.Connection,
    pos,
    *,
    order_id: str,
    timestamp: str | None = None,
) -> None:
    """Append telemetry when sell fill status cannot be read."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_FILL_CHECK_FAILED",
        status="",
        order_id=order_id,
        timestamp=timestamp,
    )


def log_exit_retry_released_event(conn: sqlite3.Connection, pos, *, timestamp: str | None = None) -> None:
    """Append telemetry when cooldown expires and exit can be re-evaluated."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_RETRY_RELEASED",
        status="ready",
        timestamp=timestamp,
    )


def log_pending_exit_recovery_event(
    conn: sqlite3.Connection,
    pos,
    *,
    event_type: str,
    reason: str,
    error: str,
    timestamp: str | None = None,
) -> None:
    """Append telemetry for recovery of malformed/stranded pending exits."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        reason=reason,
        error=error,
        timestamp=timestamp,
    )


def log_reconciled_entry_event(conn: sqlite3.Connection, pos, *, timestamp: str, details: dict | None = None) -> None:
    """Append exactly-once stage event for chain-reconciled pending fills."""
    payload = {
        "status": "entered",
        "source": "chain_reconciliation",
        "reason": "pending_fill_rescued",
        "entry_order_id": getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", ""),
        "entry_method": getattr(pos, "entry_method", ""),
        "selected_method": getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
        "applied_validations": list(getattr(pos, "applied_validations", []) or []),
        "entry_fill_verified": getattr(pos, "entry_fill_verified", False),
        "shares": getattr(pos, "shares", None),
        "cost_basis_usd": getattr(pos, "cost_basis_usd", None),
        "size_usd": getattr(pos, "size_usd", None),
        "condition_id": getattr(pos, "condition_id", ""),
        "order_status": getattr(pos, "order_status", ""),
        "chain_state": getattr(pos, "chain_state", ""),
    }
    if details:
        payload.update(details)
    legacy_columns_present = set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(
        _table_columns(conn, "position_events")
    )
    if not _legacy_runtime_position_event_schema_available(conn):
        if _canonical_position_surface_available(conn) and not legacy_columns_present:
            return
        _assert_legacy_runtime_position_event_schema(conn)
    log_position_event(
        conn,
        "POSITION_LIFECYCLE_UPDATED",
        pos,
        details=payload,
        timestamp=timestamp,
        source="chain_reconciliation",
        position_state="entered",
    )
