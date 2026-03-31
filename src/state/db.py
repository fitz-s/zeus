"""Zeus database schema and connection management.

All tables enforce the 4-timestamp constraint where applicable.
Settlement truth = Polymarket settlement result (spec §1.3).
"""

import sqlite3
from pathlib import Path
from typing import Optional

from src.config import STATE_DIR, state_path, settings


ZEUS_DB_PATH = STATE_DIR / "zeus.db"  # Shared world data + env-tagged decisions
RISK_DB_PATH = state_path("risk_state.db")  # Per-process: paper vs live isolation


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
            is_active INTEGER NOT NULL DEFAULT 1
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
            -- Attribution fields (CLAUDE.md: mandatory on every trade)
            edge_source TEXT,
            bin_type TEXT,
            discovery_mode TEXT,
            market_hours_open REAL,
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

        -- Hourly observations for diurnal curves
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

        -- Diurnal temperature curves per city×season
        CREATE TABLE IF NOT EXISTS diurnal_curves (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            hour INTEGER NOT NULL,
            avg_temp REAL NOT NULL,
            std_temp REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season, hour)
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
        CREATE INDEX IF NOT EXISTS idx_token_price_token
            ON token_price_log(token_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_market_events_slug
            ON market_events(market_slug);
        CREATE INDEX IF NOT EXISTS idx_ensemble_city_date
            ON ensemble_snapshots(city, target_date, available_at);
        CREATE INDEX IF NOT EXISTS idx_calibration_bucket
            ON calibration_pairs(cluster, season);
    """)
    
    # Safe Schema evolution for phase 3 attribution
    for col in ["entry_alpha_usd", "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd", "settlement_edge_usd"]:
        try:
            conn.execute(f"ALTER TABLE trade_decisions ADD COLUMN {col} REAL DEFAULT 0.0;")
        except sqlite3.OperationalError:
            pass

    # Provenance: env column on trade-facing tables (Decision 2)
    # Existing rows default to 'paper' — all historical data is from paper trading.
    _env_tables = ["trade_decisions", "chronicle", "decision_log"]
    for table in _env_tables:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN env TEXT NOT NULL DEFAULT 'paper';")
        except sqlite3.OperationalError:
            pass  # Column already exists
            
    try:
        conn.execute("ALTER TABLE trade_decisions ADD COLUMN edge_source TEXT;")
    except sqlite3.OperationalError:
        pass

    if own_conn:
        conn.commit()
        conn.close()

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

def log_trade_entry(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Log explicitly at entry for replay reconstruction."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        env = getattr(pos, "env", None) or settings.mode
        conn.execute("""
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, edge_source, env
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos.market_id,
            pos.bin_label,
            pos.direction,
            pos.size_usd,
            pos.entry_price,
            pos.entered_at,
            pos.p_posterior,
            pos.p_posterior,
            pos.edge,
            pos.p_posterior - (pos.entry_ci_width / 2) if pos.entry_ci_width else 0.0,
            pos.p_posterior + (pos.entry_ci_width / 2) if pos.entry_ci_width else 0.0,
            0.0,
            "entered",
            pos.edge_source,
            env
        ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log trade entry: %s', e)

def log_trade_exit(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Update or insert exit fill evidence."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        from datetime import datetime
        env = getattr(pos, "env", None) or settings.mode
        conn.execute("""
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, edge_source, env, filled_at, fill_price, settlement_edge_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos.market_id, pos.bin_label, pos.direction, pos.size_usd, pos.entry_price, pos.last_exit_at or datetime.utcnow().isoformat(),
            pos.p_posterior, pos.p_posterior, pos.edge, 0.0, 0.0, 0.0,
            "exited", pos.edge_source, env, pos.last_exit_at, pos.exit_price, getattr(pos, 'pnl', 0.0)
        ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log trade exit: %s', e)
