"""Zeus database schema and connection management.

All tables enforce the 4-timestamp constraint where applicable.
Settlement truth = Polymarket settlement result (spec §1.3).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from src.state.db_writer_lock import WriteClass

from src.architecture.decorators import capability
from src.config import STATE_DIR, get_mode, state_path
from src.state.ledger import (
    CANONICAL_POSITION_EVENT_COLUMNS,
    apply_architecture_kernel_schema,
    append_many_and_project,
)
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, POSITION_EVENT_ENVS
from src.state.collateral_ledger import init_collateral_schema
from src.state.market_topology_repo import write_market_topology_state
from src.state.snapshot_repo import init_snapshot_schema
from src.observability.counters import increment as _cnt_inc


ZEUS_DB_PATH = STATE_DIR / "zeus.db"  # LEGACY — remove after Phase 4
ZEUS_WORLD_DB_PATH = STATE_DIR / "zeus-world.db"  # Shared world data (settlements, calibration, ENS)
ZEUS_BACKTEST_DB_PATH = STATE_DIR / "zeus_backtest.db"  # Derived audit output; never runtime authority
RISK_DB_PATH = STATE_DIR / "risk_state.db"  # Single risk DB (live-only)

# T1E: configurable busy-timeout (ms → s). Default 30000ms = 30s per T0_SQLITE_POLICY.md.
# ZEUS_DB_BUSY_TIMEOUT_MS env var is in milliseconds; sqlite3.connect(timeout=) takes seconds.
# Malformed value falls back to default (catch-and-log) so daemon never crashes on bad config.
def _db_busy_timeout_s() -> float:
    """Return sqlite3 busy-timeout in seconds from ZEUS_DB_BUSY_TIMEOUT_MS env var.

    Reads env var on each call so long-running daemons pick up runtime changes.
    Default: 30000 ms (30 s) per T0_SQLITE_POLICY.md.
    """
    raw = os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    try:
        ms = float(raw)
    except (ValueError, TypeError):
        _startup_logger = logging.getLogger(__name__)
        _startup_logger.warning(
            "ZEUS_DB_BUSY_TIMEOUT_MS=%r is not a valid number; "
            "falling back to default 30000 ms (30 s)",
            raw,
        )
        return 30.0
    # T2F-NEGATIVE-ENV-VALIDATION-LOUD-FAIL: reject negative values at parse
    # time so a misconfigured daemon fails loudly rather than silently using a
    # negative sqlite3 timeout (which may behave as an indefinite lock).
    if ms < 0:
        raise ValueError(
            f"ZEUS_DB_BUSY_TIMEOUT_MS must be >= 0; got {raw!r} ({ms} ms). "
            "Fix the environment variable before starting the daemon."
        )
    return ms / 1000.0


def _zeus_trade_db_path() -> Path:
    """Physical path for the trade database."""
    return STATE_DIR / "zeus_trades.db"


def _resolve_write_class(
    explicit: WriteClass | str | None = None,
) -> "WriteClass | None":
    """Resolve the WriteClass for a connection.

    Order: explicit kwarg > ZEUS_DB_WRITE_CLASS env var > None (Phase 0.5
    helper surface; callers retrofit in Phase 1+). Returns None when the
    caller has not opted in — the connection is opened without a flock,
    matching pre-v4 behavior.

    Per v4 plan §AX3 + §10.4.
    """
    from src.state.db_writer_lock import WriteClass  # local import: avoid cycle
    if explicit is None:
        env_val = os.environ.get("ZEUS_DB_WRITE_CLASS")
        if env_val is None:
            return None
        try:
            return WriteClass(env_val.lower())
        except ValueError:
            logger.warning(
                "ZEUS_DB_WRITE_CLASS=%r is not a valid WriteClass; ignoring",
                env_val,
            )
            return None
    if isinstance(explicit, str):
        return WriteClass(explicit.lower())
    return explicit


def _connect(
    db_path: Path,
    *,
    write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Low-level connection with standard pragmas.

    Phase 0.5: ``write_class`` kwarg is accepted for caller classification
    (v4 plan §3.1, §AX3). When None and ``ZEUS_DB_WRITE_CLASS`` env var is
    unset, behavior is identical to pre-v4. When set (explicit or env), the
    class is recorded via counter; flock acquisition is reserved for
    Phase 1+ retrofits where callers wrap the connection lifetime in
    ``db_writer_lock(...)`` themselves.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # T1E: timeout read from ZEUS_DB_BUSY_TIMEOUT_MS env var (ms→s); default 30s.
    timeout_s = _db_busy_timeout_s()
    conn = sqlite3.connect(str(db_path), timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _install_connection_functions(conn)
    resolved = _resolve_write_class(write_class)
    if resolved is not None:
        _cnt_inc(f"db_connect_write_class_{resolved.value}_total")
    return conn


def get_trade_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Trade DB connection (zeus_trades.db)."""
    return _connect(_zeus_trade_db_path(), write_class=write_class)


def get_world_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Shared world data DB (settlements, calibration, ENS)."""
    return _connect(ZEUS_WORLD_DB_PATH, write_class=write_class)


def get_backtest_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Derived backtest DB connection.

    This DB is a reporting/audit surface only. Live runtime execution must not
    read it as authority or write trade/world truth through it.
    """
    return _connect(ZEUS_BACKTEST_DB_PATH, write_class=write_class)


def get_trade_connection_with_world(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Trade connection with shared DB ATTACHed for cross-DB joins.

    v4 plan §3.1.3: when an explicit ``write_class`` is supplied, the
    helper records ATTACH order under the canonical alphabetical sort
    (``risk_state.db < zeus-world.db < zeus_trades.db``) so concurrent
    cross-DB writers cannot deadlock. Without an explicit class, behavior
    matches pre-v4 (single ATTACH; no flocks).

    For *flock-acquired* cross-DB writes use the
    :func:`trade_connection_with_world_flocked` context manager instead;
    that surface acquires the per-DB writer locks in canonical order before
    yielding the ATTACHed connection.
    """
    from src.state.db_writer_lock import canonical_lock_order
    resolved = _resolve_write_class(write_class)
    conn = get_trade_connection(write_class=resolved)
    # Guard: skip ATTACH if 'world' schema already present (connection reuse)
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in attached:
        # Canonical order is recorded for telemetry; ATTACH targets are
        # the same single 'world' schema in this surface but the helper
        # call exercises the v4 §3.1.3 ordering invariant.
        if resolved is not None:
            _ = canonical_lock_order([_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH])
            _cnt_inc("db_trade_with_world_canonical_order_total")
        try:
            conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
        except sqlite3.OperationalError as exc:
            logger.warning("ATTACH world failed (non-fatal): %r", exc)
    return conn


@contextlib.contextmanager
def trade_connection_with_world_flocked(
    *,
    write_class: WriteClass | str = "live",
):
    """Context manager: cross-DB write with canonical-order flocks.

    v4 plan §3.1.3 (deadlock-free cross-DB writers). Acquires the per-DB
    writer locks for ``zeus_trades.db`` and ``zeus-world.db`` in canonical
    alphabetical order, ATTACHes ``world`` onto a trade connection, yields
    that connection, and releases the flocks (and connection) on exit.

    Default ``write_class="live"`` matches the dominant call-site shape
    (riskguard + harvester + settlement commands). Phase 1+ callers
    retrofit by replacing ``conn = get_trade_connection_with_world()``
    blocks with ``with trade_connection_with_world_flocked(...) as conn:``.
    """
    from src.state.db_writer_lock import (
        canonical_lock_order,
        db_writer_lock,
    )
    resolved = _resolve_write_class(write_class)
    if resolved is None:
        # write_class explicit & non-None — should always resolve.
        from src.state.db_writer_lock import WriteClass as _WC
        resolved = _WC.LIVE
    ordered_paths = canonical_lock_order(
        [_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH]
    )
    # Stack two flock context managers (canonical order) before opening conn.
    with db_writer_lock(ordered_paths[0], resolved):
        with db_writer_lock(ordered_paths[1], resolved):
            conn = get_trade_connection(write_class=resolved)
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                }
                if "world" not in attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS world",
                        (str(ZEUS_WORLD_DB_PATH),),
                    )
                _cnt_inc(
                    f"db_trade_with_world_flocked_{resolved.value}_total"
                )
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass


logger = logging.getLogger(__name__)


def _handle_db_write_lock(exc: sqlite3.OperationalError) -> None:
    """T1E: log degrade counter + ALERT when 'database is locked' is raised.

    Called by connection helpers when sqlite3 busy-timeout expires and the
    live cycle must continue in read-only monitor mode rather than crashing.
    Does NOT re-raise — caller decides whether to return None or raise.
    """
    _cnt_inc("db_write_lock_timeout_total")
    logger.warning(
        "telemetry_counter event=db_write_lock_timeout_total db_error=%r",
        str(exc),
    )
    logger.error(
        "ALERT db_write_lock_timeout: database is locked after busy-timeout; "
        "cycle degrades to read-only monitor for this cycle. error=%r",
        str(exc),
    )


def connect_or_degrade(
    db_path: Path,
    *,
    write_class: WriteClass | str | None = None,
) -> Optional[sqlite3.Connection]:
    """T1E: Connect to DB; on 'database is locked' degrade to None (read-only cycle).

    Used by the live cycle write path. Returns None when the DB is locked so
    the caller can skip writes for this cycle without crashing the daemon.
    Any other OperationalError is re-raised (not a lock timeout).
    """
    try:
        return _connect(db_path, write_class=write_class)
    except sqlite3.OperationalError as exc:
        if str(exc).startswith("database is locked"):
            _handle_db_write_lock(exc)
            return None
        raise


CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"
LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE = "legacy_lifecycle_projection_not_settlement_authority"
SETTLEMENT_AUTHORITY_DIAGNOSTIC_SOURCE = "position_events_or_decision_log_verified_settlement"
EXECUTION_FACT_AUTHORITY_SCOPE = "execution_lifecycle_projection_not_settlement_authority"
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
    "settlement_authority",
    "settlement_truth_source",
    "settlement_market_slug",
    "settlement_temperature_metric",
    "settlement_source",
    "settlement_value",
)
SETTLEMENT_METRIC_READY_TRUTH_SOURCES = frozenset({
    "world.settlements",
    "harvester_live_verified_settlement",
})
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
    "unknown",
)
ENTRY_ECONOMICS_LEGACY_UNKNOWN = "legacy_unknown"
ENTRY_ECONOMICS_AVG_FILL_PRICE = "avg_fill_price"
FILL_AUTHORITY_NONE = "none"
FILL_AUTHORITY_VENUE_CONFIRMED_FULL = "venue_confirmed_full"
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


def _positive_finite_decimal_text(value: object) -> int:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(parsed.is_finite() and parsed > 0)


def _decimal_text_equal(left: object, right: object) -> int:
    try:
        left_parsed = Decimal(str(left))
        right_parsed = Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(
        left_parsed.is_finite()
        and right_parsed.is_finite()
        and left_parsed == right_parsed
    )


def _install_connection_functions(conn: sqlite3.Connection) -> None:
    functions = (
        ("zeus_positive_decimal_text", 1, _positive_finite_decimal_text),
        ("zeus_decimal_text_equal", 2, _decimal_text_equal),
    )
    for name, arity, func in functions:
        try:
            conn.create_function(name, arity, func, deterministic=True)
        except TypeError:
            conn.create_function(name, arity, func)


def init_provenance_projection_schema(conn: sqlite3.Connection) -> None:
    """Create U2 raw-provenance projection tables and legacy migrations.

    U2 is intentionally append-only: command/order/trade/lot facts are facts,
    not mutable current-state rows. Later phases may derive read models from
    these tables, but they must not mutate historical provenance.
    """

    _install_connection_functions(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS venue_submission_envelopes (
          envelope_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL DEFAULT 1,
          sdk_package TEXT NOT NULL,
          sdk_version TEXT NOT NULL,
          host TEXT NOT NULL,
          chain_id INTEGER NOT NULL,
          funder_address TEXT NOT NULL,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT NOT NULL,
          outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES','NO')),
          side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
          price TEXT NOT NULL,
          size TEXT NOT NULL,
          order_type TEXT NOT NULL CHECK (order_type IN ('GTC','GTD','FOK','FAK')),
          post_only INTEGER NOT NULL CHECK (post_only IN (0,1)),
          tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          fee_details_json TEXT NOT NULL,
          canonical_pre_sign_payload_hash TEXT NOT NULL,
          signed_order_blob BLOB,
          signed_order_hash TEXT,
          raw_request_hash TEXT NOT NULL,
          raw_response_json TEXT,
          order_id TEXT,
          trade_ids_json TEXT NOT NULL DEFAULT '[]',
          transaction_hashes_json TEXT NOT NULL DEFAULT '[]',
          error_code TEXT,
          error_message TEXT,
          captured_at TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_update
        BEFORE UPDATE ON venue_submission_envelopes
        BEGIN
          SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_delete
        BEFORE DELETE ON venue_submission_envelopes
        BEGIN
          SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
        END;

        CREATE TABLE IF NOT EXISTS venue_order_facts (
          fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN (
            'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
            'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
            'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
          )),
          remaining_size TEXT,
          matched_size TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (venue_order_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_order_facts_command ON venue_order_facts (command_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_order_facts_state ON venue_order_facts (state, observed_at);

        CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_update
        BEFORE UPDATE ON venue_order_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_order_facts is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_delete
        BEFORE DELETE ON venue_order_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_order_facts is append-only');
        END;

        CREATE TABLE IF NOT EXISTS venue_trade_facts (
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
        CREATE INDEX IF NOT EXISTS idx_trade_facts_command ON venue_trade_facts (command_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_facts_trade ON venue_trade_facts (trade_id, observed_at);

        CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_update
        BEFORE UPDATE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_delete
        BEFORE DELETE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

        CREATE TABLE IF NOT EXISTS position_lots (
          lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          state TEXT NOT NULL CHECK (state IN (
            'OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE',
            'EXIT_PENDING','ECONOMICALLY_CLOSED_OPTIMISTIC',
            'ECONOMICALLY_CLOSED_CONFIRMED','SETTLED','QUARANTINED'
          )),
          shares TEXT NOT NULL,
          entry_price_avg TEXT NOT NULL,
          exit_price_avg TEXT,
          source_command_id TEXT REFERENCES venue_commands(command_id),
          source_trade_fact_id INTEGER REFERENCES venue_trade_facts(trade_fact_id),
          captured_at TEXT NOT NULL,
          state_changed_at TEXT NOT NULL,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (position_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_position_lots_state ON position_lots (state, position_id);
        CREATE INDEX IF NOT EXISTS idx_position_lots_trade ON position_lots (source_trade_fact_id);

        CREATE TRIGGER IF NOT EXISTS position_lots_optimistic_trade_authority
        BEFORE INSERT ON position_lots
        WHEN NEW.state = 'OPTIMISTIC_EXPOSURE'
        BEGIN
          SELECT RAISE(ABORT, 'OPTIMISTIC_EXPOSURE requires MATCHED/MINED source trade fact authority')
          WHERE NOT EXISTS (
            SELECT 1
              FROM venue_trade_facts tf
              JOIN venue_commands cmd
                ON cmd.command_id = tf.command_id
             WHERE tf.trade_fact_id = NEW.source_trade_fact_id
               AND tf.command_id = NEW.source_command_id
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND tf.state IN ('MATCHED','MINED')
               AND zeus_positive_decimal_text(tf.filled_size) = 1
               AND zeus_positive_decimal_text(tf.fill_price) = 1
               AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
               AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
          );
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_confirmed_trade_authority
        BEFORE INSERT ON position_lots
        WHEN NEW.state = 'CONFIRMED_EXPOSURE'
        BEGIN
          SELECT RAISE(ABORT, 'CONFIRMED_EXPOSURE requires CONFIRMED source trade fact authority')
          WHERE NOT EXISTS (
            SELECT 1
              FROM venue_trade_facts tf
              JOIN venue_commands cmd
                ON cmd.command_id = tf.command_id
             WHERE tf.trade_fact_id = NEW.source_trade_fact_id
               AND tf.command_id = NEW.source_command_id
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND tf.state = 'CONFIRMED'
               AND zeus_positive_decimal_text(tf.filled_size) = 1
               AND zeus_positive_decimal_text(tf.fill_price) = 1
               AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
               AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
          );
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_no_update
        BEFORE UPDATE ON position_lots
        BEGIN
          SELECT RAISE(ABORT, 'position_lots is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_no_delete
        BEFORE DELETE ON position_lots
        BEGIN
          SELECT RAISE(ABORT, 'position_lots is append-only');
        END;

        CREATE TABLE IF NOT EXISTS provenance_envelope_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
          subject_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          UNIQUE (subject_type, subject_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);

        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_update
        BEFORE UPDATE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_delete
        BEFORE DELETE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;
        """
    )

    try:
        conn.execute("ALTER TABLE venue_commands ADD COLUMN envelope_id TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_venue_commands_envelope ON venue_commands(envelope_id);")


DEFAULT_CONTROL_OVERRIDE_PRECEDENCE = 100
TOKEN_SUPPRESSION_REASONS = frozenset({
    "operator_quarantine_clear",
    "chain_only_quarantined",
    "settled_position",
})
RESOLVED_TOKEN_SUPPRESSION_REASONS = (
    "operator_quarantine_clear",
    "settled_position",
)


def get_connection(
    db_path: Optional[Path] = None,
    *,
    write_class: WriteClass | str | None = "bulk",
) -> sqlite3.Connection:
    """Legacy connection helper.

    v4 plan §AX3: default ``write_class="bulk"`` because the surviving
    callers of this surface are dominated by backfill / replay / etl /
    audit scripts and the legacy ``zeus.db`` path, all of which are BULK
    by classification. LIVE call sites must opt in explicitly with
    ``write_class="live"`` so the v4 flock topology routes them through
    the LIVE flock once Phase 1 retrofits land. Pass ``write_class=None``
    to suppress classification entirely (pre-v4 behavior).
    """
    db_path = db_path or ZEUS_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # T1E: timeout read from ZEUS_DB_BUSY_TIMEOUT_MS env var (ms→s); default 30s.
    timeout_s = _db_busy_timeout_s()
    conn = sqlite3.connect(str(db_path), timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _install_connection_functions(conn)
    resolved = _resolve_write_class(write_class)
    if resolved is not None:
        _cnt_inc(f"db_get_connection_{resolved.value}_total")
    return conn


def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create all Zeus tables. Idempotent.

    # Fix (task #200, 2026-05-10): PRAGMA busy_timeout must be re-applied at the
    # start of init_schema. Python's sqlite3.connect(timeout=N) installs a C-level
    # busy handler, but sqlite3.executescript() resets that handler to NULL before
    # running its SQL. Every executescript() call in this function (there are ~6)
    # wipes the timeout, leaving subsequent conn.execute() calls with no wait budget.
    # Re-applying PRAGMA busy_timeout here covers the entire init_schema call including
    # apply_v2_schema. Source: ZEUS_DB_BUSY_TIMEOUT_MS env var (ms), default 30 s.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    # Re-apply busy_timeout: executescript() resets the C-level busy handler.
    _busy_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_ms}")

    conn.executescript("""
        -- Inherited from legacy predecessor: settlement outcomes
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            -- REOPEN-2 inline: INV-14 identity spine is part of the fresh-DB
            -- schema so UNIQUE(city, target_date, temperature_metric) can
            -- reference temperature_metric without a second migration pass.
            -- Legacy DBs that predate these columns get them via the ALTER
            -- loop below, and their UNIQUE constraint is upgraded via the
            -- REOPEN-2 table-rebuild migration that runs between the ALTERs
            -- and the trigger reinstall.
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
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
            -- K1 additions: raw value/unit contract
            high_raw_value REAL,
            high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
            high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
            low_raw_value REAL,
            low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
            low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
            -- K1 additions: temporal provenance
            high_fetch_utc TEXT,
            high_local_time TEXT,
            high_collection_window_start_utc TEXT,
            high_collection_window_end_utc TEXT,
            low_fetch_utc TEXT,
            low_local_time TEXT,
            low_collection_window_start_utc TEXT,
            low_collection_window_end_utc TEXT,
            -- K1 additions: DST context
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER CHECK (dst_active IN (0, 1)),
            is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
            is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
            -- K1 additions: geographic/seasonal
            hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
            season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
            month INTEGER CHECK (month BETWEEN 1 AND 12),
            -- K1 additions: run provenance
            rebuild_run_id TEXT,
            data_source_version TEXT,
            -- K1 additions: authority + extensibility
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            high_provenance_metadata TEXT,  -- JSON
            low_provenance_metadata TEXT,  -- JSON
            UNIQUE(city, target_date, source)
        );

        CREATE TABLE IF NOT EXISTS daily_observation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            natural_key_json TEXT NOT NULL DEFAULT '{}',
            existing_row_id INTEGER NOT NULL,
            existing_combined_payload_hash TEXT,
            incoming_combined_payload_hash TEXT NOT NULL,
            existing_high_payload_hash TEXT,
            existing_low_payload_hash TEXT,
            incoming_high_payload_hash TEXT NOT NULL,
            incoming_low_payload_hash TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (
                reason IN ('payload_hash_mismatch', 'missing_existing_payload_hash')
            ),
            writer TEXT NOT NULL,
            existing_row_json TEXT NOT NULL,
            incoming_row_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        -- city/target_date/range_label carried over from legacy predecessor for bin mapping
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
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            lead_hours REAL NOT NULL,
            members_json TEXT NOT NULL,
            p_raw_json TEXT,
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT NOT NULL,
            data_version TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            temperature_metric TEXT NOT NULL,
            -- Slice P2-B1 (PR #19 phase 2, 2026-04-26): bias_corrected
            -- declared explicitly. Pre-fix, the column was added only via
            -- the ALTER TABLE migration block below, so fresh init_schema
            -- DBs (CI, dev, in-memory test fixtures) lacked it while
            -- _store_snapshot_p_raw silently expected it. Cross-environment
            -- fragility surfaced as runtime_guards test failures.
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
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
            settlement_value REAL,
            decision_group_id TEXT,
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            bin_source TEXT NOT NULL DEFAULT 'legacy'
        );

        -- Independent forecast-event units derived from calibration_pairs.
        -- Behavior-neutral substrate: active Platt routing still uses existing
        -- pair APIs until a later cutover packet explicitly switches maturity.
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
        CREATE INDEX IF NOT EXISTS idx_calibration_decision_group_bucket
            ON calibration_decision_group(cluster, season, lead_days);

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
            input_space TEXT NOT NULL DEFAULT 'raw_probability',
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))
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

        -- Durable per-decision probability lineage.
        -- This is not portfolio/lifecycle authority; it records decision-time
        -- probability vectors and explicit completeness status for replay/audit.
        CREATE TABLE IF NOT EXISTS probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            mode TEXT,
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            discovery_mode TEXT,
            entry_method TEXT,
            selected_method TEXT,
            trace_status TEXT NOT NULL CHECK (trace_status IN (
                'complete',
                'degraded_decision_context',
                'degraded_missing_vectors',
                'pre_vector_unavailable'
            )),
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            bin_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            p_posterior REAL,
            alpha REAL,
            agreement TEXT,
            n_edges_found INTEGER,
            n_edges_after_fdr INTEGER,
            rejection_stage TEXT,
            availability_status TEXT,
            -- P2 (PLAN_v3 §6.P2 stage 3): MarketPhase axis A tag for
            -- decision-time cohort attribution. Additive, default NULL
            -- for legacy rows; legacy-DB ALTER TABLE migration below.
            market_phase TEXT,
            -- A5 (PLAN.md §A5 + Bug review Finding F): MarketPhaseEvidence
            -- provenance fields. ``market_phase_source`` distinguishes
            -- verified_gamma / fallback_f1 / onchain_resolved / unknown so
            -- attribution reports can stratify by determination quality.
            -- The 3 timestamp columns capture WHICH boundaries the phase
            -- was computed against — so a future cohort report can detect
            -- a midnight-straddle drift without re-running the cycle.
            -- ``uma_resolved_source`` carries the on-chain Settle tx hash
            -- when phase_source == "onchain_resolved", NULL otherwise.
            market_phase_source TEXT,
            market_start_at TEXT,
            market_end_at TEXT,
            settlement_day_entry_utc TEXT,
            uma_resolved_source TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_probability_trace_city_target
            ON probability_trace_fact(city, target_date, recorded_at);
        CREATE INDEX IF NOT EXISTS idx_probability_trace_snapshot
            ON probability_trace_fact(decision_snapshot_id);
        -- NB: idx_probability_trace_market_phase lives in the ALTER block
        -- below (must be created AFTER the ALTER TABLE adds the column on
        -- legacy DBs; fresh DBs hit the same path through the
        -- duplicate-column-swallowed retry).

        -- Selection-family facts for active candidate-family FDR accounting.
        CREATE TABLE IF NOT EXISTS selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            decision_time_status TEXT
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

        -- Model evaluation and promotion substrate. Behavior-neutral until a
        -- future packet wires active model selection through promotion state.



        -- Append-only trade chronicle
        -- env column: added via ALTER TABLE in init_schema lines ~854-859 — see chronicler.py:76
        CREATE TABLE IF NOT EXISTS chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chronicle_dedup
          ON chronicle(trade_id, event_type);

        -- position_events is canonical-only (see apply_architecture_kernel_schema)

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

        -- ETL tables: legacy-predecessor data validated and imported

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

        -- Forecast error distribution substrate for future uncertainty correction.

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

        -- Day0 residual learning substrate.
        -- Behavior-neutral: current Day0Signal hard-floor runtime remains active.

        -- Raw forecast source rows. New-city onboarding writes here first;
        -- skill/bias/profile tables are derived from this table plus settlements.
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_basis_date TEXT,
            forecast_issue_time TEXT,
            lead_days INTEGER,
            lead_time_hours REAL,
            forecast_high REAL,
            forecast_low REAL,
            temp_unit TEXT DEFAULT 'F',
            retrieved_at TEXT,
            imported_at TEXT,
            source_id TEXT,
            raw_payload_hash TEXT,
            captured_at TEXT,
            authority_tier TEXT,
            rebuild_run_id TEXT,
            data_source_version TEXT,
            availability_provenance TEXT
                CHECK (availability_provenance IS NULL
                       OR availability_provenance IN ('derived_dissemination', 'fetch_time', 'reconstructed', 'recorded')),
            UNIQUE(city, target_date, source, forecast_basis_date)
        );
        CREATE INDEX IF NOT EXISTS idx_forecasts_city_date
            ON forecasts(city, target_date);



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
        CREATE INDEX IF NOT EXISTS idx_daily_observation_revisions_lookup
            ON daily_observation_revisions(city, target_date, source, recorded_at);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_observation_revisions_payload
            ON daily_observation_revisions(
                city, target_date, source, incoming_combined_payload_hash, reason
            );
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

        -- K2 data-coverage index — the immune system's memory for live data ingestion.
        -- One row per expected (data_table × city × data_source × target_date × sub_key);
        -- live appenders flip rows to WRITTEN, scanners write MISSING for unrecorded
        -- expected rows, and known exceptions (HKO incomplete-flag days, UKMO pre-start,
        -- new-city onboard lag) are pinned as LEGITIMATE_GAP so the scanner won't
        -- keep re-attempting them. Distinct from `availability_fact` which logs
        -- runtime cycle/order outages — this table is specifically a data-ingestion
        -- coverage ledger.
        CREATE TABLE IF NOT EXISTS data_coverage (
            data_table  TEXT NOT NULL
                CHECK (data_table IN ('observations','observation_instants','solar_daily','forecasts')),
            city        TEXT NOT NULL,
            data_source TEXT NOT NULL,
            target_date TEXT NOT NULL,
            sub_key     TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL
                CHECK (status IN ('WRITTEN','LEGITIMATE_GAP','FAILED','MISSING')),
            reason      TEXT,
            fetched_at  TEXT NOT NULL,
            expected_at TEXT,
            retry_after TEXT,
            PRIMARY KEY (data_table, city, data_source, target_date, sub_key)
        );
        CREATE INDEX IF NOT EXISTS idx_data_coverage_status
            ON data_coverage(status, data_table);
        CREATE INDEX IF NOT EXISTS idx_data_coverage_scan
            ON data_coverage(data_table, city, data_source, target_date);
        CREATE INDEX IF NOT EXISTS idx_data_coverage_retry
            ON data_coverage(status, retry_after) WHERE status = 'FAILED';

        -- PR45b data-daemon readiness provenance substrate. These tables are
        -- behavior-neutral until later phases wire ingest writers and runtime
        -- consumers through their repo modules.
        CREATE TABLE IF NOT EXISTS job_run (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL CHECK (plane IN (
                'forecast','observation','solar_aux','market_topology',
                'quote','settlement_truth','source_health','hole_backfill','telemetry_control'
            )),
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED','SKIPPED_LOCK_HELD'
            )),
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_name, scheduled_for, source_id, track)
        );
        CREATE INDEX IF NOT EXISTS idx_job_run_job_window
            ON job_run(job_name, scheduled_for);
        CREATE INDEX IF NOT EXISTS idx_job_run_plane_status
            ON job_run(plane, status, scheduled_for);
        CREATE INDEX IF NOT EXISTS idx_job_run_source_run
            ON job_run(source_run_id);

        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL CHECK (ingest_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            origin_mode TEXT NOT NULL CHECK (origin_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','NOT_RELEASED'
            )),
            partial_run INTEGER NOT NULL DEFAULT 0 CHECK (partial_run IN (0,1)),
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED'
            )),
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (partial_run = 0 OR completeness_status = 'PARTIAL')
        );
        CREATE INDEX IF NOT EXISTS idx_source_run_source_cycle
            ON source_run(source_id, track, source_cycle_time);
        CREATE INDEX IF NOT EXISTS idx_source_run_scope
            ON source_run(city_id, city_timezone, target_local_date, temperature_metric, data_version);
        CREATE INDEX IF NOT EXISTS idx_source_run_status
            ON source_run(status, completeness_status, source_cycle_time);

        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_transport TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city_id TEXT NOT NULL,
            city TEXT NOT NULL,
            city_timezone TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            target_window_start_utc TEXT NOT NULL,
            target_window_end_utc TEXT NOT NULL,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','HORIZON_OUT_OF_RANGE','NOT_RELEASED'
            )),
            readiness_status TEXT NOT NULL CHECK (readiness_status IN (
                'LIVE_ELIGIBLE','SHADOW_ONLY','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_code TEXT,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(
                source_run_id, source_id, source_transport, release_calendar_key,
                track, city_id, city_timezone, target_local_date,
                temperature_metric, data_version
            )
        );
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_scope
            ON source_run_coverage(city_id, city_timezone, target_local_date, temperature_metric, source_id, source_transport, data_version);
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_status
            ON source_run_coverage(readiness_status, completeness_status, computed_at);

        CREATE TABLE IF NOT EXISTS readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK (scope_type IN (
                'global','source','city_metric','market','strategy','quote'
            )),
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'LIVE_ELIGIBLE','SHADOW_ONLY','BLOCKED','DEGRADED_LOG_ONLY','UNKNOWN_BLOCKED'
            )),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(
                scope_type, city_id, city_timezone, target_local_date,
                temperature_metric, physical_quantity, observation_field,
                data_version, strategy_key, market_family, source_id, track,
                condition_id
            )
        );
        CREATE INDEX IF NOT EXISTS idx_readiness_state_entry_scope
            ON readiness_state(city_id, city_timezone, target_local_date, temperature_metric, strategy_key, market_family, condition_id);
        CREATE INDEX IF NOT EXISTS idx_readiness_state_status_expiry
            ON readiness_state(status, expires_at);

        CREATE TABLE IF NOT EXISTS market_topology_state (
            topology_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            market_family TEXT NOT NULL,
            event_id TEXT,
            condition_id TEXT NOT NULL,
            question_id TEXT,
            city_id TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            bin_topology_hash TEXT,
            gamma_captured_at TEXT,
            gamma_updated_at TEXT,
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISMATCH','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            authority_status TEXT NOT NULL CHECK (authority_status IN (
                'VERIFIED','STALE','EMPTY_FALLBACK','UNKNOWN'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'CURRENT','STALE','EMPTY_FALLBACK','MISMATCH','UNKNOWN'
            )),
            expires_at TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(market_family, condition_id, city_id, target_local_date, temperature_metric, data_version)
        );
        CREATE INDEX IF NOT EXISTS idx_market_topology_scope
            ON market_topology_state(city_id, city_timezone, target_local_date, temperature_metric, market_family, condition_id);
        CREATE INDEX IF NOT EXISTS idx_market_topology_status_expiry
            ON market_topology_state(status, expires_at);

        CREATE TABLE IF NOT EXISTS source_contract_audit_events (
            audit_id TEXT PRIMARY KEY,
            checked_at_utc TEXT NOT NULL,
            scan_authority TEXT NOT NULL CHECK (scan_authority IN (
                'VERIFIED','FIXTURE','STALE_CACHE','EMPTY_FALLBACK','NEVER_FETCHED'
            )),
            report_status TEXT CHECK (report_status IS NULL OR report_status IN (
                'OK','WARN','ALERT','DATA_UNAVAILABLE'
            )),
            severity TEXT NOT NULL CHECK (severity IN ('OK','WARN','ALERT','DATA_UNAVAILABLE')),
            event_id TEXT,
            slug TEXT,
            title TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISSING','AMBIGUOUS','MISMATCH','UNSUPPORTED','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            configured_source_family TEXT,
            configured_station_id TEXT,
            observed_source_family TEXT,
            observed_station_id TEXT,
            resolution_sources_json TEXT NOT NULL DEFAULT '[]',
            source_contract_json TEXT NOT NULL DEFAULT '{}',
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_city_date
            ON source_contract_audit_events(city, target_date, temperature_metric, checked_at_utc);
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_status
            ON source_contract_audit_events(source_contract_status, severity, checked_at_utc);
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_update
        BEFORE UPDATE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_delete
        BEFORE DELETE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END;

        -- Availability/outage fact log (observability — kernel §availability_fact)
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

        -- P1.S1 (INV-28 / D-P1-1-a, D-P1-2-a): durable command journal
        -- venue_commands is the pre-side-effect persistence layer for every
        -- place_limit_order / cancel call.  Written via src/state/venue_command_repo.py
        -- only — no direct SQL outside the repo module.
        CREATE TABLE IF NOT EXISTS venue_commands (
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
        );

        CREATE INDEX IF NOT EXISTS idx_venue_commands_position ON venue_commands(position_id);
        CREATE INDEX IF NOT EXISTS idx_venue_commands_state ON venue_commands(state);
        CREATE INDEX IF NOT EXISTS idx_venue_commands_decision ON venue_commands(decision_id);

        -- P1.S1 (INV-28 / D-P1-3-a): append-only event log for venue_commands.
        -- Records every state transition.  NC-18 forbids UPDATE/DELETE outside
        -- src/state/venue_command_repo.py.
        CREATE TABLE IF NOT EXISTS venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        );

        CREATE INDEX IF NOT EXISTS idx_venue_command_events_command ON venue_command_events(command_id);
        CREATE INDEX IF NOT EXISTS idx_venue_command_events_type ON venue_command_events(event_type);

    """)
    init_snapshot_schema(conn)
    init_collateral_schema(conn)
    # R3 M4 exit mutex DDL lives here to keep DB initialization independent of
    # importing src.execution modules.  The execution module repeats the same
    # idempotent CREATE TABLE for direct use.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exit_mutex_holdings (
          mutex_key TEXT PRIMARY KEY,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
          acquired_at TEXT NOT NULL,
          released_at TEXT,
          release_reason TEXT
        );
    """)
    # R3 M5 exchange reconciliation findings.  Schema stays in state/db.py so
    # DB initialization does not import the execution sweep module.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exchange_reconcile_findings (
          finding_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK (kind IN (
            'exchange_ghost_order','local_orphan_order','unrecorded_trade',
            'position_drift','heartbeat_suspected_cancel','cutover_wipe'
          )),
          subject_id TEXT NOT NULL,
          context TEXT NOT NULL CHECK (context IN (
            'periodic','ws_gap','heartbeat_loss','cutover','operator'
          )),
          evidence_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT,
          resolution TEXT,
          resolved_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_findings_unresolved
          ON exchange_reconcile_findings (resolved_at)
          WHERE resolved_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_unresolved_subject
          ON exchange_reconcile_findings (kind, subject_id, context)
          WHERE resolved_at IS NULL;
    """)
    init_provenance_projection_schema(conn)
    # Keep wrap/unwrap DDL local to the schema owner so src.state does not
    # import src.execution during DB initialization. The execution module owns
    # the command API and repeats the same idempotent DDL for direct use.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wrap_unwrap_commands (
          command_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          direction TEXT NOT NULL CHECK (direction IN ('WRAP','UNWRAP')),
          amount_micro INTEGER NOT NULL,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          requested_at TEXT NOT NULL,
          terminal_at TEXT,
          error_payload TEXT
        );

        CREATE TABLE IF NOT EXISTS wrap_unwrap_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          command_id TEXT NOT NULL REFERENCES wrap_unwrap_commands(command_id),
          event_type TEXT NOT NULL,
          payload_json TEXT,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # T1A: DDL single-source — delegate to schema owner to avoid duplication.
    from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA
    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)

    # task #200 (2026-05-10): executescript() resets the C-level busy handler.
    # Re-apply after the last executescript() so all subsequent conn.execute()
    # calls (ALTER loops, apply_v2_schema) wait under contention instead of
    # failing immediately. apply_v2_schema also sets this independently as a
    # belt-and-suspenders guard for callers that bypass init_schema.
    conn.execute(f"PRAGMA busy_timeout = {int(os.environ.get('ZEUS_DB_BUSY_TIMEOUT_MS', '30000'))}")

    # Safe Schema evolution for phase 3 attribution
    for col in ["entry_alpha_usd", "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd", "settlement_edge_usd"]:
        try:
            conn.execute(f"ALTER TABLE trade_decisions ADD COLUMN {col} REAL DEFAULT 0.0;")
        except sqlite3.OperationalError:
            pass

    # P2 (PLAN_v3 §6.P2 stage 3, 2026-05-04): probability_trace_fact gains
    # ``market_phase`` for decision-time cohort attribution. Legacy DBs
    # predate this column; CREATE TABLE IF NOT EXISTS would no-op so the
    # writer at log_probability_trace_fact would fail with
    # "table probability_trace_fact has no column named market_phase".
    # ALTER TABLE catches legacy DBs; OperationalError on duplicate-column
    # is swallowed for fresh DBs.
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN market_phase TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        # Read consumer for this index lands with PLAN_v3 §6.P9 (per-(strategy_key,
        # market_phase) cohort attribution SQL). Until then the index has no live
        # query; it is provisioned now so the first cohort report doesn't trigger
        # a full-table scan after months of writes. Do NOT GC as orphan — see
        # critic R3 ATTACK 9 (PR #53).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_probability_trace_market_phase "
            "ON probability_trace_fact(market_phase);"
        )
    except sqlite3.OperationalError:
        pass

    # A5 (PLAN.md §A5 + Bug review Finding F, 2026-05-04): MarketPhaseEvidence
    # provenance columns. Same migration pattern as ``market_phase`` above —
    # ALTER catches legacy DBs; duplicate-column OperationalError is the
    # expected fresh-DB no-op path.
    for col in (
        "market_phase_source",
        "market_start_at",
        "market_end_at",
        "settlement_day_entry_utc",
        "uma_resolved_source",
    ):
        try:
            conn.execute(
                f"ALTER TABLE probability_trace_fact ADD COLUMN {col} TEXT;"
            )
        except sqlite3.OperationalError:
            pass
    try:
        # Index on phase_source for cohort queries that group by determination
        # quality (e.g., "what % of post-A5 decisions used fallback_f1?").
        # Provisioned now so the first cohort report after wiring doesn't
        # trigger a full-table scan; mirrors the idx_probability_trace_market_phase
        # rationale from §6.P9.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_probability_trace_phase_source "
            "ON probability_trace_fact(market_phase_source);"
        )
    except sqlite3.OperationalError:
        pass

    # A5 uma_resolution table — listener writes here, cycle_runtime reads
    # it via uma_resolution_listener.lookup_resolution. Idempotent.
    from src.state.uma_resolution_listener import init_uma_resolution_schema as _init_uma
    _init_uma(conn)

    # REOPEN-1 (2026-04-23): forecasts writer at src/data/forecasts_append.py:256-262
    # inserts rebuild_run_id + data_source_version; legacy DBs predate the CREATE
    # TABLE declaration of these two columns, so CREATE TABLE IF NOT EXISTS no-ops
    # and the writer fails at runtime with "table forecasts has no column named
    # rebuild_run_id" (observed: k2_forecasts_daily FAILED every 30 min per
    # state/scheduler_jobs_health.json). ALTER path catches legacy DBs without
    # disturbing fresh DBs (OperationalError on duplicate-column is swallowed).
    for col in [
        "rebuild_run_id",
        "data_source_version",
        "source_id",
        "raw_payload_hash",
        "captured_at",
        "authority_tier",
    ]:
        try:
            conn.execute(f"ALTER TABLE forecasts ADD COLUMN {col} TEXT;")
        except sqlite3.OperationalError:
            pass

    # F11 (2026-04-28): forecasts writer at src/data/forecasts_append.py:267-274 now
    # inserts availability_provenance (D4 antibody). Same pattern as REOPEN-1 above:
    # CREATE TABLE adds the column for fresh DBs, this ALTER catches legacy DBs.
    # The CHECK constraint can't be added via ALTER in SQLite, so legacy DBs run
    # without the DB-level enum enforcement; the writer-level assertion at
    # forecasts_append.py:283-288 still rejects bad values. Fresh DBs get both.
    try:
        conn.execute("ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT;")
    except sqlite3.OperationalError:
        pass

    # U1: legacy trade DBs predate the executable snapshot citation. SQLite
    # cannot add a NOT NULL column without a table rebuild, so old DBs get the
    # nullable column while venue_command_repo.insert_command enforces it for
    # every new command row.
    try:
        conn.execute("ALTER TABLE venue_commands ADD COLUMN snapshot_id TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_venue_commands_snapshot ON venue_commands(snapshot_id);")

    try:
        conn.execute("ALTER TABLE platt_models ADD COLUMN input_space TEXT NOT NULL DEFAULT 'raw_probability';")
    except sqlite3.OperationalError:
        pass

    # Provenance: env column on trade-facing tables (Decision 2).
    # Existing non-event rows default to 'live' for legacy compatibility.
    _env_tables = ["trade_decisions", "chronicle", "decision_log"]
    for table in _env_tables:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN env TEXT NOT NULL DEFAULT 'live';")
        except sqlite3.OperationalError:
            pass  # Column already exists
    try:
        conn.execute("ALTER TABLE position_events ADD COLUMN env TEXT;")
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

    for ddl in [
        "ALTER TABLE calibration_pairs ADD COLUMN decision_group_id TEXT;",
        "ALTER TABLE calibration_pairs ADD COLUMN bias_corrected INTEGER NOT NULL DEFAULT 0;",
        # 2026-04-14 refactor: bin_source discriminator separates canonical-grid
        # training pairs from legacy market-derived pairs so the destructive
        # DELETE path in rebuild_calibration_pairs_canonical.py can target
        # WHERE bin_source='canonical_v1' without LIKE blast radius.
        "ALTER TABLE calibration_pairs ADD COLUMN bin_source TEXT NOT NULL DEFAULT 'legacy';",
        # Slice P2-B1 (PR #19 phase 2, 2026-04-26): idempotent migration
        # for legacy DBs predating the CREATE TABLE addition above. Wrapped
        # in try/except OperationalError; safe to no-op when column already
        # exists. Default 0 matches `int(False)` from _store_snapshot_p_raw.
        "ALTER TABLE ensemble_snapshots ADD COLUMN bias_corrected INTEGER NOT NULL DEFAULT 0;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # P-B (2026-04-23): INV-14 identity spine + provenance vehicle on settlements.
    # Plan: docs/operations/task_2026-04-23_data_readiness_remediation/evidence/pb_schema_plan.md
    # All columns are nullable (pre-P-E rows may carry NULL); NOT-NULL enforcement is
    # deferred to P-E DELETE+INSERT reconstruction writers.
    for ddl in [
        "ALTER TABLE settlements ADD COLUMN pm_bin_lo REAL;",
        "ALTER TABLE settlements ADD COLUMN pm_bin_hi REAL;",
        "ALTER TABLE settlements ADD COLUMN unit TEXT;",
        "ALTER TABLE settlements ADD COLUMN settlement_source_type TEXT;",
        "ALTER TABLE settlements ADD COLUMN temperature_metric TEXT "
        "CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'));",
        "ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;",
        "ALTER TABLE settlements ADD COLUMN observation_field TEXT "
        "CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp'));",
        "ALTER TABLE settlements ADD COLUMN data_version TEXT;",
        "ALTER TABLE settlements ADD COLUMN provenance_json TEXT;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # REOPEN-2 (2026-04-24, data-readiness-tail): settlements UNIQUE migration.
    # Pre-REOPEN-2 schema: UNIQUE(city, target_date) — structurally blocks
    # dual-track (a HIGH row for city+date makes a LOW row for the same
    # city+date UNIQUE-collide). Per critic-opus P0.2 forensic-triage C3+C4,
    # this is a pre-flip BLOCKER for DR-33-C — first low-market settlement
    # attempt on flag-flip would silently drop the row and break the learning
    # chain for the LOW track.
    #
    # SQLite cannot ALTER a UNIQUE constraint; the only path is table
    # recreation. Idempotent: detect whether current table already has the
    # new UNIQUE(city, target_date, temperature_metric) via sqlite_master
    # SQL inspection; skip if yes.
    #
    # Safety: scratch-DB dry-run verified (2026-04-24) that the rebuild is
    # lossless on 1,561 rows + preserves authority groups (1469 VERIFIED + 92
    # QUARANTINED) + unlocks dual-track. Migration runs BEFORE trigger DROP+
    # CREATE blocks below so triggers install against the rebuilt table.
    try:
        settlements_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()
        settlements_sql = settlements_sql_row[0] if settlements_sql_row else ""
        needs_migration = (
            settlements_sql
            and "UNIQUE(city, target_date, temperature_metric)" not in settlements_sql
            and "UNIQUE (city, target_date, temperature_metric)" not in settlements_sql
        )
        if needs_migration:
            # Dynamic column-list copy (preserves schema even if future ALTERs
            # add more columns beyond the current set).
            cols = [r[1] for r in conn.execute("PRAGMA table_info(settlements)")]
            col_list = ", ".join(cols)
            pre_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
            conn.execute(
                """
                CREATE TABLE settlements_migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    market_slug TEXT,
                    winning_bin TEXT,
                    settlement_value REAL,
                    settlement_source TEXT,
                    settled_at TEXT,
                    authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
                    pm_bin_lo REAL,
                    pm_bin_hi REAL,
                    unit TEXT,
                    settlement_source_type TEXT,
                    temperature_metric TEXT
                        CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
                    physical_quantity TEXT,
                    observation_field TEXT
                        CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
                    data_version TEXT,
                    provenance_json TEXT,
                    UNIQUE(city, target_date, temperature_metric)
                )
                """
            )
            conn.execute(
                f"INSERT INTO settlements_migrated ({col_list}) SELECT {col_list} FROM settlements"
            )
            post_count = conn.execute(
                "SELECT COUNT(*) FROM settlements_migrated"
            ).fetchone()[0]
            if post_count != pre_count:
                raise RuntimeError(
                    f"REOPEN-2 row-count drift: pre={pre_count} post={post_count} — "
                    "ABORT migration to prevent data loss"
                )
            conn.execute("DROP TABLE settlements")
            conn.execute("ALTER TABLE settlements_migrated RENAME TO settlements")
    except sqlite3.OperationalError:
        # Fresh DBs where settlements doesn't exist yet fall through to
        # CREATE TABLE IF NOT EXISTS above (which now declares new UNIQUE).
        # No action needed.
        pass

    # P-B authority-monotonic trigger (INV-FP-5 enforcement).
    # Reactivation contract: QUARANTINED->VERIFIED requires a top-level JSON key
    # `reactivated_by` that is a non-empty text value in provenance_json.
    # Substring LIKE is intentionally avoided to prevent false-positive matches
    # on keys like "not_reactivated_by". DROP + CREATE (not CREATE IF NOT EXISTS)
    # because S2.1 (2026-04-23 data-readiness-tail) extended the WHEN clause to
    # reject presence-only bypasses (reactivated_by=false / 0 / "" / {} / [])
    # — IF NOT EXISTS would silently retain the weaker v1 predicate on any DB
    # that had it. Idempotency preserved via DROP IF EXISTS.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_authority_monotonic")
        conn.execute(
            """
            CREATE TRIGGER settlements_authority_monotonic
            BEFORE UPDATE OF authority ON settlements
            WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
              OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
                  AND (NEW.provenance_json IS NULL
                       OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL
                       OR json_type(NEW.provenance_json, '$.reactivated_by') != 'text'
                       OR length(json_extract(NEW.provenance_json, '$.reactivated_by')) = 0))
            BEGIN
                SELECT RAISE(ABORT, 'settlements.authority transition forbidden: VERIFIED->UNVERIFIED blocked, or QUARANTINED->VERIFIED requires provenance_json.reactivated_by to be a non-empty text value');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    # POST-AUDIT FIX #1 (2026-04-24, adversarial-audit follow-up):
    # Close the NULL-NULL UNIQUE hole on settlements.
    #
    # REOPEN-2 (earlier today) installed UNIQUE(city, target_date,
    # temperature_metric). CHECK constraint at
    # `temperature_metric TEXT CHECK (temperature_metric IS NULL OR
    # temperature_metric IN ('high','low'))` intentionally tolerates NULL
    # so legacy-schema ALTER-added rows could pre-exist; SQLite UNIQUE
    # treats NULL as DISTINCT, so the new UNIQUE does NOT prevent
    # duplicate (city, target_date, NULL) rows. Subagent-4 adversarial
    # audit (2026-04-24) DEMONSTRATED this on the live DB: two
    # INSERTs with (TESTCITY, '2099-01-01', NULL, 'UNVERIFIED') both
    # succeeded. `scripts/onboard_cities.py:383` is the writer that
    # currently emits NULL-metric scaffold rows.
    #
    # Structural fix: a BEFORE INSERT trigger that rejects NULL metric
    # on ALL rows (not just VERIFIED — the NULL-metric scaffold path
    # bypasses the verified-integrity trigger by inserting as
    # UNVERIFIED). DROP + CREATE for v2 propagation. Live DB has 0 NULL
    # metric rows as of the audit — no existing row rejected.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_non_null_metric")
        conn.execute(
            """
            CREATE TRIGGER settlements_non_null_metric
            BEFORE INSERT ON settlements
            WHEN NEW.temperature_metric IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'settlements.temperature_metric must be non-null (high or low); REOPEN-2 post-audit fix closes the NULL-NULL UNIQUE hole');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    # S2.2 (2026-04-23, data-readiness-tail): Structural AP-2 prevention.
    # SettlementSemantics.assert_settlement_value() is a SOCIAL gate (runtime
    # only — any writer that bypasses the function bypasses the check). These
    # two triggers enforce the minimum VERIFIED-row invariants structurally at
    # DB-write time: a row with authority='VERIFIED' must carry non-null
    # settlement_value AND non-empty winning_bin. QUARANTINED rows may have
    # NULL settlement_value (that is the quarantine semantic — row is excluded
    # from the authoritative set until reactivation).
    #
    # Pre-apply probe against live DB (1,469 VERIFIED + 92 QUARANTINED rows):
    #   VERIFIED: 0 with null settlement_value / 0 with null winning_bin → none rejected
    #   QUARANTINED: 49 with null settlement_value / 92 with null winning_bin → trigger does not fire (WHEN gates on authority='VERIFIED')
    # So no legitimate historical rows are rejected by this trigger.
    #
    # DROP + CREATE (not CREATE IF NOT EXISTS) so a future refactor that
    # tightens the predicate propagates to all legacy DBs on next init_schema.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_verified_insert_integrity")
        conn.execute(
            """
            CREATE TRIGGER settlements_verified_insert_integrity
            BEFORE INSERT ON settlements
            WHEN NEW.authority = 'VERIFIED'
              AND (NEW.settlement_value IS NULL
                   OR NEW.winning_bin IS NULL
                   OR NEW.winning_bin = '')
            BEGIN
                SELECT RAISE(ABORT, 'VERIFIED settlement INSERT requires non-null settlement_value + non-empty winning_bin');
            END;
            """
        )
        conn.execute("DROP TRIGGER IF EXISTS settlements_verified_update_integrity")
        conn.execute(
            """
            CREATE TRIGGER settlements_verified_update_integrity
            BEFORE UPDATE OF authority, settlement_value, winning_bin ON settlements
            WHEN NEW.authority = 'VERIFIED'
              AND (NEW.settlement_value IS NULL
                   OR NEW.winning_bin IS NULL
                   OR NEW.winning_bin = '')
            BEGIN
                SELECT RAISE(ABORT, 'VERIFIED settlement UPDATE requires non-null settlement_value + non-empty winning_bin');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_decision_group ON calibration_pairs(decision_group_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_group_lookup "
        "ON calibration_pairs(city, target_date, forecast_available_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_group_lookup_lead "
        "ON calibration_pairs(city, target_date, forecast_available_at, lead_days)"
    )
    _ensure_calibration_decision_group_lead_key(conn)

    _ensure_runtime_bootstrap_support_tables(conn)

    # Phase 5A (B069 / SD-1): add temperature_metric to position_current so the
    # portfolio_loader_view can emit per-row metric identity.
    # Zero-Data Golden Window precondition: this ALTER must only run on an empty table.
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
        logger.info(
            "phase5a_alter_position_current: row_count=%d before ADD COLUMN temperature_metric",
            row_count,
        )
        assert row_count == 0, (
            f"Phase 5A ALTER expects empty position_current (Zero-Data Golden Window); "
            f"found {row_count} rows"
        )
        conn.execute(
            "ALTER TABLE position_current ADD COLUMN temperature_metric TEXT NOT NULL DEFAULT 'high' "
            "CHECK (temperature_metric IN ('high', 'low'));"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists — idempotent re-run

    # B091 lower half: add decision_time_status column to selection_family_fact.
    # Additive column — safe on existing DBs (idempotent; OperationalError = already present).
    try:
        conn.execute(
            "ALTER TABLE selection_family_fact ADD COLUMN decision_time_status TEXT;"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists — idempotent re-run

    # P10D S3 (eve C2 inversion): add temperature_metric to legacy ensemble_snapshots.
    # ensemble_snapshots_v2 has zero runtime writers; skipping legacy writes for LOW
    # would destroy snapshot persistence (harvester joins on snapshot_id from legacy
    # table). Add temperature_metric column here so LOW rows are distinguishable.
    # Additive column — safe on existing DBs (idempotent; OperationalError = already present).
    try:
        conn.execute(
            "ALTER TABLE ensemble_snapshots ADD COLUMN temperature_metric TEXT NOT NULL DEFAULT 'high';"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists — idempotent re-run

    # Phase 2: apply v2 schema (idempotent — safe to run on every boot).
    from src.state.schema.v2_schema import apply_v2_schema as _apply_v2_schema
    _apply_v2_schema(conn)

    if own_conn:
        conn.commit()
        conn.close()


_CALIBRATION_DECISION_GROUP_DDL = """
CREATE TABLE calibration_decision_group (
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
)
"""

_CALIBRATION_DECISION_GROUP_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_calibration_decision_group_bucket
ON calibration_decision_group(cluster, season, lead_days)
"""


def _ensure_calibration_decision_group_lead_key(conn: sqlite3.Connection) -> None:
    """Migrate calibration groups if the legacy unique key lacks lead_days."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'calibration_decision_group'"
    ).fetchone()
    if row is None:
        return

    needs_migration = False
    for idx in conn.execute("PRAGMA index_list(calibration_decision_group)").fetchall():
        is_unique = bool(idx[2])
        if not is_unique:
            continue
        idx_name = idx[1]
        cols = [
            col[2]
            for col in conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
        ]
        if cols in (
            ["city", "target_date", "forecast_available_at"],
            ["city", "target_date", "forecast_available_at", "lead_days"],
        ):
            needs_migration = True
    if not needs_migration:
        return

    required_columns = {
        "group_id",
        "city",
        "target_date",
        "forecast_available_at",
        "cluster",
        "season",
        "lead_days",
        "settlement_value",
        "winning_range_label",
        "bias_corrected",
        "n_pair_rows",
        "n_positive_rows",
        "recorded_at",
    }
    existing_columns = {
        col[1] for col in conn.execute("PRAGMA table_info(calibration_decision_group)")
    }
    missing = sorted(required_columns - existing_columns)
    n_existing = conn.execute(
        "SELECT COUNT(*) FROM calibration_decision_group"
    ).fetchone()[0]
    if missing and n_existing:
        raise sqlite3.OperationalError(
            "Cannot migrate calibration_decision_group lead_days key: "
            f"non-empty legacy table is missing required columns {missing}"
        )
    if missing:
        backup_name = "calibration_decision_group__missing_cols_backup"
        logger.warning(
            f"Migrating empty calibration_decision_group schema to add {missing}. "
            f"Backing up existing schema to {backup_name} before rebuilding."
        )
        conn.execute(f"DROP TABLE IF EXISTS {backup_name}")
        conn.execute(f"ALTER TABLE calibration_decision_group RENAME TO {backup_name}")
        conn.execute(_CALIBRATION_DECISION_GROUP_DDL)
        conn.execute(_CALIBRATION_DECISION_GROUP_INDEX_DDL)
        return

    legacy_name = "calibration_decision_group__legacy_lead_key"
    conn.execute("SAVEPOINT calibration_decision_group_lead_key_migration")
    try:
        legacy_count = n_existing
        conn.execute(f"DROP TABLE IF EXISTS {legacy_name}")
        conn.execute(f"ALTER TABLE calibration_decision_group RENAME TO {legacy_name}")
        conn.execute(_CALIBRATION_DECISION_GROUP_DDL)
        conn.execute(
            f"""
            INSERT INTO calibration_decision_group (
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                lead_days,
                settlement_value,
                winning_range_label,
                bias_corrected,
                n_pair_rows,
                n_positive_rows,
                recorded_at
            )
            SELECT
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                lead_days,
                settlement_value,
                winning_range_label,
                bias_corrected,
                n_pair_rows,
                n_positive_rows,
                recorded_at
            FROM {legacy_name}
            """
        )
        conn.execute(_CALIBRATION_DECISION_GROUP_INDEX_DDL)
        new_count = conn.execute(
            "SELECT COUNT(*) FROM calibration_decision_group"
        ).fetchone()[0]
        if new_count != legacy_count:
            raise sqlite3.IntegrityError(
                "calibration_decision_group migration row-count mismatch: "
                f"{legacy_count} legacy rows, {new_count} copied rows"
            )
        conn.execute(f"DROP TABLE {legacy_name}")
        conn.execute("RELEASE SAVEPOINT calibration_decision_group_lead_key_migration")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT calibration_decision_group_lead_key_migration")
        conn.execute("RELEASE SAVEPOINT calibration_decision_group_lead_key_migration")
        raise


def _ensure_runtime_bootstrap_support_tables(conn: sqlite3.Connection) -> None:
    """Apply canonical architecture kernel schema."""
    apply_architecture_kernel_schema(conn)


def init_backtest_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create derived backtest/reporting tables. Idempotent."""
    own_conn = conn is None
    if own_conn:
        conn = get_backtest_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL CHECK (
                lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            authority_scope TEXT NOT NULL CHECK (
                authority_scope = 'diagnostic_non_promotion'
            ),
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_outcome_comparison (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (
                lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
            ),
            subject_id TEXT NOT NULL,
            subject_kind TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            derived_wu_outcome INTEGER,
            actual_trade_outcome INTEGER,
            actual_pnl REAL,
            truth_source TEXT NOT NULL,
            divergence_status TEXT NOT NULL CHECK (
                divergence_status IN (
                    'not_applicable',
                    'match',
                    'wu_win_trade_loss',
                    'wu_loss_trade_win',
                    'trade_unresolved',
                    'wu_missing',
                    'bin_unparseable',
                    'ambiguous_subject',
                    'orphan_trade_decision',
                    'scored',
                    'no_snapshot',
                    'no_day0_nowcast_excluded',
                    'invalid_p_raw_json',
                    'empty_p_raw',
                    'label_count_mismatch',
                    'no_clob_best_bid',
                    'fdr_scan_failed',
                    'no_hypotheses'
                )
            ),
            decision_reference_source TEXT,
            forecast_reference_id TEXT,
            evidence_json TEXT NOT NULL,
            missing_reason_json TEXT NOT NULL,
            authority_scope TEXT NOT NULL DEFAULT 'diagnostic_non_promotion'
                CHECK (authority_scope = 'diagnostic_non_promotion'),
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_lane_city_date
            ON backtest_outcome_comparison(lane, city, target_date);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_subject
            ON backtest_outcome_comparison(subject_id);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_divergence
            ON backtest_outcome_comparison(divergence_status);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_run
            ON backtest_outcome_comparison(run_id);
    """)
    conn.commit()
    if own_conn:
        conn.close()



def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_has_unique_key(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool:
    """Return whether *table* has a UNIQUE index exactly matching *columns*."""
    for index_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if not bool(index_row[2]):
            continue
        index_name = index_row[1]
        index_columns = tuple(
            column_row[2]
            for column_row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        )
        if index_columns == columns:
            return True
    return False


def _view_exists(conn: sqlite3.Connection, view: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
        (view,),
    ).fetchone()
    return row is not None


def _table_or_view_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Return True if `name` exists as either a TABLE or a VIEW."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


_FORWARD_MARKET_EVENT_COLUMNS = (
    "market_slug",
    "city",
    "target_date",
    "temperature_metric",
    "condition_id",
    "token_id",
    "range_label",
    "range_low",
    "range_high",
    "outcome",
    "created_at",
    "recorded_at",
)
_FORWARD_PRICE_HISTORY_COLUMNS = (
    "market_slug",
    "token_id",
    "price",
    "recorded_at",
    "hours_since_open",
    "hours_to_resolution",
)
_FULL_LINKAGE_PRICE_HISTORY_COLUMNS = (
    "market_slug",
    "token_id",
    "price",
    "recorded_at",
    "hours_since_open",
    "hours_to_resolution",
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
    "snapshot_id",
    "condition_id",
)
_FULL_LINKAGE_PRICE_REQUIRED_COLUMNS = (
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
    "snapshot_id",
    "condition_id",
)
_FORWARD_MARKET_REQUIRED_TABLES = (
    "market_events_v2",
    "market_price_history",
)
_MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES = ("market_topology_state",)
_MARKET_TOPOLOGY_STATE_REQUIRED_COLUMNS = (
    "topology_id",
    "scope_key",
    "market_family",
    "event_id",
    "condition_id",
    "question_id",
    "city_id",
    "city_timezone",
    "target_local_date",
    "temperature_metric",
    "physical_quantity",
    "observation_field",
    "data_version",
    "token_ids_json",
    "bin_topology_hash",
    "gamma_captured_at",
    "gamma_updated_at",
    "source_contract_status",
    "source_contract_reason",
    "authority_status",
    "status",
    "expires_at",
    "provenance_json",
    "recorded_at",
)
_SOURCE_CONTRACT_AUDIT_TABLES = ("source_contract_audit_events",)
_SOURCE_CONTRACT_AUDIT_REQUIRED_COLUMNS = (
    "audit_id",
    "checked_at_utc",
    "scan_authority",
    "report_status",
    "severity",
    "event_id",
    "slug",
    "title",
    "city",
    "target_date",
    "temperature_metric",
    "source_contract_status",
    "source_contract_reason",
    "configured_source_family",
    "configured_station_id",
    "observed_source_family",
    "observed_station_id",
    "resolution_sources_json",
    "source_contract_json",
    "payload_hash",
    "created_at",
)
_SOURCE_CONTRACT_AUDIT_AUTHORITIES = frozenset({
    "VERIFIED",
    "FIXTURE",
    "STALE_CACHE",
    "EMPTY_FALLBACK",
    "NEVER_FETCHED",
})
_SOURCE_CONTRACT_AUDIT_SEVERITIES = frozenset({
    "OK",
    "WARN",
    "ALERT",
    "DATA_UNAVAILABLE",
})
_SOURCE_CONTRACT_AUDIT_REPORT_STATUSES = _SOURCE_CONTRACT_AUDIT_SEVERITIES
_SOURCE_CONTRACT_AUDIT_STATUSES = frozenset({
    "MATCH",
    "MISSING",
    "AMBIGUOUS",
    "MISMATCH",
    "UNSUPPORTED",
    "UNKNOWN",
    "QUARANTINED",
})
_SETTLEMENT_V2_COLUMNS = (
    "city",
    "target_date",
    "temperature_metric",
    "market_slug",
    "winning_bin",
    "settlement_value",
    "settlement_source",
    "settled_at",
    "authority",
    "provenance_json",
    "recorded_at",
)
_MARKET_EVENT_OUTCOME_VALUES = frozenset({"YES", "NO"})
_MARKET_EVENT_OUTCOME_UPDATE_SQL = """
    UPDATE market_events_v2
    SET outcome = ?
    WHERE market_slug = ?
      AND condition_id = ?
      AND token_id = ?
      AND city = ?
      AND target_date = ?
      AND temperature_metric = ?
"""


def _forward_clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _forward_city_name(value) -> str | None:
    name = getattr(value, "name", value)
    return _forward_clean_str(name)


def _forward_metric(value) -> str | None:
    metric = _forward_clean_str(value)
    if metric is None:
        return None
    metric = metric.lower()
    return metric if metric in {"high", "low"} else None


def _forward_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _forward_price(value) -> float | None:
    price = _forward_float(value)
    if price is None or not 0.0 <= price <= 1.0:
        return None
    return price


def _forward_values_equal(left, right) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if isinstance(right, float):
        try:
            return abs(float(left) - right) < 1e-12
        except (TypeError, ValueError):
            return False
    return str(left) == str(right)


def _forward_existing_matches(existing, expected: dict, *, ignore: set[str] | None = None) -> bool:
    ignored = ignore or set()
    for key, value in expected.items():
        if key in ignored:
            continue
        if not _forward_values_equal(existing[key], value):
            return False
    return True


def _insert_forward_market_event(conn: sqlite3.Connection, values: dict) -> str:
    existing = conn.execute(
        """
        SELECT market_slug, city, target_date, temperature_metric, condition_id,
               token_id, range_label, range_low, range_high, outcome, created_at,
               recorded_at
        FROM market_events_v2
        WHERE market_slug = ? AND condition_id = ?
        """,
        (values["market_slug"], values["condition_id"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FORWARD_MARKET_EVENT_COLUMNS, tuple(existing)))
        if _forward_clean_str(existing_values.get("outcome")) is not None:
            return "resolved_existing"
        if _forward_existing_matches(existing_values, values, ignore={"recorded_at", "outcome"}):
            return "unchanged"
        return "conflict"

    conn.execute(
        """
        INSERT INTO market_events_v2 (
            market_slug, city, target_date, temperature_metric, condition_id,
            token_id, range_label, range_low, range_high, outcome, created_at,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FORWARD_MARKET_EVENT_COLUMNS),
    )
    return "inserted"


def _insert_forward_price_history(conn: sqlite3.Connection, values: dict) -> str:
    existing = conn.execute(
        """
        SELECT market_slug, token_id, price, recorded_at, hours_since_open,
               hours_to_resolution
        FROM market_price_history
        WHERE token_id = ? AND recorded_at = ?
        """,
        (values["token_id"], values["recorded_at"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FORWARD_PRICE_HISTORY_COLUMNS, tuple(existing)))
        if _forward_existing_matches(existing_values, values):
            return "unchanged"
        return "conflict"

    conn.execute(
        """
        INSERT INTO market_price_history (
            market_slug, token_id, price, recorded_at, hours_since_open,
            hours_to_resolution
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FORWARD_PRICE_HISTORY_COLUMNS),
    )
    return "inserted"


def _insert_full_linkage_price_history(conn: sqlite3.Connection, values: dict) -> str:
    existing = conn.execute(
        """
        SELECT market_slug, token_id, price, recorded_at, hours_since_open,
               hours_to_resolution, market_price_linkage, source, best_bid,
               best_ask, raw_orderbook_hash, snapshot_id, condition_id
        FROM market_price_history
        WHERE token_id = ? AND recorded_at = ?
        """,
        (values["token_id"], values["recorded_at"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FULL_LINKAGE_PRICE_HISTORY_COLUMNS, tuple(existing)))
        if _forward_existing_matches(existing_values, values):
            return "unchanged"
        return "conflict"

    conn.execute(
        """
        INSERT INTO market_price_history (
            market_slug, token_id, price, recorded_at, hours_since_open,
            hours_to_resolution, market_price_linkage, source, best_bid,
            best_ask, raw_orderbook_hash, snapshot_id, condition_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FULL_LINKAGE_PRICE_HISTORY_COLUMNS),
    )
    return "inserted"


def _mid_price(best_bid: float, best_ask: float) -> float | None:
    if best_bid > best_ask:
        return None
    return (best_bid + best_ask) / 2.0


def log_executable_snapshot_market_price_linkage(
    conn: sqlite3.Connection | None,
    *,
    snapshot_id: str,
    source: str = "CLOB_ORDERBOOK",
    recorded_at: str | None = None,
) -> dict:
    """Persist full CLOB top-of-book linkage from an executable snapshot.

    The scanner writer records price-only Gamma substrate. This helper records
    the CLOB orderbook evidence already captured for an executable entry
    snapshot. It never opens a default DB and never commits; callers own the
    transaction boundary.
    """
    table = "market_price_history"
    snapshot_table = "executable_market_snapshots"
    if conn is None:
        return {"status": "skipped_no_connection", "tables": (table, snapshot_table)}

    snapshot_id_value = _forward_clean_str(snapshot_id)
    if snapshot_id_value is None:
        return {"status": "refused_missing_snapshot_id", "tables": (table, snapshot_table)}

    missing_tables = [
        required
        for required in (table, snapshot_table)
        if not _table_exists(conn, required)
    ]
    if missing_tables:
        return {
            "status": "skipped_missing_tables",
            "tables": (table, snapshot_table),
            "missing_tables": tuple(missing_tables),
        }

    missing_columns = tuple(
        sorted(set(_FULL_LINKAGE_PRICE_REQUIRED_COLUMNS) - _table_columns(conn, table))
    )
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }

    saved_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT snapshot_id, event_slug, condition_id, selected_outcome_token_id,
                   orderbook_top_bid, orderbook_top_ask, raw_orderbook_hash,
                   captured_at
            FROM executable_market_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id_value,),
        ).fetchone()
    finally:
        conn.row_factory = saved_factory
    if row is None:
        return {"status": "refused_missing_snapshot", "snapshot_id": snapshot_id_value}

    market_slug = _forward_clean_str(row["event_slug"])
    token_id = _forward_clean_str(row["selected_outcome_token_id"])
    condition_id = _forward_clean_str(row["condition_id"])
    best_bid = _forward_price(row["orderbook_top_bid"])
    best_ask = _forward_price(row["orderbook_top_ask"])
    raw_orderbook_hash = _forward_clean_str(row["raw_orderbook_hash"])
    recorded_at_value = _forward_clean_str(recorded_at) or _forward_clean_str(row["captured_at"])
    source_value = _forward_clean_str(source)
    if not (
        market_slug
        and token_id
        and condition_id
        and best_bid is not None
        and best_ask is not None
        and raw_orderbook_hash
        and recorded_at_value
        and source_value
    ):
        return {"status": "refused_missing_snapshot_facts", "snapshot_id": snapshot_id_value}

    price = _mid_price(best_bid, best_ask)
    if price is None:
        return {
            "status": "refused_crossed_orderbook",
            "snapshot_id": snapshot_id_value,
            "best_bid": best_bid,
            "best_ask": best_ask,
        }

    values = {
        "market_slug": market_slug,
        "token_id": token_id,
        "price": price,
        "recorded_at": recorded_at_value,
        "hours_since_open": None,
        "hours_to_resolution": None,
        "market_price_linkage": "full",
        "source": source_value,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "raw_orderbook_hash": raw_orderbook_hash,
        "snapshot_id": snapshot_id_value,
        "condition_id": condition_id,
    }
    result = _insert_full_linkage_price_history(conn, values)
    return {
        "status": result,
        "table": table,
        "snapshot_id": snapshot_id_value,
        "token_id": token_id,
        "recorded_at": recorded_at_value,
    }


def log_forward_market_substrate(
    conn: sqlite3.Connection | None,
    *,
    markets: Iterable[dict],
    recorded_at: str,
    scan_authority: str,
) -> dict:
    """Persist Gamma scanner market identity and price observations.

    This is forward-only scanner substrate. It is not CLOB VWMP/orderbook truth,
    settlement truth, or live wiring; callers must supply an explicit DB
    connection and a fresh VERIFIED scan authority.
    """
    if conn is None:
        return {"status": "skipped_no_connection", "tables": _FORWARD_MARKET_REQUIRED_TABLES}

    if str(scan_authority or "").strip().upper() != "VERIFIED":
        return {
            "status": "refused_degraded_authority",
            "tables": _FORWARD_MARKET_REQUIRED_TABLES,
            "scan_authority": scan_authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at)
    if recorded_at_value is None:
        return {"status": "refused_missing_recorded_at", "tables": _FORWARD_MARKET_REQUIRED_TABLES}

    missing_tables = [
        table for table in _FORWARD_MARKET_REQUIRED_TABLES if not _table_exists(conn, table)
    ]
    if missing_tables:
        return {
            "status": "skipped_missing_tables",
            "tables": _FORWARD_MARKET_REQUIRED_TABLES,
            "missing_tables": tuple(missing_tables),
        }

    required_columns = {
        "market_events_v2": set(_FORWARD_MARKET_EVENT_COLUMNS),
        "market_price_history": set(_FORWARD_PRICE_HISTORY_COLUMNS),
    }
    missing_columns = {
        table: tuple(sorted(required_columns[table] - _table_columns(conn, table)))
        for table in required_columns
    }
    missing_columns = {table: columns for table, columns in missing_columns.items() if columns}
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "tables": _FORWARD_MARKET_REQUIRED_TABLES,
            "missing_columns": missing_columns,
        }

    counts = {
        "market_events_inserted": 0,
        "market_events_unchanged": 0,
        "market_events_conflicted": 0,
        "price_rows_inserted": 0,
        "price_rows_unchanged": 0,
        "price_rows_conflicted": 0,
        "markets_skipped_missing_facts": 0,
        "outcomes_skipped_missing_facts": 0,
        "prices_skipped_missing_facts": 0,
        "outcomes_skipped_with_outcome_fact": 0,
    }

    for market in markets:
        if not isinstance(market, dict):
            counts["markets_skipped_missing_facts"] += 1
            continue
        market_slug = _forward_clean_str(market.get("slug"))
        city = _forward_city_name(market.get("city"))
        target_date = _forward_clean_str(market.get("target_date"))
        temperature_metric = _forward_metric(market.get("temperature_metric"))
        if not (market_slug and city and target_date and temperature_metric):
            counts["markets_skipped_missing_facts"] += 1
            continue

        hours_since_open = _forward_float(market.get("hours_since_open"))
        hours_to_resolution = _forward_float(market.get("hours_to_resolution"))

        for outcome in market.get("outcomes") or ():
            if not isinstance(outcome, dict):
                counts["outcomes_skipped_missing_facts"] += 1
                continue
            if _forward_clean_str(outcome.get("outcome")) is not None:
                counts["outcomes_skipped_with_outcome_fact"] += 1
                continue

            condition_id = _forward_clean_str(outcome.get("condition_id"))
            yes_token = _forward_clean_str(outcome.get("token_id"))
            range_label = _forward_clean_str(outcome.get("title"))
            range_low = _forward_float(outcome.get("range_low"))
            range_high = _forward_float(outcome.get("range_high"))
            if not (
                condition_id
                and yes_token
                and range_label
                and (range_low is not None or range_high is not None)
            ):
                counts["outcomes_skipped_missing_facts"] += 1
                continue

            event_values = {
                "market_slug": market_slug,
                "city": city,
                "target_date": target_date,
                "temperature_metric": temperature_metric,
                "condition_id": condition_id,
                "token_id": yes_token,
                "range_label": range_label,
                "range_low": range_low,
                "range_high": range_high,
                "outcome": None,
                "created_at": _forward_clean_str(
                    market.get("created_at") or outcome.get("market_start_at")
                ),
                "recorded_at": recorded_at_value,
            }
            event_result = _insert_forward_market_event(conn, event_values)
            if event_result == "resolved_existing":
                counts["outcomes_skipped_with_outcome_fact"] += 1
                continue
            if event_result == "conflict":
                counts["market_events_conflicted"] += 1
                continue
            counts[f"market_events_{event_result}"] += 1

            for token_key, price_key in (("token_id", "price"), ("no_token_id", "no_price")):
                token_id = _forward_clean_str(outcome.get(token_key))
                price = _forward_price(outcome.get(price_key))
                if token_id is None or price is None:
                    counts["prices_skipped_missing_facts"] += 1
                    continue
                price_values = {
                    "market_slug": market_slug,
                    "token_id": token_id,
                    "price": price,
                    "recorded_at": recorded_at_value,
                    "hours_since_open": hours_since_open,
                    "hours_to_resolution": hours_to_resolution,
                }
                price_result = _insert_forward_price_history(conn, price_values)
                price_key_name = "price_rows_conflicted" if price_result == "conflict" else f"price_rows_{price_result}"
                counts[price_key_name] += 1

    status = "written"
    if counts["market_events_conflicted"] or counts["price_rows_conflicted"]:
        status = "written_with_conflicts"
    elif (
        counts["market_events_inserted"] == 0
        and counts["price_rows_inserted"] == 0
        and (counts["market_events_unchanged"] or counts["price_rows_unchanged"])
    ):
        status = "unchanged"
    elif counts["market_events_inserted"] == 0 and counts["price_rows_inserted"] == 0:
        status = "skipped_no_valid_rows"

    return {
        "status": status,
        "tables": _FORWARD_MARKET_REQUIRED_TABLES,
        **counts,
    }


def log_market_source_contract_topology_facts(
    conn: sqlite3.Connection | None,
    *,
    markets: Iterable[dict],
    recorded_at: str,
    scan_authority: str,
) -> dict:
    """Persist scanner source-contract proof into market_topology_state.

    The writer is explicit-connection only. It does not open the default DB,
    create tables, migrate schema, commit, or change market eligibility.
    """
    if conn is None:
        return {"status": "skipped_no_connection", "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES}

    if str(scan_authority or "").strip().upper() != "VERIFIED":
        return {
            "status": "refused_degraded_authority",
            "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
            "scan_authority": scan_authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at)
    if recorded_at_value is None:
        return {"status": "refused_missing_recorded_at", "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES}

    missing_tables = [
        table for table in _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES if not _table_exists(conn, table)
    ]
    if missing_tables:
        return {
            "status": "skipped_missing_tables",
            "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
            "missing_tables": tuple(missing_tables),
        }

    missing_columns = tuple(
        sorted(set(_MARKET_TOPOLOGY_STATE_REQUIRED_COLUMNS) - _table_columns(conn, "market_topology_state"))
    )
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
            "missing_columns": {"market_topology_state": missing_columns},
        }

    counts = {
        "topology_rows_written": 0,
        "markets_skipped_missing_facts": 0,
        "markets_skipped_source_contract_status": 0,
        "outcomes_skipped_missing_facts": 0,
    }

    for market in markets:
        if not isinstance(market, dict):
            counts["markets_skipped_missing_facts"] += 1
            continue
        source_contract = market.get("source_contract") or {}
        if not isinstance(source_contract, dict):
            counts["markets_skipped_source_contract_status"] += 1
            continue
        source_contract_status = _forward_clean_str(source_contract.get("status"))
        if source_contract_status != "MATCH":
            counts["markets_skipped_source_contract_status"] += 1
            continue

        market_slug = _forward_clean_str(market.get("slug"))
        event_id = _forward_clean_str(market.get("event_id"))
        city_obj = market.get("city")
        city_name = _forward_city_name(city_obj)
        city_timezone = _forward_clean_str(
            market.get("city_timezone") or getattr(city_obj, "timezone", None)
        )
        target_date = _forward_clean_str(market.get("target_date"))
        temperature_metric = _forward_metric(market.get("temperature_metric"))
        if not (market_slug and city_name and target_date and temperature_metric):
            counts["markets_skipped_missing_facts"] += 1
            continue

        data_version = _forward_clean_str(market.get("data_version")) or "gamma_source_contract_v1"
        observation_field = (
            "daily_max_temperature" if temperature_metric == "high" else "daily_min_temperature"
        )
        resolution_sources = list(source_contract.get("resolution_sources") or market.get("resolution_sources") or [])

        for outcome in market.get("outcomes") or ():
            if not isinstance(outcome, dict):
                counts["outcomes_skipped_missing_facts"] += 1
                continue
            condition_id = _forward_clean_str(outcome.get("condition_id"))
            if condition_id is None:
                counts["outcomes_skipped_missing_facts"] += 1
                continue
            question_id = _forward_clean_str(outcome.get("question_id"))
            token_ids = [
                token_id
                for token_id in (
                    _forward_clean_str(outcome.get("token_id")),
                    _forward_clean_str(outcome.get("no_token_id")),
                )
                if token_id is not None
            ]
            provenance = {
                "writer": "log_market_source_contract_topology_facts",
                "source": "gamma_market_scanner",
                "recorded_at": recorded_at_value,
                "market_slug": market_slug,
                "event_id": event_id,
                "event_slug": market_slug,
                "condition_id": condition_id,
                "question_id": question_id,
                "outcome_title": _forward_clean_str(outcome.get("title")),
                "city": city_name,
                "target_date": target_date,
                "temperature_metric": temperature_metric,
                "resolution_sources": resolution_sources,
                "source_contract": source_contract,
            }
            topology_id = "market_source_contract:{}:{}:{}:{}:{}".format(
                market_slug,
                condition_id,
                city_name,
                target_date,
                temperature_metric,
            )
            write_market_topology_state(
                conn,
                topology_id=topology_id,
                market_family="weather_temperature",
                condition_id=condition_id,
                status="CURRENT",
                source_contract_status="MATCH",
                authority_status="VERIFIED",
                event_id=event_id,
                question_id=question_id,
                city_id=city_name,
                city_timezone=city_timezone,
                target_local_date=target_date,
                temperature_metric=temperature_metric,
                physical_quantity="temperature",
                observation_field=observation_field,
                data_version=data_version,
                token_ids_json=token_ids,
                source_contract_reason=_forward_clean_str(source_contract.get("reason")),
                provenance_json=provenance,
            )
            conn.execute(
                "UPDATE market_topology_state SET recorded_at = ? WHERE topology_id = ?",
                (recorded_at_value, topology_id),
            )
            counts["topology_rows_written"] += 1

    status = "written" if counts["topology_rows_written"] else "skipped_no_valid_rows"
    return {
        "status": status,
        "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
        **counts,
    }


def append_source_contract_audit_events(
    conn: sqlite3.Connection | None,
    *,
    report: dict,
) -> dict:
    """Append source-contract watch evidence without affecting eligibility."""
    if conn is None:
        return {"status": "skipped_no_connection", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}
    if not isinstance(report, dict):
        return {"status": "refused_invalid_report", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}

    missing_tables = [
        table for table in _SOURCE_CONTRACT_AUDIT_TABLES if not _table_exists(conn, table)
    ]
    if missing_tables:
        return {
            "status": "skipped_missing_tables",
            "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
            "missing_tables": tuple(missing_tables),
        }

    missing_columns = tuple(
        sorted(
            set(_SOURCE_CONTRACT_AUDIT_REQUIRED_COLUMNS)
            - _table_columns(conn, "source_contract_audit_events")
        )
    )
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
            "missing_columns": {"source_contract_audit_events": missing_columns},
        }

    checked_at_utc = _forward_clean_str(report.get("checked_at_utc"))
    scan_authority = _forward_clean_str(report.get("authority"))
    if checked_at_utc is None or scan_authority is None:
        return {"status": "refused_missing_scan_metadata", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}
    if scan_authority not in _SOURCE_CONTRACT_AUDIT_AUTHORITIES:
        return {
            "status": "refused_invalid_scan_authority",
            "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
            "scan_authority": scan_authority,
        }
    events = report.get("events") or []
    if not isinstance(events, list):
        return {"status": "refused_invalid_report", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}

    counts = {
        "audit_rows_inserted": 0,
        "audit_rows_unchanged": 0,
        "events_skipped_missing_facts": 0,
        "events_refused_invalid_facts": 0,
    }
    report_status = _forward_clean_str(report.get("status"))
    if report_status is not None and report_status not in _SOURCE_CONTRACT_AUDIT_REPORT_STATUSES:
        return {
            "status": "refused_invalid_report_status",
            "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
            "report_status": report_status,
        }

    for event in events:
        if not isinstance(event, dict):
            counts["events_skipped_missing_facts"] += 1
            continue
        source_contract = event.get("source_contract") or {}
        if not isinstance(source_contract, dict):
            counts["events_skipped_missing_facts"] += 1
            continue
        event_id = _forward_clean_str(event.get("event_id") or event.get("slug"))
        source_contract_status = _forward_clean_str(source_contract.get("status")) or "UNKNOWN"
        severity = _forward_clean_str(event.get("severity")) or "WARN"
        if event_id is None:
            counts["events_skipped_missing_facts"] += 1
            continue
        if severity not in _SOURCE_CONTRACT_AUDIT_SEVERITIES:
            counts["events_refused_invalid_facts"] += 1
            continue
        if source_contract_status not in _SOURCE_CONTRACT_AUDIT_STATUSES:
            counts["events_refused_invalid_facts"] += 1
            continue

        resolution_sources = list(source_contract.get("resolution_sources") or [])
        resolution_sources_json = json.dumps(
            resolution_sources,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        source_contract_json = json.dumps(
            source_contract,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        payload = {
            "checked_at_utc": checked_at_utc,
            "scan_authority": scan_authority,
            "report_status": report_status,
            "event_id": event_id,
            "slug": _forward_clean_str(event.get("slug")),
            "city": _forward_clean_str(event.get("city")),
            "target_date": _forward_clean_str(event.get("target_date")),
            "temperature_metric": _forward_metric(event.get("temperature_metric")),
            "severity": severity,
            "source_contract": source_contract,
        }
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        audit_id = hashlib.sha256(
            f"{checked_at_utc}|{event_id}|{payload_hash}".encode("utf-8")
        ).hexdigest()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO source_contract_audit_events (
                audit_id, checked_at_utc, scan_authority, report_status, severity,
                event_id, slug, title, city, target_date, temperature_metric,
                source_contract_status, source_contract_reason,
                configured_source_family, configured_station_id,
                observed_source_family, observed_station_id,
                resolution_sources_json, source_contract_json, payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                checked_at_utc,
                scan_authority,
                report_status,
                severity,
                event_id,
                _forward_clean_str(event.get("slug")),
                _forward_clean_str(event.get("title")),
                _forward_clean_str(event.get("city")),
                _forward_clean_str(event.get("target_date")),
                _forward_metric(event.get("temperature_metric")),
                source_contract_status,
                _forward_clean_str(source_contract.get("reason")),
                _forward_clean_str(source_contract.get("configured_source_family")),
                _forward_clean_str(source_contract.get("configured_station_id")),
                _forward_clean_str(source_contract.get("source_family")),
                _forward_clean_str(source_contract.get("station_id")),
                resolution_sources_json,
                source_contract_json,
                payload_hash,
            ),
        )
        if cursor.rowcount:
            counts["audit_rows_inserted"] += 1
        else:
            counts["audit_rows_unchanged"] += 1

    status = "written" if counts["audit_rows_inserted"] else "skipped_no_valid_rows"
    if counts["audit_rows_unchanged"] and not counts["audit_rows_inserted"]:
        status = "unchanged"
    return {
        "status": status,
        "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
        **counts,
    }


@capability("settlement_write", lease=True)
def log_settlement_v2(
    conn: sqlite3.Connection | None,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_slug: str | None,
    winning_bin: str | None,
    settlement_value: float | None,
    settlement_source: str | None,
    settled_at: str | None,
    authority: str,
    provenance: dict | None = None,
    recorded_at: str | None = None,
) -> dict:
    """Mirror harvester settlement truth into settlements_v2.

    The helper is intentionally substrate-only: it never opens a default DB,
    never creates/migrates tables, never commits, and never infers missing
    market identity.
    """
    table = "settlements_v2"
    if conn is None:
        return {"status": "skipped_no_connection", "table": table}
    if not _table_exists(conn, table):
        return {"status": "skipped_missing_table", "table": table}

    required_columns = set(_SETTLEMENT_V2_COLUMNS)
    missing_columns = tuple(sorted(required_columns - _table_columns(conn, table)))
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }
    unique_key = ("city", "target_date", "temperature_metric")
    if not _table_has_unique_key(conn, table, unique_key):
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_unique_key": unique_key,
        }

    clean_city = _forward_clean_str(city)
    clean_target_date = _forward_clean_str(target_date)
    clean_metric = _forward_metric(temperature_metric)
    clean_market_slug = _forward_clean_str(market_slug)
    clean_authority = _forward_clean_str(authority)
    if not (clean_city and clean_target_date and clean_metric and clean_market_slug):
        return {
            "status": "refused_missing_identity",
            "table": table,
            "missing_fields": tuple(
                field
                for field, value in (
                    ("city", clean_city),
                    ("target_date", clean_target_date),
                    ("temperature_metric", clean_metric),
                    ("market_slug", clean_market_slug),
                )
                if not value
            ),
        }
    if clean_authority not in {"VERIFIED", "UNVERIFIED", "QUARANTINED"}:
        return {
            "status": "refused_invalid_authority",
            "table": table,
            "authority": authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at) or datetime.now(timezone.utc).isoformat()
    provenance_payload = dict(provenance or {})
    provenance_payload.setdefault("legacy_table", "settlements")
    provenance_json = json.dumps(provenance_payload, sort_keys=True, default=str)

    try:
        conn.execute(
            """
            INSERT INTO settlements_v2 (
                city, target_date, temperature_metric, market_slug, winning_bin,
                settlement_value, settlement_source, settled_at, authority,
                provenance_json, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                market_slug=excluded.market_slug,
                winning_bin=excluded.winning_bin,
                settlement_value=excluded.settlement_value,
                settlement_source=excluded.settlement_source,
                settled_at=excluded.settled_at,
                authority=excluded.authority,
                provenance_json=excluded.provenance_json,
                recorded_at=excluded.recorded_at
            """,
            (
                clean_city,
                clean_target_date,
                clean_metric,
                clean_market_slug,
                winning_bin,
                settlement_value,
                _forward_clean_str(settlement_source),
                _forward_clean_str(settled_at),
                clean_authority,
                provenance_json,
                recorded_at_value,
            ),
        )
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "schema_error": str(exc),
        }
    return {"status": "written", "table": table}


def _market_event_outcome_public_result(result: dict) -> dict:
    """Strip internal SQL parameters before exposing helper results."""
    return {key: value for key, value in result.items() if key != "update_values"}


def _prepare_market_event_outcome_v2_update(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    condition_id: str | None,
    token_id: str | None,
    outcome: str,
) -> dict:
    table = "market_events_v2"
    if conn is None:
        return {"status": "skipped_no_connection", "table": table}
    if not _table_exists(conn, table):
        return {"status": "skipped_missing_table", "table": table}

    required_columns = set(_FORWARD_MARKET_EVENT_COLUMNS)
    missing_columns = tuple(sorted(required_columns - _table_columns(conn, table)))
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }
    unique_key = ("market_slug", "condition_id")
    if not _table_has_unique_key(conn, table, unique_key):
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_unique_key": unique_key,
        }

    clean_market_slug = _forward_clean_str(market_slug)
    clean_city = _forward_city_name(city)
    clean_target_date = _forward_clean_str(target_date)
    clean_metric = _forward_metric(temperature_metric)
    clean_condition_id = _forward_clean_str(condition_id)
    clean_token_id = _forward_clean_str(token_id)
    clean_outcome = _forward_clean_str(outcome)
    if clean_outcome is not None:
        clean_outcome = clean_outcome.upper()

    identity_fields = (
        ("market_slug", clean_market_slug),
        ("city", clean_city),
        ("target_date", clean_target_date),
        ("temperature_metric", clean_metric),
        ("condition_id", clean_condition_id),
        ("token_id", clean_token_id),
    )
    if not all(value for _, value in identity_fields):
        return {
            "status": "refused_missing_identity",
            "table": table,
            "missing_fields": tuple(field for field, value in identity_fields if not value),
        }
    if clean_outcome not in _MARKET_EVENT_OUTCOME_VALUES:
        return {
            "status": "refused_invalid_outcome",
            "table": table,
            "outcome": outcome,
        }

    try:
        row = conn.execute(
            """
            SELECT city, target_date, temperature_metric, token_id, outcome
            FROM market_events_v2
            WHERE market_slug = ? AND condition_id = ?
            """,
            (clean_market_slug, clean_condition_id),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "schema_error": str(exc),
        }

    if row is None:
        return {
            "status": "skipped_missing_market_event",
            "table": table,
            "market_slug": clean_market_slug,
            "condition_id": clean_condition_id,
        }

    existing = dict(zip(("city", "target_date", "temperature_metric", "token_id", "outcome"), tuple(row)))
    mismatches = tuple(
        field
        for field, expected in (
            ("city", clean_city),
            ("target_date", clean_target_date),
            ("temperature_metric", clean_metric),
            ("token_id", clean_token_id),
        )
        if str(existing.get(field)) != str(expected)
    )
    if mismatches:
        return {
            "status": "refused_identity_mismatch",
            "table": table,
            "mismatched_fields": mismatches,
        }

    existing_outcome = _forward_clean_str(existing.get("outcome"))
    if existing_outcome is not None:
        existing_outcome = existing_outcome.upper()
        if existing_outcome == clean_outcome:
            return {"status": "unchanged", "table": table}
        return {
            "status": "conflict_existing_outcome",
            "table": table,
            "existing_outcome": existing_outcome,
            "incoming_outcome": clean_outcome,
        }

    return {
        "status": "ready",
        "table": table,
        "update_values": (
            clean_outcome,
            clean_market_slug,
            clean_condition_id,
            clean_token_id,
            clean_city,
            clean_target_date,
            clean_metric,
        ),
    }


def log_market_event_outcome_v2(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    condition_id: str | None,
    token_id: str | None,
    outcome: str,
) -> dict:
    """Write a resolved child-market outcome onto existing market_events_v2 substrate.

    This helper updates only an exact scanner-produced row. It never creates
    tables, inserts missing market identities, opens a default DB, commits, or
    overwrites a conflicting resolved outcome.
    """
    prepared = _prepare_market_event_outcome_v2_update(
        conn,
        market_slug=market_slug,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        condition_id=condition_id,
        token_id=token_id,
        outcome=outcome,
    )
    if prepared.get("status") != "ready":
        return _market_event_outcome_public_result(prepared)

    try:
        conn.execute(
            _MARKET_EVENT_OUTCOME_UPDATE_SQL,
            prepared["update_values"],
        )
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": "market_events_v2",
            "schema_error": str(exc),
        }
    return {"status": "written", "table": "market_events_v2"}


def log_market_event_outcomes_v2(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    outcomes: Iterable[dict],
) -> dict:
    """Batch-update market_events_v2 outcomes using exact child identities."""
    table = "market_events_v2"
    counts = {
        "written": 0,
        "unchanged": 0,
        "skipped_missing_market_event": 0,
        "refused_missing_identity": 0,
        "refused_identity_mismatch": 0,
        "conflict_existing_outcome": 0,
        "refused_invalid_outcome": 0,
        "skipped_invalid_schema": 0,
        "skipped_missing_table": 0,
        "skipped_no_connection": 0,
    }
    prepared_updates: list[dict] = []
    details: list[dict] = []
    for outcome_row in outcomes:
        if not isinstance(outcome_row, dict):
            result = {
                "status": "refused_missing_identity",
                "table": table,
                "missing_fields": ("outcome",),
            }
        else:
            result = _prepare_market_event_outcome_v2_update(
                conn,
                market_slug=market_slug,
                city=city,
                target_date=target_date,
                temperature_metric=temperature_metric,
                condition_id=outcome_row.get("condition_id"),
                token_id=outcome_row.get("token_id"),
                outcome=outcome_row.get("outcome"),
            )
        status = str(result.get("status", "unknown"))
        if status == "ready":
            prepared_updates.append(result)
            details.append({"status": "pending_write", "table": table})
        else:
            if status in counts:
                counts[status] += 1
            details.append(_market_event_outcome_public_result(result))
        if status in {"skipped_no_connection", "skipped_missing_table", "skipped_invalid_schema"}:
            break

    blocking_statuses = {
        "skipped_missing_market_event",
        "refused_missing_identity",
        "refused_identity_mismatch",
        "conflict_existing_outcome",
        "refused_invalid_outcome",
        "skipped_invalid_schema",
        "skipped_missing_table",
        "skipped_no_connection",
    }
    if any(counts[key] for key in blocking_statuses):
        if counts["skipped_no_connection"]:
            status = "skipped_no_connection"
        elif counts["skipped_missing_table"]:
            status = "skipped_missing_table"
        elif counts["skipped_invalid_schema"]:
            status = "skipped_invalid_schema"
        elif counts["conflict_existing_outcome"] or counts["refused_identity_mismatch"]:
            status = "conflicted"
        else:
            status = "skipped_no_updates"
        return {"status": status, "table": table, **counts, "details": tuple(details)}

    if prepared_updates:
        try:
            conn.execute("SAVEPOINT market_events_v2_outcome_batch")
            for result in prepared_updates:
                conn.execute(
                    _MARKET_EVENT_OUTCOME_UPDATE_SQL,
                    result["update_values"],
                )
            conn.execute("RELEASE SAVEPOINT market_events_v2_outcome_batch")
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT market_events_v2_outcome_batch")
                conn.execute("RELEASE SAVEPOINT market_events_v2_outcome_batch")
            except sqlite3.OperationalError:
                pass
            counts["skipped_invalid_schema"] += 1
            details.append(
                {
                    "status": "skipped_invalid_schema",
                    "table": table,
                    "schema_error": str(exc),
                }
            )
            return {
                "status": "skipped_invalid_schema",
                "table": table,
                **counts,
                "details": tuple(details),
            }
        counts["written"] = len(prepared_updates)
        details = [
            {"status": "written", "table": table}
            if detail.get("status") == "pending_write"
            else detail
            for detail in details
        ]
        status = "written"
    elif counts["unchanged"]:
        status = "unchanged"
    else:
        status = "skipped_no_updates"

    return {"status": status, "table": table, **counts, "details": tuple(details)}


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


def log_rescue_event(
    conn,
    *,
    trade_id: str,
    chain_state: str,
    reason: str,
    occurred_at: str,
    temperature_metric: str,
    causality_status: str = "OK",
    authority: str = "UNVERIFIED",
    authority_source=None,
    position_id=None,
    decision_snapshot_id=None,
) -> None:
    """B063: append a durable audit row for a chain-rescue event.

    Writes to `rescue_events_v2` (Phase 2 schema). Unlike the existing
    CHAIN_RESCUE_AUDIT row in position_events, this row carries the
    temperature_metric, causality_status, and provenance authority
    needed to distinguish a legitimate low-lane N/A_CAUSAL skip from
    a silent rescue loss.

    Per SD-1 (MetricIdentity is binary) and SD-H (provenance authority
    tagging), temperature_metric stays in {'high','low'} and the
    `authority` column carries the tri-state confidence. Callers must
    resolve ambiguity via `authority='UNVERIFIED'` + concrete high/low
    tag rather than introducing a third temperature_metric value.

    Exempt from the DT#1 commit_then_export choke point — the audit row
    IS the authoritative observability record, not a derived export,
    and must be durable before the cycle acknowledges the rescue
    outcome. Same rule the existing CHAIN_RESCUE_AUDIT row follows.

    Fails closed-soft: if the table is missing on legacy DBs or the
    write raises, the error is logged but NOT re-raised, because the
    caller (chain_reconciliation._emit_rescue_event) must continue
    reconciling chain state even when the audit row cannot be
    persisted. The pre-existing CHAIN_RESCUE_AUDIT row in position_events
    provides a legacy-path audit trail as fallback.
    """
    import logging
    _logger = logging.getLogger(__name__)
    if conn is None:
        _logger.warning(
            "log_rescue_event: conn is None, skipping rescue_events_v2 write for trade_id=%s",
            trade_id,
        )
        return
    if temperature_metric not in ("high", "low"):
        _logger.error(
            "log_rescue_event: invalid temperature_metric=%r for trade_id=%s; skipping rescue_events_v2 write",
            temperature_metric,
            trade_id,
        )
        return
    try:
        conn.execute(
            """
            INSERT INTO rescue_events_v2
                (trade_id, position_id, decision_snapshot_id,
                 temperature_metric, causality_status,
                 authority, authority_source,
                 chain_state, reason, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                position_id,
                decision_snapshot_id,
                temperature_metric,
                causality_status,
                authority,
                authority_source,
                chain_state,
                reason,
                occurred_at,
            ),
        )
    except sqlite3.OperationalError as exc:
        _logger.warning(
            "log_rescue_event: rescue_events_v2 write failed for trade_id=%s: %s",
            trade_id,
            exc,
        )
    except sqlite3.IntegrityError as exc:
        _logger.info(
            "log_rescue_event: idempotent duplicate for trade_id=%s occurred_at=%s: %s",
            trade_id,
            occurred_at,
            exc,
        )


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


def _json_probability_vector(value) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    try:
        values = value.tolist() if hasattr(value, "tolist") else list(value)
    except TypeError:
        return None, False
    return json.dumps(values, ensure_ascii=False), len(values) > 0


def _candidate_bin_labels(candidate) -> list[str]:
    labels: list[str] = []
    try:
        outcomes = list(getattr(candidate, "outcomes", []) or [])
    except TypeError:
        return labels
    for outcome in outcomes:
        if outcome.get("range_low") is None and outcome.get("range_high") is None:
            continue
        title = str(outcome.get("title", "") or "")
        if title:
            labels.append(title)
    return labels


def _trace_direction(decision) -> str:
    edge = getattr(decision, "edge", None)
    direction = str(getattr(edge, "direction", "") or "unknown")
    return direction if direction in {"buy_yes", "buy_no", "unknown"} else "unknown"


def _trace_range_label(decision) -> str:
    edge = getattr(decision, "edge", None)
    return str(getattr(getattr(edge, "bin", None), "label", "") or "")


def _trace_scalar_posterior(decision) -> float | None:
    edge = getattr(decision, "edge", None)
    if edge is not None:
        try:
            return float(getattr(edge, "p_posterior", None))
        except (TypeError, ValueError):
            return None
    edge_context = getattr(decision, "edge_context", None)
    if edge_context is not None:
        try:
            return float(getattr(edge_context, "p_posterior", None))
        except (TypeError, ValueError):
            return None
    return None


def _trace_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def log_probability_trace_fact(
    conn: sqlite3.Connection | None,
    *,
    candidate,
    decision,
    recorded_at: str,
    mode: str,
) -> dict:
    """Write one durable probability trace row for one decision.

    This helper intentionally stores direct decision-time vectors only. It must
    not scalar-backfill vector lineage from BinEdge scalar fields.
    """
    if conn is None:
        logger.info("Probability trace write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "probability_trace_fact"}
    if not _table_exists(conn, "probability_trace_fact"):
        logger.info("Probability trace table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "probability_trace_fact"}

    decision_id = str(getattr(decision, "decision_id", "") or "").strip()
    if not decision_id:
        return {"status": "skipped_missing_decision_id", "table": "probability_trace_fact"}

    p_raw_json, has_p_raw = _json_probability_vector(getattr(decision, "p_raw", None))
    p_cal_json, has_p_cal = _json_probability_vector(getattr(decision, "p_cal", None))
    p_market_json, has_p_market = _json_probability_vector(getattr(decision, "p_market", None))
    p_posterior_json, _has_p_posterior_vector = _json_probability_vector(
        getattr(decision, "p_posterior_vector", None)
    )

    missing: list[str] = []
    for name, present in (
        ("p_raw_json", has_p_raw),
        ("p_cal_json", has_p_cal),
        ("p_market_json", has_p_market),
    ):
        if not present:
            missing.append(name)

    if not has_p_raw and not has_p_cal and not has_p_market:
        trace_status = "pre_vector_unavailable"
    elif not (has_p_raw and has_p_cal and has_p_market):
        trace_status = "degraded_missing_vectors"
    elif str(getattr(decision, "availability_status", "") or "").strip().upper() not in {"", "OK"}:
        trace_status = "degraded_decision_context"
    else:
        trace_status = "complete"

    rejection_stage = str(getattr(decision, "rejection_stage", "") or "")
    availability_status = str(getattr(decision, "availability_status", "") or "")
    missing_reasons = {
        "missing_vectors": missing,
        "rejection_stage": rejection_stage,
        "availability_status": availability_status,
    }
    bin_labels = _candidate_bin_labels(candidate)
    alpha = getattr(decision, "alpha", None)
    try:
        alpha = float(alpha) if alpha not in (None, "") else None
    except (TypeError, ValueError):
        alpha = None

    # P2 (PLAN_v3 §6.P2 stage 3): MarketPhase axis A — decision tag.
    # EdgeDecision.market_phase is the str-form ``.value`` stamped at the
    # cycle_runtime call site after evaluate_candidate returns; falls back
    # to the candidate's tag if the decision was constructed before
    # stage-2 plumbing (legacy / test fixtures). None when neither side
    # carries a tag (off-cycle / manual writes).
    market_phase_value: str | None = None
    decision_phase = getattr(decision, "market_phase", None)
    if decision_phase is not None:
        market_phase_value = decision_phase.value if hasattr(decision_phase, "value") else str(decision_phase)
    else:
        candidate_phase = getattr(candidate, "market_phase", None)
        if candidate_phase is not None:
            market_phase_value = candidate_phase.value if hasattr(candidate_phase, "value") else str(candidate_phase)

    conn.execute(
        """
        INSERT INTO probability_trace_fact (
            trace_id,
            decision_id,
            decision_snapshot_id,
            candidate_id,
            city,
            target_date,
            range_label,
            direction,
            mode,
            strategy_key,
            discovery_mode,
            entry_method,
            selected_method,
            trace_status,
            missing_reason_json,
            bin_labels_json,
            p_raw_json,
            p_cal_json,
            p_market_json,
            p_posterior_json,
            p_posterior,
            alpha,
            agreement,
            n_edges_found,
            n_edges_after_fdr,
            rejection_stage,
            availability_status,
            market_phase,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trace_id) DO UPDATE SET
            decision_id=excluded.decision_id,
            decision_snapshot_id=excluded.decision_snapshot_id,
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            mode=excluded.mode,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            entry_method=excluded.entry_method,
            selected_method=excluded.selected_method,
            trace_status=excluded.trace_status,
            missing_reason_json=excluded.missing_reason_json,
            bin_labels_json=excluded.bin_labels_json,
            p_raw_json=excluded.p_raw_json,
            p_cal_json=excluded.p_cal_json,
            p_market_json=excluded.p_market_json,
            p_posterior_json=excluded.p_posterior_json,
            p_posterior=excluded.p_posterior,
            alpha=excluded.alpha,
            agreement=excluded.agreement,
            n_edges_found=excluded.n_edges_found,
            n_edges_after_fdr=excluded.n_edges_after_fdr,
            rejection_stage=excluded.rejection_stage,
            availability_status=excluded.availability_status,
            market_phase=excluded.market_phase,
            recorded_at=excluded.recorded_at
        """,
        (
            f"probtrace:{decision_id}",
            decision_id,
            str(getattr(decision, "decision_snapshot_id", "") or "") or None,
            _opportunity_fact_candidate_id(candidate) or None,
            _candidate_city_name(candidate) or None,
            str(getattr(candidate, "target_date", "") or "") or None,
            _trace_range_label(decision) or None,
            _trace_direction(decision),
            str(mode or "") or None,
            str(getattr(decision, "strategy_key", "") or "").strip() or None,
            str(getattr(candidate, "discovery_mode", "") or "") or None,
            str(getattr(decision, "selected_method", "") or getattr(decision, "entry_method", "") or "") or None,
            str(getattr(decision, "selected_method", "") or "") or None,
            trace_status,
            json.dumps(missing_reasons, ensure_ascii=False, sort_keys=True),
            json.dumps(bin_labels, ensure_ascii=False),
            p_raw_json,
            p_cal_json,
            p_market_json,
            p_posterior_json,
            _trace_scalar_posterior(decision),
            alpha,
            str(getattr(decision, "agreement", "") or "") or None,
            _trace_int(getattr(decision, "n_edges_found", None)),
            _trace_int(getattr(decision, "n_edges_after_fdr", None)),
            rejection_stage or None,
            availability_status or None,
            market_phase_value,
            recorded_at,
        ),
    )
    return {
        "status": "written",
        "table": "probability_trace_fact",
        "trace_status": trace_status,
    }


def query_probability_trace_completeness(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "trace_rows": 0,
            "with_p_raw_json": 0,
            "with_p_cal_json": 0,
            "with_p_market_json": 0,
            "complete_rows": 0,
            "degraded_rows": 0,
            "pre_vector_rows": 0,
        }
    if not _table_exists(conn, "probability_trace_fact"):
        return {
            "status": "missing_table",
            "trace_rows": 0,
            "with_p_raw_json": 0,
            "with_p_cal_json": 0,
            "with_p_market_json": 0,
            "complete_rows": 0,
            "degraded_rows": 0,
            "pre_vector_rows": 0,
        }
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS trace_rows,
            SUM(CASE WHEN p_raw_json IS NOT NULL AND trim(p_raw_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_raw_json,
            SUM(CASE WHEN p_cal_json IS NOT NULL AND trim(p_cal_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_cal_json,
            SUM(CASE WHEN p_market_json IS NOT NULL AND trim(p_market_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_market_json,
            SUM(CASE WHEN trace_status = 'complete' THEN 1 ELSE 0 END) AS complete_rows,
            SUM(CASE WHEN trace_status IN ('degraded_missing_vectors', 'degraded_decision_context') THEN 1 ELSE 0 END) AS degraded_rows,
            SUM(CASE WHEN trace_status = 'pre_vector_unavailable' THEN 1 ELSE 0 END) AS pre_vector_rows
        FROM probability_trace_fact
        """
    ).fetchone()
    return {
        "status": "ok",
        "trace_rows": int(row["trace_rows"] or 0),
        "with_p_raw_json": int(row["with_p_raw_json"] or 0),
        "with_p_cal_json": int(row["with_p_cal_json"] or 0),
        "with_p_market_json": int(row["with_p_market_json"] or 0),
        "complete_rows": int(row["complete_rows"] or 0),
        "degraded_rows": int(row["degraded_rows"] or 0),
        "pre_vector_rows": int(row["pre_vector_rows"] or 0),
    }



def log_selection_family_fact(
    conn: sqlite3.Connection | None,
    *,
    family_id: str,
    cycle_mode: str,
    created_at: str,
    meta: dict,
    decision_snapshot_id: str | None = None,
    city: str | None = None,
    target_date: str | None = None,
    strategy_key: str | None = None,
    discovery_mode: str | None = None,
    decision_time_status: str | None = None,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "selection_family_fact"}
    if not _table_exists(conn, "selection_family_fact"):
        return {"status": "skipped_missing_table", "table": "selection_family_fact"}
    if not family_id:
        return {"status": "skipped_missing_family_id", "table": "selection_family_fact"}
    conn.execute(
        """
        INSERT INTO selection_family_fact (
            family_id, cycle_mode, decision_snapshot_id, city, target_date,
            strategy_key, discovery_mode, created_at, meta_json, decision_time_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(family_id) DO UPDATE SET
            cycle_mode=excluded.cycle_mode,
            decision_snapshot_id=excluded.decision_snapshot_id,
            city=excluded.city,
            target_date=excluded.target_date,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            created_at=excluded.created_at,
            meta_json=excluded.meta_json,
            decision_time_status=excluded.decision_time_status
        """,
        (
            family_id,
            cycle_mode,
            decision_snapshot_id,
            city,
            target_date,
            strategy_key,
            discovery_mode,
            created_at,
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
            decision_time_status,
        ),
    )
    return {"status": "written", "table": "selection_family_fact"}


def log_selection_hypothesis_fact(
    conn: sqlite3.Connection | None,
    *,
    hypothesis_id: str,
    family_id: str,
    city: str,
    target_date: str,
    range_label: str,
    direction: str,
    recorded_at: str,
    meta: dict,
    decision_id: str | None = None,
    candidate_id: str | None = None,
    p_value: float | None = None,
    q_value: float | None = None,
    ci_lower: float | None = None,
    ci_upper: float | None = None,
    edge: float | None = None,
    tested: bool = True,
    passed_prefilter: bool = False,
    selected_post_fdr: bool = False,
    rejection_stage: str | None = None,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "selection_hypothesis_fact"}
    if not _table_exists(conn, "selection_hypothesis_fact"):
        return {"status": "skipped_missing_table", "table": "selection_hypothesis_fact"}
    if not hypothesis_id:
        return {"status": "skipped_missing_hypothesis_id", "table": "selection_hypothesis_fact"}
    if not family_id:
        return {"status": "skipped_missing_family_id", "table": "selection_hypothesis_fact"}
    direction_value = direction if direction in {"buy_yes", "buy_no"} else "unknown"
    conn.execute(
        """
        INSERT INTO selection_hypothesis_fact (
            hypothesis_id, family_id, decision_id, candidate_id, city, target_date,
            range_label, direction, p_value, q_value, ci_lower, ci_upper, edge,
            tested, passed_prefilter, selected_post_fdr, rejection_stage,
            recorded_at, meta_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hypothesis_id) DO UPDATE SET
            family_id=excluded.family_id,
            decision_id=excluded.decision_id,
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            p_value=excluded.p_value,
            q_value=excluded.q_value,
            ci_lower=excluded.ci_lower,
            ci_upper=excluded.ci_upper,
            edge=excluded.edge,
            tested=excluded.tested,
            passed_prefilter=excluded.passed_prefilter,
            selected_post_fdr=excluded.selected_post_fdr,
            rejection_stage=excluded.rejection_stage,
            recorded_at=excluded.recorded_at,
            meta_json=excluded.meta_json
        """,
        (
            hypothesis_id,
            family_id,
            decision_id,
            candidate_id,
            city,
            target_date,
            range_label,
            direction_value,
            p_value,
            q_value,
            ci_lower,
            ci_upper,
            edge,
            int(bool(tested)),
            int(bool(passed_prefilter)),
            int(bool(selected_post_fdr)),
            rejection_stage,
            recorded_at,
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        ),
    )
    return {"status": "written", "table": "selection_hypothesis_fact"}




DATA_IMPROVEMENT_TABLES = (
    "probability_trace_fact",
    "calibration_decision_group",
    "selection_family_fact",
    "selection_hypothesis_fact",
)


def query_data_improvement_inventory(conn: sqlite3.Connection | None) -> dict:
    """Return DB-truth readiness/counts for data-improvement substrates."""
    if conn is None:
        return {"status": "skipped_no_connection", "tables": {}}
    inventory: dict[str, dict] = {}
    for table in DATA_IMPROVEMENT_TABLES:
        if not _table_exists(conn, table):
            inventory[table] = {"exists": False, "rows": 0}
            continue
        count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        inventory[table] = {"exists": True, "rows": count}
    missing = sorted(table for table, payload in inventory.items() if not payload["exists"])
    return {
        "status": "missing_tables" if missing else "ok",
        "tables": inventory,
        "missing_tables": missing,
    }


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
        INSERT INTO opportunity_fact (
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
        ON CONFLICT(decision_id) DO UPDATE SET
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            entry_method=excluded.entry_method,
            snapshot_id=excluded.snapshot_id,
            p_raw=excluded.p_raw,
            p_cal=excluded.p_cal,
            p_market=excluded.p_market,
            alpha=excluded.alpha,
            best_edge=excluded.best_edge,
            ci_width=excluded.ci_width,
            rejection_stage=excluded.rejection_stage,
            rejection_reason_json=excluded.rejection_reason_json,
            availability_status=excluded.availability_status,
            should_trade=excluded.should_trade,
            recorded_at=COALESCE(opportunity_fact.recorded_at, excluded.recorded_at)
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
        INSERT INTO availability_fact (
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
        ON CONFLICT(availability_id) DO UPDATE SET
            scope_type=excluded.scope_type,
            scope_key=excluded.scope_key,
            failure_type=excluded.failure_type,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            impact=excluded.impact,
            details_json=excluded.details_json
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
    """Parse an ISO-8601-ish timestamp string into a tz-AWARE datetime.

    Callers compare these timestamps with `>`/`<` across rows that may
    come from heterogeneous writers: runtime code uses
    datetime.now(timezone.utc).isoformat() (tz-aware), SQLite's built-in
    datetime('now') function returns "YYYY-MM-DD HH:MM:SS" with no tz
    (naive), and legacy writers sometimes used bare "Z" suffixes.
    Comparing a naive datetime with an aware one raises TypeError, which
    on 2026-04-11 crashed query_portfolio_loader_view every cycle after
    the nuke rebuild script left 7 naive timestamps in position_current.

    Contract: any value that parses at all is returned as UTC-aware. A
    naive input is assumed to already be UTC (zeus has no local-time
    writers — every producer is supposed to use UTC) and is upgraded
    by attaching tzinfo=timezone.utc. Invalid inputs return None.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Naive → assume UTC. Zeus's convention is UTC-everywhere; any
        # producer that writes naive is violating that convention and
        # the safer assumption is UTC over local time.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    clear_fill_fields: bool = False,
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
    stored_voided_at = voided_at or (current["voided_at"] if current else None)
    stored_submitted_price = submitted_price if submitted_price is not None else (current["submitted_price"] if current else None)
    stored_venue_status = venue_status if venue_status not in (None, "") else (current["venue_status"] if current else None)
    stored_terminal_status = terminal_exec_status if terminal_exec_status not in (None, "") else (current["terminal_exec_status"] if current else None)
    stored_decision_id = decision_id if decision_id not in (None, "") else (current["decision_id"] if current else None)
    stored_strategy_key = strategy_key if strategy_key not in (None, "") else (current["strategy_key"] if current else None)

    if clear_fill_fields:
        stored_filled_at = None
        stored_fill_price = None
        stored_shares = None
        stored_fill_quality = None
        stored_latency_seconds = None
        if terminal_exec_status in (None, ""):
            stored_terminal_status = "pending_fill_authority"
        if venue_status in (None, ""):
            stored_venue_status = stored_terminal_status
    else:
        stored_filled_at = filled_at or (current["filled_at"] if current else None)
        stored_fill_price = fill_price if fill_price is not None else (current["fill_price"] if current else None)
        stored_shares = shares if shares is not None else (current["shares"] if current else None)
        stored_fill_quality = fill_quality if fill_quality is not None else (current["fill_quality"] if current else None)
        if latency_seconds is None and stored_posted_at and stored_filled_at:
            posted_dt = _parse_iso_timestamp(stored_posted_at)
            filled_dt = _parse_iso_timestamp(stored_filled_at)
            if posted_dt is not None and filled_dt is not None:
                latency_seconds = max(0.0, (filled_dt - posted_dt).total_seconds())
        stored_latency_seconds = latency_seconds if latency_seconds is not None else (current["latency_seconds"] if current else None)

    conn.execute(
        """
        INSERT INTO execution_fact (
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
        ON CONFLICT(intent_id) DO UPDATE SET
            position_id=excluded.position_id,
            decision_id=excluded.decision_id,
            order_role=excluded.order_role,
            strategy_key=excluded.strategy_key,
            posted_at=excluded.posted_at,
            filled_at=excluded.filled_at,
            voided_at=excluded.voided_at,
            submitted_price=excluded.submitted_price,
            fill_price=excluded.fill_price,
            shares=excluded.shares,
            fill_quality=excluded.fill_quality,
            latency_seconds=excluded.latency_seconds,
            venue_status=excluded.venue_status,
            terminal_exec_status=excluded.terminal_exec_status
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
        INSERT INTO outcome_fact (
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
        ON CONFLICT(position_id) DO UPDATE SET
            strategy_key=excluded.strategy_key,
            entered_at=excluded.entered_at,
            exited_at=excluded.exited_at,
            settled_at=excluded.settled_at,
            exit_reason=excluded.exit_reason,
            admin_exit_reason=excluded.admin_exit_reason,
            decision_snapshot_id=excluded.decision_snapshot_id,
            pnl=excluded.pnl,
            outcome=excluded.outcome,
            hold_duration_hours=excluded.hold_duration_hours,
            monitor_count=excluded.monitor_count,
            chain_corrections_count=excluded.chain_corrections_count
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
    env = getattr(pos, "env", "unknown_env") or "unknown_env"
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




def log_execution_report(conn: sqlite3.Connection, pos, result, *, decision_id: str | None = None) -> None:
    """Append an execution telemetry event tied to the runtime trade."""
    if not getattr(pos, "trade_id", ""):
        return
    submitted_price = getattr(result, "submitted_price", None)
    reported_fill_price = getattr(result, "fill_price", None)
    reported_shares = getattr(result, "shares", None)
    status = str(getattr(result, "status", "") or "")
    command_state = str(getattr(result, "command_state", "") or "")
    order_role = str(getattr(result, "order_role", "") or "entry")
    entry_fill_authority = order_role == "entry" and bool(
        getattr(pos, "has_fill_economics_authority", False)
    )
    fill_has_finality = (
        command_state == "FILLED"
        or bool(getattr(result, "filled_at", None))
        or entry_fill_authority
    )
    fill_price = reported_fill_price if fill_has_finality else None
    shares = reported_shares if fill_has_finality else None
    if entry_fill_authority:
        authority_price = _finite_float_or_zero(getattr(pos, "entry_price_avg_fill", None))
        authority_shares = _finite_float_or_zero(getattr(pos, "shares_filled", None))
        authority_cost = _finite_float_or_zero(getattr(pos, "filled_cost_basis_usd", None))
        if authority_price <= 0.0 and authority_cost > 0.0 and authority_shares > 0.0:
            authority_price = authority_cost / authority_shares
        if authority_price > 0.0:
            fill_price = authority_price
        if authority_shares > 0.0:
            shares = authority_shares
    fill_quality = None
    if fill_has_finality and fill_price not in (None, 0) and submitted_price not in (None, 0):
        try:
            fill_quality = (float(fill_price) - float(submitted_price)) / float(submitted_price)
        except (TypeError, ValueError, ZeroDivisionError):
            fill_quality = None
    if fill_quality is None and fill_has_finality:
        fill_quality = getattr(pos, "fill_quality", None)

    details = {
        "status": status,
        "reason": getattr(result, "reason", None),
        "submitted_price": submitted_price,
        "fill_price": fill_price,
        "reported_fill_price_ignored": (
            reported_fill_price if reported_fill_price not in (None, 0) and not fill_has_finality else None
        ),
        "shares": shares,
        "reported_shares_ignored": (
            reported_shares if reported_shares is not None and not fill_has_finality else None
        ),
        "timeout_seconds": getattr(result, "timeout_seconds", None),
        "fill_quality": fill_quality,
        "order_status": getattr(pos, "order_status", ""),
    }
    event_timestamp = (
        (getattr(result, "filled_at", None) if fill_has_finality else None)
        or getattr(pos, "order_posted_at", None)
        or datetime.now(timezone.utc).isoformat()
    )
    terminal_exec_status = status or None
    if not fill_has_finality and (
        status.lower() in {"filled", "confirmed"}
        or reported_fill_price not in (None, 0)
        or reported_shares is not None
        or not status
    ):
        terminal_exec_status = "pending_fill_authority"
    clear_fill_fields = not fill_has_finality
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
        filled_at=getattr(result, "filled_at", None) if status == "filled" and fill_has_finality else None,
        voided_at=voided_at,
        submitted_price=submitted_price,
        fill_price=fill_price,
        shares=shares,
        fill_quality=fill_quality,
        venue_status=str(getattr(result, "venue_status", "") or getattr(pos, "order_status", "") or status or "") or None,
        terminal_exec_status=terminal_exec_status,
        clear_fill_fields=clear_fill_fields,
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



def log_trade_exit(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Update or insert exit fill evidence."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        from datetime import datetime
        env = getattr(pos, "env", "unknown_env") or "unknown_env"
        status = "voided" if getattr(pos, "state", "") == "voided" else "exited"
        values = (
            pos.market_id, pos.bin_label, pos.direction, pos.size_usd, pos.entry_price, pos.last_exit_at or datetime.now(timezone.utc).isoformat(),
            getattr(pos, "decision_snapshot_id", None) or None,
            getattr(pos, "calibration_version", "") or None,
            getattr(pos, "p_raw", None), getattr(pos, "p_posterior", None), pos.edge, 0.0, 0.0, 0.0,
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
    if not _table_exists(conn, "trade_decisions"):
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


def _settlement_truth_ready(normalized: dict) -> bool:
    authority = str(normalized.get("settlement_authority") or "").strip().upper()
    source = str(normalized.get("settlement_truth_source") or "").strip()
    metric = str(normalized.get("settlement_temperature_metric") or "").strip().lower()
    return (
        authority == "VERIFIED"
        and source in SETTLEMENT_METRIC_READY_TRUTH_SOURCES
        and metric in {"high", "low"}
        and normalized.get("settlement_value") is not None
    )




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
        "settlement_authority": str(details.get("settlement_authority") or "UNKNOWN").upper(),
        "settlement_truth_source": str(details.get("settlement_truth_source") or ""),
        "settlement_market_slug": str(details.get("settlement_market_slug") or ""),
        "settlement_temperature_metric": str(details.get("settlement_temperature_metric") or ""),
        "settlement_source": str(details.get("settlement_source") or ""),
        "settlement_value": _coerce_settlement_float(details.get("settlement_value")),
        "env": str(event.get("env") or ""),
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
            "learning_snapshot_ready": False,
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
    truth_ready = _settlement_truth_ready(normalized)
    if not truth_ready:
        degraded_reasons.append("missing_verified_settlement_truth")
    normalized.update({
        "is_degraded": bool(degraded_reasons),
        "degraded_reason": "; ".join(degraded_reasons),
        "contract_missing_fields": contract_missing_fields,
        "canonical_payload_complete": not contract_missing_fields,
        "learning_snapshot_ready": bool(normalized["decision_snapshot_id"]) and truth_ready,
        "metric_ready": truth_ready,
        "required_missing_fields": [],
    })
    return normalized


def query_position_events(conn: sqlite3.Connection, runtime_trade_id: str, limit: int = 50) -> list[dict]:
    """Load recent canonical position events for one position."""
    rows = conn.execute(
        """
        SELECT event_type,
               position_id AS runtime_trade_id,
               NULL AS position_state,
               order_id,
               snapshot_id AS decision_snapshot_id,
               NULL AS city,
               NULL AS target_date,
               NULL AS market_id,
               NULL AS bin_label,
               NULL AS direction,
               strategy_key AS strategy,
               NULL AS edge_source,
               source_module AS source,
               payload_json AS details_json,
               occurred_at AS timestamp,
               env
        FROM position_events
        WHERE position_id = ?
        ORDER BY sequence_no ASC
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
    """Load recent canonical SETTLED events from the durable event spine."""
    from src.state.projection import normalize_position_event_env

    query_env = normalize_position_event_env(env, default=get_mode())
    event_filters = ["event_type = 'SETTLED'", "env = ?"]
    event_params: list[object] = [query_env]
    filters: list[str] = []
    params: list[object] = []
    if city is not None:
        filters.append("pc.city = ?")
        params.append(city)
    if target_date is not None:
        filters.append("pc.target_date = ?")
        params.append(target_date)
    if not_before is not None:
        event_filters.append("occurred_at >= ?")
        event_params.append(not_before)
    event_where_clause = " AND ".join(event_filters)
    where_clause = " AND ".join(["rn = 1", *filters])
    query = f"""
        SELECT e.event_type,
               e.position_id AS runtime_trade_id,
               NULL AS position_state,
               e.order_id,
               e.snapshot_id AS decision_snapshot_id,
               pc.city,
               pc.target_date,
               pc.market_id,
               pc.bin_label,
               pc.direction,
               e.strategy_key AS strategy,
               pc.edge_source,
               e.source_module AS source,
               e.payload_json AS details_json,
               e.occurred_at AS timestamp,
               e.env
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY position_id ORDER BY sequence_no DESC) AS rn
            FROM position_events
            WHERE {event_where_clause}
        ) e
        LEFT JOIN position_current pc ON pc.position_id = e.position_id
        WHERE {where_clause}
        ORDER BY e.occurred_at DESC
        """
    params = event_params + params
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
    """Prefer stage-level settlement events, then fall back to legacy decision_log blobs.

    ``env`` gates both canonical ``position_events`` and legacy
    ``decision_log`` rows. Missing canonical env is not live authority.
    """
    stage_events = []
    if _table_exists(conn, "position_events") and _table_exists(conn, "position_current"):
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
    if not _table_exists(conn, "decision_log"):
        return []
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
    settlement_authority_missing_tables = []
    if not _table_exists(conn, "position_events"):
        settlement_authority_missing_tables.append("position_events")
        if not _table_exists(conn, "decision_log"):
            settlement_authority_missing_tables.append("decision_log")
    refresh_time = as_of or datetime.now(timezone.utc).isoformat()
    if missing_required_tables:
        return {
            "status": "skipped_missing_inputs",
            "table": "strategy_health",
            "rows_written": 0,
            "as_of": refresh_time,
            "missing_required_tables": missing_required_tables,
            "missing_optional_tables": missing_optional_tables,
            "settlement_authority_missing_tables": settlement_authority_missing_tables,
            "omitted_fields": [
                "risk_level",
                "brier_30d",
                "edge_trend_30d",
            ],
        }

    position_view = query_position_current_status_view(conn)
    position_metrics: dict[str, dict[str, float]] = {}
    for position in position_view.get("positions", []):
        strategy_key = str(position.get("strategy") or "unclassified")
        bucket = position_metrics.setdefault(
            strategy_key,
            {
                "open_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["open_exposure_usd"] += float(
            position.get("effective_cost_basis_usd")
            if position.get("effective_cost_basis_usd") is not None
            else position.get("size_usd", 0.0)
            or 0.0
        )
        bucket["unrealized_pnl"] += float(position.get("unrealized_pnl", 0.0) or 0.0)
    position_metrics = {
        strategy_key: {
            "open_exposure_usd": round(float(bucket.get("open_exposure_usd", 0.0) or 0.0), 2),
            "unrealized_pnl": round(float(bucket.get("unrealized_pnl", 0.0) or 0.0), 2),
        }
        for strategy_key, bucket in position_metrics.items()
    }

    settled_cutoff = _shift_iso_timestamp(refresh_time, days=30)
    settled_cutoff_dt = _parse_iso_timestamp(settled_cutoff)
    settlement_metrics: dict[str, dict] = {}
    settlement_rows = query_authoritative_settlement_rows(conn, limit=None)
    settlement_degraded_rows = 0
    for settlement_row in settlement_rows:
        if settlement_row.get("is_degraded", False):
            settlement_degraded_rows += 1
        if not settlement_row.get("metric_ready", False):
            continue
        settled_at = str(settlement_row.get("settled_at") or "")
        settled_at_dt = _parse_iso_timestamp(settled_at)
        if not settled_at:
            continue
        if settled_cutoff_dt is not None:
            if settled_at_dt is None or settled_at_dt < settled_cutoff_dt:
                continue
        elif settled_at < settled_cutoff:
            continue
        strategy_key = str(settlement_row.get("strategy") or "unclassified")
        bucket = settlement_metrics.setdefault(
            strategy_key,
            {
                "settled_trades_30d": 0,
                "realized_pnl_30d": 0.0,
                "wins": 0,
            },
        )
        bucket["settled_trades_30d"] += 1
        bucket["realized_pnl_30d"] += float(settlement_row.get("pnl") or 0.0)
        if int(settlement_row.get("outcome") or 0) == 1:
            bucket["wins"] += 1
    settlement_metrics = {
        strategy_key: {
            "settled_trades_30d": int(bucket["settled_trades_30d"]),
            "realized_pnl_30d": round(float(bucket["realized_pnl_30d"]), 2),
            "win_rate_30d": round(float(bucket["wins"]) / int(bucket["settled_trades_30d"]), 4)
            if int(bucket["settled_trades_30d"])
            else None,
        }
        for strategy_key, bucket in settlement_metrics.items()
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
    settlement_authority_degraded = bool(
        settlement_authority_missing_tables or settlement_degraded_rows
    )
    if rows_written:
        refresh_status = "refreshed_degraded" if settlement_authority_degraded else "refreshed"
    else:
        refresh_status = "refreshed_empty_degraded" if settlement_authority_degraded else "refreshed_empty"
    return {
        "status": refresh_status,
        "table": "strategy_health",
        "rows_written": rows_written,
        "as_of": refresh_time,
        "missing_required_tables": missing_required_tables,
        "missing_optional_tables": missing_optional_tables,
        "settlement_authority_missing_tables": settlement_authority_missing_tables,
        "settlement_degraded_rows": settlement_degraded_rows,
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
               decision_snapshot_id, last_monitor_market_price,
               token_id, no_token_id, condition_id
        FROM position_current
        ORDER BY updated_at DESC, position_id
        """
    ).fetchall()
    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    fill_hints = _query_entry_execution_fill_hints(conn, trade_ids)

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
        hints = transitional_hints.get(trade_id, {})
        fill_economics = _position_current_effective_entry_economics(
            row,
            fill_hints.get(trade_id),
        )
        chain_state = str(row["chain_state"] or "unknown")
        exit_state = str(hints.get("exit_state") or "none")
        entry_fill_verified = bool(
            hints.get("entry_fill_verified", False)
            or fill_economics["entry_fill_verified"]
        )
        admin_exit_reason = str(hints.get("admin_exit_reason") or "")
        day0_entered_at = str(hints.get("day0_entered_at") or "")
        shares = float(fill_economics["effective_shares"] or 0.0)
        mark_price = row["last_monitor_market_price"]
        cost_basis_usd = fill_economics["pnl_cost_basis_usd"]
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
                "size_usd": float(fill_economics["effective_cost_basis_usd"] or 0.0),
                "submitted_size_usd": float(fill_economics["submitted_size_usd"] or 0.0),
                "effective_cost_basis_usd": float(fill_economics["effective_cost_basis_usd"] or 0.0),
                "entry_economics_authority": fill_economics["entry_economics_authority"],
                "fill_authority": fill_economics["fill_authority"],
                "entry_economics_source": fill_economics["entry_economics_source"],
                "entry_price_avg_fill": float(fill_economics["entry_price_avg_fill"] or 0.0),
                "shares_filled": float(fill_economics["shares_filled"] or 0.0),
                "filled_cost_basis_usd": float(fill_economics["filled_cost_basis_usd"] or 0.0),
                "execution_fact_intent_id": fill_economics["execution_fact_intent_id"],
                "execution_fact_filled_at": fill_economics["execution_fact_filled_at"],
                "shares": shares,
                "entry_price": fill_economics["effective_entry_price"],
                "edge": None,
                "bin_label": str(row["bin_label"] or ""),
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "token_id": str(row["token_id"] or ""),
                "no_token_id": str(row["no_token_id"] or ""),
                "condition_id": str(row["condition_id"] or ""),
                "day0_entered_at": day0_entered_at,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
            }
        )

        strategy_key = str(row["strategy_key"] or "unclassified")
        strategy_open_counts[strategy_key] = strategy_open_counts.get(strategy_key, 0) + 1
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
        exit_state_counts[exit_state] = exit_state_counts.get(exit_state, 0) + 1
        total_exposure_usd += float(fill_economics["effective_cost_basis_usd"] or 0.0)
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


def _latest_position_event_envs(
    conn: sqlite3.Connection,
    position_ids: list[str],
) -> dict[str, str]:
    if not position_ids:
        return {}
    if not _table_exists(conn, "position_events"):
        return {}
    if "env" not in _table_columns(conn, "position_events"):
        return {}
    placeholders = ", ".join(["?"] * len(position_ids))
    rows = conn.execute(
        f"""
        SELECT position_id, env
        FROM (
            SELECT position_id,
                   env,
                   ROW_NUMBER() OVER (
                       PARTITION BY position_id
                       ORDER BY sequence_no DESC
                   ) AS rn
            FROM position_events
            WHERE position_id IN ({placeholders})
        )
        WHERE rn = 1
        """,
        tuple(position_ids),
    ).fetchall()
    envs: dict[str, str] = {}
    for row in rows:
        env = str(row["env"] or "").strip().lower()
        if env in POSITION_EVENT_ENVS:
            envs[str(row["position_id"])] = env
    return envs


def query_portfolio_loader_view(conn: sqlite3.Connection | None, *, temperature_metric: str | None = None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }
    if not _table_exists(conn, "position_current"):
        return {
            "status": "missing_table",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }

    actual_cols = {row[1] for row in conn.execute("PRAGMA table_info(position_current)").fetchall()}
    if "temperature_metric" not in actual_cols:
        raise RuntimeError(
            "position_current.temperature_metric column missing; "
            "init_schema ALTER must have failed. Re-run init or check DB integrity."
        )

    where_clause = ""
    params: tuple = ()
    if temperature_metric is not None:
        where_clause = "WHERE temperature_metric = ?"
        params = (temperature_metric,)

    position_current_env_expr = (
        "env"
        if "env" in actual_cols
        else "NULL AS env"
    )

    rows = conn.execute(
        f"""
        SELECT position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
               direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
               last_monitor_prob, last_monitor_edge, last_monitor_market_price,
               decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
               chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at,
               temperature_metric, {position_current_env_expr}
        FROM position_current {where_clause}
        ORDER BY updated_at DESC, position_id
        """,
        params,
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }

    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    position_ids = [str(row["position_id"] or row["trade_id"] or "") for row in rows]
    event_envs = _latest_position_event_envs(conn, position_ids)
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    fill_hints = _query_entry_execution_fill_hints(conn, trade_ids)

    positions: list[dict] = []
    for row in rows:
        trade_id = str(row["trade_id"] or row["position_id"] or "")
        phase = str(row["phase"] or "")
        hints = transitional_hints.get(trade_id, {})
        fill_economics = _position_current_effective_entry_economics(
            row,
            fill_hints.get(trade_id),
        )
        runtime_state = PORTFOLIO_LOADER_PHASE_TO_RUNTIME_STATE.get(phase, phase)
        explicit_env = str(row["env"] or event_envs.get(str(row["position_id"] or "")) or "unknown_env")
        positions.append(
            {
                "trade_id": trade_id,
                "market_id": row["market_id"],
                "city": row["city"],
                "cluster": row["cluster"],
                "target_date": row["target_date"],
                "bin_label": row["bin_label"],
                "direction": row["direction"],
                "unit": row["unit"],
                "size_usd": fill_economics["effective_cost_basis_usd"],
                "submitted_size_usd": fill_economics["submitted_size_usd"],
                "shares": fill_economics["effective_shares"],
                "cost_basis_usd": fill_economics["pnl_cost_basis_usd"],
                "projection_cost_basis_usd": fill_economics["projection_cost_basis_usd"],
                "entry_price": fill_economics["effective_entry_price"],
                "entry_price_avg_fill": fill_economics["entry_price_avg_fill"],
                "shares_filled": fill_economics["shares_filled"],
                "filled_cost_basis_usd": fill_economics["filled_cost_basis_usd"],
                "effective_cost_basis_usd": fill_economics["effective_cost_basis_usd"],
                "entry_economics_authority": fill_economics["entry_economics_authority"],
                "fill_authority": fill_economics["fill_authority"],
                "entry_economics_source": fill_economics["entry_economics_source"],
                "execution_fact_intent_id": fill_economics["execution_fact_intent_id"],
                "execution_fact_filled_at": fill_economics["execution_fact_filled_at"],
                "p_posterior": row["p_posterior"],
                "last_monitor_prob": _finite_float_or_none(row["last_monitor_prob"]),
                "last_monitor_edge": _finite_float_or_none(row["last_monitor_edge"]),
                "last_monitor_market_price": row["last_monitor_market_price"],
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "entry_method": str(row["entry_method"] or ""),
                "strategy_key": str(row["strategy_key"] or ""),
                "strategy": str(row["strategy_key"] or ""),
                "edge_source": str(row["edge_source"] or ""),
                "discovery_mode": str(row["discovery_mode"] or ""),
                "chain_state": str(row["chain_state"] or "unknown"),
                "token_id": str(row["token_id"] or ""),
                "no_token_id": str(row["no_token_id"] or ""),
                "condition_id": str(row["condition_id"] or ""),
                "order_id": str(row["order_id"] or ""),
                "order_status": str(row["order_status"] or ""),
                "state": runtime_state,
                "env": explicit_env,
                "entered_at": str(hints.get("entered_at") or ""),
                "day0_entered_at": str(hints.get("day0_entered_at") or ""),
                "exit_state": str(hints.get("exit_state") or ""),
                "admin_exit_reason": str(hints.get("admin_exit_reason") or ""),
                "entry_fill_verified": bool(
                    hints.get("entry_fill_verified", False)
                    or fill_economics["entry_fill_verified"]
                ),
                "temperature_metric": str(row["temperature_metric"] or "high"),
            }
        )
    return {
        "status": "ok" if positions else "empty",
        "table": "position_current",
        "positions": positions,
        "temperature_metric": temperature_metric,
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
    """Append a control override event. Writes into the append-only
    `control_overrides_history` log; the `control_overrides` VIEW projects
    the latest row (by `history_id`, AUTOINCREMENT) per `override_id`. See B070."""
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides"}
    if not _table_exists(conn, "control_overrides_history"):
        return {"status": "skipped_missing_table", "table": "control_overrides"}
    recorded_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence,
            operation, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upsert', ?)
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
            recorded_at,
        ),
    )
    return {"status": "written", "table": "control_overrides", "override_id": override_id}


def record_token_suppression(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    suppression_reason: str,
    source_module: str,
    condition_id: str | None = None,
    created_at: str | None = None,
    evidence: dict | None = None,
) -> dict:
    """Append a token suppression event to the append-only history log.

    B071: writes into `token_suppression_history` (append-only log) AND
    into the legacy `token_suppression` table (upsert, for backward compat
    with callers that query the legacy table directly). The `token_suppression_current`
    VIEW projects the latest row per `token_id` from the history table.

    After running migrate_b071_token_suppression_to_history.py --apply --drop-legacy,
    the legacy table is DROPped and replaced by a VIEW alias, and the dual-write
    can be removed in a future cleanup phase.
    """
    if conn is None:
        return {"status": "skipped_no_connection", "table": "token_suppression"}
    if not _table_exists(conn, "token_suppression_history"):
        return {"status": "skipped_missing_table", "table": "token_suppression"}
    normalized_token = str(token_id or "").strip()
    if not normalized_token:
        raise ValueError("token suppression requires token_id")
    normalized_reason = str(suppression_reason or "").strip()
    if normalized_reason not in TOKEN_SUPPRESSION_REASONS:
        raise ValueError(f"unknown token suppression reason: {suppression_reason!r}")
    normalized_source = str(source_module or "").strip()
    if not normalized_source:
        raise ValueError("token suppression requires source_module")
    now = created_at or datetime.now(timezone.utc).isoformat()
    recorded_at = datetime.now(timezone.utc).isoformat()
    evidence_payload = dict(evidence or {})
    if normalized_reason == "chain_only_quarantined":
        # Use MAX(history_id) — strictly monotone, no clock/tie dependency (B071).
        # Fall back to legacy token_suppression table if no history row exists yet
        # (pre-migration DBs that have rows only in the legacy table).
        existing = conn.execute(
            """
            SELECT suppression_reason, created_at, evidence_json
            FROM token_suppression_history
            WHERE token_id = ?
              AND history_id = (
                  SELECT MAX(h2.history_id)
                  FROM token_suppression_history h2
                  WHERE h2.token_id = ?
              )
            """,
            (normalized_token, normalized_token),
        ).fetchone()
        if existing is None and _table_exists(conn, "token_suppression"):
            existing = conn.execute(
                """
                SELECT suppression_reason, created_at, evidence_json
                FROM token_suppression
                WHERE token_id = ?
                """,
                (normalized_token,),
            ).fetchone()
        if existing is not None and str(existing["suppression_reason"] or "") == "chain_only_quarantined":
            try:
                existing_evidence = json.loads(str(existing["evidence_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                existing_evidence = {}
            first_seen_at = str(
                existing_evidence.get("first_seen_at")
                or existing["created_at"]
                or ""
            )
            if first_seen_at:
                evidence_payload["first_seen_at"] = first_seen_at
    evidence_json = json.dumps(evidence_payload, sort_keys=True)
    # B071 cycle-2 critic MINOR #1: wrap dual-write in a single transaction.
    # Without this, a failure between the history INSERT and the legacy UPSERT
    # leaves the two tables inconsistent — history says "suppressed" while
    # legacy still shows the prior state (or nothing). `with conn:` uses the
    # connection as a context manager that commits on success, rolls back on
    # exception. Dual-write becomes atomic at the write-side seam.
    with conn:
        # Append to history (B071 — append-only, audit trail).
        conn.execute(
            """
            INSERT INTO token_suppression_history (
                token_id, condition_id, suppression_reason, source_module,
                created_at, updated_at, evidence_json, operation, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'record', ?)
            """,
            (
                normalized_token,
                str(condition_id or ""),
                normalized_reason,
                normalized_source,
                now,
                now,
                evidence_json,
                recorded_at,
            ),
        )
        # Dual-write: keep legacy token_suppression table in sync for backward
        # compat with callers that query it directly (pre-migration). Removed
        # after migrate_b071 --drop-legacy creates the VIEW alias.
        if _table_exists(conn, "token_suppression"):
            conn.execute(
                """
                INSERT INTO token_suppression (
                    token_id, condition_id, suppression_reason, source_module,
                    created_at, updated_at, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    condition_id = CASE
                        WHEN excluded.condition_id IS NULL OR excluded.condition_id = ''
                        THEN token_suppression.condition_id
                        ELSE excluded.condition_id
                    END,
                    suppression_reason = excluded.suppression_reason,
                    source_module = excluded.source_module,
                    updated_at = excluded.updated_at,
                    evidence_json = excluded.evidence_json
                """,
                (
                    normalized_token,
                    str(condition_id or ""),
                    normalized_reason,
                    normalized_source,
                    now,
                    now,
                    evidence_json,
                ),
            )
    return {
        "status": "written",
        "table": "token_suppression",
        "token_id": normalized_token,
    }


def query_token_suppression_tokens(conn: sqlite3.Connection | None) -> list[str]:
    """Return tokens that reconciliation must not resurrect from chain-only state.

    Reads from `token_suppression` which is either the legacy mutable table
    (pre-migration) or the VIEW alias created by
    migrate_b071_token_suppression_to_history.py --apply --drop-legacy (B071).
    The VIEW projects the latest row per token_id from the append-only history.
    """
    if conn is None or not _table_or_view_exists(conn, "token_suppression"):
        return []
    rows = conn.execute(
        f"""
        SELECT token_id
        FROM token_suppression
        WHERE suppression_reason IN ({", ".join(["?"] * len(RESOLVED_TOKEN_SUPPRESSION_REASONS))})
        ORDER BY created_at ASC, token_id ASC
        """,
        RESOLVED_TOKEN_SUPPRESSION_REASONS,
    ).fetchall()
    return [str(row["token_id"] or "") for row in rows if str(row["token_id"] or "")]


def query_chain_only_quarantine_rows(conn: sqlite3.Connection | None) -> list[dict]:
    """Return unresolved chain-only quarantine facts for runtime cache hydration.

    Reads from `token_suppression` which is either the legacy mutable table
    (pre-migration) or the VIEW alias created by B071 migration. The VIEW
    projects the latest row per token_id from the append-only history.
    """
    if conn is None or not _table_or_view_exists(conn, "token_suppression"):
        return []
    rows = conn.execute(
        """
        SELECT token_id, condition_id, created_at, updated_at, evidence_json
        FROM token_suppression
        WHERE suppression_reason = 'chain_only_quarantined'
        ORDER BY created_at ASC, token_id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def expire_control_override(
    conn: sqlite3.Connection | None,
    *,
    override_id: str,
    expired_at: str,
) -> dict:
    """Append an 'expire' event to `control_overrides_history` that sets
    `effective_until = expired_at` on the latest row for this override_id.
    No-op if no currently-active row exists. See B070."""
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides", "expired_count": 0}
    if not _table_exists(conn, "control_overrides_history"):
        return {"status": "skipped_missing_table", "table": "control_overrides", "expired_count": 0}
    recorded_at = datetime.now(timezone.utc).isoformat()
    # Use history_id (AUTOINCREMENT) not recorded_at for the latest-row
    # lookup: strictly monotone, no clock/tie dependency.
    cur = conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence,
            operation, recorded_at
        )
        SELECT h.override_id, h.target_type, h.target_key, h.action_type, h.value,
               h.issued_by, h.issued_at, ?, h.reason, h.precedence,
               'expire', ?
        FROM control_overrides_history h
        WHERE h.override_id = ?
          AND h.history_id = (
              SELECT MAX(h2.history_id)
              FROM control_overrides_history h2
              WHERE h2.override_id = ?
          )
          AND (h.effective_until IS NULL OR h.effective_until > ?)
        """,
        (expired_at, recorded_at, override_id, override_id, expired_at),
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
            "entries_pause_source": None,
            "entries_pause_reason": None,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    if not _table_exists(conn, "control_overrides_history"):
        return {
            "status": "missing_table",
            "entries_paused": False,
            "entries_pause_source": None,
            "entries_pause_reason": None,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    rows = conn.execute(
        """
        SELECT override_id, target_type, target_key, action_type, value, issued_by, issued_at, reason, precedence
        FROM control_overrides
        WHERE target_type IN ('global', 'strategy')
          AND issued_at <= ?
          AND (effective_until IS NULL OR effective_until > ?)
        ORDER BY precedence DESC, issued_at DESC, override_id DESC
        """,
        (current_time, current_time),
    ).fetchall()
    entries_paused = False
    entries_pause_source = None
    entries_pause_reason = None
    edge_threshold_multiplier = 1.0
    # G6 BLOCKER #2 fix (2026-04-26, con-nyx review): emit GateDecision-shaped
    # dicts (not bare bool) so control_plane.strategy_gates() — which expects
    # dict and raises ValueError on bool — can deserialize them via
    # GateDecision.from_dict. K1 migration set the in-memory writer
    # (set_strategy_gate puts dict) but missed the DB reader; the boot
    # guard introduced by G6 forced this latent debt onto every live launch.
    strategy_gates: dict[str, dict] = {}
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
            if entries_paused:
                reason = str(row["reason"] or "")
                issued_by = str(row["issued_by"] or "")
                if issued_by == "system_auto_pause" or issued_by.startswith("auto:"):
                    entries_pause_source = "auto_exception"
                    entries_pause_reason = reason if issued_by == "system_auto_pause" else issued_by.replace("auto:", "", 1)
                elif issued_by == "control_plane":
                    entries_pause_source = "manual_command"
                    entries_pause_reason = reason
                else:
                    entries_pause_source = "manual_command"
                    entries_pause_reason = f"external:{issued_by}"
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
            # value="true" means gate IS active (strategy DISABLED), so enabled = NOT value.
            # Synthesize GateDecision-shape from the row columns the DB already carries.
            # reason_code defaults to OPERATOR_OVERRIDE since the DB doesn't store the original
            # ReasonCode enum; reason_snapshot empty (DB doesn't store snapshot either).
            strategy_gates[target_key] = {
                "enabled": not _parse_boolish_text(value),
                "reason_code": "operator_override",
                "reason_snapshot": {},
                "gated_at": str(row["issued_at"] or ""),
                "gated_by": str(row["issued_by"] or "unknown"),
            }
            seen_strategy_gate.add(target_key)
    return {
        "status": "ok",
        "entries_paused": entries_paused,
        "entries_pause_source": entries_pause_source,
        "entries_pause_reason": entries_pause_reason,
        "edge_threshold_multiplier": edge_threshold_multiplier,
        "strategy_gates": strategy_gates,
    }


def _shift_iso_timestamp(timestamp: str, *, days: int) -> str:
    parsed = _parse_iso_timestamp(timestamp)
    if parsed is None:
        return timestamp
    return (parsed - timedelta(days=days)).isoformat()


def _parse_boolish_text(raw: str) -> bool:
    # K1/#71: removed "gate" — action keyword, not boolean literal.
    # Same rationale as _parse_boolish in policy.py.
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"unsupported boolish value in DB: {raw!r}")


def _finite_float_or_zero(value) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return 0.0
    return numeric


def _finite_float_or_none(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _query_entry_execution_fill_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, dict]:
    """Return confirmed entry fill economics from canonical execution facts.

    `position_current` lacks durable fill-authority columns. This read-side
    enrichment is intentionally narrower than a schema migration: it consumes
    only terminal filled entry execution facts with filled_at + positive price
    and shares, then leaves legacy/projection rows explicitly non-fill-grade.
    """
    if not trade_ids or not _table_exists(conn, "execution_fact"):
        return {}
    columns = _table_columns(conn, "execution_fact")
    required = {
        "intent_id",
        "position_id",
        "order_role",
        "filled_at",
        "posted_at",
        "fill_price",
        "shares",
        "terminal_exec_status",
        "venue_status",
    }
    if not required.issubset(columns):
        return {}
    normalized_trade_ids = sorted({str(trade_id or "") for trade_id in trade_ids if str(trade_id or "")})
    if not normalized_trade_ids:
        return {}
    placeholders = ", ".join("?" for _ in normalized_trade_ids)
    rows = conn.execute(
        f"""
        SELECT position_id, intent_id, filled_at, posted_at, fill_price, shares,
               terminal_exec_status, venue_status
        FROM execution_fact
        WHERE position_id IN ({placeholders})
          AND order_role = 'entry'
          AND lower(COALESCE(terminal_exec_status, '')) = 'filled'
          AND filled_at IS NOT NULL
          AND COALESCE(fill_price, 0.0) > 0.0
          AND COALESCE(shares, 0.0) > 0.0
        ORDER BY position_id,
                 COALESCE(filled_at, posted_at, '') DESC,
                 intent_id DESC
        """,
        normalized_trade_ids,
    ).fetchall()
    hints: dict[str, dict] = {}
    for row in rows:
        trade_id = str(row["position_id"] or "")
        if not trade_id or trade_id in hints:
            continue
        fill_price = _finite_float_or_zero(row["fill_price"])
        shares = _finite_float_or_zero(row["shares"])
        filled_cost_basis_usd = fill_price * shares
        if fill_price <= 0.0 or shares <= 0.0 or filled_cost_basis_usd <= 0.0:
            continue
        hints[trade_id] = {
            "entry_price_avg_fill": fill_price,
            "shares_filled": shares,
            "filled_cost_basis_usd": filled_cost_basis_usd,
            "entry_economics_authority": ENTRY_ECONOMICS_AVG_FILL_PRICE,
            "fill_authority": FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
            "entry_fill_verified": True,
            "entry_economics_source": "execution_fact",
            "execution_fact_intent_id": str(row["intent_id"] or ""),
            "execution_fact_filled_at": str(row["filled_at"] or ""),
            "execution_fact_venue_status": str(row["venue_status"] or ""),
        }
    return hints


def _position_current_effective_entry_economics(row, fill_hint: dict | None) -> dict:
    from src.state.portfolio import fill_authority_effective_open_cost_basis

    submitted_size_usd = _finite_float_or_zero(row["size_usd"])
    projection_shares = _finite_float_or_zero(row["shares"])
    projection_cost_basis_usd = _finite_float_or_zero(row["cost_basis_usd"])
    projection_entry_price = _finite_float_or_zero(row["entry_price"])
    phase = str(row["phase"] or "")

    if fill_hint:
        filled_cost_basis_usd = _finite_float_or_zero(fill_hint.get("filled_cost_basis_usd"))
        filled_shares = _finite_float_or_zero(fill_hint.get("shares_filled"))
        avg_fill_price = _finite_float_or_zero(fill_hint.get("entry_price_avg_fill"))
        effective_cost_basis_usd = fill_authority_effective_open_cost_basis(
            current_open_cost=projection_cost_basis_usd,
            current_open_shares=projection_shares,
            entry_fill_cost=filled_cost_basis_usd,
            entry_fill_shares=filled_shares,
        )
        effective_shares = filled_shares
        if projection_shares > 0.0:
            effective_shares = min(projection_shares, filled_shares)
        effective_entry_price = avg_fill_price
        if effective_entry_price <= 0.0 and effective_cost_basis_usd > 0.0 and effective_shares > 0.0:
            effective_entry_price = effective_cost_basis_usd / effective_shares
        return {
            "submitted_size_usd": submitted_size_usd,
            "projection_cost_basis_usd": projection_cost_basis_usd,
            "effective_cost_basis_usd": effective_cost_basis_usd,
            "effective_shares": effective_shares,
            "pnl_cost_basis_usd": effective_cost_basis_usd,
            "effective_entry_price": effective_entry_price,
            "entry_price_avg_fill": avg_fill_price,
            "shares_filled": filled_shares,
            "filled_cost_basis_usd": filled_cost_basis_usd,
            "entry_economics_authority": ENTRY_ECONOMICS_AVG_FILL_PRICE,
            "fill_authority": FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
            "entry_economics_source": str(fill_hint.get("entry_economics_source") or "execution_fact"),
            "entry_fill_verified": True,
            "execution_fact_intent_id": str(fill_hint.get("execution_fact_intent_id") or ""),
            "execution_fact_filled_at": str(fill_hint.get("execution_fact_filled_at") or ""),
            "execution_fact_venue_status": str(fill_hint.get("execution_fact_venue_status") or ""),
        }

    if phase == "pending_entry":
        return {
            "submitted_size_usd": submitted_size_usd,
            "projection_cost_basis_usd": projection_cost_basis_usd,
            "effective_cost_basis_usd": 0.0,
            "effective_shares": 0.0,
            "pnl_cost_basis_usd": 0.0,
            "effective_entry_price": 0.0,
            "entry_price_avg_fill": 0.0,
            "shares_filled": 0.0,
            "filled_cost_basis_usd": 0.0,
            "entry_economics_authority": ENTRY_ECONOMICS_LEGACY_UNKNOWN,
            "fill_authority": FILL_AUTHORITY_NONE,
            "entry_economics_source": "pending_entry_without_fill_authority",
            "entry_fill_verified": False,
            "execution_fact_intent_id": "",
            "execution_fact_filled_at": "",
            "execution_fact_venue_status": "",
        }

    pnl_cost_basis_usd = projection_cost_basis_usd if projection_cost_basis_usd > 0.0 else submitted_size_usd
    return {
        "submitted_size_usd": submitted_size_usd,
        "projection_cost_basis_usd": projection_cost_basis_usd,
        "effective_cost_basis_usd": submitted_size_usd,
        "effective_shares": projection_shares,
        "pnl_cost_basis_usd": pnl_cost_basis_usd,
        "effective_entry_price": projection_entry_price,
        "entry_price_avg_fill": 0.0,
        "shares_filled": 0.0,
        "filled_cost_basis_usd": 0.0,
        "entry_economics_authority": ENTRY_ECONOMICS_LEGACY_UNKNOWN,
        "fill_authority": FILL_AUTHORITY_NONE,
        "entry_economics_source": "position_current_projection",
        "entry_fill_verified": False,
        "execution_fact_intent_id": "",
        "execution_fact_filled_at": "",
        "execution_fact_venue_status": "",
    }






def _query_transitional_position_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, dict]:
    if not trade_ids:
        return {}
    columns = _table_columns(conn, "position_events")
    placeholders = ", ".join("?" for _ in trade_ids)
    if {"position_id", "payload_json", "occurred_at"}.issubset(columns):
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
        logger.warning("position_events table missing expected columns"); return {}
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
        # Non-settlement lifecycle hints are env-filtered by their caller scope.
    return hints


def _settlement_authority_smoke_summary(conn: sqlite3.Connection) -> dict:
    original_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rows = query_authoritative_settlement_rows(conn, limit=None)
    finally:
        conn.row_factory = original_row_factory
    ready_rows = 0
    learning_rows = 0
    degraded_rows = 0
    authority_levels: dict[str, int] = {}
    for row in rows:
        level = str(row.get("authority_level") or "unknown")
        authority_levels[level] = authority_levels.get(level, 0) + 1
        if row.get("is_degraded", False):
            degraded_rows += 1
        if row.get("metric_ready", False) and not row.get("is_degraded", False):
            ready_rows += 1
        if row.get("learning_snapshot_ready", False) and not row.get("is_degraded", False):
            learning_rows += 1

    surface_available = (
        (_table_exists(conn, "position_events") and _table_exists(conn, "position_current"))
        or _table_exists(conn, "decision_log")
    )
    return {
        "source": SETTLEMENT_AUTHORITY_DIAGNOSTIC_SOURCE,
        "surface_available": surface_available,
        "ready_rows": ready_rows,
        "learning_eligible_rows": learning_rows,
        "degraded_rows": degraded_rows,
        "authority_levels": authority_levels,
    }


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
        "execution": {
            "total": 0,
            "terminal_status_counts": {},
            "avg_fill_quality": None,
            "authority_scope": EXECUTION_FACT_AUTHORITY_SCOPE,
        },
        "outcome": {
            "total": 0,
            "wins": 0,
            "pnl_total": 0.0,
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
            "learning_eligible": False,
            "promotion_eligible": False,
        },
        "settlement_authority": _settlement_authority_smoke_summary(conn),
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
            "authority_scope": EXECUTION_FACT_AUTHORITY_SCOPE,
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
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
            "learning_eligible": False,
            "promotion_eligible": False,
        }
    summary["separation"]["execution_vs_outcome_gap"] = max(
        0,
        summary["execution"]["total"] - summary["outcome"]["total"],
    )
    return summary


def query_execution_event_summary(
    conn: sqlite3.Connection,
    *,
    limit: int | None = 500,
    not_before: str | None = None,
) -> dict:
    """Execution event summary from canonical position_events."""
    filters = []
    params: list[object] = []
    if not_before is not None:
        filters.append("occurred_at >= ?")
        params.append(not_before)
    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = f"""
        SELECT event_type, strategy_key
        FROM position_events
        {where_clause}
        ORDER BY occurred_at DESC
        """
    if limit is not None:
        query += "\n        LIMIT ?"
        params.append(limit)
    try:
        rows = conn.execute(query, params).fetchall()
    except Exception:
        rows = []

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
        "POSITION_OPEN_INTENT": "entry_attempted",
        "ENTRY_ORDER_FILLED": "entry_filled",
        "ENTRY_ORDER_REJECTED": "entry_rejected",
        "EXIT_ORDER_POSTED": "exit_attempted",
        "EXIT_ORDER_FILLED": "exit_filled",
        "EXIT_ORDER_VOIDED": "exit_fill_confirmed",
        "EXIT_ORDER_REJECTED": "exit_backoff_exhausted",
        "EXIT_RETRY_SCHEDULED": "exit_retry_scheduled",
    }

    for row in rows:
        event_type = str(row["event_type"])
        counter_key = mapping.get(event_type)
        if counter_key is None:
            continue
        overall[counter_key] += 1
        strategy = str(row["strategy_key"] or "unclassified")
        bucket = by_strategy.setdefault(strategy, _blank())
        bucket[counter_key] += 1

    return {
        "event_sample_size": len(rows),
        "overall": overall,
        "by_strategy": by_strategy,
    }

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
    }:
        terminal_exec_status = None
        voided_at = None
        filled_at = None
        exit_has_fill_finality = event_type == "EXIT_ORDER_FILLED"
        if event_type == "EXIT_ORDER_FILLED":
            terminal_exec_status = "filled"
            filled_at = timestamp or getattr(pos, "last_exit_at", None) or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_RETRY_SCHEDULED", "EXIT_BACKOFF_EXHAUSTED", "EXIT_ORDER_REJECTED", "EXIT_ORDER_VOIDED"}:
            terminal_exec_status = str(payload.get("status") or getattr(pos, "exit_state", "") or "rejected")
            voided_at = timestamp or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_ORDER_ATTEMPTED", "EXIT_ORDER_POSTED"}:
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
            posted_at=posted_at if event_type in {"EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED"} else None,
            filled_at=filled_at,
            voided_at=voided_at,
            submitted_price=submitted_price,
            fill_price=payload.get("fill_price") if exit_has_fill_finality else None,
            shares=(
                payload.get("shares")
                if payload.get("shares") is not None
                else getattr(pos, "effective_shares", getattr(pos, "shares", None))
            ) if exit_has_fill_finality else None,
            fill_quality=None,
            venue_status=str(payload.get("status") or status or "") or None,
            terminal_exec_status=terminal_exec_status,
            clear_fill_fields=not exit_has_fill_finality,
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
    event_type = "EXIT_FILL_CONFIRMED" if status == "CONFIRMED" else "EXIT_FILL_CHECKED"
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
        "status": "CONFIRMED",
        "fill_price": fill_price,
        "current_market_price": current_market_price,
        "best_bid": best_bid,
        "shares": getattr(pos, "effective_shares", getattr(pos, "shares", None)),
    }
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_FILLED",
        status="CONFIRMED",
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
