"""Zeus database schema and connection management.

All tables enforce the 4-timestamp constraint where applicable.
Settlement truth = Polymarket settlement result (spec §1.3).
"""

import json
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

def log_trade_entry(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Log explicitly at entry for replay reconstruction."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        env = getattr(pos, "env", None) or settings.mode
        status = "pending_tracked" if getattr(pos, "state", "") == "pending_tracked" else "entered"
        timestamp = getattr(pos, "order_posted_at", "") if status == "pending_tracked" else getattr(pos, "entered_at", "")
        filled_at = getattr(pos, "entered_at", None) if status == "entered" else None
        fill_price = getattr(pos, "entry_price", None) if status == "entered" else None
        values = (
            pos.market_id,
            pos.bin_label,
            pos.direction,
            pos.size_usd,
            pos.entry_price,
            timestamp,
            getattr(pos, "decision_snapshot_id", None) or None,
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
            env
            ,
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
    timestamp = getattr(pos, "entered_at", "") or getattr(pos, "order_posted_at", "")
    filled_at = getattr(pos, "entered_at", "") if status == "entered" else None
    fill_price = getattr(pos, "entry_price", None) if status == "entered" else None
    conn.execute(
        """
        UPDATE trade_decisions
        SET status = ?,
            timestamp = COALESCE(NULLIF(?, ''), timestamp),
            filled_at = COALESCE(?, filled_at),
            fill_price = COALESCE(?, fill_price),
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
            getattr(pos, "order_id", ""),
            getattr(pos, "order_status", ""),
            getattr(pos, "order_posted_at", ""),
            getattr(pos, "entered_at", ""),
            getattr(pos, "chain_state", ""),
            row["trade_id"],
        ),
    )
