#!/usr/bin/env python3
"""Event-time floor-crossing investigation and repair loop for Zeus."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "total_loss_loop.toml"
OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit")
SCHEMA_VERSION = 3
_FULL_LOSS_RATIO = 0.95
# Bump only when a completed settlement backfill needs one bounded replay.
# Existing rows with the prior identity are revisited once, then converge.
_SETTLEMENT_BACKFILL_IDENTITY_POLICY_REVISION = "settlement_identity_v2"
_probe_lock = threading.Lock()
_probe_thread: threading.Thread | None = None
_probe_process_groups: set[int] = set()
_writer_lease_lock_fds: dict[str, int] = {}
_spawn_witness_fds: dict[str, int] = {}
_SPAWN_AMBIGUITY_SECONDS = 30.0
_STARTUP_BUDGET: dict[str, Any] | None = None
_STARTUP_RUN_QUEUE: dict[str, list[Path]] = {}
_STARTUP_RUN_CURSOR: dict[str, int] = {}
_STARTUP_RUN_REMAINING: dict[str, bool] = {}
_STARTUP_RUN_BATCH_LIMIT: dict[str, int] = {}
_TRIGGER_DEADLINE: float | None = None
_MAINTENANCE_DEADLINE: float | None = None
_EVIDENCE_BUILD_CONTEXT: dict[str, Any] | None = None
_LAST_EVIDENCE_CYCLE: dict[str, Any] = {}
_EVIDENCE_HASH_CACHE: dict[tuple[str, int, int], str] = {}


class ExecutionFactCapabilityError(RuntimeError):
    """Canonical entry-fact schema is absent or too old for loss attribution."""


class SettlementBasisPending(RuntimeError):
    """Execution-fact schema is valid, but command-deduped basis is not ready."""


class StartupMaintenanceDeferred(RuntimeError):
    """Startup maintenance exceeded its bounded slice and must resume later."""


class SchemaMaintenanceDeferred(StartupMaintenanceDeferred):
    """Existing memory schema is incomplete; explicit migration must retry."""


class _MaintenanceOutcome(list[str]):
    """Committed maintenance IDs remain observable after a late deadline."""

    def __init__(self, values: Iterable[str] = (), *, postcommit_deferred: bool = False) -> None:
        super().__init__(values)
        self.committed = True
        self.postcommit_deferred = postcommit_deferred


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).astimezone(UTC).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def digest(*parts: object, length: int = 24) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _startup_read_json_file(path: Path) -> Any:
    budget = _STARTUP_BUDGET
    max_bytes = int(budget["max_run_json_bytes"]) if budget is not None else 2**63
    try:
        _startup_guard()
        if path.stat().st_size > max_bytes:
            raise StartupMaintenanceDeferred("startup_maintenance_deferred:file_size")
        with path.open("rb") as handle:
            payload = json.loads(handle.read())
        _startup_guard()
        return payload
    except (OSError, json.JSONDecodeError) as exc:
        raise StartupMaintenanceDeferred(f"startup_maintenance_deferred:file_read:{type(exc).__name__}") from exc


def _startup_hash_file(path: Path) -> str:
    """Hash a bounded startup input without reading it past the shared deadline."""
    budget = _STARTUP_BUDGET
    max_bytes = int(budget["max_run_json_bytes"]) if budget is not None else 2**63
    try:
        _startup_guard()
        if path.stat().st_size > max_bytes:
            raise StartupMaintenanceDeferred("startup_maintenance_deferred:file_size")
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                _startup_guard()
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        _startup_guard()
        return hasher.hexdigest()
    except OSError as exc:
        raise StartupMaintenanceDeferred(f"startup_maintenance_deferred:file_read:{type(exc).__name__}") from exc


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    paths = cfg.setdefault("paths", {})
    for key in ("trades_db", "forecasts_db", "settings", "runtime", "prompt", "deploy_script", "pr_monitor"):
        raw = Path(str(paths[key])).expanduser()
        paths[key] = str(raw if raw.is_absolute() else (ROOT / raw).resolve())
    cfg["_config_path"] = str(path.resolve())
    return cfg


def runtime_dir(cfg: Mapping[str, Any]) -> Path:
    return Path(str(cfg["paths"]["runtime"]))


class _ClosingConnection:
    """Transparent SQLite proxy whose context exit also releases the FD."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_conn", "_closed"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            object.__setattr__(self, "_closed", True)

    def __enter__(self) -> "_ClosingConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            self._conn.__exit__(exc_type, exc, traceback)
        finally:
            self.close()
        return False


def open_ro(path: Path, *, timeout: float = 2.0) -> _ClosingConnection:
    active_deadlines = [value for value in (_TRIGGER_DEADLINE, _MAINTENANCE_DEADLINE) if value is not None]
    if _EVIDENCE_BUILD_CONTEXT is not None:
        active_deadlines.append(float(_EVIDENCE_BUILD_CONTEXT["deadline"]))
    if active_deadlines:
        remaining = min(active_deadlines) - time.monotonic()
        if remaining <= 0:
            if _EVIDENCE_BUILD_CONTEXT is not None:
                raise EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")
            raise sqlite3.OperationalError("interrupted: bounded database deadline")
        timeout = min(float(timeout), remaining)
    timeout = max(0.001, float(timeout))
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={max(1, int(timeout * 1000))}")
    return _ClosingConnection(conn)


def floor_price(cfg: Mapping[str, Any]) -> float:
    settings_path = Path(str(cfg["paths"]["settings"]))
    settings = _startup_read_json_file(settings_path) if _STARTUP_BUDGET is not None else read_json(settings_path, None)
    if not isinstance(settings, Mapping):
        raise RuntimeError("active execution floor unavailable: settings unreadable")
    current: Any = settings
    for part in str(cfg["loop"]["floor_config_key"]).split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise RuntimeError(f"active execution floor unavailable: missing {part}")
        current = current[part]
    try:
        value = float(current)
    except (TypeError, ValueError):
        raise RuntimeError("active execution floor unavailable: non-numeric value") from None
    if not math.isfinite(value) or not 0 < value < 1:
        raise RuntimeError("active execution floor unavailable: out-of-range value")
    return value


MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hard','precursor')),
    position_id TEXT NOT NULL,
    crossing_evidence_id TEXT NOT NULL,
    crossing_kind TEXT NOT NULL,
    held_token_id TEXT NOT NULL,
    held_direction TEXT NOT NULL,
    t_floor TEXT,
    floor_price REAL NOT NULL,
    observed_bid REAL,
    detected_at TEXT NOT NULL,
    priority REAL NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'blind',
    evidence_revision INTEGER NOT NULL DEFAULT 1,
    diagnosis_session_id TEXT,
    repair_session_id TEXT,
    root_relation TEXT,
    root_id TEXT,
    earliest_preventable_time TEXT,
    avoidable_loss_usd REAL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_crossing
    ON incidents(position_id, crossing_evidence_id, kind);
CREATE INDEX IF NOT EXISTS idx_evidence_queue
    ON incidents(kind,status,priority DESC,detected_at,incident_id);
CREATE TABLE IF NOT EXISTS incident_transitions (
    transition_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    run_id TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_quote_state (
    position_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    quote_seen_at TEXT NOT NULL,
    best_bid REAL,
    quote_status TEXT NOT NULL DEFAULT 'unknown',
    below_floor INTEGER NOT NULL CHECK (below_floor IN (0,1)),
    no_bid_episode_generation INTEGER NOT NULL DEFAULT 0,
    no_bid_episode_open INTEGER NOT NULL DEFAULT 0 CHECK (no_bid_episode_open IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backfill_quote_state (
    position_id TEXT PRIMARY KEY,
    exposure_fingerprint TEXT NOT NULL DEFAULT '',
    last_quote_seen_at TEXT,
    last_rowid INTEGER NOT NULL DEFAULT 0,
    last_bid REAL,
    below_floor INTEGER NOT NULL DEFAULT 0 CHECK (below_floor IN (0,1)),
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roots (
    root_id TEXT PRIMARY KEY,
    causal_seam TEXT NOT NULL,
    mechanism_fingerprint TEXT NOT NULL,
    earliest_divergence TEXT,
    affected_symbols_json TEXT NOT NULL DEFAULT '[]',
    reproduction TEXT NOT NULL,
    repair_sha TEXT,
    relationship_test TEXT,
    deployed_sha TEXT,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    measured_avoided_loss_usd REAL NOT NULL DEFAULT 0,
    utility REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_root_links (
    incident_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (incident_id, root_id)
);
CREATE TABLE IF NOT EXISTS fixes (
    fix_id TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    commit_sha TEXT,
    pr_url TEXT,
    relationship_test TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id TEXT PRIMARY KEY,
    fix_id TEXT NOT NULL,
    merge_sha TEXT,
    loaded_sha TEXT,
    deployed_at TEXT,
    verification_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    fix_id TEXT,
    information_lead_seconds REAL,
    decision_lead_seconds REAL,
    actuation_lead_seconds REAL,
    execution_lead_seconds REAL,
    avoidable_loss_usd REAL,
    false_exit_cost_usd REAL,
    recurrence INTEGER,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_runs (
    run_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    session_id TEXT,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    events_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spawn_intents (
    run_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    child_pid INTEGER,
    witness_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pre_spawn','child_started','persisted','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spawn_intents_incident_state
    ON spawn_intents(incident_id,state);
CREATE TABLE IF NOT EXISTS controller_debt (
    debt_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    config_fingerprint TEXT NOT NULL DEFAULT '',
    capacity_fingerprint TEXT NOT NULL DEFAULT '',
    data_fingerprint TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settlement_backfill_state (
    position_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    completed INTEGER NOT NULL CHECK (completed IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_writer_leases (
    cwd TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    child_pid INTEGER,
    lock_path TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loop_versions (
    version_id TEXT PRIMARY KEY,
    code_sha TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    benchmark_json TEXT NOT NULL DEFAULT '{}',
    activated_at TEXT NOT NULL
);
"""


_STARTUP_SCHEMA_TABLES = frozenset(
    {
        "meta",
        "incidents",
        "incident_transitions",
        "position_quote_state",
        "backfill_quote_state",
        "roots",
        "incident_root_links",
        "fixes",
        "deployments",
        "evaluations",
        "model_runs",
        "spawn_intents",
        "controller_debt",
        "settlement_backfill_state",
        "workspace_writer_leases",
        "loop_versions",
    }
)
_STARTUP_SCHEMA_COLUMNS = {
    "meta": {"key", "value", "updated_at"},
    "incidents": {
        "incident_id", "kind", "position_id", "crossing_evidence_id", "crossing_kind",
        "held_token_id", "held_direction", "t_floor", "floor_price", "observed_bid",
        "detected_at", "priority", "status", "stage", "evidence_revision",
        "diagnosis_session_id", "repair_session_id", "root_relation", "root_id",
        "earliest_preventable_time", "avoidable_loss_usd", "updated_at",
    },
    "incident_transitions": {
        "transition_id", "incident_id", "from_stage", "to_stage", "run_id", "reason", "created_at",
    },
    "position_quote_state": {
        "position_id", "evidence_id", "quote_seen_at", "best_bid", "quote_status", "below_floor",
        "no_bid_episode_generation", "no_bid_episode_open", "updated_at",
    },
    "backfill_quote_state": {
        "position_id", "exposure_fingerprint", "last_quote_seen_at", "last_rowid", "last_bid",
        "below_floor", "completed", "updated_at",
    },
    "roots": {
        "root_id", "causal_seam", "mechanism_fingerprint", "earliest_divergence", "affected_symbols_json",
        "reproduction", "repair_sha", "relationship_test", "deployed_sha", "recurrence_count",
        "measured_avoided_loss_usd", "utility", "updated_at",
    },
    "incident_root_links": {"incident_id", "root_id", "relation", "confidence", "created_at"},
    "fixes": {"fix_id", "root_id", "commit_sha", "pr_url", "relationship_test", "status", "created_at", "updated_at"},
    "deployments": {"deployment_id", "fix_id", "merge_sha", "loaded_sha", "deployed_at", "verification_json"},
    "evaluations": {
        "evaluation_id", "incident_id", "fix_id", "information_lead_seconds", "decision_lead_seconds",
        "actuation_lead_seconds", "execution_lead_seconds", "avoidable_loss_usd", "false_exit_cost_usd",
        "recurrence", "observed_at",
    },
    "model_runs": {
        "run_id", "incident_id", "stage", "session_id", "model", "reasoning_effort", "started_at",
        "completed_at", "status", "usage_json", "events_path",
    },
    "spawn_intents": {
        "run_id", "incident_id", "stage", "owner_pid", "child_pid", "witness_path", "state", "created_at", "updated_at",
    },
    "controller_debt": {
        "debt_id", "kind", "status", "reason", "updated_at", "fingerprint", "config_fingerprint",
        "capacity_fingerprint", "data_fingerprint", "attempts",
    },
    "settlement_backfill_state": {"position_id", "fingerprint", "completed", "updated_at"},
    "workspace_writer_leases": {"cwd", "run_id", "stage", "owner_pid", "child_pid", "lock_path", "acquired_at"},
    "loop_versions": {"version_id", "code_sha", "config_hash", "benchmark_json", "activated_at"},
}


def _startup_index_contract(
    conn: sqlite3.Connection,
    *,
    table: str,
    name: str,
    unique: bool,
    columns: tuple[str, ...],
    descending: tuple[bool, ...],
) -> bool:
    """Verify one index's table, uniqueness, SQL, order, and DESC flags."""
    _startup_guard()
    index_row = next(
        (
            row
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
            if str(row[1]) == name
        ),
        None,
    )
    if index_row is None or bool(index_row[2]) is not unique:
        return False
    catalog = conn.execute(
        "SELECT tbl_name,sql FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    if catalog is None or str(catalog[0]) != table or not catalog[1]:
        return False
    normalized_sql = re.sub(r"\s+", " ", str(catalog[1]).strip()).lower()
    normalized_sql = re.sub(r"\s*,\s*", ",", normalized_sql)
    expected_sql = re.sub(
        r"\s+",
        " ",
        (
            "CREATE UNIQUE INDEX " if unique else "CREATE INDEX "
        ) + name + " ON " + table + "(" + ",".join(
            f"{column}{' DESC' if is_desc else ''}" for column, is_desc in zip(columns, descending)
        ) + ")",
    ).lower()
    if normalized_sql != expected_sql:
        return False
    _startup_guard()
    detail = [
        row
        for row in conn.execute(f"PRAGMA index_xinfo({name})").fetchall()
        if int(row[5]) == 1
    ]
    if len(detail) != len(columns):
        return False
    return all(
        str(row[2]) == column and bool(row[3]) is is_desc
        for row, column, is_desc in zip(detail, columns, descending)
    )


def _startup_schema_complete(conn: sqlite3.Connection) -> bool:
    """Verify the existing schema with catalog-only reads before startup DDL."""
    _startup_guard()
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not _STARTUP_SCHEMA_TABLES.issubset(tables):
        return False
    for table, required in _STARTUP_SCHEMA_COLUMNS.items():
        _startup_guard()
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not required.issubset(columns):
            return False
    _startup_guard()
    return (
        _startup_index_contract(
            conn,
            table="incidents",
            name="idx_incident_crossing",
            unique=True,
            columns=("position_id", "crossing_evidence_id", "kind"),
            descending=(False, False, False),
        )
        and _startup_index_contract(
            conn,
            table="incidents",
            name="idx_incident_queue",
            unique=False,
            columns=("status", "stage", "kind", "priority", "detected_at"),
            descending=(False, False, False, True, False),
        )
        and _startup_index_contract(
            conn,
            table="spawn_intents",
            name="idx_spawn_intents_incident_state",
            unique=False,
            columns=("incident_id", "state"),
            descending=(False, False),
        )
        and _startup_index_contract(
            conn,
            table="incidents",
            name="idx_evidence_queue",
            unique=False,
            columns=("kind", "status", "priority", "detected_at", "incident_id"),
            descending=(False, False, True, False, False),
        )
    )


def _record_schema_debt(cfg: Mapping[str, Any], conn: sqlite3.Connection, reason: str) -> None:
    atomic_json(
        runtime_dir(cfg) / "schema-debt.json",
        {"kind": "memory_schema", "status": "retry_pending", "reason": reason, "updated_at": iso()},
    )
    try:
        conn.execute(
            "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(debt_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at",
            ("memory_schema", "schema_migration", "retry_pending", reason, iso()),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()


def _install_memory_deadline(conn: sqlite3.Connection) -> None:
    deadlines = [value for value in (_TRIGGER_DEADLINE, _MAINTENANCE_DEADLINE)]
    if _EVIDENCE_BUILD_CONTEXT is not None:
        deadlines.append(float(_EVIDENCE_BUILD_CONTEXT["deadline"]))
    deadlines = [value for value in deadlines if value is not None]
    if not deadlines:
        return
    deadline = min(deadlines)
    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
    conn.execute(f"PRAGMA busy_timeout={remaining_ms}")
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)


def _memory_deadline_guard() -> None:
    deadlines = [value for value in (_TRIGGER_DEADLINE, _MAINTENANCE_DEADLINE) if value is not None]
    if deadlines and time.monotonic() >= min(deadlines):
        raise sqlite3.OperationalError("interrupted: bounded memory deadline")


def memory(cfg: Mapping[str, Any], *, allow_schema_migration: bool = False) -> _ClosingConnection:
    path = runtime_dir(cfg) / "memory.db"
    if _EVIDENCE_BUILD_CONTEXT is not None:
        _evidence_guard()
    _memory_deadline_guard()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _EVIDENCE_BUILD_CONTEXT is not None:
        _evidence_guard()
    _memory_deadline_guard()
    fresh = not path.exists()
    startup_timeout = 5.0
    if _STARTUP_BUDGET is not None:
        startup_timeout = max(0.001, float(_STARTUP_BUDGET["deadline"]) - time.monotonic())
    elif _TRIGGER_DEADLINE is not None or _MAINTENANCE_DEADLINE is not None or _EVIDENCE_BUILD_CONTEXT is not None:
        deadlines = [value for value in (_TRIGGER_DEADLINE, _MAINTENANCE_DEADLINE)]
        if _EVIDENCE_BUILD_CONTEXT is not None:
            deadlines.append(float(_EVIDENCE_BUILD_CONTEXT["deadline"]))
        deadlines = [value for value in deadlines if value is not None]
        startup_timeout = max(0.001, min(deadlines) - time.monotonic())
    _memory_deadline_guard()
    conn = sqlite3.connect(path, timeout=startup_timeout)
    conn.row_factory = sqlite3.Row
    _startup_sql_budget(conn)
    _install_memory_deadline(conn)
    if _EVIDENCE_BUILD_CONTEXT is not None:
        _apply_evidence_sql_budget(conn, _EVIDENCE_BUILD_CONTEXT)
    if _STARTUP_BUDGET is not None or (not fresh and not allow_schema_migration):
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if journal_mode == "wal" and _startup_schema_complete(conn):
            conn.execute("PRAGMA foreign_keys=ON")
            _startup_guard()
            return _ClosingConnection(conn)
    if not fresh and not allow_schema_migration and _STARTUP_BUDGET is None:
        _record_schema_debt(cfg, conn, "memory_schema_incomplete_or_index_contract")
        conn.close()
        raise SchemaMaintenanceDeferred("memory_schema_deferred:explicit_migration_required")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(MEMORY_SCHEMA)
    backfill_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(backfill_quote_state)")
    }
    if "exposure_fingerprint" not in backfill_columns:
        conn.execute(
            "ALTER TABLE backfill_quote_state "
            "ADD COLUMN exposure_fingerprint TEXT NOT NULL DEFAULT ''"
        )
    incident_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(incidents)")
    }
    if "stage" not in incident_columns:
        conn.execute("ALTER TABLE incidents ADD COLUMN stage TEXT NOT NULL DEFAULT 'blind'")
    quote_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(position_quote_state)")
    }
    if "quote_status" not in quote_columns:
        conn.execute(
            "ALTER TABLE position_quote_state "
            "ADD COLUMN quote_status TEXT NOT NULL DEFAULT 'unknown'"
        )
    if "no_bid_episode_generation" not in quote_columns:
        conn.execute(
            "ALTER TABLE position_quote_state "
            "ADD COLUMN no_bid_episode_generation INTEGER NOT NULL DEFAULT 0"
        )
    if "no_bid_episode_open" not in quote_columns:
        conn.execute(
            "ALTER TABLE position_quote_state "
            "ADD COLUMN no_bid_episode_open INTEGER NOT NULL DEFAULT 0 "
            "CHECK (no_bid_episode_open IN (0,1))"
        )
    debt_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(controller_debt)")}
    for name, definition in (
        ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("config_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("capacity_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("data_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in debt_columns:
            conn.execute(f"ALTER TABLE controller_debt ADD COLUMN {name} {definition}")
    if not _startup_index_contract(
        conn,
        table="incidents",
        name="idx_incident_crossing",
        unique=True,
        columns=("position_id", "crossing_evidence_id", "kind"),
        descending=(False, False, False),
    ):
        conn.execute("DROP INDEX IF EXISTS idx_incident_crossing")
        conn.execute(
            "CREATE UNIQUE INDEX idx_incident_crossing "
            "ON incidents(position_id,crossing_evidence_id,kind)"
        )
    if not _startup_index_contract(
        conn,
        table="spawn_intents",
        name="idx_spawn_intents_incident_state",
        unique=False,
        columns=("incident_id", "state"),
        descending=(False, False),
    ):
        conn.execute("DROP INDEX IF EXISTS idx_spawn_intents_incident_state")
        conn.execute(
            "CREATE INDEX idx_spawn_intents_incident_state "
            "ON spawn_intents(incident_id,state)"
        )
    if not _startup_index_contract(
        conn,
        table="incidents",
        name="idx_incident_queue",
        unique=False,
        columns=("status", "stage", "kind", "priority", "detected_at"),
        descending=(False, False, False, True, False),
    ):
        conn.execute("DROP INDEX IF EXISTS idx_incident_queue")
        conn.execute(
            "CREATE INDEX idx_incident_queue "
            "ON incidents(status,stage,kind,priority DESC,detected_at)"
        )
    return _ClosingConnection(conn)


def memory_ro(cfg: Mapping[str, Any]) -> _ClosingConnection:
    path = runtime_dir(cfg) / "memory.db"
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return _ClosingConnection(conn)


def transition(
    conn: sqlite3.Connection,
    incident_id: str,
    to_stage: str,
    *,
    reason: str,
    run_id: str | None = None,
    status: str = "running",
) -> None:
    row = conn.execute(
        "SELECT stage FROM incidents WHERE incident_id=?",
        (incident_id,),
    ).fetchone()
    from_stage = str(row[0]) if row else None
    conn.execute(
        "UPDATE incidents SET stage=?,status=?,updated_at=? WHERE incident_id=?",
        (to_stage, status, iso(), incident_id),
    )
    stamp = iso()
    conn.execute(
        "INSERT INTO incident_transitions VALUES (?,?,?,?,?,?,?)",
        (
            digest(incident_id, from_stage, to_stage, run_id, stamp),
            incident_id,
            from_stage,
            to_stage,
            run_id,
            reason,
            stamp,
        ),
    )


def _transition_if_status(
    conn: sqlite3.Connection,
    incident_id: str,
    to_stage: str,
    *,
    expected_status: str,
    reason: str,
    run_id: str | None = None,
    status: str,
) -> bool:
    """CAS lifecycle transition used by crash/retry recovery paths."""

    row = conn.execute(
        "SELECT stage FROM incidents WHERE incident_id=? AND status=?",
        (incident_id, expected_status),
    ).fetchone()
    if row is None:
        return False
    stamp = iso()
    updated = conn.execute(
        "UPDATE incidents SET stage=?,status=?,updated_at=? "
        "WHERE incident_id=? AND status=?",
        (to_stage, status, stamp, incident_id, expected_status),
    )
    if updated.rowcount != 1:
        return False
    conn.execute(
        "INSERT INTO incident_transitions VALUES (?,?,?,?,?,?,?)",
        (
            digest(incident_id, row[0], to_stage, run_id, stamp),
            incident_id,
            str(row[0]),
            to_stage,
            run_id,
            reason,
            stamp,
        ),
    )
    return True


def meta_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO meta(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (key, str(value), iso()),
    )


def held_token(row: Mapping[str, Any]) -> str:
    if str(row.get("direction") or "").lower() == "buy_no":
        return str(row.get("no_token_id") or row.get("token_id") or "")
    return str(row.get("token_id") or row.get("no_token_id") or "")


def held_sell_direction(row: Mapping[str, Any]) -> str:
    return "sell_no" if str(row.get("direction") or "").lower() == "buy_no" else "sell_yes"


def effective_shares(position: Mapping[str, Any]) -> float:
    """Return Chain-authoritative exposure without reviving a zero Chain fact."""

    chain = _float(position.get("chain_shares"))
    if chain is not None:
        return max(0.0, chain)
    return max(0.0, _float(position.get("shares")) or 0.0)


def has_material_share_precision(position: Mapping[str, Any]) -> bool:
    """True when at least one venue-representable 0.01-share unit remains."""

    return math.floor(effective_shares(position) * 100.0 + 1e-9) >= 1


def _depth_best_bid(raw: object) -> tuple[bool, float | None]:
    """Return (depth_is_authoritative, executable top bid)."""

    if not isinstance(raw, str) or not raw.strip():
        return False, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False, None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("bids"), list):
        return False, None
    prices: list[float] = []
    for level in payload["bids"]:
        if not isinstance(level, Mapping):
            return False, None
        price = _float(level.get("price"))
        size = _float(level.get("size"))
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            return False, None
        prices.append(price)
    return True, max(prices) if prices else None


def reconcile_held_quote(quote: Mapping[str, Any]) -> tuple[str, float | None]:
    """Classify one internally consistent held-side executable quote witness."""

    depth_is_authoritative, depth_bid = _depth_best_bid(
        quote.get("depth_before_json")
    )
    scalar = _float(quote.get("best_bid_before"))
    if not depth_is_authoritative:
        return "quote_incomplete", None
    if depth_bid is None:
        return (
            ("no_bid", None)
            if scalar is None or scalar <= 0
            else ("quote_integrity_conflict", None)
        )
    if scalar is not None and (
        scalar <= 0 or not math.isclose(scalar, depth_bid, abs_tol=1e-9)
    ):
        return "quote_integrity_conflict", depth_bid
    return "executable", depth_bid


def authoritative_held_bid(quote: Mapping[str, Any]) -> float | None:
    """Return a bid only from a complete, internally consistent witness."""

    status, bid = reconcile_held_quote(quote)
    return bid if status == "executable" else None


def tracked_positions(conn: sqlite3.Connection, *, history_days: int) -> dict[str, dict[str, Any]]:
    cutoff = iso(now() - timedelta(days=history_days))
    placeholders = ",".join("?" for _ in OPEN_PHASES)
    rows = conn.execute(
        f"""
        SELECT pc.*,
               (
                   SELECT MIN(pe.occurred_at)
                     FROM position_events pe
                    WHERE pe.position_id=pc.position_id
                      AND pe.event_type IN ('ENTRY_ORDER_FILLED','VENUE_POSITION_OBSERVED','CHAIN_SYNCED')
               ) AS exposure_start_at,
               COALESCE((
                   SELECT MIN(pe.occurred_at)
                     FROM position_events pe
                    WHERE pe.position_id=pc.position_id
                      AND pe.event_type IN ('EXIT_ORDER_FILLED','SETTLED','ADMIN_VOIDED')
               ), CASE
                    WHEN pc.phase NOT IN ('pending_entry','active','day0_window','pending_exit')
                    THEN COALESCE(pc.settled_at,pc.updated_at)
               END) AS exposure_end_at
          FROM position_current pc
         WHERE (
             pc.phase IN ({placeholders})
             AND COALESCE(pc.chain_shares,pc.shares,0) > 0
         ) OR (
             COALESCE(pc.shares,0) > 0
             AND COALESCE(pc.settled_at,pc.updated_at) >= ?
         )
        """,
        (*OPEN_PHASES, cutoff),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        token = held_token(row)
        if token and has_material_share_precision(row):
            row["held_token_id"] = token
            row["held_sell_direction"] = held_sell_direction(row)
            result[str(row["position_id"])] = row
    return result


def _position_with_exposure(
    conn: sqlite3.Connection,
    position_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT pc.*,
               (
                   SELECT MIN(pe.occurred_at)
                     FROM position_events pe
                    WHERE pe.position_id=pc.position_id
                      AND pe.event_type IN ('ENTRY_ORDER_FILLED','VENUE_POSITION_OBSERVED','CHAIN_SYNCED')
               ) AS exposure_start_at,
               COALESCE((
                   SELECT MIN(pe.occurred_at)
                     FROM position_events pe
                    WHERE pe.position_id=pc.position_id
                      AND pe.event_type IN ('EXIT_ORDER_FILLED','SETTLED','ADMIN_VOIDED')
               ), CASE
                    WHEN pc.phase NOT IN ('pending_entry','active','day0_window','pending_exit')
                    THEN COALESCE(pc.settled_at,pc.updated_at)
               END) AS exposure_end_at
          FROM position_current pc
         WHERE pc.position_id=?
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    position = dict(row)
    position["held_token_id"] = held_token(position)
    position["held_sell_direction"] = held_sell_direction(position)
    return position


def revalidate_blind_hard_incidents(
    mem: sqlite3.Connection,
    trades: sqlite3.Connection,
) -> int:
    """Retire queued legacy triggers disproved by current detector invariants."""

    rows = mem.execute(
        "SELECT incident_id,position_id,crossing_evidence_id,crossing_kind,floor_price "
        "FROM incidents WHERE kind='hard' AND stage='blind' "
        "AND status IN ('queued','retry_pending')"
    ).fetchall()
    retired = 0
    for row in rows:
        position = _position_with_exposure(trades, str(row["position_id"]))
        quote_row = trades.execute(
            "SELECT * FROM execution_feasibility_evidence WHERE evidence_id=?",
            (row["crossing_evidence_id"],),
        ).fetchone()
        if position is None or quote_row is None:
            continue
        quote = dict(quote_row)
        status, bid = reconcile_held_quote(quote)
        incident_floor = _float(row["floor_price"])
        if incident_floor is None or not 0 < incident_floor < 1:
            continue
        reason = None
        if not _quote_within_exposure(position, str(quote["quote_seen_at"])):
            reason = "detector_revalidated:crossing_outside_exposure"
        elif (
            position.get("phase") in OPEN_PHASES
            and not has_material_share_precision(position)
        ):
            reason = "detector_revalidated:unrepresentable_residual_dust"
        elif status in {"quote_incomplete", "quote_integrity_conflict"}:
            reason = f"detector_revalidated:{status}"
        elif row["crossing_kind"] == "below_floor" and (
            status != "executable" or bid is None or bid >= incident_floor
        ):
            reason = "detector_revalidated:below_floor_refuted"
        elif row["crossing_kind"] == "no_bid" and status != "no_bid":
            reason = "detector_revalidated:no_bid_refuted"
        if reason is None:
            continue
        stamp = iso()
        updated = mem.execute(
            "UPDATE incidents SET stage='observing',status='observing',updated_at=? "
            "WHERE incident_id=? AND stage='blind' "
            "AND status IN ('queued','retry_pending')",
            (stamp, row["incident_id"]),
        )
        if updated.rowcount != 1:
            continue
        mem.execute(
            "INSERT INTO incident_transitions VALUES (?,?,?,?,?,?,?)",
            (
                digest(row["incident_id"], "blind", "observing", None, stamp),
                row["incident_id"],
                "blind",
                "observing",
                None,
                reason,
                stamp,
            ),
        )
        retired += 1
    return retired


def _exposure_fingerprint(position: Mapping[str, Any]) -> str:
    return digest(
        position["position_id"],
        position.get("exposure_start_at"),
        position.get("exposure_end_at"),
    )


def _quote_within_exposure(position: Mapping[str, Any], quote_seen_at: str) -> bool:
    """True only while the position had economic exposure.

    Open positions without a reconstructed start remain eligible for current
    observations, but historical replay requires an authoritative start.
    """

    quote_at = parse_time(quote_seen_at)
    if quote_at is None:
        return False
    start = parse_time(position.get("exposure_start_at"))
    end = parse_time(position.get("exposure_end_at"))
    if start is not None and quote_at < start:
        return False
    if end is not None and quote_at >= end:
        return False
    return end is None or start is not None


def _insert_incident(
    conn: sqlite3.Connection,
    *,
    position: Mapping[str, Any],
    evidence_id: str,
    quote_seen_at: str,
    bid: float | None,
    floor: float,
    kind: str,
    priority: float,
    allow_new_episode: bool = False,
) -> str | None:
    position_id = str(position["position_id"])
    incident_id = digest(position_id, evidence_id) if kind == "hard" else digest(kind, position_id)
    crossing_kind = "no_bid" if bid is None else ("below_floor" if kind == "hard" else "precursor")
    if kind == "hard":
        existing = conn.execute(
            "SELECT incident_id,t_floor,status FROM incidents "
            "WHERE position_id=? AND kind='hard' AND crossing_kind=? "
            "AND status IN ('queued','running','retry_pending') "
            "ORDER BY t_floor LIMIT 1",
            (position_id, crossing_kind),
        ).fetchone()
        existing_floor = parse_time(str(existing[1])) if existing and existing[1] else None
        candidate_floor = parse_time(quote_seen_at)
        if bid is not None and existing and candidate_floor and (existing_floor is None or candidate_floor < existing_floor):
            conn.execute(
                "UPDATE incidents SET crossing_evidence_id=?,crossing_kind=?,t_floor=?,"
                "observed_bid=?,evidence_revision=evidence_revision+1,updated_at=? "
                "WHERE incident_id=?",
                (evidence_id, crossing_kind, quote_seen_at, bid, iso(), existing[0]),
            )
            return str(existing[0])
        if existing and not allow_new_episode:
            return None
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO incidents(
            incident_id,kind,position_id,crossing_evidence_id,crossing_kind,
            held_token_id,held_direction,t_floor,floor_price,observed_bid,
            detected_at,priority,status,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            incident_id,
            kind,
            position_id,
            evidence_id,
            crossing_kind,
            position["held_token_id"],
            position["held_sell_direction"],
            quote_seen_at if kind == "hard" and bid is not None else None,
            floor,
            bid,
            iso(),
            priority,
            "queued",
            iso(),
        ),
    )
    return incident_id if conn.total_changes > before else None


def _observe_quote(
    mem: sqlite3.Connection,
    position: Mapping[str, Any],
    quote: Mapping[str, Any],
    floor: float,
) -> str | None:
    position_id = str(position["position_id"])
    evidence_id = str(quote["evidence_id"])
    seen_at = str(quote["quote_seen_at"])
    if not _quote_within_exposure(position, seen_at):
        return None
    quote_status, reconciled_bid = reconcile_held_quote(quote)
    bid = reconciled_bid if quote_status == "executable" else None
    below = bid is not None and bid < floor
    no_bid = quote_status == "no_bid"
    previous = mem.execute(
        "SELECT below_floor,quote_seen_at,best_bid,quote_status,"
        "no_bid_episode_generation,no_bid_episode_open "
        "FROM position_quote_state WHERE position_id=?",
        (position_id,),
    ).fetchone()
    created = None
    episode_generation = int(previous[4]) if previous is not None else 0
    episode_open = bool(previous[5]) if previous is not None else False
    previous_at = parse_time(str(previous[1])) if previous else None
    seen_time = parse_time(seen_at)
    out_of_order = previous_at is not None and seen_time is not None and seen_time < previous_at
    no_bid_episode: sqlite3.Row | None = None
    if no_bid:
        no_bid_episode = mem.execute(
            "SELECT incident_id FROM incidents WHERE position_id=? AND kind='hard' "
            "AND crossing_kind='no_bid' ORDER BY updated_at DESC,detected_at DESC LIMIT 1",
            (position_id,),
        ).fetchone()
        if (
            episode_generation == 0
            and not episode_open
            and previous is not None
            and previous[3] == "no_bid"
            and no_bid_episode
        ):
            episode_open = True
    if below:
        earliest = mem.execute(
            "SELECT incident_id,t_floor,status FROM incidents "
            "WHERE position_id=? AND kind='hard' AND crossing_kind='below_floor' "
            "AND t_floor IS NOT NULL ORDER BY t_floor LIMIT 1",
            (position_id,),
        ).fetchone()
        earliest_at = parse_time(str(earliest[1])) if earliest else None
        if earliest and seen_time and (earliest_at is None or seen_time < earliest_at):
            reopen = str(earliest[2]) not in {"queued", "running", "retry_pending"}
            mem.execute(
                "UPDATE incidents SET crossing_evidence_id=?,t_floor=?,observed_bid=?,"
                "evidence_revision=evidence_revision+1,status=CASE WHEN ? THEN 'queued' ELSE status END,"
                "stage=CASE WHEN ? THEN 'blind' ELSE stage END,updated_at=? WHERE incident_id=?",
                (evidence_id, seen_at, bid, int(reopen), int(reopen), iso(), earliest[0]),
            )
            created = str(earliest[0])
    if created is None and no_bid and episode_open and no_bid_episode is not None and not out_of_order:
        mem.execute(
            "UPDATE incidents SET crossing_evidence_id=?,evidence_revision=evidence_revision+1,"
            "updated_at=? WHERE incident_id=?",
            (evidence_id, iso(), no_bid_episode[0]),
        )
        created = str(no_bid_episode[0])
    if created is None and (
        (below and (previous is None or not bool(previous[0])))
        or (no_bid and (not episode_open or no_bid_episode is None))
    ):
        created = _insert_incident(
            mem,
            position=position,
            evidence_id=evidence_id,
            quote_seen_at=seen_at,
            bid=(None if no_bid else bid),
            floor=floor,
            kind="hard",
            priority=1_000_000.0,
            allow_new_episode=bool(no_bid and not episode_open and no_bid_episode is not None),
        )
    if out_of_order:
        return created
    state_bid = reconciled_bid
    state_below = int(below)
    state_quote_status = quote_status
    if quote_status == "executable" and bid is not None and bid >= floor and episode_open:
        episode_open = False
        episode_generation += 1
    if quote_status == "quote_incomplete" and previous is not None:
        state_bid = previous[2]
        state_below = int(previous[0])
        if episode_open:
            # An incomplete book is not recovery. Preserve the durable
            # no-bid episode marker across restarts and malformed quotes.
            state_quote_status = "no_bid"
    if no_bid:
        episode_open = True
    mem.execute(
        """
        INSERT INTO position_quote_state(
            position_id,evidence_id,quote_seen_at,best_bid,quote_status,below_floor,
            no_bid_episode_generation,no_bid_episode_open,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(position_id) DO UPDATE SET
            evidence_id=excluded.evidence_id,
            quote_seen_at=excluded.quote_seen_at,
            best_bid=excluded.best_bid,
            quote_status=excluded.quote_status,
            below_floor=excluded.below_floor,
            no_bid_episode_generation=excluded.no_bid_episode_generation,
            no_bid_episode_open=excluded.no_bid_episode_open,
            updated_at=excluded.updated_at
        """,
        (
            position_id,
            evidence_id,
            seen_at,
            state_bid,
            state_quote_status,
            state_below,
            episode_generation,
            int(episode_open),
            iso(),
        ),
    )
    return created


def _latest_quotes(
    trades: sqlite3.Connection,
    positions: Iterable[Mapping[str, Any]],
    *,
    deadline: float | None = None,
) -> dict[str, dict[str, Any]]:
    if deadline is not None and time.monotonic() >= deadline:
        raise sqlite3.OperationalError("interrupted: maintenance budget")
    result: dict[str, dict[str, Any]] = {}
    for position in positions:
        if deadline is not None and time.monotonic() >= deadline:
            raise sqlite3.OperationalError("interrupted: maintenance budget")
        row = trades.execute(
            """
            SELECT evidence_id,token_id,direction,quote_seen_at,best_bid_before,
                   best_ask_before,depth_before_json,book_hash_before
              FROM execution_feasibility_latest
             WHERE token_id=? AND direction=?
             LIMIT 1
            """,
            (position["held_token_id"], position["held_sell_direction"]),
        ).fetchone()
        current = dict(row) if row is not None else None
        if current is not None and reconcile_held_quote(current)[0] != "quote_incomplete":
            result[str(position["position_id"])] = current
            continue
        authoritative_row = trades.execute(
            "SELECT evidence_id,token_id,direction,quote_seen_at,best_bid_before,"
            "best_ask_before,depth_before_json,book_hash_before "
            "FROM execution_feasibility_evidence "
            "WHERE token_id=? AND direction=? AND depth_before_json IS NOT NULL "
            "ORDER BY quote_seen_at DESC,rowid DESC LIMIT 1",
            (position["held_token_id"], position["direction"]),
        ).fetchone()
        if authoritative_row is None:
            if current is not None:
                result[str(position["position_id"])] = current
            continue
        authoritative = dict(authoritative_row)
        if reconcile_held_quote(authoritative)[0] not in {"executable", "no_bid"}:
            if current is not None:
                result[str(position["position_id"])] = current
            continue
        authoritative["_current_quote"] = current
        result[str(position["position_id"])] = authoritative
    return result


def _new_quote_rows(
    trades: sqlite3.Connection,
    cursor: int,
    cutoff: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = trades.execute(
        """
        SELECT rowid AS quote_rowid,evidence_id,token_id,direction,quote_seen_at,
               best_bid_before,best_ask_before,depth_before_json,book_hash_before
          FROM execution_feasibility_evidence
         WHERE rowid > ? AND quote_seen_at >= ?
           AND direction IN ('buy_yes','buy_no')
         ORDER BY rowid LIMIT ?
        """,
        (cursor, cutoff, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def backfill_step(
    mem: sqlite3.Connection,
    trades: sqlite3.Connection,
    positions: Mapping[str, Mapping[str, Any]],
    *,
    cutoff: str,
    floor: float,
    row_limit: int = 250,
    budget_ms: float = 50.0,
) -> list[str]:
    """Advance one historical position without delaying the live cursor lane."""

    candidate = None
    for position_id in sorted(positions):
        state = mem.execute(
            "SELECT * FROM backfill_quote_state WHERE position_id=?",
            (position_id,),
        ).fetchone()
        current_fingerprint = _exposure_fingerprint(positions[position_id])
        if (
            state is None
            or str(state["exposure_fingerprint"] or "") != current_fingerprint
            or not bool(state["completed"])
        ):
            candidate = (positions[position_id], state)
            break
    if candidate is None:
        return []
    position, state = candidate
    exposure_start = position.get("exposure_start_at")
    if not exposure_start:
        mem.execute(
            "INSERT INTO backfill_quote_state(position_id,exposure_fingerprint,completed,updated_at) "
            "VALUES (?,?,1,?) ON CONFLICT(position_id) DO UPDATE SET "
            "exposure_fingerprint=excluded.exposure_fingerprint,completed=1,updated_at=excluded.updated_at",
            (position["position_id"], _exposure_fingerprint(position), iso()),
        )
        return []
    fingerprint = _exposure_fingerprint(position)
    if state is not None and str(state["exposure_fingerprint"] or "") != fingerprint:
        state = None
    replay_start = max(filter(None, (parse_time(cutoff), parse_time(str(exposure_start)))))
    replay_start_iso = iso(replay_start)
    exposure_end = position.get("exposure_end_at")
    last_at = str(state["last_quote_seen_at"] or replay_start_iso) if state else replay_start_iso
    last_rowid = int(state["last_rowid"] or 0) if state else 0
    deadline = time.monotonic() + max(0.001, budget_ms / 1000.0)
    trades.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        rows = trades.execute(
            """
            SELECT rowid AS quote_rowid,evidence_id,token_id,direction,quote_seen_at,
                   best_bid_before,best_ask_before,depth_before_json,book_hash_before
              FROM execution_feasibility_evidence
             WHERE token_id=? AND quote_seen_at >= ?
               AND (? IS NULL OR quote_seen_at < ?)
               AND direction IN ('buy_yes','buy_no')
               AND (quote_seen_at > ? OR (quote_seen_at = ? AND rowid > ?))
             ORDER BY quote_seen_at,rowid LIMIT ?
            """,
            (
                position["held_token_id"], replay_start_iso,
                exposure_end, exposure_end,
                last_at, last_at, last_rowid, row_limit,
            ),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "interrupted" not in str(exc).lower():
            raise
        return []
    finally:
        trades.set_progress_handler(None, 0)
    previous_below = bool(state["below_floor"]) if state else False
    previous_bid = state["last_bid"] if state else None
    created: list[str] = []
    found_crossing = False
    tail: dict[str, Any] | None = None
    processed = 0
    for raw in rows:
        if time.monotonic() >= deadline:
            break
        quote = dict(raw)
        if not _quote_within_exposure(position, str(quote["quote_seen_at"])):
            continue
        tail = quote
        processed += 1
        quote_status, bid = reconcile_held_quote(quote)
        if quote_status not in {"executable", "no_bid"}:
            continue
        below = bid is not None and bid < floor
        if below and not previous_below:
            found_crossing = True
            ident = _insert_incident(
                mem,
                position=position,
                evidence_id=str(quote["evidence_id"]),
                quote_seen_at=str(quote["quote_seen_at"]),
                bid=bid,
                floor=floor,
                kind="hard",
                priority=1_000_000.0,
            )
            if ident:
                created.append(ident)
            previous_below = True
            previous_bid = bid
            break
        previous_below = below
        previous_bid = bid
    if rows:
        if tail is None:
            return []
        completed = int(found_crossing or (processed == len(rows) and len(rows) < row_limit))
        mem.execute(
            """
            INSERT INTO backfill_quote_state(position_id,exposure_fingerprint,last_quote_seen_at,last_rowid,last_bid,below_floor,completed,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(position_id) DO UPDATE SET
                exposure_fingerprint=excluded.exposure_fingerprint,
                last_quote_seen_at=excluded.last_quote_seen_at,last_rowid=excluded.last_rowid,
                last_bid=excluded.last_bid,below_floor=excluded.below_floor,
                completed=excluded.completed,updated_at=excluded.updated_at
            """,
            (position["position_id"], fingerprint, tail["quote_seen_at"], tail["quote_rowid"], previous_bid, int(previous_below), completed, iso()),
        )
    else:
        mem.execute(
            "INSERT INTO backfill_quote_state(position_id,exposure_fingerprint,completed,updated_at) VALUES (?,?,1,?) "
            "ON CONFLICT(position_id) DO UPDATE SET exposure_fingerprint=excluded.exposure_fingerprint,"
            "completed=1,updated_at=excluded.updated_at",
            (position["position_id"], fingerprint, iso()),
        )
    return created


def _entry_execution_fill_aggregate(
    trades: sqlite3.Connection,
    position_id: str,
) -> dict[str, Any] | None:
    """Read the command-deduplicated original entry basis, fail closed on gaps."""

    try:
        from src.state.db import query_entry_execution_fill_aggregate
    except (ImportError, ModuleNotFoundError):
        raise ExecutionFactCapabilityError("execution_fact_schema_unavailable:import")
    try:
        columns = {
            str(row[1])
            for row in trades.execute("PRAGMA table_info(execution_fact)").fetchall()
        }
    except sqlite3.Error as exc:
        raise ExecutionFactCapabilityError(
            f"execution_fact_schema_unavailable:{exc}"
        ) from exc
    required = {
        "intent_id", "position_id", "command_id", "order_role", "filled_at",
        "posted_at", "fill_price", "shares", "terminal_exec_status", "venue_status",
    }
    if not required.issubset(columns):
        missing = ",".join(sorted(required - columns))
        raise ExecutionFactCapabilityError(
            f"execution_fact_schema_unavailable:missing={missing}"
        )
    try:
        aggregate = query_entry_execution_fill_aggregate(
            trades, str(position_id), strict=False
        )
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "no such table" in message or "no such column" in message:
            raise ExecutionFactCapabilityError(
                f"execution_fact_schema_unavailable:{exc}"
            ) from exc
        raise
    except (KeyError, TypeError, ValueError):
        return None
    return dict(aggregate) if isinstance(aggregate, Mapping) else None


def _settlement_economic_identity(
    position: Mapping[str, Any], payload: Mapping[str, Any]
) -> str | None:
    settlement_price = _float(position.get("settlement_price"))
    if settlement_price is None:
        settlement_price = _float(
            payload.get("settlement_price", payload.get("outcome"))
        )
    if settlement_price is None:
        return None
    return digest("settlement_economics", settlement_price)


def _settlement_full_loss_candidate(
    trades: sqlite3.Connection,
    position: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a canonical settlement-backed loss, never inventing a quote floor."""

    position_id = str(position.get("position_id") or "")
    if not position_id or not has_material_share_precision(position):
        return None
    terminal = trades.execute(
        "SELECT * FROM position_events WHERE position_id=? AND event_type='SETTLED' "
        "ORDER BY sequence_no DESC,occurred_at DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    if terminal is None:
        return None
    terminal_row = dict(terminal)
    payload = read_json_text(str(terminal_row.get("payload_json") or "{}"))
    # The canonical position projection is authoritative when present.  Payload
    # fallbacks are accepted only for older projections that lack settlement_price.
    settlement_price = _float(position.get("settlement_price"))
    if settlement_price is None:
        settlement_price = _float(
            payload.get("settlement_price", payload.get("outcome"))
        )
    if settlement_price is None or settlement_price > 0:
        return None
    if any(
        str(row[0]) == "EXIT_ORDER_FILLED"
        for row in trades.execute(
            "SELECT event_type FROM position_events WHERE position_id=? "
            "AND event_type='EXIT_ORDER_FILLED' AND sequence_no < ?",
            (position_id, terminal_row.get("sequence_no")),
        ).fetchall()
    ):
        return None
    aggregate = _entry_execution_fill_aggregate(trades, position_id)
    if aggregate is None or aggregate.get("entry_fill_command_identity_complete") is not True:
        raise SettlementBasisPending(
            f"entry_fill_command_identity_pending:position_id={position_id}"
        )
    basis = _float(aggregate.get("filled_cost_basis_usd"))
    realized = _float(position.get("realized_pnl_usd"))
    if basis is None or basis <= 0 or realized is None or not math.isfinite(realized):
        raise SettlementBasisPending(
            f"entry_fill_basis_pending:position_id={position_id}"
        )
    if realized >= 0 or -realized < _FULL_LOSS_RATIO * basis:
        return None
    command_ids = aggregate.get("execution_fact_command_ids")
    if isinstance(command_ids, (list, tuple)) and command_ids:
        entry_identity = digest(
            "entry_commands", *sorted(str(value) for value in command_ids)
        )
    else:
        entry_identity = digest("entry_basis", basis)
    # Chain mirrors may emit many SETTLED rows for one terminal fact.  Anchor
    # on stable economics only; event IDs, timestamps, source and enrichment
    # are projection metadata, not a new loss.
    payout_identity = _settlement_economic_identity(position, payload)
    if payout_identity is None:
        return None
    evidence_id = digest(
        position_id, "settlement_full_loss", payout_identity
    )
    settled_at = str(terminal_row.get("occurred_at") or position.get("settled_at") or "")
    return {
        "position_id": position_id,
        "evidence_id": evidence_id,
        "event_id": str(terminal_row.get("event_id") or ""),
        "settled_at": settled_at,
        "settlement_price": settlement_price,
        "payout_identity": payout_identity,
        "entry_identity": entry_identity,
        "payload": payload,
        "basis": basis,
        "realized": realized,
    }


def _insert_settlement_full_loss_incident(
    mem: sqlite3.Connection,
    position: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    floor: float,
) -> str | None:
    """Persist settlement truth as a hard incident with no synthetic quote facts."""

    incident_id = digest(position["position_id"], candidate["evidence_id"])
    existing = mem.execute(
        "SELECT incident_id FROM incidents WHERE incident_id=? OR "
        "(position_id=? AND crossing_evidence_id=? AND kind='hard')",
        (incident_id, position["position_id"], candidate["evidence_id"]),
    ).fetchone()
    if existing is not None:
        _consolidate_legacy_settlement_incidents(
            mem, str(position["position_id"]), str(existing["incident_id"])
        )
        return None
    mem.execute(
        "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
        "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'blind',?)",
        (
            incident_id, "hard", position["position_id"], candidate["evidence_id"],
            "settlement_full_loss", held_token(position), held_sell_direction(position),
            None, floor, None, candidate["settled_at"] or iso(),
            1_000_000_000.0, "queued", iso(),
        ),
    )
    _consolidate_legacy_settlement_incidents(
        mem, str(position["position_id"]), incident_id
    )
    return incident_id


def _consolidate_legacy_settlement_incidents(
    mem: sqlite3.Connection,
    position_id: str,
    canonical_id: str,
) -> None:
    """Stop future duplicate queueing without killing an active model run."""

    rows = mem.execute(
        "SELECT incident_id,stage,status FROM incidents WHERE position_id=? "
        "AND crossing_kind='settlement_full_loss' AND incident_id<>? "
        "AND status IN ('queued','retry_pending')",
        (position_id, canonical_id),
    ).fetchall()
    for row in rows:
        transition(
            mem,
            str(row["incident_id"]),
            str(row["stage"]),
            reason=f"superseded_by_stable_settlement_identity:{canonical_id}",
            status="observing",
        )


def _revise_settlement_non_loss_incidents(
    mem: sqlite3.Connection,
    trades: sqlite3.Connection,
    position: Mapping[str, Any],
) -> None:
    """Retire an existing loss incident when canonical settlement corrects it."""

    terminal = trades.execute(
        "SELECT payload_json FROM position_events WHERE position_id=? AND event_type='SETTLED' "
        "ORDER BY sequence_no DESC,occurred_at DESC LIMIT 1",
        (position.get("position_id"),),
    ).fetchone()
    if terminal is None or _settlement_economic_identity(
        position, read_json_text(str(terminal[0] or "{}"))
    ) is None:
        return
    rows = mem.execute(
        "SELECT incident_id,stage FROM incidents WHERE position_id=? "
        "AND kind='hard' AND crossing_kind='settlement_full_loss' "
        "AND status IN ('queued','running','retry_pending')",
        (position.get("position_id"),),
    ).fetchall()
    for row in rows:
        transition(
            mem,
            str(row["incident_id"]),
            str(row["stage"]),
            reason="canonical_settlement_no_longer_full_loss",
            status="observing",
        )


def _settlement_backfill_fingerprint(position: Mapping[str, Any]) -> str:
    return digest(
        _SETTLEMENT_BACKFILL_IDENTITY_POLICY_REVISION,
        position.get("position_id"), position.get("settled_at"),
        position.get("updated_at"), position.get("realized_pnl_usd"),
        position.get("settlement_price"), position.get("shares"),
        position.get("chain_shares"),
    )


def _settlement_backfill_positions(
    cfg: Mapping[str, Any],
    trades: sqlite3.Connection,
    mem: sqlite3.Connection,
    *,
    history_days: int,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    loop_cfg = cfg["loop"]
    range_days = max(
        history_days,
        int(loop_cfg.get("settlement_backfill_days", history_days)),
        int(loop_cfg.get("settlement_bootstrap_days", 0)),
        int(loop_cfg.get("backfill_history_days", 0)),
    )
    cutoff = iso(now() - timedelta(days=range_days))
    limit = max(1, int(loop_cfg.get("settlement_backfill_positions_per_cycle", 32)))
    if deadline is not None and time.monotonic() >= deadline:
        raise sqlite3.OperationalError("interrupted: maintenance budget")
    rows = trades.execute(
        "SELECT * FROM position_current WHERE phase='settled' "
        "AND COALESCE(settled_at,updated_at) >= ? "
        "ORDER BY COALESCE(settled_at,updated_at),position_id LIMIT ?",
        (cutoff, limit * 4),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        if deadline is not None and time.monotonic() >= deadline:
            raise sqlite3.OperationalError("interrupted: maintenance budget")
        position = dict(raw)
        terminal = trades.execute(
            "SELECT event_id,occurred_at,payload_json FROM position_events "
            "WHERE position_id=? AND event_type='SETTLED' "
            "ORDER BY sequence_no DESC,occurred_at DESC LIMIT 1",
            (position["position_id"],),
        ).fetchone()
        payload = read_json_text(str(terminal[2] or "{}")) if terminal else {}
        fingerprint = digest(
            _settlement_backfill_fingerprint(position),
            _settlement_economic_identity(position, payload) or "",
        )
        state = mem.execute(
            "SELECT fingerprint,completed FROM settlement_backfill_state WHERE position_id=?",
            (position["position_id"],),
        ).fetchone()
        if state is not None and state["completed"] and state["fingerprint"] == fingerprint:
            continue
        position["held_token_id"] = held_token(position)
        position["held_sell_direction"] = held_sell_direction(position)
        position["_settlement_backfill_fingerprint"] = fingerprint
        result.append(position)
        if len(result) >= limit:
            break
    return result


def _velocity(
    trades: sqlite3.Connection,
    token_id: str,
    carrier_direction: str,
) -> tuple[float, float]:
    rows = trades.execute(
        """
        SELECT quote_seen_at,best_bid_before
          FROM execution_feasibility_evidence
         WHERE token_id=? AND direction=?
           AND best_bid_before IS NOT NULL
         ORDER BY quote_seen_at DESC,rowid DESC LIMIT 3
        """,
        (token_id, carrier_direction),
    ).fetchall()
    if len(rows) < 2:
        return 0.0, 0.0
    points = [(parse_time(row[0]), float(row[1])) for row in reversed(rows)]
    velocities: list[float] = []
    for left, right in zip(points, points[1:]):
        if left[0] is None or right[0] is None:
            continue
        seconds = (right[0] - left[0]).total_seconds()
        if seconds > 0:
            velocities.append((right[1] - left[1]) / seconds)
    if not velocities:
        return 0.0, 0.0
    acceleration = velocities[-1] - velocities[-2] if len(velocities) > 1 else 0.0
    return velocities[-1], acceleration


def _monitor_dynamics(
    trades: sqlite3.Connection,
    position_id: str,
) -> tuple[float, float, float | None, bool, datetime | None]:
    rows = trades.execute(
        "SELECT occurred_at,payload_json FROM position_events "
        "WHERE position_id=? AND event_type='MONITOR_REFRESHED' "
        "ORDER BY sequence_no DESC LIMIT 3",
        (position_id,),
    ).fetchall()
    points: list[tuple[datetime, float | None, float | None]] = []
    latest_probability: float | None = None
    latest_fresh = False
    latest_at: datetime | None = None
    for raw in reversed(rows):
        at = parse_time(str(raw[0]))
        payload = read_json_text(str(raw[1] or "{}"))
        probability = _float(payload.get("last_monitor_prob"))
        market = _float(payload.get("last_monitor_market_price"))
        if at is not None:
            points.append((at, probability, market))
        latest_probability = probability
        latest_fresh = (
            payload.get("last_monitor_prob_is_fresh") is True
            and payload.get("last_monitor_market_price_is_fresh") is True
        )
        latest_at = at

    def slope(index: int) -> float:
        valid = [(at, values[index]) for at, *values in points if values[index] is not None]
        if len(valid) < 2:
            return 0.0
        left, right = valid[-2:]
        seconds = (right[0] - left[0]).total_seconds()
        return (float(right[1]) - float(left[1])) / seconds if seconds > 0 else 0.0

    return slope(0), slope(1), latest_probability, latest_fresh, latest_at


def refresh_precursor(
    mem: sqlite3.Connection,
    trades: sqlite3.Connection,
    open_positions: list[dict[str, Any]],
    latest: Mapping[str, Mapping[str, Any]],
    floor: float,
    *,
    deadline: float | None = None,
) -> str | None:
    if not open_positions:
        return None
    if deadline is not None and time.monotonic() >= deadline:
        raise sqlite3.OperationalError("interrupted: maintenance budget")
    pending_hard_positions = {
        str(row[0])
        for row in mem.execute(
            "SELECT position_id FROM incidents WHERE kind='hard' "
            "AND status IN ('queued','running','retry_pending')"
        ).fetchall()
    }
    ranked: list[tuple[float, dict[str, Any], Mapping[str, Any]]] = []
    for position in open_positions:
        if deadline is not None and time.monotonic() >= deadline:
            raise sqlite3.OperationalError("interrupted: maintenance budget")
        if str(position["position_id"]) in pending_hard_positions:
            continue
        quote = latest.get(str(position["position_id"]))
        if not quote:
            continue
        quote_status, bid = reconcile_held_quote(quote)
        if quote_status != "executable" or bid is None:
            continue
        if bid < floor:
            continue
        velocity, acceleration = _velocity(
            trades,
            str(position["held_token_id"]),
            str(position["direction"]),
        )
        distance = max(0.0, bid - floor)
        time_to_floor = distance / max(-velocity, 1e-9) if velocity < 0 else float("inf")
        current_quote = quote.get("_current_quote", quote)
        quote_at = parse_time(str(current_quote["quote_seen_at"])) if current_quote else None
        quote_age = max(0.0, (now() - quote_at).total_seconds()) if quote_at else 1e9
        probability_velocity, monitor_market_velocity, probability, monitor_fresh, monitor_at = _monitor_dynamics(
            trades,
            str(position["position_id"]),
        )
        monitor_age = max(0.0, (now() - monitor_at).total_seconds()) if monitor_at else 1e9
        depth_loss = 1.0 if current_quote is None or reconcile_held_quote(current_quote)[0] == "quote_incomplete" else 0.0
        market_ahead = max(0.0, probability_velocity - min(velocity, monitor_market_velocity))
        belief_gap = max(0.0, probability - bid) if probability is not None else 0.0
        score = (
            (1.0 / max(distance, 0.001))
            + (1.0 / max(time_to_floor, 0.001) if math.isfinite(time_to_floor) else 0.0)
            + max(0.0, -velocity) * 100.0
            + max(0.0, -acceleration) * 25.0
            + min(quote_age, 300.0) / 300.0
            + min(monitor_age, 300.0) / 150.0
            + market_ahead * 100.0
            + belief_gap * 2.0
            + (0.0 if monitor_fresh else 2.0)
            + depth_loss
        )
        ranked.append((score, position, quote))
    if not ranked:
        return None
    score, position, quote = max(ranked, key=lambda item: item[0])
    precursor_id = digest("precursor", position["position_id"])
    existing = mem.execute(
        "SELECT incident_id,status,crossing_evidence_id FROM incidents WHERE incident_id=?",
        (precursor_id,),
    ).fetchone()
    if existing:
        if existing[1] == "queued" and existing[2] != quote["evidence_id"]:
            before = mem.total_changes
            mem.execute(
                "UPDATE incidents SET crossing_evidence_id=?,observed_bid=?,priority=?,"
                "evidence_revision=evidence_revision+1,updated_at=? "
                "WHERE incident_id=? AND status='queued'",
                (quote["evidence_id"], bid, score, iso(), precursor_id),
            )
            return precursor_id if mem.total_changes > before else None
        return None
    _insert_incident(
        mem,
        position=position,
        evidence_id=str(quote["evidence_id"]),
        quote_seen_at=str(quote["quote_seen_at"]),
        bid=bid,
        floor=floor,
        kind="precursor",
        priority=score,
    )
    return precursor_id


@contextmanager
def _maintenance_connections(cfg: Mapping[str, Any], deadline: float):
    global _MAINTENANCE_DEADLINE
    previous = _MAINTENANCE_DEADLINE
    _MAINTENANCE_DEADLINE = deadline
    try:
        remaining = max(0.001, deadline - time.monotonic())
        with open_ro(Path(str(cfg["paths"]["trades_db"])), timeout=remaining) as trades, memory(cfg) as mem:
            with _sqlite_deadline(trades, deadline), _sqlite_deadline(mem, deadline):
                yield trades, mem
    finally:
        _MAINTENANCE_DEADLINE = previous


def _maintenance_guard() -> None:
    if _MAINTENANCE_DEADLINE is not None and time.monotonic() >= _MAINTENANCE_DEADLINE:
        raise sqlite3.OperationalError("interrupted: maintenance budget")


def _detect_maintenance(cfg: Mapping[str, Any], deadline: float | None = None) -> list[str]:
    detector_deadline = deadline or (time.monotonic() + max(
        0.001,
        float(cfg["loop"].get("detector_budget_ms", 200.0)) / 1000.0,
    ))
    floor = _bounded_floor_price(cfg, detector_deadline)
    history_days = int(cfg["loop"].get("history_days", 7))
    cutoff = iso(now() - timedelta(days=history_days))
    created: list[str] = []
    with _maintenance_connections(cfg, detector_deadline) as (trades, mem):
        _maintenance_guard()
        revalidate_blind_hard_incidents(mem, trades)
        _maintenance_guard()
        positions = tracked_positions(trades, history_days=history_days)
        # Settlement is an independent terminal truth path.  It must run even
        # after quote backfill is exhausted: a settled full loss with no quote
        # row is still dispatchable, but never gets a fabricated floor time.
        settlement_positions = dict(positions)
        for position in _settlement_backfill_positions(
            cfg, trades, mem, history_days=history_days, deadline=detector_deadline
        ):
            settlement_positions[str(position["position_id"])] = position
        for position in settlement_positions.values():
            _maintenance_guard()
            capability_failed = False
            try:
                candidate = _settlement_full_loss_candidate(trades, position)
            except ExecutionFactCapabilityError as exc:
                capability_failed = True
                mem.execute(
                    "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) "
                    "VALUES ('execution_fact_schema','execution_fact','blocked',?,?) "
                    "ON CONFLICT(debt_id) DO UPDATE SET status=excluded.status,"
                    "reason=excluded.reason,updated_at=excluded.updated_at",
                    (str(exc), iso()),
                )
                continue
            except SettlementBasisPending as exc:
                capability_failed = True
                mem.execute(
                    "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) "
                    "VALUES ('settlement_basis:' || ?, 'settlement_basis','retry_pending',?,?) "
                    "ON CONFLICT(debt_id) DO UPDATE SET status=excluded.status,"
                    "reason=excluded.reason,updated_at=excluded.updated_at",
                    (str(position["position_id"]), str(exc), iso()),
                )
                continue
            mem.execute(
                "UPDATE controller_debt SET status='resolved',reason='execution_fact_schema_available',"
                "updated_at=? WHERE debt_id='execution_fact_schema'",
                (iso(),),
            )
            if candidate:
                incident_id = _insert_settlement_full_loss_incident(
                    mem, position, candidate, floor=floor
                )
                if incident_id:
                    created.append(incident_id)
            else:
                _revise_settlement_non_loss_incidents(mem, trades, position)
            mem.execute(
                "UPDATE controller_debt SET status='resolved',reason='settlement_basis_complete',"
                "updated_at=? WHERE debt_id=?",
                (iso(), f"settlement_basis:{position['position_id']}"),
            )
            fingerprint = position.get("_settlement_backfill_fingerprint")
            if fingerprint and not capability_failed:
                mem.execute(
                    "INSERT INTO settlement_backfill_state(position_id,fingerprint,completed,updated_at) "
                    "VALUES (?,?,1,?) ON CONFLICT(position_id) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint,completed=1,updated_at=excluded.updated_at",
                    (position["position_id"], fingerprint, iso()),
                )
        by_token: dict[str, list[dict[str, Any]]] = {}
        for position in positions.values():
            _maintenance_guard()
            by_token.setdefault(str(position["held_token_id"]), []).append(position)
        raw_cursor = meta_get(mem, "quote_cursor", "")
        if raw_cursor == "":
            latest_rowid = trades.execute(
                "SELECT MAX(rowid) FROM execution_feasibility_evidence"
            ).fetchone()
            cursor = int(latest_rowid[0]) if latest_rowid and latest_rowid[0] is not None else 0
            meta_set(mem, "quote_cursor", cursor)
            quote_rows: list[dict[str, Any]] = []
        else:
            cursor = int(raw_cursor)
            quote_rows = _new_quote_rows(
                trades,
                cursor,
                cutoff,
                limit=max(1, int(cfg["loop"].get("quote_batch_size", 2000))),
            )
        for quote in quote_rows:
            _maintenance_guard()
            for position in by_token.get(str(quote["token_id"]), []):
                ident = _observe_quote(mem, position, quote, floor)
                if ident:
                    created.append(ident)
        if quote_rows:
            meta_set(mem, "quote_cursor", max(int(row["quote_rowid"]) for row in quote_rows))
        open_positions = [
            row for row in positions.values()
            if row.get("phase") in OPEN_PHASES
            and has_material_share_precision(row)
        ]
        latest = _latest_quotes(trades, open_positions, deadline=detector_deadline)
        for position in open_positions:
            _maintenance_guard()
            quote = latest.get(str(position["position_id"]))
            if quote:
                observed_quote = quote.get("_current_quote", quote)
                if observed_quote is not None:
                    ident = _observe_quote(mem, position, observed_quote, floor)
                    if ident:
                        created.append(ident)
        detector_remaining_ms = max(0.0, (detector_deadline - time.monotonic()) * 1000.0)
        backfill_budget_ms = min(
            float(cfg["loop"].get("backfill_budget_ms", 50.0)),
            detector_remaining_ms,
        )
        backfill_deadline = time.monotonic() + max(0.001, backfill_budget_ms / 1000.0)
        backfill_positions = (
            max(1, int(cfg["loop"].get("backfill_positions_per_cycle", 8)))
            if backfill_budget_ms > 1.0
            else 0
        )
        for _ in range(backfill_positions):
            _maintenance_guard()
            remaining_ms = (backfill_deadline - time.monotonic()) * 1000.0
            if remaining_ms <= 1.0:
                break
            created.extend(
                backfill_step(
                    mem,
                    trades,
                    positions,
                    cutoff=cutoff,
                    floor=floor,
                    row_limit=250,
                    budget_ms=remaining_ms,
                )
            )
        precursor = (
            refresh_precursor(mem, trades, open_positions, latest, floor, deadline=detector_deadline)
            if time.monotonic() < detector_deadline
            else None
        )
        if precursor:
            created.append(precursor)
        mem.commit()
        postcommit_deferred = time.monotonic() >= detector_deadline
    return _MaintenanceOutcome(
        list(dict.fromkeys(created)),
        postcommit_deferred=postcommit_deferred,
    )


def _detect_trigger(
    cfg: Mapping[str, Any], deadline: float | None = None, floor: float | None = None
) -> list[str]:
    """Persist newly observed hard crossings before maintenance can delay them."""

    global _TRIGGER_DEADLINE
    deadline = deadline or (
        time.monotonic() + max(0.01, float(cfg["loop"].get("trigger_budget_ms", 100.0))) / 1000.0
    )
    _TRIGGER_DEADLINE = deadline
    history_days = int(cfg["loop"].get("history_days", 7))
    created: list[str] = []
    committed = False
    try:
        if floor is None:
            floor = floor_price(cfg)
        remaining = max(0.001, deadline - time.monotonic())
        with open_ro(Path(str(cfg["paths"]["trades_db"])), timeout=remaining) as trades, memory(cfg) as mem:
            with _sqlite_deadline(trades, deadline), _sqlite_deadline(mem, deadline):
                positions = tracked_positions(trades, history_days=history_days)
                open_positions = [
                    row for row in positions.values()
                    if row.get("phase") in OPEN_PHASES and has_material_share_precision(row)
                ]
                latest = _latest_quotes(trades, open_positions, deadline=deadline)
                for position in open_positions:
                    quote = latest.get(str(position["position_id"]))
                    if quote is None:
                        continue
                    observed_quote = quote.get("_current_quote", quote)
                    if observed_quote is None:
                        continue
                    incident_id = _observe_quote(mem, position, observed_quote, floor)
                    if incident_id:
                        created.append(incident_id)
                mem.commit()
                committed = True
    except sqlite3.OperationalError as exc:
        if not any(token in str(exc).lower() for token in ("interrupted", "locked")):
            raise
        if not committed:
            created.clear()
    finally:
        _TRIGGER_DEADLINE = None
    return list(dict.fromkeys(created))


def _phase_heartbeat(cfg: Mapping[str, Any], phase: str, **extra: Any) -> None:
    try:
        atomic_json(
            runtime_dir(cfg) / "status.json",
            {"alive": True, "pid": os.getpid(), "at": iso(), "phase": phase, **extra},
        )
    except OSError:
        pass


def _bounded_floor_price(cfg: Mapping[str, Any], deadline: float) -> float:
    if time.monotonic() >= deadline:
        raise sqlite3.OperationalError("interrupted: floor-price budget")
    path = Path(str(cfg["paths"]["settings"]))
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("active execution floor unavailable: settings unreadable") from exc
    if time.monotonic() >= deadline or not isinstance(payload, Mapping):
        raise sqlite3.OperationalError("interrupted: floor-price budget")
    current: Any = payload
    for part in str(cfg["loop"]["floor_config_key"]).split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise RuntimeError(f"active execution floor unavailable: missing {part}")
        current = current[part]
    value = float(current)
    if not math.isfinite(value) or not 0 < value < 1:
        raise RuntimeError("active execution floor unavailable: out-of-range value")
    return value


def _receipt_guard(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("committed receipt debt deadline")


def _receipt_read_json(path: Path, deadline: float) -> Any:
    _receipt_guard(deadline)
    payload = read_json(path, {})
    _receipt_guard(deadline)
    return payload


def _receipt_atomic_json(path: Path, payload: Any, deadline: float) -> None:
    _receipt_guard(deadline)
    path.parent.mkdir(parents=True, exist_ok=True)
    _receipt_guard(deadline)
    atomic_json(path, payload)
    _receipt_guard(deadline)


def _receipt_debt_deadline(cfg: Mapping[str, Any], deadline: float | None) -> float:
    now_value = time.monotonic()
    if deadline is not None and deadline > now_value:
        return deadline
    # A post-commit failure must still get a small, independent persistence
    # slice; it cannot inherit an already-expired trigger slice.
    return now_value + max(
        0.01, float(cfg["loop"].get("receipt_debt_budget_ms", 50.0)) / 1000.0
    )


def _record_committed_receipt_debt(
    cfg: Mapping[str, Any], incident_ids: Iterable[str], reason: str, *, deadline: float | None = None
) -> None:
    ids = list(dict.fromkeys(str(value) for value in incident_ids if str(value)))
    if not ids:
        return
    debt_id = "trigger_receipt:" + digest(*ids)
    receipt_deadline = _receipt_debt_deadline(cfg, deadline)
    receipt_payload = {
        "debt_id": debt_id,
        "kind": "trigger_receipt",
        "status": "retry_pending",
        "incident_ids": ids,
        "reason": reason,
        "deadline": receipt_deadline,
    }
    global _TRIGGER_DEADLINE
    previous_deadline = _TRIGGER_DEADLINE
    _TRIGGER_DEADLINE = receipt_deadline
    json_ok = False
    try:
        debt_dir = runtime_dir(cfg) / "trigger-receipt-debts"
        _receipt_guard(receipt_deadline)
        debt_dir.mkdir(parents=True, exist_ok=True)
        _receipt_atomic_json(debt_dir / f"{debt_id}.json", receipt_payload, receipt_deadline)
        json_ok = True
        try:
            _receipt_atomic_json(runtime_dir(cfg) / "trigger-receipt-debt.json", receipt_payload, receipt_deadline)
        except (OSError, TimeoutError):
            pass
    except (OSError, TimeoutError):
        json_ok = False
    db_ok = False
    try:
        _receipt_guard(receipt_deadline)
        with memory(cfg) as mem:
            _receipt_guard(receipt_deadline)
            durable_reason = reason[:650] + "|incident_ids=" + ",".join(ids)
            mem.execute(
                "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(debt_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at",
                (debt_id, "trigger_receipt", "retry_pending", durable_reason[:1000], iso()),
            )
            _receipt_guard(receipt_deadline)
            mem.commit()
            db_ok = True
    except (OSError, TimeoutError, sqlite3.Error, StartupMaintenanceDeferred):
        db_ok = False
    finally:
        _TRIGGER_DEADLINE = previous_deadline
    if not json_ok and not db_ok:
        raise OSError("committed_receipt_pending:durable_state_unavailable")


def _publish_trigger_receipt(cfg: Mapping[str, Any], incident_ids: Iterable[str], deadline: float) -> None:
    ids = list(dict.fromkeys(str(value) for value in incident_ids if str(value)))
    if not ids:
        return
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError("trigger receipt deadline")
        atomic_json(
            runtime_dir(cfg) / "trigger-committed.json",
            {"at": iso(), "incident_ids": ids, "status": "committed"},
        )
        if time.monotonic() >= deadline:
            raise TimeoutError("trigger receipt deadline")
    except (OSError, TimeoutError) as exc:
        _record_committed_receipt_debt(
            cfg,
            ids,
            f"committed_receipt_pending:{type(exc).__name__}:{exc}",
            deadline=deadline,
        )


def _retry_committed_receipt(cfg: Mapping[str, Any], deadline: float) -> None:
    global _TRIGGER_DEADLINE
    previous_deadline = _TRIGGER_DEADLINE
    _TRIGGER_DEADLINE = deadline
    try:
        _receipt_guard(deadline)
        pending: dict[str, tuple[list[str], Path | None]] = {}
        debt_dir = runtime_dir(cfg) / "trigger-receipt-debts"
        _receipt_guard(deadline)
        debt_paths: list[Path] = []
        if debt_dir.is_dir():
            for path in debt_dir.iterdir():
                _receipt_guard(deadline)
                if path.suffix == ".json":
                    debt_paths.append(path)
                if len(debt_paths) >= 32:
                    break
        for path in debt_paths:
            payload = _receipt_read_json(path, deadline)
            ids = payload.get("incident_ids") if isinstance(payload, Mapping) else None
            debt_id = str(payload.get("debt_id") or path.stem) if isinstance(payload, Mapping) else path.stem
            if isinstance(ids, list) and ids and str(payload.get("status")) != "resolved":
                pending[debt_id] = ([str(value) for value in ids], path)
        legacy = _receipt_read_json(runtime_dir(cfg) / "trigger-receipt-debt.json", deadline)
        if isinstance(legacy, Mapping) and isinstance(legacy.get("incident_ids"), list) and str(legacy.get("status")) != "resolved":
            debt_id = str(legacy.get("debt_id") or "trigger_receipt:" + digest(*legacy["incident_ids"]))
            pending.setdefault(debt_id, ([str(value) for value in legacy["incident_ids"]], runtime_dir(cfg) / "trigger-receipt-debt.json"))
        pending_rows: list[sqlite3.Row] = []
        try:
            _receipt_guard(deadline)
            with memory(cfg) as mem:
                _receipt_guard(deadline)
                pending_rows = mem.execute(
                    "SELECT debt_id,reason FROM controller_debt WHERE kind='trigger_receipt' AND "
                    "(status='retry_pending' OR (status='resolved' AND reason LIKE '%projection_pending%')) "
                    "ORDER BY updated_at LIMIT 32"
                ).fetchall()
                _receipt_guard(deadline)
        except (sqlite3.Error, StartupMaintenanceDeferred):
            pending_rows = []
        for row in pending_rows:
            marker = "incident_ids="
            reason = str(row[1])
            ids = [value for value in reason.split(marker, 1)[1].split(",") if value] if marker in reason else []
            if ids:
                pending.setdefault(str(row[0]), (ids, None))
        if not pending:
            return
        ids = list(dict.fromkeys(value for values, _path in pending.values() for value in values))
        _receipt_atomic_json(
            runtime_dir(cfg) / "trigger-committed.json",
            {"at": iso(), "incident_ids": ids, "status": "committed"},
            deadline,
        )
        with memory(cfg) as mem:
            stamp = iso()
            for debt_id, (_ids, _path) in pending.items():
                _receipt_guard(deadline)
                durable_reason = "committed_receipt_complete|incident_ids=" + ",".join(_ids) + "|projection_pending"
                updated = mem.execute(
                    "UPDATE controller_debt SET status='resolved',reason=?,updated_at=? "
                    "WHERE debt_id=? AND kind='trigger_receipt' AND status='retry_pending'",
                    (durable_reason[:1000], stamp, debt_id),
                ).rowcount
                if not updated:
                    mem.execute(
                        "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(debt_id) DO NOTHING",
                        (debt_id, "trigger_receipt", "resolved", durable_reason[:1000], stamp),
                    )
            _receipt_guard(deadline)
            mem.commit()
        resolved: list[str] = []
        for debt_id, (_ids, path) in pending.items():
            if path is None:
                resolved.append(debt_id)
                continue
            target = path
            _receipt_atomic_json(
                target,
                {"debt_id": debt_id, "incident_ids": _ids, "status": "resolved", "updated_at": iso()},
                deadline,
            )
            resolved.append(debt_id)
        if resolved:
            with memory(cfg) as mem:
                for debt_id in resolved:
                    _receipt_guard(deadline)
                    mem.execute(
                        "UPDATE controller_debt SET reason='committed_receipt_complete',updated_at=? "
                        "WHERE debt_id=? AND kind='trigger_receipt' AND status='resolved' AND reason LIKE '%projection_pending%'",
                        (iso(), debt_id),
                    )
                _receipt_guard(deadline)
                mem.commit()
    except (OSError, TimeoutError, sqlite3.Error):
        return
    finally:
        _TRIGGER_DEADLINE = previous_deadline


def detect(cfg: Mapping[str, Any]) -> list[str]:
    global _LAST_EVIDENCE_CYCLE
    _LAST_EVIDENCE_CYCLE = {"built": [], "deferred": [], "attempted": 0, "validated": 0, "bytes": 0}
    budget = _new_evidence_budget(cfg)
    trigger_deadline = time.monotonic() + max(0.01, float(cfg["loop"].get("trigger_budget_ms", 100.0))) / 1000.0
    _phase_heartbeat(cfg, "trigger_start")
    floor = _bounded_floor_price(cfg, trigger_deadline)
    trigger_created = _detect_trigger(cfg, trigger_deadline, floor)
    if trigger_created:
        _publish_trigger_receipt(cfg, trigger_created, trigger_deadline)
    _phase_heartbeat(cfg, "trigger_committed", created=trigger_created)
    receipt_deadline = time.monotonic() + max(0.01, float(cfg["loop"].get("receipt_budget_ms", 50.0))) / 1000.0
    _retry_committed_receipt(cfg, receipt_deadline)
    _phase_heartbeat(cfg, "evidence_start", created=trigger_created)
    # Both trigger connections are closed before this independent local
    # snapshot transaction begins.
    evidence_summary = _capture_hard_evidence(cfg, trigger_created, budget=budget)
    _phase_heartbeat(
        cfg,
        "evidence_deferred" if evidence_summary.get("deferred") else "evidence_committed",
        created=trigger_created,
        evidence_built=evidence_summary.get("built", []),
        evidence_deferred=evidence_summary.get("deferred", []),
    )
    _phase_heartbeat(cfg, "maintenance_start", created=trigger_created)
    maintenance_deadline = time.monotonic() + max(
        0.001,
        float(cfg["loop"].get("maintenance_budget_ms", cfg["loop"].get("detector_budget_ms", 200.0))) / 1000.0,
    )
    try:
        maintenance_result = _detect_maintenance(cfg, maintenance_deadline)
    except sqlite3.OperationalError as exc:
        if "interrupted" not in str(exc).lower():
            raise
        _capture_hard_evidence(cfg, trigger_created, budget=budget)
        return list(dict.fromkeys(trigger_created))
    maintenance_ids = list(maintenance_result)
    created = list(dict.fromkeys([*maintenance_ids, *trigger_created]))
    maintenance_deferred = bool(getattr(maintenance_result, "postcommit_deferred", False))
    if maintenance_ids:
        maintenance_receipt_deadline = time.monotonic() + max(
            0.01, float(cfg["loop"].get("receipt_budget_ms", 50.0))
        ) / 1000.0
        _publish_trigger_receipt(cfg, maintenance_ids, maintenance_receipt_deadline)
    _phase_heartbeat(
        cfg,
        "maintenance_committed",
        created=created,
        maintenance_created=maintenance_ids,
        postcommit_deferred=maintenance_deferred,
    )
    _capture_hard_evidence(cfg, created, budget=budget)
    return list(dict.fromkeys(created))


EVIDENCE_SCHEMA = """
CREATE TABLE incident(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE position(position_id TEXT PRIMARY KEY,row_json TEXT NOT NULL);
CREATE TABLE price_ticks(evidence_id TEXT PRIMARY KEY,quote_seen_at TEXT NOT NULL,best_bid REAL,best_ask REAL,depth_json TEXT,book_hash TEXT,direction TEXT,raw_json TEXT NOT NULL);
CREATE TABLE probability_ticks(event_id TEXT PRIMARY KEY,occurred_at TEXT NOT NULL,probability REAL,edge REAL,market_price REAL,is_fresh INTEGER,raw_json TEXT NOT NULL);
CREATE TABLE source_clocks(source_key TEXT PRIMARY KEY,source_cycle_time TEXT,source_available_at TEXT,computed_at TEXT,recorded_at TEXT,raw_json TEXT NOT NULL);
CREATE TABLE monitor_events(event_id TEXT PRIMARY KEY,occurred_at TEXT NOT NULL,raw_json TEXT NOT NULL);
CREATE TABLE exit_decisions(event_id TEXT PRIMARY KEY,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,command_id TEXT,raw_json TEXT NOT NULL);
CREATE TABLE venue_commands(command_id TEXT PRIMARY KEY,created_at TEXT,updated_at TEXT,state TEXT,raw_json TEXT NOT NULL);
CREATE TABLE order_facts(fact_key TEXT PRIMARY KEY,observed_at TEXT,raw_json TEXT NOT NULL);
CREATE TABLE trade_facts(fact_key TEXT PRIMARY KEY,observed_at TEXT,fill_price REAL,filled_size REAL,raw_json TEXT NOT NULL);
CREATE TABLE fills(fact_key TEXT PRIMARY KEY,observed_at TEXT,price REAL,size REAL,raw_json TEXT NOT NULL);
CREATE TABLE settlement_facts(fact_key TEXT PRIMARY KEY,settled_at TEXT NOT NULL,raw_json TEXT NOT NULL);
CREATE TABLE daemon_health(name TEXT PRIMARY KEY,observed_at TEXT,raw_json TEXT NOT NULL);
CREATE TABLE code_versions(name TEXT PRIMARY KEY,sha TEXT,path TEXT,observed_at TEXT);
CREATE TABLE config_snapshot(name TEXT PRIMARY KEY,value_json TEXT NOT NULL,sha256 TEXT NOT NULL);
"""


def _json_number(payload: Mapping[str, Any], names: Iterable[str]) -> float | None:
    stack: list[Any] = [payload]
    wanted = set(names)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in wanted:
                    try:
                        number = float(child)
                    except (TypeError, ValueError):
                        pass
                    else:
                        if math.isfinite(number):
                            return number
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return None


class EvidenceCapacityExceeded(RuntimeError):
    """A snapshot exceeded the controller's bounded maintenance capacity."""


def _new_evidence_budget(cfg: Mapping[str, Any]) -> dict[str, Any]:
    settings = cfg["loop"]
    return {
        "remaining": max(0, int(settings.get("evidence_builds_per_cycle", 1))),
        "deadline": time.monotonic() + max(0.001, float(settings.get("evidence_build_budget_ms", 1000))) / 1000.0,
        "max_bytes": max(1, int(settings.get("evidence_max_bytes", 32 * 1024 * 1024))),
        "built": 0,
        "bytes": 0,
    }


def _budget_check(
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
    *,
    extra_bytes: int = 0,
) -> None:
    budget = _EVIDENCE_BUILD_CONTEXT
    if budget is None:
        return
    if time.monotonic() >= float(budget["deadline"]):
        raise EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")
    size = 0
    if path is not None:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
    if conn is not None:
        try:
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            size = max(size, page_count * page_size)
        except sqlite3.Error:
            pass
    projected = size + max(0, int(extra_bytes))
    if projected > int(budget["max_bytes"]):
        raise EvidenceCapacityExceeded(f"evidence_snapshot_oversized:bytes={projected}")
    budget["bytes"] = max(int(budget.get("bytes", 0)), size)


def _evidence_guard() -> None:
    budget = _EVIDENCE_BUILD_CONTEXT
    if budget is not None and time.monotonic() >= float(budget["deadline"]):
        raise EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")


def _apply_evidence_sql_budget(conn: sqlite3.Connection, budget: Mapping[str, Any] | None = None) -> None:
    budget = budget or _EVIDENCE_BUILD_CONTEXT
    if budget is None:
        return
    remaining = float(budget["deadline"]) - time.monotonic()
    if remaining <= 0:
        raise EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")
    remaining_ms = max(1, int(remaining * 1000))
    conn.execute(f"PRAGMA busy_timeout={remaining_ms}")
    conn.set_progress_handler(
        lambda: int(time.monotonic() >= float(budget["deadline"])),
        1000,
    )


def _cleanup_unpublished_generation(
    incident_dir: Path, generation_id: str, generation_dir: Path
) -> None:
    try:
        current_generation = (incident_dir / "CURRENT").read_text().strip()
    except OSError:
        current_generation = ""
    if current_generation != generation_id:
        shutil.rmtree(generation_dir, ignore_errors=True)


def _reap_incomplete_generations(cfg: Mapping[str, Any], incident_id: str) -> None:
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    generations = incident_dir / "generations"
    if not generations.is_dir():
        return
    pointer = ""
    try:
        pointer = (incident_dir / "CURRENT").read_text().strip()
    except OSError:
        pass
    reap_after = max(0.0, float(cfg["loop"].get("evidence_generation_reap_age_seconds", 60)))
    for generation_dir in generations.iterdir():
        _evidence_guard()
        if not generation_dir.is_dir() or generation_dir.name == pointer:
            continue
        if (generation_dir / "evidence.db").is_file() and (generation_dir / "manifest.json").is_file():
            continue
        try:
            age = time.time() - generation_dir.stat().st_mtime
        except OSError:
            continue
        if age < reap_after:
            continue
        shutil.rmtree(generation_dir, ignore_errors=True)


def _build_evidence_snapshot(cfg: Mapping[str, Any], incident_id: str) -> Path:
    build_budget = _EVIDENCE_BUILD_CONTEXT or _new_evidence_budget(cfg)
    run = runtime_dir(cfg)
    incident_dir = run / "incidents" / incident_id
    incident_dir.mkdir(parents=True, exist_ok=True)
    generation_id = digest(incident_id, iso(), time.monotonic_ns())
    generation_dir = incident_dir / "generations" / generation_id
    generation_dir.mkdir(parents=True, exist_ok=False)
    evidence = generation_dir / ".evidence.db.tmp"
    final_evidence = generation_dir / "evidence.db"
    manifest_path = generation_dir / "manifest.json"
    manifest_tmp = generation_dir / ".manifest.json.tmp"
    evidence.unlink(missing_ok=True)
    with memory(cfg) as mem:
        incident_row = mem.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
    if incident_row is None:
        raise KeyError(f"unknown incident {incident_id}")
    incident = dict(incident_row)
    row_limit = max(1, int(cfg["loop"].get("evidence_max_rows", cfg["loop"].get("max_evidence_rows_per_table", 250000))))
    window_days = min(7, max(1, int(cfg["loop"].get("evidence_window_days", 7))))
    window_start = (parse_time(incident.get("t_floor")) or now()) - timedelta(days=window_days / 2)
    window_end = window_start + timedelta(days=window_days)
    _budget_check(evidence)
    with open_ro(Path(str(cfg["paths"]["trades_db"]))) as trades:
        _apply_evidence_sql_budget(trades)
        position_row = trades.execute("SELECT * FROM position_current WHERE position_id=?", (incident["position_id"],)).fetchone()
        if position_row is None:
            raise RuntimeError("incident position missing from canonical projection")
        position = dict(position_row)
        events = list(reversed(trades.execute(
            "SELECT * FROM position_events WHERE position_id=? AND occurred_at BETWEEN ? AND ? "
            "ORDER BY sequence_no DESC LIMIT ?",
            (incident["position_id"], iso(window_start), iso(window_end), row_limit),
        ).fetchall()))
        event_times = [parse_time(str(row["occurred_at"])) for row in events]
        event_times = [value for value in event_times if value is not None]
        start = max(window_start, min(event_times) - timedelta(hours=1) if event_times else window_start)
        end = min(window_end, max(start, max(event_times) + timedelta(hours=6) if event_times else window_end))
        quote_rows = trades.execute(
            """
            SELECT * FROM execution_feasibility_evidence
             WHERE token_id=? AND quote_seen_at BETWEEN ? AND ?
             ORDER BY quote_seen_at,rowid LIMIT ?
            """,
            (incident["held_token_id"], iso(start), iso(end), row_limit),
        ).fetchall()
        latest = trades.execute(
            "SELECT * FROM execution_feasibility_latest WHERE token_id=? ORDER BY direction",
            (incident["held_token_id"],),
        ).fetchall()
        commands = trades.execute(
            "SELECT * FROM venue_commands WHERE position_id=? AND created_at BETWEEN ? AND ? ORDER BY created_at LIMIT ?",
            (incident["position_id"], iso(window_start), iso(window_end), row_limit),
        ).fetchall()
        command_ids = [str(row["command_id"]) for row in commands]
        order_facts: list[sqlite3.Row] = []
        trade_facts: list[sqlite3.Row] = []
        command_events: list[sqlite3.Row] = []
        fills: list[sqlite3.Row] = []
        if command_ids:
            marks = ",".join("?" for _ in command_ids)
            order_facts = trades.execute(
                f"SELECT * FROM venue_order_facts WHERE command_id IN ({marks}) ORDER BY observed_at,local_sequence LIMIT ?",
                [*command_ids, row_limit],
            ).fetchall()
            trade_facts = trades.execute(
                f"SELECT * FROM venue_trade_facts WHERE command_id IN ({marks}) ORDER BY observed_at,local_sequence LIMIT ?",
                [*command_ids, row_limit],
            ).fetchall()
            command_events = trades.execute(
                f"SELECT * FROM venue_command_events WHERE command_id IN ({marks}) ORDER BY occurred_at,sequence_no LIMIT ?",
                [*command_ids, row_limit],
            ).fetchall()
        trade_ids = list(dict.fromkeys(
            trade_id
            for row in trade_facts
            if (trade_id := str(row["trade_id"] or "").strip())
        ))
        seen_fill_ids: set[int] = set()
        for offset in range(0, len(trade_ids), 900):
            remaining = row_limit - len(fills)
            if remaining <= 0:
                break
            trade_id_batch = trade_ids[offset:offset + 900]
            marks = ",".join("?" for _ in trade_id_batch)
            for row in trades.execute(
                f"SELECT * FROM wallet_fill_observations WHERE trade_id IN ({marks}) "
                "ORDER BY observed_at,id LIMIT ?",
                [*trade_id_batch, remaining],
            ).fetchall():
                if row["id"] in seen_fill_ids:
                    continue
                seen_fill_ids.add(row["id"])
                fills.append(row)
    with sqlite3.connect(evidence, timeout=0.1) as out:
        _apply_evidence_sql_budget(out)
        out.executescript(EVIDENCE_SCHEMA)
        for key, value in incident.items():
            out.execute("INSERT INTO incident VALUES (?,?)", (str(key), json.dumps(value, default=str)))
        out.execute("INSERT INTO position VALUES (?,?)", (incident["position_id"], json.dumps(position, default=str)))
        seen_quotes: set[str] = set()
        for raw in [*quote_rows, *latest]:
            _budget_check(evidence, out)
            row = dict(raw)
            key = str(row["evidence_id"])
            if key in seen_quotes:
                continue
            seen_quotes.add(key)
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute(
                "INSERT INTO price_ticks VALUES (?,?,?,?,?,?,?,?)",
                (key, row["quote_seen_at"], row.get("best_bid_before"), row.get("best_ask_before"), row.get("depth_before_json"), row.get("book_hash_before"), row.get("direction"), raw_json),
            )
        for raw in events:
            _budget_check(evidence, out)
            row = dict(raw)
            payload = read_json_text(str(row.get("payload_json") or "{}"))
            packed = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(packed.encode()))
            if row["event_type"] == "SETTLED":
                fact_key = digest(
                    incident["position_id"], row.get("event_id"),
                    payload.get("payout_id") or payload.get("settlement_id") or "",
                )
                out.execute(
                    "INSERT OR REPLACE INTO settlement_facts VALUES (?,?,?)",
                    (fact_key, row.get("occurred_at") or "", json.dumps({"event": row, "payload": payload}, default=str)),
                )
            if row["event_type"] == "MONITOR_REFRESHED":
                out.execute("INSERT INTO monitor_events VALUES (?,?,?)", (row["event_id"], row["occurred_at"], packed))
                probability = _json_number(payload, ("last_monitor_prob", "p_posterior", "held_probability", "probability", "q"))
                edge = _json_number(payload, ("last_monitor_edge", "edge", "held_edge"))
                market_price = _json_number(payload, ("last_monitor_market_price", "market_price", "best_bid", "held_bid"))
                fresh = _json_number(payload, ("last_monitor_prob_is_fresh", "probability_is_fresh", "is_fresh"))
                out.execute(
                    "INSERT INTO probability_ticks VALUES (?,?,?,?,?,?,?)",
                    (row["event_id"], row["occurred_at"], probability, edge, market_price, int(bool(fresh)) if fresh is not None else None, json.dumps({"monitor_event_id": row["event_id"]})),
                )
            if row["event_type"] in {"MONITOR_REFRESHED", "EXIT_INTENT", "EXIT_ORDER_POSTED", "EXIT_ORDER_FILLED", "EXIT_ORDER_REJECTED", "EXIT_RETRY_RELEASED"}:
                decision_json = packed
                if row["event_type"] == "MONITOR_REFRESHED":
                    decision_json = json.dumps(
                        {
                            "monitor_event_id": row["event_id"],
                            "should_exit": payload.get("exit_decision_should_exit"),
                            "reason": payload.get("exit_decision_reason"),
                            "trigger": payload.get("exit_decision_trigger"),
                            "urgency": payload.get("exit_decision_urgency"),
                        },
                        default=str,
                    )
                out.execute("INSERT INTO exit_decisions VALUES (?,?,?,?,?)", (row["event_id"], row["occurred_at"], row["event_type"], row.get("command_id"), decision_json))
        for raw in commands:
            _budget_check(evidence, out)
            row = dict(raw)
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute("INSERT INTO venue_commands VALUES (?,?,?,?,?)", (row["command_id"], row.get("created_at"), row.get("updated_at"), row.get("state"), raw_json))
        for raw in command_events:
            _budget_check(evidence, out)
            row = dict(raw)
            key = f"command-event:{row['event_id']}"
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute("INSERT INTO order_facts VALUES (?,?,?)", (key, row.get("occurred_at"), raw_json))
        for raw in order_facts:
            _budget_check(evidence, out)
            row = dict(raw)
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute("INSERT INTO order_facts VALUES (?,?,?)", (f"order:{row['fact_id']}", row.get("observed_at"), raw_json))
        for raw in trade_facts:
            _budget_check(evidence, out)
            row = dict(raw)
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute("INSERT INTO trade_facts VALUES (?,?,?,?,?)", (f"trade:{row['trade_fact_id']}", row.get("observed_at"), _float(row.get("fill_price")), _float(row.get("filled_size")), raw_json))
        for raw in fills:
            _budget_check(evidence, out)
            row = dict(raw)
            raw_json = json.dumps(row, default=str)
            _budget_check(evidence, out, extra_bytes=len(raw_json.encode()))
            out.execute("INSERT INTO fills VALUES (?,?,?,?,?)", (f"wallet:{row['id']}", row.get("observed_at"), _float(row.get("price")), _float(row.get("size")), raw_json))
        _copy_source_clocks(cfg, out, position, row_limit=min(row_limit, int(cfg["loop"].get("evidence_source_rows", 1000))))
        _copy_runtime_health(out)
        _copy_versions_and_config(cfg, out)
        out.commit()
    _budget_check(evidence)
    try:
        coverage = _evidence_coverage(evidence, budget=build_budget)
        sha256 = _stream_evidence_hash(evidence, build_budget)
        _budget_check(evidence)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": incident_id,
            "position_id": incident["position_id"],
            "held_token_id": incident["held_token_id"],
            "crossing_evidence_id": incident["crossing_evidence_id"],
            "t_floor": incident["t_floor"],
            "floor_price": incident["floor_price"],
            "evidence_db": str(final_evidence),
            "row_limit_per_table": row_limit,
            "source_row_limit": min(row_limit, int(cfg["loop"].get("evidence_source_rows", 1000))),
            "size_bytes": evidence.stat().st_size,
            "evidence_mtime_ns": evidence.stat().st_mtime_ns,
            "capacity": {
                "max_bytes": int(build_budget.get("max_bytes", cfg["loop"].get("evidence_max_bytes", 32 * 1024 * 1024))),
                "max_rows_per_table": row_limit,
                "window_days": window_days,
            },
            "coverage": coverage,
            "loaded_sha": _active_loaded_sha(cfg),
            "created_at": iso(),
            "sha256": sha256,
        }
    except BaseException:
        _cleanup_unpublished_generation(incident_dir, generation_id, generation_dir)
        raise
    try:
        _budget_check(evidence)
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _budget_check(evidence)
        os.replace(evidence, final_evidence)
        os.replace(manifest_tmp, manifest_path)
        _budget_check(final_evidence)
        pointer_tmp = incident_dir / f".CURRENT.{os.getpid()}.{time.monotonic_ns()}.tmp"
        pointer_tmp.write_text(generation_id + "\n")
        _budget_check(final_evidence)
        os.replace(pointer_tmp, incident_dir / "CURRENT")
    except BaseException:
        _cleanup_unpublished_generation(incident_dir, generation_id, generation_dir)
        raise
    return final_evidence


def build_evidence(cfg: Mapping[str, Any], incident_id: str) -> Path:
    global _EVIDENCE_BUILD_CONTEXT
    previous_budget = _EVIDENCE_BUILD_CONTEXT
    if previous_budget is None:
        _EVIDENCE_BUILD_CONTEXT = _new_evidence_budget(cfg)
    try:
        return _build_evidence_snapshot(cfg, incident_id)
    except BaseException:
        incident_dir = runtime_dir(cfg) / "incidents" / incident_id
        for pattern in ("generations/*/.evidence.db.tmp", "generations/*/.manifest.json.tmp", ".CURRENT.*.tmp", ".*.evidence.db.*.tmp", ".*.manifest.json.*.tmp"):
            for path in incident_dir.glob(pattern):
                path.unlink(missing_ok=True)
        raise
    finally:
        _EVIDENCE_BUILD_CONTEXT = previous_budget


def _run_bounded_evidence_build(
    cfg: Mapping[str, Any], incident_id: str, budget: dict[str, Any]
) -> Path:
    global _EVIDENCE_BUILD_CONTEXT
    previous = _EVIDENCE_BUILD_CONTEXT
    _EVIDENCE_BUILD_CONTEXT = budget
    try:
        return build_evidence(cfg, incident_id)
    finally:
        _EVIDENCE_BUILD_CONTEXT = previous


def _evidence_debt_id(incident_id: str) -> str:
    return f"evidence_snapshot:{incident_id}"


def _evidence_fingerprints(
    cfg: Mapping[str, Any], incident_id: str, budget: Mapping[str, Any]
) -> tuple[str, str, str, str]:
    _evidence_guard()
    settings = cfg["loop"]
    config_payload = {
        "history_days": settings.get("history_days"),
        "window_days": min(7, max(1, int(settings.get("evidence_window_days", 7)))),
        "rows": int(settings.get("evidence_max_rows", settings.get("max_evidence_rows_per_table", 250000))),
        "source_rows": int(settings.get("evidence_source_rows", 1000)),
    }
    capacity_payload = {
        "builds_per_cycle": int(settings.get("evidence_builds_per_cycle", 1)),
        "budget_ms": float(settings.get("evidence_build_budget_ms", 1000)),
        "max_bytes": int(settings.get("evidence_max_bytes", 32 * 1024 * 1024)),
    }
    with memory(cfg) as mem:
        incident = mem.execute(
            "SELECT incident_id,position_id,crossing_evidence_id,held_token_id,evidence_revision,t_floor,detected_at "
            "FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
    if incident is None:
        raise KeyError(f"unknown incident {incident_id}")
    position: Mapping[str, Any] = {}
    fingerprint_rows = min(
        max(1, int(settings.get("evidence_max_rows", settings.get("max_evidence_rows_per_table", 250000)))),
        256,
    )
    window_days = min(7, max(1, int(settings.get("evidence_window_days", 7))))
    floor_at = parse_time(str(incident["t_floor"] or "")) or parse_time(str(incident["detected_at"] or "")) or now()
    window_start = floor_at - timedelta(days=window_days / 2)
    window_end = window_start + timedelta(days=window_days)
    canonical_rows: dict[str, list[dict[str, Any]]] = {}
    with open_ro(Path(str(cfg["paths"]["trades_db"]))) as trades:
        _apply_evidence_sql_budget(trades, budget)
        row = trades.execute(
            "SELECT position_id,phase,city,target_date,temperature_metric,token_id,updated_at "
            "FROM position_current WHERE position_id=?",
            (str(incident["position_id"]),),
        ).fetchone()
        if row is not None:
            position = dict(row)
        canonical_rows["events"] = [dict(row) for row in trades.execute(
            "SELECT event_id,sequence_no,event_type,occurred_at,command_id,substr(payload_json,1,4096) AS payload_json "
            "FROM position_events WHERE position_id=? AND occurred_at BETWEEN ? AND ? "
            "ORDER BY sequence_no DESC LIMIT ?",
            (str(incident["position_id"]), iso(window_start), iso(window_end), fingerprint_rows),
        ).fetchall()]
        canonical_rows["quotes"] = [dict(row) for row in trades.execute(
            "SELECT evidence_id,event_id,token_id,direction,quote_seen_at,book_hash_before,best_bid_before,best_ask_before,"
            "substr(depth_before_json,1,4096) AS depth_before_json "
            "FROM execution_feasibility_evidence WHERE token_id=? AND quote_seen_at BETWEEN ? AND ? "
            "ORDER BY quote_seen_at,rowid LIMIT ?",
            (str(incident["held_token_id"]), iso(window_start), iso(window_end), fingerprint_rows),
        ).fetchall()]
        canonical_rows["commands"] = [dict(row) for row in trades.execute(
            "SELECT command_id,position_id,created_at,updated_at,state FROM venue_commands "
            "WHERE position_id=? AND created_at BETWEEN ? AND ? ORDER BY created_at LIMIT ?",
            (str(incident["position_id"]), iso(window_start), iso(window_end), fingerprint_rows),
        ).fetchall()]
        command_ids = [str(row["command_id"]) for row in canonical_rows["commands"]]
        if command_ids:
            marks = ",".join("?" for _ in command_ids)
            canonical_rows["command_events"] = [dict(row) for row in trades.execute(
                f"SELECT event_id,command_id,sequence_no,event_type,occurred_at,state_after,substr(payload_json,1,4096) AS payload_json "
                f"FROM venue_command_events WHERE command_id IN ({marks}) ORDER BY occurred_at,sequence_no LIMIT ?",
                [*command_ids, fingerprint_rows],
            ).fetchall()]
            trade_facts = [dict(row) for row in trades.execute(
                f"SELECT trade_fact_id,command_id,trade_id,observed_at,local_sequence,fill_price,filled_size "
                f"FROM venue_trade_facts WHERE command_id IN ({marks}) ORDER BY observed_at,local_sequence LIMIT ?",
                [*command_ids, fingerprint_rows],
            ).fetchall()]
            canonical_rows["trade_facts"] = trade_facts
            trade_ids = list(dict.fromkeys(str(row["trade_id"]) for row in trade_facts if row.get("trade_id")))
            if trade_ids:
                trade_marks = ",".join("?" for _ in trade_ids[:900])
                canonical_rows["fills"] = [dict(row) for row in trades.execute(
                    f"SELECT id,trade_id,observed_at,price,size FROM wallet_fill_observations "
                    f"WHERE trade_id IN ({trade_marks}) ORDER BY observed_at,id LIMIT ?",
                    [*trade_ids[:900], fingerprint_rows],
                ).fetchall()]
    source_rows: dict[str, list[dict[str, Any]]] = {}
    forecasts_path = Path(str(cfg["paths"]["forecasts_db"]))
    if forecasts_path.exists():
        with open_ro(forecasts_path) as forecasts:
            _apply_evidence_sql_budget(forecasts, budget)
            source_start = iso(floor_at - timedelta(days=window_days / 2))
            source_end = iso(floor_at + timedelta(days=window_days / 2))
            source_rows["posteriors"] = [dict(row) for row in forecasts.execute(
                "SELECT * FROM forecast_posteriors WHERE lower(city)=lower(?) AND target_date=? AND temperature_metric=? "
                "AND (source_available_at BETWEEN ? AND ? OR source_available_at IS NULL) "
                "ORDER BY computed_at DESC LIMIT ?",
                (position.get("city"), position.get("target_date"), position.get("temperature_metric"), source_start, source_end, fingerprint_rows),
            ).fetchall()]
            source_rows["ensembles"] = [dict(row) for row in forecasts.execute(
                "SELECT * FROM ensemble_snapshots WHERE lower(city)=lower(?) AND target_date=? AND temperature_metric=? "
                "AND (available_at BETWEEN ? AND ? OR available_at IS NULL) "
                "ORDER BY available_at DESC LIMIT ?",
                (position.get("city"), position.get("target_date"), position.get("temperature_metric"), source_start, source_end, fingerprint_rows),
            ).fetchall()]
    def bounded_records(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, str]:
        return {
            name: digest(
                json.dumps(
                    [json.dumps(row, sort_keys=True, default=str)[:4096] for row in values],
                    sort_keys=True,
                ),
                length=32,
            )
            for name, values in rows.items()
        }
    data_payload = {
        "incident": dict(incident),
        "position": position,
        "window": [iso(window_start), iso(window_end)],
        "canonical_row_digests": bounded_records(canonical_rows),
        "source_row_digests": bounded_records(source_rows),
    }
    config_fp = digest(json.dumps(config_payload, sort_keys=True, default=str), length=32)
    capacity_fp = digest(json.dumps(capacity_payload, sort_keys=True, default=str), length=32)
    data_fp = digest(json.dumps(data_payload, sort_keys=True, default=str), length=32)
    fingerprint = digest(config_fp, capacity_fp, data_fp, length=32)
    return fingerprint, config_fp, capacity_fp, data_fp


def _evidence_identity_fingerprints_inner(
    cfg: Mapping[str, Any], incident_id: str, budget: Mapping[str, Any]
) -> tuple[str, str, str, str]:
    _evidence_guard()
    settings = cfg["loop"]
    config_payload = {
        "history_days": settings.get("history_days"),
        "window_days": min(7, max(1, int(settings.get("evidence_window_days", 7)))),
        "rows": int(settings.get("evidence_max_rows", settings.get("max_evidence_rows_per_table", 250000))),
        "source_rows": int(settings.get("evidence_source_rows", 1000)),
    }
    capacity_payload = {
        "builds_per_cycle": int(settings.get("evidence_builds_per_cycle", 1)),
        "budget_ms": float(settings.get("evidence_build_budget_ms", 1000)),
        "max_bytes": int(settings.get("evidence_max_bytes", 32 * 1024 * 1024)),
    }
    with memory(cfg) as mem:
        _apply_evidence_sql_budget(mem, budget)
        _evidence_guard()
        incident = mem.execute(
            "SELECT incident_id,position_id,crossing_evidence_id,held_token_id,evidence_revision,t_floor,detected_at "
            "FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        _evidence_guard()
    if incident is None:
        raise KeyError(f"unknown incident {incident_id}")
    remaining = float(budget["deadline"]) - time.monotonic()
    if remaining <= 0:
        raise EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")
    with open_ro(Path(str(cfg["paths"]["trades_db"])), timeout=remaining) as trades:
        _apply_evidence_sql_budget(trades, budget)
        _evidence_guard()
        position_row = trades.execute(
            "SELECT position_id,phase,city,target_date,temperature_metric,token_id,updated_at "
            "FROM position_current WHERE position_id=?",
            (str(incident["position_id"]),),
        ).fetchone()
        _evidence_guard()
    data_fp = digest(json.dumps({"incident": dict(incident), "position": dict(position_row) if position_row else {}}, sort_keys=True, default=str), length=32)
    config_fp = digest(json.dumps(config_payload, sort_keys=True, default=str), length=32)
    capacity_fp = digest(json.dumps(capacity_payload, sort_keys=True, default=str), length=32)
    return digest(config_fp, capacity_fp, data_fp, length=32), config_fp, capacity_fp, data_fp


def _evidence_identity_fingerprints(
    cfg: Mapping[str, Any], incident_id: str, budget: Mapping[str, Any] | None = None
) -> tuple[str, str, str, str]:
    global _EVIDENCE_BUILD_CONTEXT
    active = budget or _EVIDENCE_BUILD_CONTEXT or _new_evidence_budget(cfg)
    previous = _EVIDENCE_BUILD_CONTEXT
    _EVIDENCE_BUILD_CONTEXT = active
    try:
        return _evidence_identity_fingerprints_inner(cfg, incident_id, active)
    finally:
        _EVIDENCE_BUILD_CONTEXT = previous


def _has_capacity_failure_debt(cfg: Mapping[str, Any], incident_id: str) -> bool:
    with memory(cfg) as mem:
        row = mem.execute(
            "SELECT reason FROM controller_debt WHERE debt_id=? AND status='retry_pending'",
            (_evidence_debt_id(incident_id),),
        ).fetchone()
    return bool(row and str(row[0]).startswith("evidence_snapshot_capacity_failure:"))


def _capacity_debt_matches(cfg: Mapping[str, Any], incident_id: str, fingerprint: str) -> bool:
    with memory(cfg) as mem:
        row = mem.execute(
            "SELECT status,reason,fingerprint FROM controller_debt WHERE debt_id=?",
            (_evidence_debt_id(incident_id),),
        ).fetchone()
    return bool(
        row
        and str(row[0]) == "retry_pending"
        and str(row[1]).startswith("evidence_snapshot_capacity_failure:")
        and str(row[2]) == fingerprint
    )


def _record_evidence_debt(
    cfg: Mapping[str, Any],
    incident_id: str,
    reason: str,
    *,
    preserve_incident_state: bool = False,
    fingerprints: tuple[str, str, str, str] | None = None,
) -> None:
    stamp = iso()
    fingerprint, config_fp, capacity_fp, data_fp = fingerprints or ("", "", "", "")
    with memory(cfg) as mem:
        mem.execute(
            "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,fingerprint,config_fingerprint,capacity_fingerprint,data_fingerprint,attempts) "
            "VALUES (?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(debt_id) DO UPDATE SET kind=excluded.kind,status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at,"
            "fingerprint=excluded.fingerprint,config_fingerprint=excluded.config_fingerprint,capacity_fingerprint=excluded.capacity_fingerprint,"
            "data_fingerprint=excluded.data_fingerprint,attempts=controller_debt.attempts+1",
            (_evidence_debt_id(incident_id), "evidence_snapshot", "retry_pending", reason[:1000], stamp, fingerprint, config_fp, capacity_fp, data_fp),
        )
        if not preserve_incident_state:
            mem.execute(
                "UPDATE incidents SET stage='evidence',status=CASE WHEN status IN ('queued','retry_pending','observing') THEN 'blocked' ELSE status END,updated_at=? WHERE incident_id=?",
                (stamp, incident_id),
            )
        mem.commit()


def _resolve_evidence_debt(cfg: Mapping[str, Any], incident_id: str) -> None:
    stamp = iso()
    with memory(cfg) as mem:
        mem.execute(
            "UPDATE controller_debt SET status='resolved',reason='evidence_snapshot_complete',updated_at=? WHERE debt_id=?",
            (stamp, _evidence_debt_id(incident_id)),
        )
        mem.execute(
            "UPDATE incidents SET stage='blind',status='queued',updated_at=? WHERE incident_id=? AND stage='evidence' AND status='blocked'",
            (stamp, incident_id),
        )
        mem.commit()


def _evidence_pair_paths(cfg: Mapping[str, Any], incident_id: str) -> tuple[Path, Path] | None:
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    pointer = incident_dir / "CURRENT"
    try:
        generation = pointer.read_text().strip()
    except OSError:
        return None
    if not generation or Path(generation).name != generation:
        return None
    snapshot_dir = incident_dir / "generations" / generation
    return snapshot_dir / "evidence.db", snapshot_dir / "manifest.json"


def _stream_evidence_hash(path: Path, budget: Mapping[str, Any]) -> str:
    digestor = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if time.monotonic() >= float(budget["deadline"]):
                raise EvidenceCapacityExceeded("evidence_pair_hash_deferred:time_budget")
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digestor.hexdigest()
            digestor.update(chunk)


def _evidence_pair_valid(
    cfg: Mapping[str, Any], incident_id: str, *, budget: Mapping[str, Any] | None = None
) -> bool:
    pair = _evidence_pair_paths(cfg, incident_id)
    if pair is None:
        return False
    evidence, manifest_path = pair
    manifest = read_json(manifest_path, None)
    if not evidence.is_file() or not isinstance(manifest, Mapping):
        return False
    if str(manifest.get("incident_id") or "") != incident_id:
        return False
    hash_budget = budget or _EVIDENCE_BUILD_CONTEXT or _new_evidence_budget(cfg)
    try:
        if Path(str(manifest.get("evidence_db") or "")).resolve() != evidence.resolve():
            return False
        stat = evidence.stat()
        expected_size = int(manifest.get("size_bytes") or -1)
        expected_mtime = int(manifest.get("evidence_mtime_ns") or -1)
        if expected_size < 0 or expected_mtime < 0:
            return False
        cache_key = (str(evidence), stat.st_size, stat.st_mtime_ns)
        expected_hash = str(manifest.get("sha256") or "")
        cached_hash = _EVIDENCE_HASH_CACHE.get(cache_key)
        if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime:
            actual_hash = cached_hash or _stream_evidence_hash(evidence, hash_budget)
            _EVIDENCE_HASH_CACHE[cache_key] = actual_hash
            if actual_hash != expected_hash:
                return False
        elif cached_hash is not None and cached_hash != expected_hash:
            return False
        with sqlite3.connect(evidence, timeout=0.1) as conn:
            _apply_evidence_sql_budget(conn, hash_budget)
            _budget_check(conn=conn)
            row = conn.execute("SELECT value_json FROM incident WHERE key='incident_id'").fetchone()
        return row is not None and json.loads(str(row[0])) == incident_id
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise EvidenceCapacityExceeded("evidence_pair_identity_deferred:time_budget") from exc
        return False
    except (OSError, sqlite3.Error, json.JSONDecodeError):
        return False


def _capture_pair_valid(
    cfg: Mapping[str, Any], incident_id: str, budget: Mapping[str, Any]
) -> bool:
    try:
        return _evidence_pair_valid(cfg, incident_id, budget=budget)
    except TypeError as exc:
        if "budget" not in str(exc):
            raise
        return _evidence_pair_valid(cfg, incident_id)


def _capture_hard_evidence_inner(
    cfg: Mapping[str, Any],
    incident_ids: Iterable[str] = (),
    *,
    scan_all: bool = False,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _EVIDENCE_BUILD_CONTEXT, _LAST_EVIDENCE_CYCLE
    budget = budget or _new_evidence_budget(cfg)
    _evidence_guard()
    created_order = [str(value) for value in incident_ids if str(value)]
    candidates = set(created_order)
    queue_limit = max(1, int(cfg["loop"].get("evidence_queue_batch_size", 32)))
    queued_ids: list[str] = []
    queue_cursor = 0
    with memory(cfg) as mem:
        _evidence_guard()
        try:
            queue_cursor = max(0, int(meta_get(mem, "evidence_queue_cursor", "0")))
            queued_ids = [str(value) for value in json.loads(meta_get(mem, "evidence_queue", "[]"))][:queue_limit]
        except (TypeError, ValueError, json.JSONDecodeError):
            queued_ids = []
        if scan_all or not queued_ids:
            query = (
                "SELECT incident_id FROM incidents WHERE kind='hard' "
                "ORDER BY CASE WHEN status IN ('queued','running','retry_pending') THEN 0 ELSE 1 END, "
                "priority DESC, detected_at DESC, incident_id LIMIT ? OFFSET ?"
            )
            queued_ids = [str(row[0]) for row in mem.execute(query, (queue_limit, queue_cursor)).fetchall()]
            if not queued_ids and queue_cursor:
                queue_cursor = 0
                queued_ids = [str(row[0]) for row in mem.execute(query, (queue_limit, 0)).fetchall()]
            meta_set(mem, "evidence_queue", json.dumps(queued_ids, separators=(",", ":")))
            mem.commit()
        candidates.update(queued_ids)
        debts = mem.execute(
            "SELECT debt_id FROM controller_debt WHERE kind='evidence_snapshot' AND status='retry_pending' "
            "ORDER BY updated_at DESC LIMIT ?", (queue_limit,)
        ).fetchall()
        _evidence_guard()
        candidates.update(str(row[0]).removeprefix("evidence_snapshot:") for row in debts if str(row[0]).startswith("evidence_snapshot:"))
    if not candidates:
        return {"built": [], "deferred": [], "attempted": 0, "validated": 0, "bytes": 0}
    with memory(cfg) as mem:
        _evidence_guard()
        rows = []
        candidate_list = list(candidates)
        for offset in range(0, len(candidate_list), 900):
            chunk = candidate_list[offset:offset + 900]
            rows.extend(mem.execute(
                "SELECT incident_id,status,position_id,detected_at FROM incidents WHERE kind='hard' AND incident_id IN "
                f"({','.join('?' for _ in chunk)})", tuple(chunk)
            ).fetchall())
    positions: dict[str, dict[str, Any]] = {}
    try:
        with open_ro(Path(str(cfg["paths"]["trades_db"]))) as trades:
            _apply_evidence_sql_budget(trades, budget)
            position_ids = list(dict.fromkeys(str(row[2]) for row in rows))
            for offset in range(0, len(position_ids), 900):
                chunk = position_ids[offset:offset + 900]
                if not chunk:
                    continue
                for row in trades.execute(
                    "SELECT position_id,phase,city,updated_at FROM position_current WHERE position_id IN "
                    f"({','.join('?' for _ in chunk)})", tuple(chunk)
                ).fetchall():
                    positions[str(row[0])] = dict(row)
    except (OSError, sqlite3.Error):
        positions = {}
    status_rank = {"queued": 0, "retry_pending": 0, "blocked": 1, "observing": 2, "running": 3}
    open_rank = {phase: 0 for phase in OPEN_PHASES}
    row_by_id = {str(row[0]): row for row in rows}
    created_rank = {incident_id: index for index, incident_id in enumerate(created_order)}
    def priority(incident_id: str) -> tuple[Any, ...]:
        row = row_by_id[incident_id]
        position = positions.get(str(row[2]), {})
        detected = parse_time(str(row[3]))
        return (
            0 if incident_id in created_rank else 1,
            created_rank.get(incident_id, 10**9),
            open_rank.get(str(position.get("phase") or ""), 2),
            0 if str(position.get("city") or "").casefold() == "tel aviv" else 1,
            status_rank.get(str(row[1]), 9),
            -(detected.timestamp() if detected else 0.0),
            incident_id,
        )
    ordered = sorted(row_by_id, key=priority)
    summary = {"built": [], "deferred": [], "attempted": 0, "validated": 0, "bytes": 0}
    resolved_queue: set[str] = set()
    for incident_id in ordered:
        _evidence_guard()
        _reap_incomplete_generations(cfg, incident_id)
        try:
            pair_valid = _capture_pair_valid(cfg, incident_id, budget)
        except EvidenceCapacityExceeded as exc:
            _record_evidence_debt(
                cfg,
                incident_id,
                f"evidence_snapshot_capacity_failure:{exc}",
                preserve_incident_state=incident_id not in created_rank,
            )
            summary["deferred"].append(incident_id)
            continue
        if pair_valid:
            _resolve_evidence_debt(cfg, incident_id)
            resolved_queue.add(incident_id)
            continue
        summary["validated"] += 1
        has_failure_debt = _has_capacity_failure_debt(cfg, incident_id)
        if has_failure_debt and time.monotonic() >= float(budget["deadline"]):
            summary["deferred"].append(incident_id)
            continue
        if not has_failure_debt and int(budget["remaining"]) <= 0:
            fingerprints = _evidence_identity_fingerprints(cfg, incident_id, budget)
            _record_evidence_debt(
                cfg,
                incident_id,
                "evidence_snapshot_deferred:capacity_count",
                preserve_incident_state=incident_id not in created_rank,
                fingerprints=fingerprints,
            )
            summary["deferred"].append(incident_id)
            continue
        if not has_failure_debt and time.monotonic() >= float(budget["deadline"]):
            fingerprints = _evidence_identity_fingerprints(cfg, incident_id, budget)
            _record_evidence_debt(
                cfg,
                incident_id,
                "evidence_snapshot_deferred:time_budget",
                preserve_incident_state=incident_id not in created_rank,
                fingerprints=fingerprints,
            )
            summary["deferred"].append(incident_id)
            continue
        try:
            _evidence_guard()
            fingerprints = _evidence_fingerprints(cfg, incident_id, budget)
        except Exception as exc:
            _record_evidence_debt(
                cfg,
                incident_id,
                f"{type(exc).__name__}:{exc}",
                preserve_incident_state=incident_id not in created_rank,
            )
            summary["deferred"].append(incident_id)
            continue
        if _capacity_debt_matches(cfg, incident_id, fingerprints[0]):
            summary["deferred"].append(incident_id)
            continue
        if time.monotonic() >= float(budget["deadline"]):
            reason = "evidence_snapshot_deferred:time_budget"
            _record_evidence_debt(
                cfg,
                incident_id,
                reason,
                preserve_incident_state=incident_id not in created_rank,
                fingerprints=fingerprints,
            )
            summary["deferred"].append(incident_id)
            continue
        budget["remaining"] = int(budget["remaining"]) - 1
        budget["attempted"] = int(budget.get("attempted", 0)) + 1
        summary["attempted"] += 1
        try:
            _run_bounded_evidence_build(cfg, incident_id, budget)
            budget["built"] = int(budget.get("built", 0)) + 1
            summary["built"].append(incident_id)
        except RuntimeError as exc:
            if isinstance(exc, EvidenceCapacityExceeded):
                _record_evidence_debt(
                    cfg,
                    incident_id,
                    f"evidence_snapshot_capacity_failure:{exc}",
                    preserve_incident_state=incident_id not in created_rank,
                    fingerprints=fingerprints,
                )
                summary["deferred"].append(incident_id)
                continue
            if "position missing" in str(exc):
                _record_evidence_debt(cfg, incident_id, f"{type(exc).__name__}:{exc}", preserve_incident_state=incident_id not in created_rank, fingerprints=fingerprints)
                continue
            _record_evidence_debt(cfg, incident_id, f"{type(exc).__name__}:{exc}", preserve_incident_state=incident_id not in created_rank, fingerprints=fingerprints)
        except Exception as exc:
            _record_evidence_debt(cfg, incident_id, f"{type(exc).__name__}:{exc}", preserve_incident_state=incident_id not in created_rank, fingerprints=fingerprints)
        else:
            _resolve_evidence_debt(cfg, incident_id)
            resolved_queue.add(incident_id)
        finally:
            _EVIDENCE_BUILD_CONTEXT = None
    if queued_ids and resolved_queue:
        _evidence_guard()
        with memory(cfg) as mem:
            remaining = [value for value in queued_ids if value not in resolved_queue]
            meta_set(mem, "evidence_queue", json.dumps(remaining, separators=(",", ":")))
            if len(remaining) == 0:
                meta_set(mem, "evidence_queue_cursor", str(queue_cursor + len(queued_ids)))
            mem.commit()
    summary["bytes"] = int(budget.get("bytes", 0))
    _evidence_guard()
    _LAST_EVIDENCE_CYCLE["built"] = list(dict.fromkeys([*_LAST_EVIDENCE_CYCLE.get("built", []), *summary["built"]]))
    _LAST_EVIDENCE_CYCLE["deferred"] = list(dict.fromkeys([*_LAST_EVIDENCE_CYCLE.get("deferred", []), *summary["deferred"]]))
    _LAST_EVIDENCE_CYCLE["attempted"] = int(_LAST_EVIDENCE_CYCLE.get("attempted", 0)) + int(summary["attempted"])
    _LAST_EVIDENCE_CYCLE["validated"] = int(_LAST_EVIDENCE_CYCLE.get("validated", 0)) + int(summary["validated"])
    _LAST_EVIDENCE_CYCLE["bytes"] = max(int(_LAST_EVIDENCE_CYCLE.get("bytes", 0)), int(summary["bytes"]))
    return summary


def _capture_hard_evidence(
    cfg: Mapping[str, Any],
    incident_ids: Iterable[str] = (),
    *,
    scan_all: bool = False,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the whole evidence slice under one absolute deadline."""

    global _EVIDENCE_BUILD_CONTEXT
    budget = budget or _new_evidence_budget(cfg)
    previous = _EVIDENCE_BUILD_CONTEXT
    _EVIDENCE_BUILD_CONTEXT = budget
    try:
        return _capture_hard_evidence_inner(
            cfg, incident_ids, scan_all=scan_all, budget=budget
        )
    finally:
        _EVIDENCE_BUILD_CONTEXT = previous


def _active_loaded_sha(cfg: Mapping[str, Any]) -> str | None:
    loaded = read_json(Path(str(cfg["paths"]["trades_db"])).parent / "loaded_sha.json", {})
    if not isinstance(loaded, Mapping):
        return None
    value = str(loaded.get("loaded_sha") or loaded.get("boot_sha") or "")
    return value or None


def _evidence_coverage(
    path: Path, *, budget: Mapping[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    time_columns = {
        "price_ticks": "quote_seen_at",
        "probability_ticks": "occurred_at",
        "monitor_events": "occurred_at",
        "exit_decisions": "occurred_at",
        "venue_commands": "created_at",
        "order_facts": "observed_at",
        "trade_facts": "observed_at",
        "fills": "observed_at",
    }
    result: dict[str, dict[str, Any]] = {}
    budget = budget or _EVIDENCE_BUILD_CONTEXT or {"deadline": time.monotonic() + 1.0, "max_bytes": 2**63}
    try:
        with sqlite3.connect(path, timeout=0.1) as conn:
            _apply_evidence_sql_budget(conn, budget)
            _budget_check(conn=conn)
            tables = [
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            for table in tables:
                _budget_check(conn=conn)
                count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                coverage: dict[str, Any] = {"rows": count}
                column = time_columns.get(table)
                if column:
                    _budget_check(conn=conn)
                    first, last = conn.execute(
                        f'SELECT MIN("{column}"),MAX("{column}") FROM "{table}"'
                    ).fetchone()
                    coverage.update(first_at=first, last_at=last)
                result[table] = coverage
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise EvidenceCapacityExceeded("evidence_coverage_deferred:time_budget") from exc
        raise
    return result


def read_json_text(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _copy_source_clocks(
    cfg: Mapping[str, Any],
    out: sqlite3.Connection,
    position: Mapping[str, Any],
    *,
    row_limit: int = 1000,
) -> None:
    path = Path(str(cfg["paths"]["forecasts_db"]))
    if not path.exists():
        return
    with open_ro(path) as forecasts:
        target = parse_time(f"{position.get('target_date')}T00:00:00+00:00") or now()
        source_start = iso(target - timedelta(days=3))
        source_end = iso(target + timedelta(days=4))
        budget = _EVIDENCE_BUILD_CONTEXT
        _apply_evidence_sql_budget(forecasts, budget)
        try:
            rows = forecasts.execute(
                """
                SELECT * FROM forecast_posteriors
                 WHERE lower(city)=lower(?) AND target_date=? AND temperature_metric=?
                   AND (source_available_at BETWEEN ? AND ? OR source_available_at IS NULL)
                 ORDER BY computed_at DESC LIMIT ?
                """,
                (position.get("city"), position.get("target_date"), position.get("temperature_metric"), source_start, source_end, row_limit),
            ).fetchall()
            for raw in rows:
                _budget_check(conn=out)
                row = dict(raw)
                key = f"posterior:{row['posterior_id']}"
                raw_json = json.dumps(row, default=str)
                _budget_check(conn=out, extra_bytes=len(raw_json.encode()))
                out.execute(
                    "INSERT OR REPLACE INTO source_clocks VALUES (?,?,?,?,?,?)",
                    (key, row.get("source_cycle_time"), row.get("source_available_at"), row.get("computed_at"), row.get("recorded_at"), raw_json),
                )
            ens = forecasts.execute(
                """
                SELECT * FROM ensemble_snapshots
                 WHERE lower(city)=lower(?) AND target_date=? AND temperature_metric=?
                   AND (available_at BETWEEN ? AND ? OR available_at IS NULL)
                 ORDER BY available_at DESC LIMIT ?
                """,
                (position.get("city"), position.get("target_date"), position.get("temperature_metric"), source_start, source_end, row_limit),
            ).fetchall()
            for raw in ens:
                _budget_check(conn=out)
                row = dict(raw)
                key = f"ensemble:{row['snapshot_id']}"
                raw_json = json.dumps(row, default=str)
                _budget_check(conn=out, extra_bytes=len(raw_json.encode()))
                out.execute(
                    "INSERT OR REPLACE INTO source_clocks VALUES (?,?,?,?,?,?)",
                    (key, row.get("source_cycle_time") or row.get("issue_time"), row.get("source_available_at") or row.get("available_at"), row.get("fetch_time"), row.get("recorded_at"), raw_json),
                )
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower() and budget is not None:
                raise EvidenceCapacityExceeded("evidence_snapshot_deferred:forecast_query_budget") from exc
            raise


def _copy_runtime_health(out: sqlite3.Connection) -> None:
    candidates = {
        "main_heartbeat": ROOT / "state" / "forecast_live_heartbeat.json",
        "status_summary": ROOT / "state" / "status_summary.json",
        "market_channel": ROOT / "state" / "market-channel-continuity.json",
    }
    for name, path in candidates.items():
        _budget_check(conn=out)
        payload = read_json(path, None)
        if payload is not None:
            _budget_check(conn=out, extra_bytes=len(json.dumps(payload, default=str).encode()))
            observed = payload.get("at") or payload.get("observed_at") or payload.get("timestamp") if isinstance(payload, Mapping) else None
            out.execute("INSERT INTO daemon_health VALUES (?,?,?)", (name, observed, json.dumps(payload, default=str)))


def _copy_versions_and_config(cfg: Mapping[str, Any], out: sqlite3.Connection) -> None:
    _budget_check(conn=out)
    budget = _EVIDENCE_BUILD_CONTEXT
    timeout = max(0.01, float(budget["deadline"]) - time.monotonic()) if budget is not None else 5.0
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)
    out.execute("INSERT INTO code_versions VALUES (?,?,?,?)", ("repo_head", proc.stdout.strip(), str(ROOT), iso()))
    loaded_path = Path(str(cfg["paths"]["trades_db"])).parent / "loaded_sha.json"
    loaded = read_json(loaded_path, {})
    if isinstance(loaded, Mapping):
        loaded_sha = str(loaded.get("loaded_sha") or loaded.get("boot_sha") or "")
        loaded_at = loaded.get("generated_at") or loaded.get("loaded_at") or loaded.get("booted_at") or loaded.get("at")
        if loaded_sha:
            out.execute(
                "INSERT INTO code_versions VALUES (?,?,?,?)",
                ("live_loaded", loaded_sha, str(loaded_path), loaded_at or iso()),
            )
    for name, path in (("loop", Path(str(cfg.get("_config_path") or CONFIG_PATH))), ("settings", Path(str(cfg["paths"]["settings"])))):
        _budget_check(conn=out)
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if name == "settings":
            parsed = read_json(path, {})
            value: Any = {key: parsed.get(key) for key in ("execution", "edli_v1", "monitor") if isinstance(parsed, Mapping) and key in parsed}
        else:
            value = raw.decode(errors="replace")
        raw_json = json.dumps(value, default=str)
        _budget_check(conn=out, extra_bytes=len(raw_json.encode()))
        out.execute("INSERT INTO config_snapshot VALUES (?,?,?)", (name, raw_json, hashlib.sha256(raw).hexdigest()))


DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "incident_id", "root", "earliest_preventable_time", "causal_seam",
        "capital_counterfactual", "timeline", "changed_symbols", "evidence_refs",
    ],
    "properties": {
        "incident_id": {"type": "string"},
        "root": {"type": "string"},
        "earliest_preventable_time": {"type": ["string", "null"]},
        "causal_seam": {"type": "string"},
        "capital_counterfactual": {
            "type": "object",
            "additionalProperties": False,
            "required": ["executable_at", "recoverable_usd", "actual_recovery_usd", "avoidable_loss_usd", "assumptions"],
            "properties": {
                "executable_at": {"type": ["string", "null"]},
                "recoverable_usd": {"type": ["number", "null"]},
                "actual_recovery_usd": {"type": ["number", "null"]},
                "avoidable_loss_usd": {"type": ["number", "null"]},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["clock", "at", "evidence"],
                "properties": {
                    "clock": {"type": "string", "enum": ["source", "probability", "monitor", "decision", "command", "fill", "floor"]},
                    "at": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                },
            },
        },
        "changed_symbols": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
}

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_id", "relation", "root_id", "mechanism_fingerprint", "reason"],
    "properties": {
        "incident_id": {"type": "string"},
        "relation": {
            "type": "string",
            "enum": ["same_root", "root_variant", "new_root", "fix_not_deployed", "fix_incomplete", "antibody_failed"],
        },
        "root_id": {"type": "string"},
        "mechanism_fingerprint": {"type": "string"},
        "reason": {"type": "string"},
    },
}

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_id", "status", "changed_symbols", "verification", "replay", "commit_sha", "blocker"],
    "properties": {
        "incident_id": {"type": "string"},
        "status": {"type": "string", "enum": ["patch_ready", "blocked", "no_change_needed"]},
        "changed_symbols": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "replay": {
            "type": "object",
            "additionalProperties": False,
            "required": ["command", "passed", "baseline_action_at", "patched_action_at", "t_floor", "capital_effect_usd"],
            "properties": {
                "command": {"type": "string"},
                "passed": {"type": "boolean"},
                "baseline_action_at": {"type": ["string", "null"]},
                "patched_action_at": {"type": ["string", "null"]},
                "t_floor": {"type": ["string", "null"]},
                "capital_effect_usd": {"type": ["number", "null"]},
            },
        },
        "commit_sha": {"type": ["string", "null"]},
        "blocker": {"type": ["string", "null"]},
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["blocking", "findings", "coverage"],
    "properties": {
        "blocking": {"type": "boolean"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "file", "line", "finding"],
                "properties": {
                    "severity": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "finding": {"type": "string"},
                },
            },
        },
        "coverage": {"type": "string"},
    },
}

DELIVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_id", "status", "pr", "head_sha", "merge_sha", "verification", "blocker"],
    "properties": {
        "incident_id": {"type": "string"},
        "status": {"type": "string", "enum": ["merged", "blocked"]},
        "pr": {"type": ["string", "null"]},
        "head_sha": {"type": ["string", "null"]},
        "merge_sha": {"type": ["string", "null"]},
        "verification": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": ["string", "null"]},
    },
}

PRODUCTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_id", "status", "merge_sha", "deploy_sha", "loaded_sha", "observed_seconds", "verification", "blocker"],
    "properties": {
        "incident_id": {"type": "string"},
        "status": {"type": "string", "enum": ["production_verified", "blocked"]},
        "merge_sha": {"type": "string"},
        "deploy_sha": {"type": ["string", "null"]},
        "loaded_sha": {"type": ["string", "null"]},
        "observed_seconds": {"type": "number"},
        "verification": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": ["string", "null"]},
    },
}


def _schema_file(cfg: Mapping[str, Any], name: str, schema: Mapping[str, Any]) -> Path:
    path = runtime_dir(cfg) / "schemas" / f"{name}.json"
    atomic_json(path, schema)
    return path


def codex_bin() -> str:
    return shutil.which("codex") or str(Path.home() / ".npm-global" / "bin" / "codex")


def required_reasoning_effort(cfg: Mapping[str, Any]) -> str:
    """The dedicated investigator is operator-pinned to exact high reasoning."""

    profile = cfg["profiles"][cfg["active"]["profile"]]
    preferred = str(profile.get("preferred_reasoning") or "")
    fallbacks = list(profile.get("fallback_reasoning") or [])
    if preferred != "high" or fallbacks:
        raise RuntimeError(
            "total-loss Codex profile must use preferred_reasoning=high "
            "with no fallback"
        )
    return "high"


def isolated_codex_home(cfg: Mapping[str, Any]) -> Path:
    home = runtime_dir(cfg) / "codex-home"
    _startup_guard()
    home.mkdir(parents=True, exist_ok=True)
    _startup_guard()
    home.chmod(0o700)
    _startup_guard()
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    source_auth = source_home / "auth.json"
    target_auth = home / "auth.json"
    _startup_guard()
    if target_auth.is_symlink() and target_auth.resolve() != source_auth:
        raise RuntimeError("isolated Codex auth link targets an unexpected file")
    if not target_auth.exists():
        if not source_auth.is_file():
            raise RuntimeError("Codex auth unavailable for isolated home")
        _startup_guard()
        target_auth.symlink_to(source_auth)
    _startup_guard()
    (home / "config.toml").write_text(
        "[features]\nmemories = false\nmulti_agent = true\n\n"
        "[memories]\nuse_memories = false\ngenerate_memories = false\n\n"
        "[agents]\nenabled = true\n"
    )
    _startup_guard()
    return home


def _run_capture(command: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout: int = 60, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    effective_timeout = timeout
    if _STARTUP_BUDGET is not None:
        _startup_guard()
        effective_timeout = min(timeout, max(0.01, float(_STARTUP_BUDGET["deadline"]) - time.monotonic()))
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=effective_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if _STARTUP_BUDGET is not None:
            raise StartupMaintenanceDeferred("startup_maintenance_deferred:subprocess_timeout") from exc
        raise
    if _STARTUP_BUDGET is not None:
        _startup_guard()
    return result


def _run_probe_capture(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    with _probe_lock:
        _probe_process_groups.add(child.pid)
    try:
        stdout, stderr = child.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(child.pid)
        stdout, stderr = child.communicate()
        return subprocess.CompletedProcess(command, 124, stdout, stderr)
    finally:
        with _probe_lock:
            _probe_process_groups.discard(child.pid)
    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)


def probe_capabilities(cfg: Mapping[str, Any], *, smoke: bool = True) -> dict[str, Any]:
    home = isolated_codex_home(cfg)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    env["TERM"] = "xterm-256color"
    binary = codex_bin()
    version = _run_probe_capture([binary, "--version"], cwd=ROOT, env=env, timeout=60)
    doctor = _run_probe_capture([binary, "doctor"], cwd=ROOT, env=env, timeout=120)
    models = _run_probe_capture([binary, "debug", "models"], cwd=ROOT, env=env, timeout=120)
    try:
        catalog = json.loads(models.stdout)
    except json.JSONDecodeError:
        catalog = {}
    profile_name = str(cfg["active"]["profile"])
    profile = cfg["profiles"][profile_name]
    wanted = str(profile["model"])
    required_effort = required_reasoning_effort(cfg)
    selected = next((row for row in catalog.get("models", []) if row.get("slug") == wanted), None)
    supported = [str(row.get("effort")) for row in (selected or {}).get("supported_reasoning_levels", [])]
    effort = required_effort if required_effort in supported else None
    if selected is None or effort is None:
        raise RuntimeError(f"configured Codex profile unavailable: model={wanted} supported={supported}")
    prompt_probe = _run_probe_capture(
        [binary, "debug", "prompt-input", "total-loss isolation probe"],
        cwd=ROOT,
        env=env,
        timeout=120,
    )
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probed_at": iso(),
        "binary": binary,
        "version": version.stdout.strip(),
        "doctor_ok": doctor.returncode == 0,
        "doctor_digest": hashlib.sha256((doctor.stdout + doctor.stderr).encode()).hexdigest(),
        "model": wanted,
        "reasoning_effort": effort,
        "supported_reasoning": supported,
        "context_window": (selected or {}).get("context_window"),
        "prompt_input_ok": (
            prompt_probe.returncode == 0
            and ".codex/memories" not in prompt_probe.stdout
            and "memory_summary" not in prompt_probe.stdout.lower()
        ),
        "prompt_input_digest": hashlib.sha256(prompt_probe.stdout.encode()).hexdigest(),
        "structured_output_ok": None,
        "workspace_write_ok": None,
        "delivery_network_ok": None,
        "resume_ok": None,
        "multi_agent_ok": None,
    }
    if smoke:
        smoke_dir = runtime_dir(cfg) / "probe-workspace"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        schema = _schema_file(
            cfg,
            "capability-smoke",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean", "const": True}},
            },
        )
        output = runtime_dir(cfg) / "capability-smoke.json"
        command = _codex_exec_base(
            cfg, sandbox="workspace-write", cwd=smoke_dir, schema=schema,
            output=output, persistent=True, reasoning_effort=effort,
        )
        smoke_run = _run_probe_capture(
            command,
            cwd=smoke_dir,
            env=env,
            timeout=300,
            stdin="Create probe.txt containing exactly ok, verify it, and return {\"ok\":true}.",
        )
        result["structured_output_ok"] = smoke_run.returncode == 0 and read_json(output, {}) == {"ok": True}
        result["workspace_write_ok"] = (smoke_dir / "probe.txt").read_text().strip() == "ok" if (smoke_dir / "probe.txt").exists() else False
        session_id = None
        for line in smoke_run.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                session_id = str(event.get("thread_id") or "") or None
                break
        resume_output = runtime_dir(cfg) / "capability-resume-smoke.json"
        if session_id:
            resume_command = [
                binary, "-a", "never", "exec", "resume", session_id,
                "--ignore-user-config", "--strict-config",
                "-m", wanted,
                "-c", f'model_reasoning_effort="{effort}"',
                "-c", "features.memories=false",
                "-c", "features.multi_agent=true",
                "--output-schema", str(schema),
                "--output-last-message", str(resume_output),
                "--json", "-",
            ]
            resume_run = _run_probe_capture(
                resume_command,
                cwd=smoke_dir,
                env=env,
                timeout=300,
                stdin="Verify probe.txt still contains exactly ok and return {\"ok\":true}.",
            )
            result["resume_ok"] = resume_run.returncode == 0 and read_json(resume_output, {}) == {"ok": True}
        features = _run_probe_capture([binary, "features", "list"], cwd=ROOT, env=env, timeout=60)
        result["multi_agent_ok"] = features.returncode == 0 and "multi_agent" in features.stdout
        network_output = runtime_dir(cfg) / "capability-network-smoke.json"
        network_command = _codex_exec_base(
            cfg,
            sandbox="workspace-write",
            cwd=smoke_dir,
            schema=schema,
            output=network_output,
            persistent=False,
            network=True,
            reasoning_effort=effort,
        )
        network_run = _run_probe_capture(
            network_command,
            cwd=smoke_dir,
            env=env,
            timeout=300,
            stdin=(
                "Run `gh repo view --json nameWithOwner` without modifying the repository. "
                "Return {\"ok\":true} only if it succeeds."
            ),
        )
        result["delivery_network_ok"] = (
            network_run.returncode == 0
            and read_json(network_output, {}) == {"ok": True}
        )
    atomic_json(runtime_dir(cfg) / "capabilities.json", result)
    return result


def capabilities(cfg: Mapping[str, Any]) -> dict[str, Any]:
    path = runtime_dir(cfg) / "capabilities.json"
    value = read_json(path, None)
    fingerprint = _capability_fingerprint(cfg)
    current = read_json(runtime_dir(cfg) / "capability-fingerprint.json", {})
    profile = cfg["profiles"][cfg["active"]["profile"]]
    if (
        not isinstance(value, dict)
        or current.get("value") != fingerprint
        or value.get("model") != profile.get("model")
        or value.get("reasoning_effort") != required_reasoning_effort(cfg)
    ):
        value = probe_capabilities(cfg, smoke=True)
        atomic_json(runtime_dir(cfg) / "capability-fingerprint.json", {"value": fingerprint, "at": iso()})
    return value


def _capability_fingerprint(cfg: Mapping[str, Any]) -> str:
    binary = Path(codex_bin())
    stamp = binary.stat().st_mtime_ns if binary.exists() else 0
    profile_name = str(cfg["active"]["profile"])
    profile = cfg["profiles"][profile_name]
    profile_hash = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{binary}:{stamp}:{profile_name}:{profile_hash}"


def current_capabilities(cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    value = read_json(runtime_dir(cfg) / "capabilities.json", None)
    current = read_json(runtime_dir(cfg) / "capability-fingerprint.json", {})
    profile = cfg["profiles"][cfg["active"]["profile"]]
    if (
        not isinstance(value, dict)
        or current.get("value") != _capability_fingerprint(cfg)
        or value.get("model") != profile.get("model")
        or value.get("reasoning_effort") != required_reasoning_effort(cfg)
    ):
        return None
    required = (
        "structured_output_ok",
        "workspace_write_ok",
        "delivery_network_ok",
        "resume_ok",
        "multi_agent_ok",
    )
    if any(value.get(key) is not True for key in required):
        return None
    return value


def ensure_capability_probe(cfg: Mapping[str, Any]) -> None:
    """Probe off the detector thread so quote crossings remain sub-second."""

    global _probe_thread
    if current_capabilities(cfg) is not None:
        return
    with _probe_lock:
        if _probe_thread is not None and _probe_thread.is_alive():
            return

        def run() -> None:
            launch_lock: int | None = None
            try:
                launch_lock = _acquire_provider_launch_lock(cfg)
                if _provider_backoff(cfg) is not None:
                    return
                probe_capabilities(cfg, smoke=True)
                atomic_json(
                    runtime_dir(cfg) / "capability-fingerprint.json",
                    {"value": _capability_fingerprint(cfg), "at": iso()},
                )
                (runtime_dir(cfg) / "capability-error.json").unlink(missing_ok=True)
            except Exception as exc:
                atomic_json(
                    runtime_dir(cfg) / "capability-error.json",
                    {"at": iso(), "error": f"{type(exc).__name__}: {exc}"},
                )
            finally:
                if launch_lock is not None:
                    _release_provider_launch_lock(launch_lock)

        _probe_thread = threading.Thread(
            target=run,
            name="total-loss-capability-probe",
            daemon=True,
        )
        _probe_thread.start()


def _codex_exec_base(
    cfg: Mapping[str, Any],
    *,
    sandbox: str,
    cwd: Path,
    schema: Path,
    output: Path,
    persistent: bool,
    network: bool = False,
    reasoning_effort: str | None = None,
) -> list[str]:
    profile = cfg["profiles"][cfg["active"]["profile"]]
    required_effort = required_reasoning_effort(cfg)
    if reasoning_effort is not None and reasoning_effort != required_effort:
        raise RuntimeError("total-loss Codex runs require reasoning_effort=high")
    command = [
        codex_bin(),
        "-a", "never",
        "exec",
        "--ignore-user-config",
        "--strict-config",
        "--sandbox", sandbox,
        "-C", str(cwd),
        "--skip-git-repo-check",
        "-m", str(profile["model"]),
        "-c", f'model_reasoning_effort="{required_effort}"',
        "-c", "features.memories=false",
        "-c", "features.multi_agent=true",
        "--output-schema", str(schema),
        "--output-last-message", str(output),
        "--json",
    ]
    if network:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    if not persistent:
        command.append("--ephemeral")
    command.append("-")
    return command


def _codex_resume_base(
    cfg: Mapping[str, Any],
    *,
    session_id: str,
    schema: Path,
    output: Path,
) -> list[str]:
    cap = current_capabilities(cfg) or {"reasoning_effort": required_reasoning_effort(cfg)}
    profile = cfg["profiles"][cfg["active"]["profile"]]
    if cap.get("reasoning_effort") != required_reasoning_effort(cfg):
        raise RuntimeError("total-loss Codex resume requires reasoning_effort=high")
    return [
        codex_bin(), "-a", "never", "exec", "resume", session_id,
        "--ignore-user-config", "--strict-config",
        "-m", str(profile["model"]),
        "-c", 'model_reasoning_effort="high"',
        "-c", "features.memories=false",
        "-c", "features.multi_agent=true",
        "--output-schema", str(schema),
        "--output-last-message", str(output),
        "--json", "-",
    ]


def _parse_session(events_path: Path) -> tuple[str | None, dict[str, Any]]:
    session = None
    usage: dict[str, Any] = {}
    try:
        lines = events_path.read_text(errors="replace").splitlines()
    except OSError:
        return None, {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            session = str(event.get("thread_id") or "") or session
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = dict(event["usage"])
    return session, usage


_PROVIDER_QUOTA_MESSAGE = re.compile(
    r"^(?:you(?:'|’)ve|you have)\s+hit\s+your\s+usage\s+limit\b"
    r"|^usage\s+limit\s+(?:reached|exceeded)\b"
)
_PROVIDER_RATE_MESSAGE = re.compile(
    r"^rate\s+limit\s+(?:reached|exceeded)\b"
    r"|^too\s+many\s+requests\b"
    r"|^resource\s+exhausted\b"
)


def _terminal_error_message(event: Mapping[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, Mapping):
        value = error.get("message") or error.get("detail") or error.get("reason")
    else:
        value = event.get("message") or event.get("reason") or error
    return str(value or "")


def _parse_terminal_failure(
    events_path: Path, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Read terminal Codex failure events even when the CLI exits rc=0."""

    turn_errors: list[Mapping[str, Any]] = []
    current_turn_id: str | None = None
    failed: Mapping[str, Any] | None = None
    linked_errors: list[Mapping[str, Any]] = []
    try:
        lines = events_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            current_turn_id = str(event.get("turn_id") or event.get("id") or "") or None
            turn_errors = []
        elif event_type == "error":
            turn_errors.append(event)
        elif event_type in {"turn.failed", "turn_failed"}:
            failed = event
            failed_turn_id = str(event.get("turn_id") or "") or current_turn_id
            linked_errors = [
                item for item in turn_errors
                if failed_turn_id is None
                or not str(item.get("turn_id") or "")
                or str(item.get("turn_id") or "") == failed_turn_id
            ]
            break
    if failed is None:
        return None
    detail = failed.get("error") or failed.get("message") or failed.get("reason")
    detail_text = json.dumps(detail, ensure_ascii=False, default=str) if isinstance(detail, (Mapping, list)) else str(detail or "")
    linked_detail = [
        _terminal_error_message(item)
        for item in linked_errors
    ]
    if not detail_text and linked_detail:
        detail_text = linked_detail[-1]
    codes: list[str] = []
    for item in [failed, *linked_errors]:
        error = item.get("error")
        for source in [item, error if isinstance(error, Mapping) else {}]:
            for key in ("code", "error_code", "provider_code"):
                if source.get(key):
                    codes.append(str(source[key]).lower().replace("-", "_"))
    quota_codes = {
        "usage_limit", "usage_limit_exceeded", "quota_exceeded",
    }
    rate_codes = {"rate_limit", "rate_limit_exceeded", "resource_exhausted"}
    provider_messages = [
        _terminal_error_message(item)
        for item in [failed, *linked_errors]
    ]
    quota_signal = any(code in quota_codes or code.endswith("_quota_exceeded") for code in codes)
    rate_signal = any(code in rate_codes for code in codes)
    quota_signal = quota_signal or any(
        bool(_PROVIDER_QUOTA_MESSAGE.search(" ".join(message.lower().replace("’", "'").split())))
        for message in provider_messages
    )
    rate_signal = rate_signal or any(
        bool(_PROVIDER_RATE_MESSAGE.search(" ".join(message.lower().split())))
        for message in provider_messages
    )
    provider_limit = quota_signal or rate_signal
    retry_at = _retry_at_from_failure(failed, cfg)
    if retry_at is None:
        for error_event in reversed(linked_errors):
            retry_at = _retry_at_from_failure(error_event, cfg)
            if retry_at is not None:
                break
    return {
        "kind": (
            "provider_quota_limit" if quota_signal
            else "provider_rate_limit" if rate_signal
            else "terminal_failure"
        ),
        "reason": detail_text[:1000] or "codex_turn_failed",
        "provider_wide": provider_limit,
        "retry_at": retry_at,
    }


def _retry_at_from_failure(
    event: Mapping[str, Any], cfg: Mapping[str, Any] | None = None
) -> str | None:
    max_seconds = float((cfg or {}).get("loop", {}).get("max_provider_backoff_seconds", 86_400))
    max_seconds = max(1.0, min(max_seconds, 86_400.0))
    minimum_seconds = float((cfg or {}).get("loop", {}).get("provider_cooldown_seconds", 300))
    minimum_seconds = max(1.0, min(minimum_seconds, max_seconds))

    def bounded_absolute(value: datetime) -> str | None:
        seconds = (value - now()).total_seconds()
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        return iso(now() + timedelta(seconds=max(minimum_seconds, min(seconds, max_seconds))))

    for key in (
        "next_retry_at", "retry_at", "reset_at", "retry_after",
        "retry_after_seconds", "retry_after_ms",
    ):
        value = event.get(key)
        if value is None and isinstance(event.get("error"), Mapping):
            value = event["error"].get(key)
        if value is None:
            continue
        parsed = parse_time(str(value))
        if parsed is not None:
            bounded = bounded_absolute(parsed)
            if bounded is not None:
                return bounded
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(numeric) or numeric <= 0:
            continue
        try:
            if key.endswith("_ms") or key == "retry_after_ms":
                seconds = numeric / 1000.0
            elif numeric >= 1_000_000_000_000:
                absolute = datetime.fromtimestamp(numeric / 1000.0, UTC)
                bounded = bounded_absolute(absolute)
                if bounded is not None:
                    return bounded
                continue
            elif numeric >= 1_000_000_000:
                absolute = datetime.fromtimestamp(numeric, UTC)
                bounded = bounded_absolute(absolute)
                if bounded is not None:
                    return bounded
                continue
            elif key.endswith("_seconds") or key == "retry_after":
                seconds = numeric
            elif numeric <= 86_400:
                seconds = numeric
            elif numeric <= 86_400_000:
                seconds = numeric / 1000.0
            else:
                continue
            if not math.isfinite(seconds) or seconds <= 0:
                continue
            return iso(now() + timedelta(seconds=min(seconds, 86_400.0)))
        except (ValueError, OverflowError, OSError):
            continue
    return None


def _provider_backoff(cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        with memory_ro(cfg) as mem:
            raw = meta_get(mem, "codex_provider_backoff", "")
    except (OSError, sqlite3.Error):
        return None
    value = read_json_text(raw)
    retry_at = parse_time(str(value.get("next_retry_at") or ""))
    if not isinstance(value, Mapping) or retry_at is None:
        return None
    if retry_at <= now():
        return None
    max_seconds = max(1.0, min(float(cfg["loop"].get("max_provider_backoff_seconds", 86_400)), 86_400.0))
    minimum_seconds = max(1.0, min(float(cfg["loop"].get("provider_cooldown_seconds", 300)), max_seconds))
    remaining = (retry_at - now()).total_seconds()
    result = dict(value)
    legacy = not result.get("policy_revision")
    kind = str(result.get("kind") or "provider_rate_limit")
    if legacy:
        bounded_seconds = max_seconds if kind == "provider_quota_limit" else minimum_seconds
        bounded_at = now() + timedelta(seconds=bounded_seconds)
        result["policy_revision"] = "provider-backoff-v2"
        result["policy_class"] = kind
    elif remaining > max_seconds:
        bounded_at = now() + timedelta(seconds=max_seconds)
        result["next_retry_at"] = iso(bounded_at)
        result["updated_at"] = iso()
        with memory(cfg) as mem:
            meta_set(mem, "codex_provider_backoff", json.dumps(result, sort_keys=True))
            mem.commit()
        return result
    if legacy:
        result["next_retry_at"] = iso(bounded_at)
        result["updated_at"] = iso()
        with memory(cfg) as mem:
            meta_set(mem, "codex_provider_backoff", json.dumps(result, sort_keys=True))
            mem.commit()
    return result


def _set_provider_backoff(
    cfg: Mapping[str, Any],
    mem: sqlite3.Connection,
    failure: Mapping[str, Any],
) -> dict[str, Any]:
    retry_at = parse_time(str(failure.get("retry_at") or ""))
    max_seconds = max(1.0, min(float(cfg["loop"].get("max_provider_backoff_seconds", 86_400)), 86_400.0))
    minimum_seconds = max(1.0, min(float(cfg["loop"].get("provider_cooldown_seconds", 300)), max_seconds))
    if retry_at is not None:
        remaining = (retry_at - now()).total_seconds()
        if not math.isfinite(remaining) or remaining <= 0:
            retry_at = None
        else:
            retry_at = now() + timedelta(seconds=max(minimum_seconds, min(remaining, max_seconds)))
    if retry_at is None:
        fallback_seconds = (
            max_seconds
            if str(failure.get("kind") or "") == "provider_quota_limit"
            else minimum_seconds
        )
        retry_at = now() + timedelta(seconds=fallback_seconds)
    payload = {
        "next_retry_at": iso(retry_at),
        "reason": str(failure.get("reason") or "provider_quota_limit")[:1000],
        "kind": str(failure.get("kind") or "provider_quota_limit"),
        "policy_revision": "provider-backoff-v2",
        "policy_class": str(failure.get("kind") or "provider_quota_limit"),
        "updated_at": iso(),
    }
    meta_set(mem, "codex_provider_backoff", json.dumps(payload, sort_keys=True))
    return payload


def _spawn_run_unlocked(
    cfg: Mapping[str, Any],
    *,
    incident_id: str,
    kind: str,
    stage: str,
    command: list[str],
    cwd: Path,
    prompt: str,
    output: Path,
    events: Path,
    session_id: str | None = None,
    workspace_branch: str | None = None,
    resume_owned_workspace: bool = False,
) -> dict[str, Any]:
    started_at = iso()
    run_id = digest(incident_id, stage, started_at, os.getpid(), time.monotonic_ns())
    writer_lease = stage in _WORKTREE_WRITE_STAGES
    events.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(isolated_codex_home(cfg))
    env["TERM"] = "xterm-256color"
    prompt_file = events.with_suffix(".prompt.md")
    prompt_file.write_text(prompt)
    prompt_handle = prompt_file.open("rb")
    events_handle = events.open("wb")
    wrapped = command
    nice = shutil.which("nice")
    if nice:
        wrapped = [nice, "-n", str(int(cfg["capital_lane"].get("agent_nice", 15))), *command]
    lease_acquired = False
    lease_fd: int | None = None
    witness_fd: int | None = None
    witness_path = (
        runtime_dir(cfg) / "writer-leases" / f"{run_id}.lock"
        if writer_lease
        else _spawn_witness_path(cfg, run_id)
    )
    _create_spawn_intent(
        cfg,
        run_id=run_id,
        incident_id=incident_id,
        stage=stage,
        witness_path=witness_path,
    )
    child: subprocess.Popen[Any] | None = None
    try:
        if writer_lease:
            lease_fd = _acquire_writer_lease(
                cfg,
                cwd=cwd,
                run_id=run_id,
                stage=stage,
            )
            lease_acquired = True
            if workspace_branch:
                _ensure_writer_worktree_branch(
                    cfg,
                    cwd=cwd,
                    branch=workspace_branch,
                    allow_owned_dirty=resume_owned_workspace,
                )
        else:
            witness_fd, witness_path = _acquire_spawn_witness(cfg, run_id)
        child = subprocess.Popen(
            wrapped,
            cwd=cwd,
            env=env,
            stdin=prompt_handle,
            stdout=events_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(lease_fd if lease_fd is not None else witness_fd,)
            if (lease_fd is not None or witness_fd is not None) else (),
        )
        _mark_spawn_child(cfg, run_id, child.pid)
        if writer_lease:
            _bind_writer_lease_child(
                cfg,
                cwd=cwd,
                run_id=run_id,
                child_pid=child.pid,
            )
    except Exception:
        if child is not None:
            _terminate_process_group(child.pid)
        _finish_spawn_intent(cfg, run_id, "failed")
        if lease_acquired:
            _release_writer_lease(cfg, cwd=cwd, run_id=run_id)
        elif witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
        raise
    finally:
        prompt_handle.close()
        events_handle.close()
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "incident_id": incident_id,
        "kind": kind,
        "stage": stage,
        "pid": child.pid,
        "started_at": started_at,
        "cwd": str(cwd),
        "output": str(output),
        "events": str(events),
        "command": command,
        "session_id": session_id,
        "workspace_branch": workspace_branch,
        "resume_owned_workspace": resume_owned_workspace,
        "status": "running",
    }
    run_path = runtime_dir(cfg) / "runs" / f"{run_id}.json"
    try:
        atomic_json(run_path, record)
        with memory(cfg) as mem:
            profile = cfg["profiles"][cfg["active"]["profile"]]
            cap = current_capabilities(cfg) or {"reasoning_effort": required_reasoning_effort(cfg)}
            mem.execute(
                "INSERT INTO model_runs(run_id,incident_id,stage,session_id,model,reasoning_effort,started_at,status,events_path) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, incident_id, stage, session_id, profile["model"], cap["reasoning_effort"], record["started_at"], "running", str(events)),
            )
            mem.commit()
        _finish_spawn_intent(cfg, run_id, "persisted")
        if not writer_lease and witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
    except Exception as exc:
        _terminate_process_group(child.pid)
        failed = {
            **record,
            "status": "spawn_persistence_failed",
            "completed_at": iso(),
            "error": f"{type(exc).__name__}:{exc}",
            "lease_finalization_complete": True,
        }
        try:
            atomic_json(run_path, failed)
        except Exception:
            pass
        _finish_spawn_intent(cfg, run_id, "failed")
        if writer_lease:
            _release_writer_lease(cfg, cwd=cwd, run_id=run_id)
        elif witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
        raise
    return record


def _spawn_controller_run_unlocked(
    cfg: Mapping[str, Any],
    *,
    incident_id: str,
    kind: str,
    stage: str,
    command: list[str],
    cwd: Path,
    output: Path,
    events: Path,
) -> dict[str, Any]:
    started_at = iso()
    run_id = digest(incident_id, stage, started_at, os.getpid(), time.monotonic_ns())
    writer_lease = stage in _WORKTREE_WRITE_STAGES
    events.parent.mkdir(parents=True, exist_ok=True)
    events_handle = events.open("wb")
    lease_acquired = False
    lease_fd: int | None = None
    witness_fd: int | None = None
    witness_path = (
        runtime_dir(cfg) / "writer-leases" / f"{run_id}.lock"
        if writer_lease
        else _spawn_witness_path(cfg, run_id)
    )
    _create_spawn_intent(
        cfg,
        run_id=run_id,
        incident_id=incident_id,
        stage=stage,
        witness_path=witness_path,
    )
    child: subprocess.Popen[Any] | None = None
    try:
        if writer_lease:
            lease_fd = _acquire_writer_lease(
                cfg,
                cwd=cwd,
                run_id=run_id,
                stage=stage,
            )
            lease_acquired = True
        else:
            witness_fd, witness_path = _acquire_spawn_witness(cfg, run_id)
        child = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=events_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(lease_fd if lease_fd is not None else witness_fd,)
            if (lease_fd is not None or witness_fd is not None) else (),
        )
        _mark_spawn_child(cfg, run_id, child.pid)
        if writer_lease:
            _bind_writer_lease_child(
                cfg,
                cwd=cwd,
                run_id=run_id,
                child_pid=child.pid,
            )
    except Exception:
        if child is not None:
            _terminate_process_group(child.pid)
        _finish_spawn_intent(cfg, run_id, "failed")
        if lease_acquired:
            _release_writer_lease(cfg, cwd=cwd, run_id=run_id)
        elif witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
        raise
    finally:
        events_handle.close()
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "incident_id": incident_id,
        "kind": kind,
        "stage": stage,
        "pid": child.pid,
        "started_at": started_at,
        "cwd": str(cwd),
        "output": str(output),
        "events": str(events),
        "command": command,
        "controller": True,
        "status": "running",
    }
    run_path = runtime_dir(cfg) / "runs" / f"{run_id}.json"
    try:
        atomic_json(run_path, record)
        _finish_spawn_intent(cfg, run_id, "persisted")
        if not writer_lease and witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
    except Exception as exc:
        _terminate_process_group(child.pid)
        failed = {
            **record,
            "status": "spawn_persistence_failed",
            "completed_at": iso(),
            "error": f"{type(exc).__name__}:{exc}",
            "lease_finalization_complete": True,
        }
        try:
            atomic_json(run_path, failed)
        except Exception:
            pass
        _finish_spawn_intent(cfg, run_id, "failed")
        if writer_lease:
            _release_writer_lease(cfg, cwd=cwd, run_id=run_id)
        elif witness_fd is not None:
            _release_spawn_witness(cfg, run_id, witness_path)
        raise
    return record


def _acquire_provider_launch_lock(cfg: Mapping[str, Any]) -> int:
    path = runtime_dir(cfg) / "provider-launch.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_provider_launch_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def _provider_launch_guard(cfg: Mapping[str, Any]):
    fd = _acquire_provider_launch_lock(cfg)
    try:
        if _provider_backoff(cfg) is not None:
            raise ProviderBackoffActive("codex provider backoff active")
        yield
    finally:
        _release_provider_launch_lock(fd)


def _spawn_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _provider_launch_guard(args[0] if args else kwargs["cfg"]):
        return _spawn_run_unlocked(*args, **kwargs)


def _spawn_controller_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _provider_launch_guard(args[0] if args else kwargs["cfg"]):
        return _spawn_controller_run_unlocked(*args, **kwargs)


def _running(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for path in (runtime_dir(cfg) / "runs").glob("*.json"):
        row = read_json(path, {})
        if row.get("status") == "running":
            result.append(row)
    return result


def _spawn_witness_path(cfg: Mapping[str, Any], run_id: str) -> Path:
    path = runtime_dir(cfg) / "spawn-witness" / f"{run_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _acquire_spawn_witness(cfg: Mapping[str, Any], run_id: str) -> tuple[int, Path]:
    path = _spawn_witness_path(cfg, run_id)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        os.close(fd)
        raise
    _spawn_witness_fds[run_id] = fd
    return fd, path


def _release_spawn_witness(cfg: Mapping[str, Any], run_id: str, path: Path) -> None:
    fd = _spawn_witness_fds.pop(run_id, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _create_spawn_intent(
    cfg: Mapping[str, Any],
    *,
    run_id: str,
    incident_id: str,
    stage: str,
    witness_path: Path,
) -> None:
    stamp = iso()
    with memory(cfg) as mem:
        mem.execute(
            "INSERT INTO spawn_intents(run_id,incident_id,stage,owner_pid,child_pid,"
            "witness_path,state,created_at,updated_at) VALUES (?,?,?,?,NULL,?,?,?,?)",
            (run_id, incident_id, stage, os.getpid(), str(witness_path), "pre_spawn", stamp, stamp),
        )
        mem.commit()


def _mark_spawn_child(cfg: Mapping[str, Any], run_id: str, child_pid: int) -> None:
    with memory(cfg) as mem:
        updated = mem.execute(
            "UPDATE spawn_intents SET child_pid=?,state='child_started',updated_at=? "
            "WHERE run_id=? AND state='pre_spawn'",
            (child_pid, iso(), run_id),
        )
        if updated.rowcount != 1:
            mem.rollback()
            _terminate_process_group(child_pid)
            raise RuntimeError("spawn intent lost before child witness bind")
        mem.commit()


def _finish_spawn_intent(cfg: Mapping[str, Any], run_id: str, state: str) -> None:
    with memory(cfg) as mem:
        mem.execute(
            "UPDATE spawn_intents SET state=?,updated_at=? WHERE run_id=? "
            "AND state IN ('pre_spawn','child_started')",
            (state, iso(), run_id),
        )
        mem.commit()


def _new_startup_budget(cfg: Mapping[str, Any]) -> dict[str, Any]:
    settings = cfg["loop"]
    configured_batch = max(1, int(settings.get("startup_run_batch_size", 64)))
    key = str(runtime_dir(cfg).resolve())
    return {
        "deadline": time.monotonic() + max(0.01, float(settings.get("startup_maintenance_budget_ms", 250))) / 1000.0,
        "max_run_json_bytes": max(16 * 1024, int(settings.get("startup_max_run_json_bytes", 256 * 1024))),
        "run_batch_size": min(configured_batch, _STARTUP_RUN_BATCH_LIMIT.get(key, configured_batch)),
    }


def _shrink_startup_batch(cfg: Mapping[str, Any]) -> None:
    key = str(runtime_dir(cfg).resolve())
    current = int(_STARTUP_RUN_BATCH_LIMIT.get(key, cfg["loop"].get("startup_run_batch_size", 64)))
    _STARTUP_RUN_BATCH_LIMIT[key] = max(1, current // 2)


def _startup_sql_budget(conn: sqlite3.Connection) -> None:
    budget = _STARTUP_BUDGET
    if budget is None:
        return
    remaining_ms = max(1, int((float(budget["deadline"]) - time.monotonic()) * 1000))
    conn.execute(f"PRAGMA busy_timeout={remaining_ms}")
    conn.set_progress_handler(
        lambda: int(_STARTUP_BUDGET is not budget or time.monotonic() >= float(budget["deadline"])),
        1000,
    )


def _startup_guard() -> None:
    if _STARTUP_BUDGET is not None and time.monotonic() >= float(_STARTUP_BUDGET["deadline"]):
        raise StartupMaintenanceDeferred("startup_maintenance_deferred:time_budget")


@contextmanager
def _sqlite_deadline(conn: sqlite3.Connection, deadline: float):
    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
    conn.execute(f"PRAGMA busy_timeout={remaining_ms}")
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        yield conn
    finally:
        conn.set_progress_handler(None, 0)


def _startup_run_metadata(path: Path) -> dict[str, Any]:
    _startup_guard()
    budget = _STARTUP_BUDGET
    max_bytes = int(budget["max_run_json_bytes"]) if budget is not None else 2**63
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size <= max_bytes:
                raw = handle.read()
                _startup_guard()
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            head = handle.read(max_bytes // 2)
            handle.seek(max(0, size - max_bytes // 2))
            tail = handle.read(max_bytes // 2)
    except (OSError, json.JSONDecodeError):
        return {}
    _startup_guard()
    sample = (head + b"\n" + tail).decode("utf-8", errors="ignore")
    metadata: dict[str, Any] = {}
    for key in ("run_id", "incident_id", "status", "pid", "started_at"):
        match = re.search(
            rf'"{re.escape(key)}"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+)',
            sample,
        )
        if not match:
            continue
        value = match.group(1)
        try:
            metadata[key] = json.loads(value)
        except json.JSONDecodeError:
            continue
    return metadata


def _startup_run_batch(cfg: Mapping[str, Any]) -> tuple[list[Path], int]:
    key = str(runtime_dir(cfg).resolve())
    if key not in _STARTUP_RUN_QUEUE:
        _startup_guard()
        queue: list[Path] = []
        runs_dir = runtime_dir(cfg) / "runs"
        try:
            with os.scandir(runs_dir) as entries:
                for entry in entries:
                    _startup_guard()
                    if entry.name.endswith(".json"):
                        queue.append(Path(entry.path))
        except OSError:
            queue = []
        _STARTUP_RUN_QUEUE[key] = queue
        cursor = 0
        cursor_path = runtime_dir(cfg) / "startup-cursor.json"
        if cursor_path.is_file():
            try:
                checkpoint = _startup_read_json_file(cursor_path)
            except StartupMaintenanceDeferred:
                checkpoint = {}
            if isinstance(checkpoint, Mapping) and int(checkpoint.get("run_count", -1)) == len(queue):
                cursor = max(0, min(len(queue), int(checkpoint.get("cursor", 0))))
        _STARTUP_RUN_CURSOR[key] = cursor
    cursor = _STARTUP_RUN_CURSOR.get(key, 0)
    batch_size = int((_STARTUP_BUDGET or {}).get("run_batch_size", 64))
    end = min(len(_STARTUP_RUN_QUEUE[key]), cursor + batch_size)
    return _STARTUP_RUN_QUEUE[key][cursor:end], end


def _startup_checkpoint(cfg: Mapping[str, Any], cursor: int, remaining: bool) -> None:
    key = str(runtime_dir(cfg).resolve())
    queue = _STARTUP_RUN_QUEUE.get(key, [])
    _startup_guard()
    try:
        atomic_json(
            runtime_dir(cfg) / "startup-cursor.json",
            {
                "kind": "startup_reconcile",
                "cursor": cursor,
                "run_count": len(queue),
                "remaining": remaining,
                "updated_at": iso(),
            },
        )
    except OSError as exc:
        raise StartupMaintenanceDeferred("startup_maintenance_deferred:cursor_io") from exc
    # The pointer replace is the commit boundary.  Do not run another deadline
    # guard here: if the deadline expires during the tiny local replace, the
    # in-memory cursor must advance with the durable pointer rather than report
    # a false failure after publishing it.
    _STARTUP_RUN_CURSOR[key] = cursor
    _STARTUP_RUN_REMAINING[key] = remaining


def _startup_reconcile_remaining(cfg: Mapping[str, Any]) -> bool:
    return _STARTUP_RUN_REMAINING.get(str(runtime_dir(cfg).resolve()), False)


def _record_startup_debt(cfg: Mapping[str, Any], reason: str, *, status: str = "retry_pending") -> None:
    # This receipt is the durable escape hatch after a budget expires; it must
    # still be written so the next cycle can resume instead of losing the debt.
    atomic_json(
        runtime_dir(cfg) / "startup-debt.json",
        {"kind": "startup_maintenance", "status": status, "reason": reason, "updated_at": iso()},
    )


def _startup_debt_pending(cfg: Mapping[str, Any]) -> bool:
    """Fail closed globally until bounded startup maintenance drains.

    SCOPE: all Codex/provider dispatch for this controller runtime.
    DRAIN: daemon startup cycles advance the durable run cursor in batches.
    RESET: one complete reconcile pass writes startup-debt.json as resolved.
    """
    if _STARTUP_BUDGET is not None:
        return True
    payload = read_json(runtime_dir(cfg) / "startup-debt.json", {})
    return isinstance(payload, Mapping) and str(payload.get("status") or "") == "retry_pending"


@contextmanager
def _startup_reconcile_memory(cfg: Mapping[str, Any]):
    """Rollback and close a bounded reconcile transaction before deferring."""
    try:
        with memory(cfg) as mem:
            try:
                yield mem
            except sqlite3.OperationalError as exc:
                try:
                    mem.rollback()
                except sqlite3.Error:
                    pass
                message = str(exc).lower()
                if _STARTUP_BUDGET is not None and (
                    "interrupted" in message or "locked" in message
                ):
                    raise StartupMaintenanceDeferred(
                        "startup_maintenance_deferred:sqlite"
                    ) from exc
                raise
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if _STARTUP_BUDGET is not None and (
            "interrupted" in message or "locked" in message
        ):
            raise StartupMaintenanceDeferred(
                "startup_maintenance_deferred:sqlite"
            ) from exc
        raise


def reconcile_orphan_incidents(cfg: Mapping[str, Any]) -> list[str]:
    """Reclaim only running claims with no live controller/worker witness."""

    runs_by_incident: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    startup_batch: list[Path] | None = None
    startup_batch_end = 0
    if _STARTUP_BUDGET is not None:
        startup_batch, startup_batch_end = _startup_run_batch(cfg)
        run_paths = startup_batch
    else:
        run_paths = (runtime_dir(cfg) / "runs").glob("*.json")
    for path in run_paths:
        _startup_guard()
        row = _startup_run_metadata(path) if _STARTUP_BUDGET is not None else read_json(path, {})
        if row.get("status") == "running" and row.get("incident_id"):
            runs_by_incident.setdefault(str(row["incident_id"]), []).append((path, row))
    reclaimed: list[str] = []
    orphaned_runs: list[tuple[Path, dict[str, Any]]] = []
    with _startup_reconcile_memory(cfg) as mem:
        _startup_guard()
        protected_incidents: set[str] = set()
        if _STARTUP_BUDGET is None:
            intents = mem.execute(
                "SELECT * FROM spawn_intents WHERE state IN ('pre_spawn','child_started')"
            ).fetchall()
        elif runs_by_incident:
            incident_ids = tuple(runs_by_incident)
            marks = ",".join("?" for _ in incident_ids)
            intents = mem.execute(
                "SELECT * FROM spawn_intents WHERE state IN ('pre_spawn','child_started') "
                f"AND incident_id IN ({marks})",
                incident_ids,
            ).fetchall()
        else:
            intents = []
        _startup_guard()
        for intent in intents:
            witness_busy = _writer_lock_held(Path(str(intent["witness_path"])))
            owner_alive = _pid_alive(intent["owner_pid"])
            child_alive = _pid_alive(intent["child_pid"])
            created_at = parse_time(str(intent["created_at"] or ""))
            age = (now() - created_at).total_seconds() if created_at else 0.0
            if witness_busy or child_alive or owner_alive or age < _SPAWN_AMBIGUITY_SECONDS:
                protected_incidents.add(str(intent["incident_id"]))
                continue
            mem.execute(
                "UPDATE spawn_intents SET state='failed',updated_at=? WHERE run_id=? "
                "AND state IN ('pre_spawn','child_started')",
                (iso(), str(intent["run_id"])),
            )
            try:
                Path(str(intent["witness_path"])).unlink(missing_ok=True)
            except OSError:
                pass
        if _STARTUP_BUDGET is None:
            rows = mem.execute(
                "SELECT incident_id,stage FROM incidents WHERE status='running'"
            ).fetchall()
        elif runs_by_incident:
            incident_ids = tuple(runs_by_incident)
            marks = ",".join("?" for _ in incident_ids)
            rows = mem.execute(
                f"SELECT incident_id,stage FROM incidents WHERE status='running' AND incident_id IN ({marks})",
                incident_ids,
            ).fetchall()
        else:
            rows = []
        _startup_guard()
        for row in rows:
            incident_id = str(row["incident_id"])
            if incident_id in protected_incidents:
                continue
            witnesses = runs_by_incident.get(incident_id, [])
            live = any(_pid_alive(run.get("pid")) for _, run in witnesses)
            if live:
                continue
            for path, run in witnesses:
                run["status"] = "orphaned"
                run["completed_at"] = iso()
                run["error"] = "orphaned_running_incident"
                orphaned_runs.append((path, run))
                mem.execute(
                    "UPDATE model_runs SET status='failed',completed_at=? "
                    "WHERE run_id=? AND status='running'",
                    (run["completed_at"], str(run.get("run_id") or "")),
                )
            _transition_if_status(
                mem,
                incident_id,
                str(row["stage"] or "blind"),
                expected_status="running",
                reason="orphaned_running_claim_reclaimed",
                status="retry_pending",
            )
            reclaimed.append(incident_id)
        mem.commit()
    # File witnesses follow the committed DB transition.  A DB timeout must
    # leave the batch's source run records marked running so the next slice can
    # retry the same incident instead of losing its only witness.
    for path, run in orphaned_runs:
        try:
            atomic_json(path, run)
        except OSError:
            pass
    if _STARTUP_BUDGET is not None:
        key = str(runtime_dir(cfg).resolve())
        _startup_checkpoint(
            cfg,
            startup_batch_end,
            startup_batch_end < len(_STARTUP_RUN_QUEUE.get(key, [])),
        )
    return reclaimed


def _poll_process(pid: int) -> int | None:
    try:
        waited, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return 0
        return None
    if waited == 0:
        return None
    return os.waitstatus_to_exitcode(status)


def _terminate_process_group(pid: int, *, grace_seconds: float = 5.0) -> None:
    """Stop a Codex run and every subprocess it owns, then reap when possible."""

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if _poll_process(pid) is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _claim(cfg: Mapping[str, Any], kind: str) -> sqlite3.Row | None:
    try:
        with open_ro(Path(str(cfg["paths"]["trades_db"]))) as trades:
            exposed_positions = {
                str(row[0])
                for row in trades.execute(
                    "SELECT position_id FROM position_current WHERE phase IN "
                    "('pending_entry','active','day0_window','pending_exit') "
                    "AND CAST(COALESCE(chain_shares, shares, 0) AS REAL) > 0"
                ).fetchall()
            }
    except sqlite3.Error:
        return None
    with memory(cfg) as mem:
        rows = mem.execute(
            "SELECT * FROM incidents WHERE kind=? AND status='queued' AND stage='blind'",
            (kind,),
        ).fetchall()
        row = next(
            iter(sorted(
                rows,
                key=lambda candidate: (
                    str(candidate["position_id"]) in exposed_positions,
                    float(candidate["priority"] or 0),
                    float(candidate["avoidable_loss_usd"] or 0),
                    str(candidate["detected_at"] or ""),
                ),
                reverse=True,
            )),
            None,
        )
        if row is None:
            return None
        mem.execute("UPDATE incidents SET status='running',updated_at=? WHERE incident_id=?", (iso(), row["incident_id"]))
        mem.commit()
        return row


def _retry_command(cfg: Mapping[str, Any], prior: Mapping[str, Any]) -> list[str]:
    session_id = str(prior.get("session_id") or "")
    stage = str(prior.get("stage") or "")
    schemas = {
        "diagnosis": ("diagnosis", DIAGNOSIS_SCHEMA),
        "classification": ("classification", CLASSIFICATION_SCHEMA),
        "repair": ("patch", PATCH_SCHEMA),
        "repair_feedback": ("patch", PATCH_SCHEMA),
        "delivery": ("delivery", DELIVERY_SCHEMA),
    }
    # A feedback run follows an independently produced review.  Resuming a
    # feedback session after a schema/identity failure repeats the same
    # contaminated output and can occupy the only repair worktree forever.
    # Retry feedback from a fresh workspace-write session; the controller-owned
    # incident envelope below supplies the exact identity again.
    if stage == "repair_feedback":
        return _codex_exec_base(
            cfg,
            sandbox="workspace-write",
            cwd=Path(str(prior["cwd"])),
            schema=_schema_file(cfg, "patch", PATCH_SCHEMA),
            output=Path(str(prior["output"])),
            persistent=True,
        )
    if session_id and stage in schemas:
        schema_name, schema = schemas[stage]
        return _codex_resume_base(
            cfg,
            session_id=session_id,
            schema=_schema_file(cfg, schema_name, schema),
            output=Path(str(prior["output"])),
        )
    raise RuntimeError("cannot safely retry a total-loss run without a typed stage")


def _bounded_retry_events(
    cfg: Mapping[str, Any],
    *,
    incident_id: str,
    stage: str,
) -> Path:
    """Use one bounded retry stem; never append to an already chained stem."""

    token = digest("retry", incident_id, stage)
    return runtime_dir(cfg) / "incidents" / incident_id / f"codex-{stage}-{token}.jsonl"


def _mark_spawn_failure(
    cfg: Mapping[str, Any],
    incident_id: str,
    *,
    stage: str,
    reason: str,
    run_id: str | None = None,
) -> None:
    """Return a claimed incident to retry_pending with typed durable evidence."""

    with memory(cfg) as mem:
        row = mem.execute(
            "SELECT status FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        if row is not None and str(row[0]) == "running":
            _transition_if_status(
                mem,
                incident_id,
                stage,
                expected_status="running",
                reason=f"spawn_persistence_failed:{reason[:180]}",
                run_id=run_id,
                status="retry_pending",
            )
            mem.commit()


_WORKTREE_WRITE_STAGES = frozenset(
    {"repair", "repair_feedback", "delivery", "production"}
)


class WriterLeaseBusy(RuntimeError):
    """A live child already owns the canonical workspace writer lease."""


class ProviderBackoffActive(RuntimeError):
    """A durable provider cooldown won the launch gate."""


def _pid_alive(pid: object) -> bool:
    try:
        numeric_pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False
    try:
        os.kill(numeric_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _writer_cwd(cwd: Path) -> str:
    return str(cwd.resolve())


def _writer_lease_finalized(cfg: Mapping[str, Any], run_id: str) -> bool:
    record = read_json(runtime_dir(cfg) / "runs" / f"{run_id}.json", None)
    return bool(
        isinstance(record, Mapping)
        and record.get("lease_finalization_complete") is True
    )


def _writer_lock_held(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _acquire_writer_lease(
    cfg: Mapping[str, Any],
    *,
    cwd: Path,
    run_id: str,
    stage: str,
) -> int:
    canonical_cwd = _writer_cwd(cwd)
    lock_dir = runtime_dir(cfg) / "writer-leases"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{run_id}.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with memory(cfg) as mem:
            mem.execute("BEGIN IMMEDIATE")
            current = mem.execute(
                "SELECT * FROM workspace_writer_leases WHERE cwd=?",
                (canonical_cwd,),
            ).fetchone()
            if current is not None:
                owner_alive = _pid_alive(current["owner_pid"])
                child_alive = _pid_alive(current["child_pid"])
                kernel_lock_held = bool(
                    not owner_alive
                    and _writer_lock_held(Path(str(current["lock_path"])))
                )
                finalized = _writer_lease_finalized(
                    cfg,
                    str(current["run_id"]),
                )
                if (
                    kernel_lock_held
                    or child_alive
                    or (owner_alive and not finalized)
                ):
                    mem.rollback()
                    raise WriterLeaseBusy(
                        "workspace writer busy: "
                        f"cwd={canonical_cwd} stage={current['stage']} "
                        f"run_id={current['run_id']}"
                    )
                mem.execute(
                    "DELETE FROM workspace_writer_leases WHERE cwd=? AND run_id=?",
                    (canonical_cwd, str(current["run_id"])),
                )
                try:
                    Path(str(current["lock_path"])).unlink(missing_ok=True)
                except OSError:
                    pass
            mem.execute(
                "INSERT INTO workspace_writer_leases"
                "(cwd,run_id,stage,owner_pid,child_pid,lock_path,acquired_at) "
                "VALUES (?,?,?,?,NULL,?,?)",
                (
                    canonical_cwd,
                    run_id,
                    stage,
                    os.getpid(),
                    str(lock_path),
                    iso(),
                ),
            )
            mem.commit()
        _writer_lease_lock_fds[run_id] = lock_fd
        return lock_fd
    except Exception:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _bind_writer_lease_child(
    cfg: Mapping[str, Any],
    *,
    cwd: Path,
    run_id: str,
    child_pid: int,
) -> None:
    with memory(cfg) as mem:
        updated = mem.execute(
            "UPDATE workspace_writer_leases SET child_pid=? "
            "WHERE cwd=? AND run_id=? AND owner_pid=?",
            (child_pid, _writer_cwd(cwd), run_id, os.getpid()),
        )
        if updated.rowcount != 1:
            mem.rollback()
            _terminate_process_group(child_pid)
            raise RuntimeError("workspace writer lease lost before child bind")
        mem.commit()


def _release_writer_lease(
    cfg: Mapping[str, Any],
    *,
    cwd: Path,
    run_id: str,
) -> None:
    last_error: sqlite3.Error | None = None
    for attempt in range(3):
        try:
            with memory(cfg) as mem:
                mem.execute(
                    "DELETE FROM workspace_writer_leases WHERE cwd=? AND run_id=?",
                    (_writer_cwd(cwd), run_id),
                )
                mem.commit()
            fd = _writer_lease_lock_fds.pop(run_id, None)
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            lock_path = runtime_dir(cfg) / "writer-leases" / f"{run_id}.lock"
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        except sqlite3.Error as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(
        f"workspace writer lease release failed: cwd={_writer_cwd(cwd)} "
        f"run_id={run_id} error={last_error}"
    ) from last_error


def _worktree_writer_running(
    running: list[dict[str, Any]],
    *,
    stage: str,
    cwd: Path,
) -> bool:
    if stage not in _WORKTREE_WRITE_STAGES:
        return False
    target = cwd.resolve()
    return any(
        str(row.get("stage") or "") in _WORKTREE_WRITE_STAGES
        and Path(str(row.get("cwd") or ROOT)).resolve() == target
        for row in running
    )


def _retry_pending(cfg: Mapping[str, Any], running: list[dict[str, Any]]) -> list[str]:
    active_incidents = {str(row["incident_id"]) for row in running}
    by_kind = {
        kind: sum(1 for row in running if str(row.get("kind") or "") == kind)
        for kind in ("hard", "precursor")
    }
    with memory(cfg) as mem:
        incidents = mem.execute(
            "SELECT incident_id,kind,updated_at FROM incidents WHERE status='retry_pending' "
            "ORDER BY priority DESC,updated_at"
        ).fetchall()
    launched: list[str] = []
    retry_delay = float(cfg["loop"].get("stage_retry_seconds", 60))
    for incident in incidents:
        incident_id = str(incident["incident_id"])
        kind = str(incident["kind"])
        if incident_id in active_incidents:
            continue
        if by_kind.get(kind, 0) >= int(cfg["loop"].get(f"{kind}_slots", 1)):
            continue
        candidates = sorted(
            (runtime_dir(cfg) / "runs").glob("*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        prior = None
        for path in candidates:
            candidate = read_json(path, {})
            if candidate.get("incident_id") == incident_id:
                prior = candidate
                break
        if not isinstance(prior, dict) or not isinstance(prior.get("command"), list):
            continue
        prior_stage = str(prior.get("stage") or "")
        prior_cwd = Path(str(prior.get("cwd") or ROOT))
        if _worktree_writer_running(
            running,
            stage=prior_stage,
            cwd=prior_cwd,
        ):
            continue
        completed_at = parse_time(str(prior.get("completed_at") or prior.get("started_at") or ""))
        incident_updated = parse_time(str(incident["updated_at"] or ""))
        if incident_updated is not None and (completed_at is None or incident_updated > completed_at):
            completed_at = incident_updated
        if completed_at is not None and (now() - completed_at).total_seconds() < retry_delay:
            continue
        retry_events = _bounded_retry_events(
            cfg,
            incident_id=incident_id,
            stage=prior_stage,
        )
        if prior.get("controller"):
            try:
                retried = _spawn_controller_run(
                    cfg,
                    incident_id=incident_id,
                    kind=str(incident["kind"]),
                    stage=str(prior["stage"]),
                    command=[str(value) for value in prior["command"]],
                    cwd=Path(str(prior["cwd"])),
                    output=Path(str(prior["output"])),
                    events=retry_events,
                )
            except WriterLeaseBusy:
                continue
            except ProviderBackoffActive:
                return launched
            except (OSError, RuntimeError) as exc:
                _mark_spawn_failure(cfg, incident_id, stage=prior_stage, reason=f"{type(exc).__name__}:{exc}")
                continue
            with memory(cfg) as mem:
                transition(mem, incident_id, str(prior["stage"]), reason="retry_controller_stage", run_id=str(retried["run_id"]))
                mem.commit()
            launched.append(incident_id)
            by_kind[kind] = by_kind.get(kind, 0) + 1
            continue
        prompt_path = Path(str(prior["events"])).with_suffix(".prompt.md")
        if not prompt_path.is_file():
            continue
        prompt = (
            f"CONTROLLER INCIDENT ENVELOPE: incident_id={incident_id}. "
            "Return this exact full incident_id unchanged.\n\n"
            + prompt_path.read_text()
        )
        try:
            retried = _spawn_run(
                cfg,
                incident_id=incident_id,
                kind=str(incident["kind"]),
                stage=str(prior["stage"]),
                command=_retry_command(cfg, prior),
                cwd=Path(str(prior["cwd"])),
                prompt=prompt,
                output=Path(str(prior["output"])),
                events=retry_events,
                session_id=(
                    None
                    if prior_stage == "repair_feedback"
                    else prior.get("session_id")
                ),
                workspace_branch=str(
                    prior.get("workspace_branch")
                    or _repair_branch(cfg, incident_id)
                ) if prior_stage in _WORKTREE_WRITE_STAGES - {"production"} else None,
                resume_owned_workspace=(
                    prior_stage in _WORKTREE_WRITE_STAGES - {"production"}
                ),
            )
        except WriterLeaseBusy:
            continue
        except ProviderBackoffActive:
            return launched
        except (OSError, RuntimeError) as exc:
            _mark_spawn_failure(cfg, incident_id, stage=prior_stage, reason=f"{type(exc).__name__}:{exc}")
            continue
        if prior.get("repair_session_id"):
            retried["repair_session_id"] = prior["repair_session_id"]
            atomic_json(
                runtime_dir(cfg) / "runs" / f"{retried['run_id']}.json",
                retried,
            )
        with memory(cfg) as mem:
            transition(
                mem,
                incident_id,
                str(prior["stage"]),
                reason="retry_failed_stage",
                run_id=str(retried["run_id"]),
            )
            mem.commit()
        launched.append(incident_id)
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return launched


def dispatch(cfg: Mapping[str, Any]) -> list[str]:
    if _startup_debt_pending(cfg):
        return []
    reconcile_orphan_incidents(cfg)
    if _provider_backoff(cfg) is not None:
        # Backoff gates only provider/Codex work; bounded local evidence maintenance
        # still runs and carries its remaining debt into the next cycle.
        _capture_hard_evidence(cfg, scan_all=True, budget=_new_evidence_budget(cfg))
        return []
    if current_capabilities(cfg) is None:
        ensure_capability_probe(cfg)
        return []
    running = _running(cfg)
    if _active_loaded_sha(cfg):
        with memory(cfg) as mem:
            blocked = mem.execute(
                "SELECT incident_id FROM incidents WHERE stage='evidence' AND status='blocked'"
            ).fetchall()
            for row in blocked:
                transition(mem, str(row[0]), "blind", reason="loaded_sha_recovered", status="queued")
            mem.commit()
    launched = _retry_pending(cfg, running)
    if launched:
        running = _running(cfg)
    _recover_classification_debt(cfg, running)
    by_kind = {
        kind: sum(
            1
            for row in running
            if row.get("kind") == kind
        )
        for kind in ("hard", "precursor")
    }
    for kind, setting in (("hard", "hard_slots"), ("precursor", "precursor_slots")):
        slots = int(cfg["loop"].get(setting, 1))
        while by_kind[kind] < slots:
            incident = _claim(cfg, kind)
            if incident is None:
                break
            incident_id = str(incident["incident_id"])
            try:
                build_budget = _new_evidence_budget(cfg)
                evidence = _run_bounded_evidence_build(cfg, incident_id, build_budget)
                pair = _evidence_pair_paths(cfg, incident_id)
                expected_evidence = pair[0] if pair is not None else None
                if expected_evidence is None or Path(evidence) != expected_evidence:
                    _record_evidence_debt(cfg, incident_id, "evidence_snapshot_path_mismatch")
                    continue
                if not _capture_pair_valid(cfg, incident_id, build_budget):
                    _record_evidence_debt(cfg, incident_id, "evidence_snapshot_pair_invalid")
                    continue
                incident_dir = runtime_dir(cfg) / "incidents" / incident_id
                if not read_json(pair[1], {}).get("loaded_sha"):
                    with memory(cfg) as mem:
                        transition(
                            mem,
                            incident_id,
                            "evidence",
                            reason="live_loaded_sha_missing",
                            status="blocked",
                        )
                        mem.commit()
                    continue
                output = incident_dir / "diagnosis.json"
                events = incident_dir / "codex-diagnosis.jsonl"
                schema = _schema_file(cfg, "diagnosis", DIAGNOSIS_SCHEMA)
                prompt = Path(str(cfg["paths"]["prompt"])).read_text() + "\n\nBLIND PHASE: historical root memory is intentionally unavailable.\n" + f"incident_id={incident_id}\nevidence_db={evidence}\nmanifest={pair[1]}\n"
                command = _codex_exec_base(cfg, sandbox="read-only", cwd=ROOT, schema=schema, output=output, persistent=True)
                _spawn_run(cfg, incident_id=incident_id, kind=kind, stage="diagnosis", command=command, cwd=ROOT, prompt=prompt, output=output, events=events)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                if isinstance(exc, ProviderBackoffActive):
                    _mark_spawn_failure(
                        cfg, incident_id, stage="blind", reason="provider_backoff_active"
                    )
                    return launched
                _mark_spawn_failure(
                    cfg,
                    incident_id,
                    stage="blind",
                    reason=f"{type(exc).__name__}:{exc}",
                )
                continue
            launched.append(incident_id)
            by_kind[kind] += 1
    # Blind hard debt has consumed the unified hard slot before repair_waiting
    # is considered.  This ordering is the liveness guarantee for quote-less
    # settlement incidents.
    running = _running(cfg)
    repair = _dispatch_repair_waiting(cfg, running)
    if repair:
        launched.append(repair)
    return launched


def _useful_roots(cfg: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> list[dict[str, Any]]:
    symbols = set(str(value) for value in diagnosis.get("changed_symbols", []))
    seam = str(diagnosis.get("causal_seam") or "")
    with memory(cfg) as mem:
        rows = [dict(row) for row in mem.execute("SELECT * FROM roots ORDER BY utility DESC,updated_at DESC LIMIT 100").fetchall()]
    def score(row: Mapping[str, Any]) -> tuple[float, float]:
        try:
            parsed = json.loads(str(row.get("affected_symbols_json") or "[]"))
        except json.JSONDecodeError:
            parsed = []
        affected = set(str(value) for value in parsed) if isinstance(parsed, list) else set()
        overlap = len(symbols & affected)
        seam_match = 1 if seam and seam == row.get("causal_seam") else 0
        return (seam_match * 100 + overlap * 10 + float(row.get("utility") or 0), float(row.get("measured_avoided_loss_usd") or 0))
    return sorted(rows, key=score, reverse=True)[:12]


def _repair_branch(cfg: Mapping[str, Any], incident_id: str) -> str:
    return f"{cfg['delivery']['branch_prefix']}/{incident_id[:12]}"


def _worktree(cfg: Mapping[str, Any], incident_id: str) -> Path:
    configured = os.environ.get("ZEUS_TOTAL_LOSS_REPAIR_WORKTREE", "").strip()
    if not configured:
        raise RuntimeError("managed repair worktree is not provisioned")
    path = Path(configured).expanduser().resolve()
    listing = _run_capture(["git", "worktree", "list", "--porcelain"], cwd=ROOT)
    registered = any(line == f"worktree {path}" for line in listing.stdout.splitlines())
    if listing.returncode != 0 or not registered or path == ROOT:
        raise RuntimeError("configured repair worktree is not a registered non-live worktree")
    return path


def _ensure_writer_worktree_branch(
    cfg: Mapping[str, Any],
    *,
    cwd: Path,
    branch: str,
    allow_owned_dirty: bool = False,
) -> None:
    """Provision the incident branch while holding the cwd writer lease."""

    path = cwd.resolve()
    current = _run_capture(["git", "branch", "--show-current"], cwd=path)
    if current.returncode != 0:
        raise RuntimeError("configured repair worktree branch is unreadable")
    current_branch = current.stdout.strip()
    dirty = _run_capture(["git", "status", "--porcelain", "--untracked-files=all"], cwd=path)
    if dirty.returncode != 0:
        raise RuntimeError("configured repair worktree status is unreadable")
    if dirty.stdout.strip() and not (
        allow_owned_dirty and current_branch == branch
    ):
        raise RuntimeError("configured repair worktree is dirty")
    if current_branch != branch:
        exists = _run_capture(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=ROOT)
        switch = ["git", "switch", branch] if exists.returncode == 0 else [
            "git", "switch", "-c", branch, str(cfg["delivery"]["base_branch"])
        ]
        changed = _run_capture(switch, cwd=path)
        if changed.returncode != 0:
            raise RuntimeError(f"managed repair branch provisioning failed: {changed.stderr.strip()}")


def _live_checkout(base_branch: str) -> Path:
    listing = _run_capture(["git", "worktree", "list", "--porcelain"], cwd=ROOT)
    if listing.returncode != 0:
        raise RuntimeError(f"cannot resolve live checkout: {listing.stderr.strip()}")
    worktree: Path | None = None
    for line in [*listing.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree ")).resolve()
        elif line == f"branch refs/heads/{base_branch}" and worktree is not None:
            return worktree
        elif not line:
            worktree = None
    raise RuntimeError(f"no checkout owns refs/heads/{base_branch}")


def _finish_run_inner(cfg: Mapping[str, Any], run: dict[str, Any], returncode: int) -> None:
    path = runtime_dir(cfg) / "runs" / f"{run['run_id']}.json"
    events = Path(str(run["events"]))
    session, usage = _parse_session(events)
    terminal_failure = _parse_terminal_failure(events, cfg)
    effective_failed = returncode != 0 or terminal_failure is not None
    run["status"] = "failed" if effective_failed else "completed"
    run["returncode"] = returncode
    run["completed_at"] = iso()
    run["session_id"] = session or run.get("session_id")
    if terminal_failure is not None:
        run["terminal_failure"] = terminal_failure
    provider_failure = bool(
        terminal_failure is not None and terminal_failure.get("provider_wide")
    )
    atomic_json(path, run)
    with memory(cfg) as mem:
        if not run.get("controller"):
            mem.execute(
                "UPDATE model_runs SET session_id=?,completed_at=?,status=?,usage_json=? WHERE run_id=?",
                (run.get("session_id"), run["completed_at"], run["status"], json.dumps(usage), run["run_id"]),
            )
        if effective_failed:
            reason = (
                f"codex_terminal_failure:{terminal_failure['kind']}:{terminal_failure['reason']}"
                if terminal_failure is not None
                else f"run_failed:{returncode}"
            )
            transition(
                mem,
                str(run["incident_id"]),
                str(run["stage"]),
                reason=reason[:1200],
                run_id=str(run["run_id"]),
                status="retry_pending",
            )
            mem.commit()
    if provider_failure:
        launch_lock = _acquire_provider_launch_lock(cfg)
        try:
            with memory(cfg) as backoff_mem:
                run["provider_backoff"] = _set_provider_backoff(
                    cfg, backoff_mem, terminal_failure or {}
                )
                backoff_mem.commit()
        finally:
            _release_provider_launch_lock(launch_lock)
    if effective_failed:
        atomic_json(path, run)
        return
    result = read_json(Path(str(run["output"])), None)
    if not isinstance(result, dict) or result.get("incident_id") not in {None, run["incident_id"]}:
        with memory(cfg) as mem:
            transition(
                mem,
                str(run["incident_id"]),
                str(run["stage"]),
                reason="invalid_structured_result",
                run_id=str(run["run_id"]),
                status="retry_pending",
            )
            mem.commit()
        return
    if run["stage"] == "diagnosis":
        clocks = {
            str(item.get("clock"))
            for item in result.get("timeline", [])
            if isinstance(item, Mapping)
        }
        required_clocks = {"source", "probability", "monitor", "decision", "command", "fill", "floor"}
        if clocks != required_clocks or not result.get("causal_seam") or not result.get("evidence_refs"):
            with memory(cfg) as mem:
                transition(
                    mem,
                    str(run["incident_id"]),
                    "diagnosis",
                    reason="diagnosis_missing_required_causal_evidence",
                    run_id=str(run["run_id"]),
                    status="retry_pending",
                )
                mem.commit()
            return
    if run["stage"] == "diagnosis":
        _after_diagnosis(cfg, run, result)
    elif run["stage"] == "classification":
        _after_classification(cfg, run, result)
    elif run["stage"] in {"repair", "repair_feedback"}:
        _after_repair(cfg, run, result)
    elif run["stage"] == "review":
        _after_review(cfg, run, result)
    elif run["stage"] == "delivery":
        _after_delivery(cfg, run, result)
    elif run["stage"] == "production":
        _after_production(cfg, run, result)


def _finish_run(cfg: Mapping[str, Any], run: dict[str, Any], returncode: int) -> None:
    try:
        _finish_run_inner(cfg, run, returncode)
    finally:
        if str(run.get("stage") or "") in _WORKTREE_WRITE_STAGES:
            run_path = runtime_dir(cfg) / "runs" / f"{run['run_id']}.json"
            finalized = read_json(run_path, dict(run))
            if not isinstance(finalized, dict):
                finalized = dict(run)
            finalized["lease_finalization_complete"] = True
            try:
                atomic_json(run_path, finalized)
            finally:
                _release_writer_lease(
                    cfg,
                    cwd=Path(str(run.get("cwd") or ROOT)),
                    run_id=str(run["run_id"]),
                )


def _after_diagnosis(cfg: Mapping[str, Any], run: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    priors = _useful_roots(cfg, diagnosis)
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    output = incident_dir / "classification.json"
    events = incident_dir / "codex-classification.jsonl"
    schema = _schema_file(cfg, "classification", CLASSIFICATION_SCHEMA)
    prompt = "Blind diagnosis is complete. Compare it now against only the dedicated episodic roots below. Do not revise event-time facts merely to match history.\n\nDIAGNOSIS:\n" + json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n\nDEDICATED ROOTS:\n" + json.dumps(priors, ensure_ascii=False, indent=2)
    command = _codex_resume_base(cfg, session_id=str(run["session_id"]), schema=schema, output=output)
    spawned = _spawn_run(cfg, incident_id=incident_id, kind=str(run["kind"]), stage="classification", command=command, cwd=ROOT, prompt=prompt, output=output, events=events, session_id=str(run["session_id"]))
    with memory(cfg) as mem:
        transition(mem, incident_id, "classification", reason="blind_diagnosis_complete", run_id=str(spawned["run_id"]))
        mem.commit()


def _after_classification(cfg: Mapping[str, Any], run: Mapping[str, Any], classification: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    diagnosis = read_json(incident_dir / "diagnosis.json", {})
    root_id = str(classification["root_id"])
    with memory(cfg) as mem:
        linked = mem.execute(
            "SELECT 1 FROM incident_root_links WHERE incident_id=? AND root_id=?",
            (incident_id, root_id),
        ).fetchone()
        existing = mem.execute("SELECT root_id FROM roots WHERE root_id=?", (root_id,)).fetchone()
        if existing is None:
            mem.execute(
                "INSERT INTO roots(root_id,causal_seam,mechanism_fingerprint,earliest_divergence,affected_symbols_json,reproduction,updated_at) VALUES (?,?,?,?,?,?,?)",
                (root_id, diagnosis.get("causal_seam", ""), classification.get("mechanism_fingerprint", ""), diagnosis.get("earliest_preventable_time"), json.dumps(diagnosis.get("changed_symbols", [])), json.dumps(diagnosis.get("evidence_refs", [])), iso()),
            )
        elif linked is None:
            mem.execute("UPDATE roots SET recurrence_count=recurrence_count+1,updated_at=? WHERE root_id=?", (iso(), root_id))
        mem.execute(
            "INSERT OR REPLACE INTO incident_root_links VALUES (?,?,?,?,?)",
            (incident_id, root_id, classification["relation"], 1.0, iso()),
        )
        counterfactual = diagnosis.get("capital_counterfactual", {})
        mem.execute(
            "UPDATE incidents SET root_relation=?,root_id=?,earliest_preventable_time=?,avoidable_loss_usd=?,updated_at=? WHERE incident_id=?",
            (classification["relation"], root_id, diagnosis.get("earliest_preventable_time"), counterfactual.get("avoidable_loss_usd"), iso(), incident_id),
        )
        mem.commit()
    counterfactual = diagnosis.get("capital_counterfactual", {})
    avoidable = float(counterfactual.get("avoidable_loss_usd") or 0)
    if not diagnosis.get("earliest_preventable_time") or avoidable <= 0:
        with memory(cfg) as mem:
            transition(
                mem, incident_id, "observing",
                reason="no_engine_preventable_capital_loss", status="observing",
            )
            mem.commit()
        return
    with memory(cfg) as mem:
        transition(mem, incident_id, "repair_waiting", reason="root_classified", status="queued")
        mem.commit()


def _start_repair(cfg: Mapping[str, Any], incident_id: str, kind: str) -> str:
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    diagnosis = read_json(incident_dir / "diagnosis.json", {})
    classification = read_json(incident_dir / "classification.json", {})
    worktree = _worktree(cfg, incident_id)
    workspace_branch = _repair_branch(cfg, incident_id)
    output = incident_dir / "patch.json"
    events = incident_dir / "codex-repair.jsonl"
    schema = _schema_file(cfg, "patch", PATCH_SCHEMA)
    prompt = Path(str(cfg["paths"]["prompt"])).read_text() + "\n\nIMPLEMENTATION PHASE. Implement and test the structural repair in this incident worktree. Do not commit, push, open a PR, merge, or deploy; the controller owns Git metadata and will commit the proven diff before fresh review.\n\nDIAGNOSIS:\n" + json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n\nCLASSIFICATION:\n" + json.dumps(classification, ensure_ascii=False, indent=2) + f"\n\nincident evidence={incident_dir / 'evidence.db'}\n"
    command = _codex_exec_base(cfg, sandbox="workspace-write", cwd=worktree, schema=schema, output=output, persistent=True)
    spawned = _spawn_run(
        cfg,
        incident_id=incident_id,
        kind=kind,
        stage="repair",
        command=command,
        cwd=worktree,
        prompt=prompt,
        output=output,
        events=events,
        workspace_branch=workspace_branch,
    )
    with memory(cfg) as mem:
        transition(mem, incident_id, "repair", reason="root_classified", run_id=str(spawned["run_id"]))
        mem.commit()
    return incident_id


def _recover_classification_debt(cfg: Mapping[str, Any], running: list[dict[str, Any]]) -> None:
    active = {str(row.get("incident_id")) for row in running}
    with memory(cfg) as mem:
        rows = mem.execute(
            "SELECT incident_id,kind FROM incidents WHERE stage='classification' AND status='running'"
        ).fetchall()
    for row in rows:
        incident_id = str(row["incident_id"])
        if incident_id in active:
            continue
        incident_dir = runtime_dir(cfg) / "incidents" / incident_id
        classification = read_json(incident_dir / "classification.json", None)
        diagnosis = read_json(incident_dir / "diagnosis.json", None)
        if not isinstance(classification, Mapping) or not isinstance(diagnosis, Mapping):
            continue
        _after_classification(
            cfg,
            {"incident_id": incident_id, "kind": str(row["kind"]), "run_id": "recovery"},
            classification,
        )


def _dispatch_repair_waiting(cfg: Mapping[str, Any], running: list[dict[str, Any]]) -> str | None:
    if any(str(row.get("stage")) in {"repair", "repair_feedback", "review", "delivery", "production"} for row in running):
        return None
    with memory(cfg) as mem:
        row = mem.execute(
            "SELECT incident_id,kind FROM incidents WHERE stage='repair_waiting' AND status='queued' "
            "ORDER BY priority DESC,detected_at LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    kind = str(row["kind"])
    if sum(1 for candidate in running if str(candidate.get("kind") or "") == kind) >= int(
        cfg["loop"].get(f"{kind}_slots", 1)
    ):
        return None
    try:
        return _start_repair(cfg, str(row["incident_id"]), kind)
    except RuntimeError:
        return None


def _after_repair(cfg: Mapping[str, Any], run: Mapping[str, Any], patch: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    if patch.get("status") != "patch_ready":
        with memory(cfg) as mem:
            mem.execute("UPDATE incidents SET status=?,updated_at=? WHERE incident_id=?", ("blocked" if patch.get("status") == "blocked" else "observing", iso(), incident_id))
            mem.commit()
        return
    if not isinstance(patch.get("replay"), Mapping) or patch["replay"].get("passed") is not True:
        with memory(cfg) as mem:
            transition(
                mem,
                incident_id,
                str(run["stage"]),
                reason="exact_replay_not_green",
                run_id=str(run["run_id"]),
                status="retry_pending",
            )
            mem.commit()
        return
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    worktree = Path(str(run["cwd"]))
    commit = _ensure_repair_commit(worktree, incident_id, patch)
    if commit is None:
        with memory(cfg) as mem:
            transition(
                mem, incident_id, str(run["stage"]),
                reason="controller_commit_failed", run_id=str(run["run_id"]),
                status="retry_pending",
            )
            mem.commit()
        return
    output = incident_dir / "review.json"
    events = incident_dir / f"codex-review-{int(time.time())}.jsonl"
    schema = _schema_file(cfg, "review", REVIEW_SCHEMA)
    command = _codex_exec_base(
        cfg, sandbox="read-only", cwd=worktree, schema=schema,
        output=output, persistent=False,
    )
    prompt = (
        "Fresh independent code review. Review git diff "
        f"{cfg['delivery']['base_branch']}...HEAD at commit {commit} against repository law "
        "and the incident evidence. Lead with live-money findings. Return blocking=true "
        "for any unresolved correctness, causality, replay, or delivery defect."
    )
    review_run = _spawn_run(
        cfg,
        incident_id=incident_id,
        kind=str(run["kind"]),
        stage="review",
        command=command,
        cwd=worktree,
        prompt=prompt,
        output=output,
        events=events,
        workspace_branch=str(
            run.get("workspace_branch")
            or _repair_branch(cfg, incident_id)
        ),
    )
    review_run["repair_session_id"] = run.get("session_id")
    atomic_json(runtime_dir(cfg) / "runs" / f"{review_run['run_id']}.json", review_run)
    with memory(cfg) as mem:
        transition(mem, incident_id, "review", reason="repair_ready", run_id=str(review_run["run_id"]))
        mem.commit()


def _ensure_repair_commit(
    worktree: Path, incident_id: str, patch: Mapping[str, Any]
) -> str | None:
    expected = str(patch.get("commit_sha") or "").strip()
    head = _run_capture(["git", "rev-parse", "HEAD"], cwd=worktree)
    if head.returncode != 0:
        return None
    if expected and head.stdout.strip().startswith(expected):
        return head.stdout.strip()
    dirty = _run_capture(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree)
    checked = _run_capture(["git", "diff", "--check"], cwd=worktree)
    if dirty.returncode != 0 or not dirty.stdout.strip() or checked.returncode != 0:
        return None
    staged = _run_capture(["git", "add", "-A"], cwd=worktree)
    if staged.returncode != 0:
        return None
    committed = _run_capture(
        ["git", "commit", "-m", f"fix(total-loss): repair {incident_id[:12]}"],
        cwd=worktree,
        timeout=120,
    )
    if committed.returncode != 0:
        return None
    return _run_capture(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip() or None


def _after_review(cfg: Mapping[str, Any], run: Mapping[str, Any], review: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    worktree = Path(str(run["cwd"]))
    if review.get("blocking"):
        if _worktree_writer_running(
            _running(cfg),
            stage="repair_feedback",
            cwd=worktree,
        ):
            # The review is read-only, so it may finish while another incident
            # owns the shared repair worktree.  Re-review later instead of
            # starting a second workspace writer against that checkout.
            with memory(cfg) as mem:
                transition(
                    mem,
                    incident_id,
                    "review",
                    reason="feedback_waiting_for_worktree_writer",
                    run_id=str(run.get("run_id") or ""),
                    status="retry_pending",
                )
                mem.commit()
            return
        output = incident_dir / "patch.json"
        events = incident_dir / f"codex-repair-feedback-{int(time.time())}.jsonl"
        schema = _schema_file(cfg, "patch", PATCH_SCHEMA)
        prompt = (
            f"CONTROLLER INCIDENT ENVELOPE: incident_id={incident_id}. "
            "Return this exact full incident_id unchanged.\n\n"
            "Fresh independent review found blocking issues. Fix every finding "
            "and rerun affected tests. Do not commit; the controller owns Git "
            "metadata and will commit the proven follow-up diff. Return "
            "patch_ready only when resolved.\n\n"
            + json.dumps(review, ensure_ascii=False, indent=2)
        )
        command = _codex_exec_base(
            cfg, sandbox="workspace-write", cwd=worktree, schema=schema,
            output=output, persistent=True,
        )
        try:
            spawned = _spawn_run(
                cfg, incident_id=incident_id, kind=str(run["kind"]),
                stage="repair_feedback", command=command, cwd=worktree,
                prompt=prompt, output=output, events=events,
                workspace_branch=str(
                    run.get("workspace_branch")
                    or _repair_branch(cfg, incident_id)
                ),
            )
        except WriterLeaseBusy:
            with memory(cfg) as mem:
                transition(
                    mem,
                    incident_id,
                    "review",
                    reason="feedback_writer_lease_busy",
                    run_id=str(run.get("run_id") or ""),
                    status="retry_pending",
                )
                mem.commit()
            return
        with memory(cfg) as mem:
            transition(mem, incident_id, "repair_feedback", reason="fresh_review_blocking", run_id=str(spawned["run_id"]))
            mem.commit()
        return
    if not bool(cfg.get("delivery", {}).get("enabled", False)):
        with memory(cfg) as mem:
            mem.execute(
                "UPDATE incidents SET status='blocked',updated_at=? WHERE incident_id=?",
                (iso(), incident_id),
            )
            mem.commit()
        return
    cap = current_capabilities(cfg) or {}
    if cap.get("delivery_network_ok") is not True:
        with memory(cfg) as mem:
            mem.execute(
                "UPDATE incidents SET status='blocked',updated_at=? WHERE incident_id=?",
                (iso(), incident_id),
            )
            mem.commit()
        atomic_json(
            incident_dir / "delivery-blocker.json",
            {"at": iso(), "reason": "Codex workspace-write network capability is not proven"},
        )
        return
    live_checkout = _live_checkout(str(cfg["delivery"]["base_branch"]))
    output = incident_dir / "delivery.json"
    events = incident_dir / "codex-delivery.jsonl"
    schema = _schema_file(cfg, "delivery", DELIVERY_SCHEMA)
    prompt = (
        Path(str(cfg["paths"]["prompt"])).read_text()
        + "\n\nDELIVERY PHASE. Fresh review is non-blocking. You have workspace-write, "
        "network, and read access to current live truth. Push the committed incident branch; open a PR "
        "against live; monitor every CI result and non-self review comment; repair and "
        "fresh-review any new code change; merge only after every finding is dispositioned; "
        "then return the exact PR, head SHA, and merge SHA. Do not modify the live checkout "
        "and do not deploy: the controller owns those authority transitions and verifies "
        "their receipts independently. Never bypass a failed gate or use danger-full-access.\n\n"
        f"incident_id={incident_id}\nincident_dir={incident_dir}\n"
        f"repair_worktree={worktree}\nlive_checkout={live_checkout}\n"
        f"pr_monitor={cfg['paths']['pr_monitor']}\n"
        "\nREVIEW:\n"
        + json.dumps(review, ensure_ascii=False, indent=2)
        + "\n\nPATCH:\n"
        + json.dumps(read_json(incident_dir / "patch.json", {}), ensure_ascii=False, indent=2)
    )
    command = _codex_exec_base(
        cfg,
        sandbox="workspace-write",
        cwd=worktree,
        schema=schema,
        output=output,
        persistent=True,
        network=True,
    )
    try:
        spawned = _spawn_run(
            cfg,
            incident_id=incident_id,
            kind=str(run["kind"]),
            stage="delivery",
            command=command,
            cwd=worktree,
            prompt=prompt,
            output=output,
            events=events,
            workspace_branch=str(
                run.get("workspace_branch")
                or _repair_branch(cfg, incident_id)
            ),
        )
    except WriterLeaseBusy:
        with memory(cfg) as mem:
            transition(
                mem,
                incident_id,
                "review",
                reason="delivery_writer_lease_busy",
                run_id=str(run.get("run_id") or ""),
                status="retry_pending",
            )
            mem.commit()
        return
    with memory(cfg) as mem:
        transition(mem, incident_id, "delivery", reason="fresh_review_clear", run_id=str(spawned["run_id"]))
        mem.commit()


def _after_delivery(cfg: Mapping[str, Any], run: Mapping[str, Any], delivery: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    merge_sha = str(delivery.get("merge_sha") or "")
    head_sha = str(delivery.get("head_sha") or "")
    pr = str(delivery.get("pr") or "")
    def valid_sha(value: str) -> bool:
        return len(value) == 40 and all(char in "0123456789abcdef" for char in value.lower())
    if delivery.get("status") != "merged" or not pr or not valid_sha(head_sha) or not valid_sha(merge_sha):
        with memory(cfg) as mem:
            transition(
                mem,
                incident_id,
                "delivery",
                reason=f"delivery_not_merge_ready:{delivery.get('blocker') or 'missing_receipt'}",
                run_id=str(run["run_id"]),
                status="retry_pending",
            )
            mem.commit()
        return
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    output = incident_dir / "production.json"
    events = incident_dir / "controller-production.jsonl"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(cfg.get("_config_path") or CONFIG_PATH),
        "deploy-incident",
        incident_id,
    ]
    try:
        spawned = _spawn_controller_run(
            cfg,
            incident_id=incident_id,
            kind=str(run["kind"]),
            stage="production",
            command=command,
            cwd=ROOT,
            output=output,
            events=events,
        )
    except WriterLeaseBusy:
        with memory(cfg) as mem:
            transition(
                mem,
                incident_id,
                "delivery",
                reason="production_writer_lease_busy",
                run_id=str(run.get("run_id") or ""),
                status="retry_pending",
            )
            mem.commit()
        return
    with memory(cfg) as mem:
        transition(mem, incident_id, "production", reason="merge_receipt_ready", run_id=str(spawned["run_id"]))
        mem.commit()


def _after_production(cfg: Mapping[str, Any], run: Mapping[str, Any], production: Mapping[str, Any]) -> None:
    incident_id = str(run["incident_id"])
    stamp = iso()
    with memory(cfg) as mem:
        incident = mem.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        root_id = str(incident["root_id"] or "unknown") if incident else "unknown"
        incident_dir = runtime_dir(cfg) / "incidents" / incident_id
        delivery = read_json(incident_dir / "delivery.json", {})
        patch = read_json(incident_dir / "patch.json", {})
        diagnosis = read_json(incident_dir / "diagnosis.json", {})
        fix_id = digest(root_id, production.get("merge_sha"), production.get("deploy_sha"))
        status = "completed" if production.get("status") == "production_verified" else "retry_pending"
        mem.execute(
            "INSERT OR REPLACE INTO fixes VALUES (?,?,?,?,?,?,?,?)",
            (
                fix_id,
                root_id,
                delivery.get("head_sha"),
                delivery.get("pr"),
                (patch.get("replay") or {}).get("command") if isinstance(patch.get("replay"), Mapping) else None,
                status,
                stamp,
                stamp,
            ),
        )
        if production.get("loaded_sha"):
            deployment_id = digest(fix_id, production.get("loaded_sha"))
            mem.execute(
                "INSERT OR REPLACE INTO deployments VALUES (?,?,?,?,?,?)",
                (deployment_id, fix_id, production.get("merge_sha"), production.get("loaded_sha"), stamp, json.dumps(production.get("verification", []))),
            )
        if status == "completed":
            floor_at = parse_time(str(incident["t_floor"] or "")) if incident else None
            clocks = {
                str(item.get("clock")): parse_time(str(item.get("at") or ""))
                for item in diagnosis.get("timeline", [])
                if isinstance(item, Mapping)
            }
            def lead(clock: str) -> float | None:
                at = clocks.get(clock)
                return (floor_at - at).total_seconds() if floor_at is not None and at is not None else None
            evaluation_id = digest(incident_id, production.get("loaded_sha"), stamp)
            mem.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evaluation_id,
                    incident_id,
                    fix_id,
                    lead("probability"),
                    lead("decision"),
                    lead("command"),
                    lead("fill"),
                    incident["avoidable_loss_usd"] if incident else None,
                    None,
                    0,
                    stamp,
                ),
            )
            mem.execute(
                "UPDATE roots SET repair_sha=?,relationship_test=?,deployed_sha=?,"
                "utility=utility+1,updated_at=? WHERE root_id=?",
                (
                    delivery.get("head_sha"),
                    (patch.get("replay") or {}).get("command") if isinstance(patch.get("replay"), Mapping) else None,
                    production.get("loaded_sha"),
                    stamp,
                    root_id,
                ),
            )
        transition(
            mem,
            incident_id,
            "completed" if status == "completed" else "production",
            reason="production_receipt_verified" if status == "completed" else f"production_blocked:{production.get('blocker')}",
            run_id=str(run["run_id"]),
            status=status,
        )
        mem.commit()


def _production_health(
    cfg: Mapping[str, Any],
    *,
    live_checkout: Path,
    expected_sha: str,
    incident_id: str,
    deployed_at: datetime,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    loaded = read_json(live_checkout / "state" / "loaded_sha.json", {})
    loaded_sha = str(loaded.get("loaded_sha") or loaded.get("boot_sha") or "") if isinstance(loaded, Mapping) else ""
    if loaded_sha != expected_sha:
        reasons.append(f"loaded_sha_mismatch:{loaded_sha or 'missing'}")
    heartbeat = read_json(live_checkout / "state" / "daemon-heartbeat.json", {})
    heartbeat_at = parse_time(str(heartbeat.get("timestamp") or "")) if isinstance(heartbeat, Mapping) else None
    max_heartbeat_age = float(cfg.get("capital_lane", {}).get("max_main_heartbeat_age_seconds", 90))
    if not isinstance(heartbeat, Mapping) or heartbeat.get("alive") is not True or heartbeat_at is None:
        reasons.append("main_heartbeat_missing")
    elif (now() - heartbeat_at).total_seconds() > max_heartbeat_age:
        reasons.append("main_heartbeat_stale")
    with open_ro(Path(str(cfg["paths"]["trades_db"]))) as trades:
        monitor_rows = trades.execute(
            """
            WITH latest AS (
                SELECT position_id,MAX(sequence_no) AS sequence_no
                  FROM position_events WHERE event_type='MONITOR_REFRESHED'
                 GROUP BY position_id
            )
            SELECT pc.position_id,pe.occurred_at
              FROM position_current pc
              LEFT JOIN latest l ON l.position_id=pc.position_id
              LEFT JOIN position_events pe
                ON pe.position_id=l.position_id AND pe.sequence_no=l.sequence_no
             WHERE pc.phase IN ('active','day0_window','pending_exit')
               AND COALESCE(NULLIF(pc.chain_shares,0),pc.shares,0)>0
            """
        ).fetchall()
    max_monitor_age = float(cfg.get("capital_lane", {}).get("max_open_monitor_age_seconds", 120))
    for row in monitor_rows:
        at = parse_time(str(row[1] or ""))
        if at is None or (now() - at).total_seconds() > max_monitor_age:
            reasons.append(f"monitor_stale:{row[0]}")
    with memory(cfg) as mem:
        new_hard = int(mem.execute(
            "SELECT COUNT(*) FROM incidents WHERE kind='hard' AND incident_id<>? AND detected_at>=?",
            (incident_id, iso(deployed_at)),
        ).fetchone()[0])
        if new_hard:
            reasons.append(f"new_hard_incidents:{new_hard}")
        root = mem.execute("SELECT root_id FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        if root and root[0]:
            recurrence = mem.execute(
                "SELECT COUNT(*) FROM incidents WHERE root_id=? AND incident_id<>? AND detected_at>=?",
                (root[0], incident_id, iso(deployed_at)),
            ).fetchone()[0]
            if int(recurrence):
                reasons.append(f"same_root_recurrence:{recurrence}")
    return not reasons, reasons


def deploy_incident(cfg: Mapping[str, Any], incident_id: str) -> int:
    incident_dir = runtime_dir(cfg) / "incidents" / incident_id
    output = incident_dir / "production.json"
    delivery = read_json(incident_dir / "delivery.json", {})
    merge_sha = str(delivery.get("merge_sha") or "")
    head_sha = str(delivery.get("head_sha") or "")
    pr = str(delivery.get("pr") or "")
    verification: list[str] = []

    def blocked(reason: str, *, loaded_sha: str | None = None, deploy_sha: str | None = None) -> int:
        atomic_json(
            output,
            {
                "incident_id": incident_id,
                "status": "blocked",
                "merge_sha": merge_sha,
                "deploy_sha": deploy_sha,
                "loaded_sha": loaded_sha,
                "observed_seconds": 0.0,
                "verification": verification,
                "blocker": reason,
            },
        )
        return 0

    pr_view = _run_capture(
        [
            "gh", "pr", "view", pr, "--json",
            "state,headRefOid,mergeCommit,reviewDecision,statusCheckRollup,reviews",
        ],
        cwd=ROOT,
        timeout=60,
    )
    if pr_view.returncode != 0:
        return blocked(f"pr_receipt_unreadable:{pr_view.stderr.strip()}")
    pr_fact = read_json_text(pr_view.stdout)
    remote_merge = str((pr_fact.get("mergeCommit") or {}).get("oid") or "")
    if pr_fact.get("state") != "MERGED" or pr_fact.get("headRefOid") != head_sha or remote_merge != merge_sha:
        return blocked("pr_merge_receipt_mismatch")
    repo_view = _run_capture(
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        cwd=ROOT,
        timeout=60,
    )
    repo = str(read_json_text(repo_view.stdout).get("nameWithOwner") or "")
    pr_number = pr.rstrip("/").split("/")[-1]
    files_view = _run_capture(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{pr_number}/files"],
        cwd=ROOT,
        timeout=120,
    )
    try:
        pages = json.loads(files_view.stdout)
    except json.JSONDecodeError:
        pages = None
    if repo_view.returncode != 0 or files_view.returncode != 0 or not repo or not isinstance(pages, list):
        return blocked("pr_changed_files_unavailable")
    files = [item for page in pages for item in (page if isinstance(page, list) else [page]) if isinstance(item, Mapping)]
    paths = [str(item.get("filename") or "") for item in files]
    allowed_source = {
        "src/engine/monitor_refresh.py",
        "src/execution/exit_lifecycle.py",
        "src/events/triggers/market_channel_ingestor.py",
        "src/ingest/price_channel_ingest.py",
    }
    forbidden = [
        path for path in paths
        if not path.startswith("tests/") and path not in allowed_source
    ]
    destructive = [
        str(item.get("filename") or "")
        for item in files
        if str(item.get("status") or "") in {"removed", "renamed"}
    ]
    if forbidden:
        return blocked("automation_forbidden_paths:" + ",".join(forbidden))
    if destructive:
        return blocked("automation_destructive_diff:" + ",".join(destructive))
    checks = pr_fact.get("statusCheckRollup")
    if not isinstance(checks, list) or not checks:
        return blocked("pr_checks_missing")
    bad_checks = [
        str(item.get("name") or item.get("context") or "unknown")
        for item in checks
        if not isinstance(item, Mapping)
        or str(item.get("status") or "").upper() != "COMPLETED"
        or str(item.get("conclusion") or "").upper() != "SUCCESS"
    ]
    if bad_checks:
        return blocked("pr_checks_not_green:" + ",".join(bad_checks))
    reviews = pr_fact.get("reviews") or []
    if any(
        isinstance(review, Mapping)
        and str(review.get("state") or "").upper() == "CHANGES_REQUESTED"
        for review in reviews
    ):
        return blocked("pr_changes_requested")
    verification.append(f"pr_merged:{pr}:{merge_sha}")
    verification.append(f"pr_checks_green:{len(checks)}")

    live_checkout = _live_checkout(str(cfg["delivery"]["base_branch"]))
    dirty = _run_capture(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=live_checkout,
    )
    if dirty.returncode != 0 or dirty.stdout.strip():
        return blocked(f"live_checkout_dirty:{dirty.stdout.strip() or dirty.stderr.strip()}")
    fetch = _run_capture(["git", "fetch", "origin", str(cfg["delivery"]["base_branch"])], cwd=live_checkout, timeout=120)
    if fetch.returncode != 0:
        return blocked(f"live_fetch_failed:{fetch.stderr.strip()}")
    ancestor = _run_capture(
        ["git", "merge-base", "--is-ancestor", merge_sha, f"origin/{cfg['delivery']['base_branch']}"],
        cwd=live_checkout,
    )
    if ancestor.returncode != 0:
        return blocked("merge_sha_not_in_origin_live")
    fast_forward = _run_capture(
        ["git", "merge", "--ff-only", f"origin/{cfg['delivery']['base_branch']}"],
        cwd=live_checkout,
        timeout=120,
    )
    if fast_forward.returncode != 0:
        return blocked(f"live_fast_forward_failed:{fast_forward.stderr.strip()}")
    deploy_sha = _run_capture(["git", "rev-parse", "HEAD"], cwd=live_checkout).stdout.strip()
    verification.append(f"live_fast_forward:{deploy_sha}")

    configured_deploy = Path(str(cfg["paths"]["deploy_script"]))
    try:
        deploy_relative = configured_deploy.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return blocked("deploy_script_outside_repo", deploy_sha=deploy_sha)
    deploy_script = live_checkout / deploy_relative
    python = live_checkout / ".venv" / "bin" / "python"
    deploy = _run_capture(
        [str(python if python.is_file() else Path(sys.executable)), str(deploy_script), "restart", "all"],
        cwd=live_checkout,
        timeout=int(cfg["delivery"].get("deploy_timeout_seconds", 1200)),
    )
    (incident_dir / "deploy-receipt.log").write_text(deploy.stdout + deploy.stderr)
    if deploy.returncode != 0:
        return blocked(f"deploy_live_failed:{deploy.returncode}", deploy_sha=deploy_sha)
    loaded = read_json(live_checkout / "state" / "loaded_sha.json", {})
    loaded_sha = str(loaded.get("loaded_sha") or loaded.get("boot_sha") or "") if isinstance(loaded, Mapping) else ""
    if loaded_sha != deploy_sha:
        return blocked("loaded_sha_not_deploy_sha", loaded_sha=loaded_sha, deploy_sha=deploy_sha)
    verification.append(f"loaded_sha:{loaded_sha}")

    deployed_at = now()
    observation = max(0.0, float(cfg["delivery"].get("production_observation_seconds", 900)))
    deadline = time.monotonic() + observation
    while True:
        healthy, reasons = _production_health(
            cfg,
            live_checkout=live_checkout,
            expected_sha=deploy_sha,
            incident_id=incident_id,
            deployed_at=deployed_at,
        )
        if not healthy:
            return blocked(",".join(reasons), loaded_sha=loaded_sha, deploy_sha=deploy_sha)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
    verification.append(f"production_observed_seconds:{observation}")
    atomic_json(
        output,
        {
            "incident_id": incident_id,
            "status": "production_verified",
            "merge_sha": merge_sha,
            "deploy_sha": deploy_sha,
            "loaded_sha": loaded_sha,
            "observed_seconds": observation,
            "verification": verification,
            "blocker": None,
        },
    )
    return 0


def poll_runs(
    cfg: Mapping[str, Any],
    running: list[dict[str, Any]] | None = None,
) -> list[str]:
    completed: list[str] = []
    for run in (running if running is not None else _running(cfg)):
        pid = int(run["pid"])
        started = parse_time(str(run.get("started_at") or ""))
        timeout = int(cfg["loop"].get("agent_timeout_seconds", 5400))
        if started is not None and (now() - started).total_seconds() > timeout:
            _terminate_process_group(pid)
            _finish_run(cfg, run, 124)
            completed.append(str(run["run_id"]))
            continue
        returncode = _poll_process(pid)
        if returncode is None:
            continue
        _finish_run(cfg, run, returncode)
        completed.append(str(run["run_id"]))
    return completed


def _bootstrap_memory_version(cfg: Mapping[str, Any]) -> None:
    try:
        with memory(cfg, allow_schema_migration=True) as mem:
            code = _run_capture(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
            _startup_guard()
            config_hash = _startup_hash_file(Path(str(cfg.get("_config_path") or CONFIG_PATH)))
            _startup_guard()
            version_id = digest(code, config_hash)
            mem.execute(
                "INSERT OR IGNORE INTO loop_versions(version_id,code_sha,config_hash,activated_at) VALUES (?,?,?,?)",
                (version_id, code, config_hash, iso()),
            )
            mem.commit()
    except sqlite3.OperationalError as exc:
        if _STARTUP_BUDGET is not None:
            raise StartupMaintenanceDeferred("startup_maintenance_deferred:memory_sqlite") from exc
        raise


def bootstrap(cfg: Mapping[str, Any]) -> dict[str, Any]:
    run = runtime_dir(cfg)
    for rel in ("incidents", "worktrees", "benchmarks", "logs", "runs", "schemas"):
        _startup_guard()
        (run / rel).mkdir(parents=True, exist_ok=True)
    _startup_guard()
    run.chmod(0o700)
    _startup_guard()
    isolated_codex_home(cfg)
    _startup_guard()
    _bootstrap_memory_version(cfg)
    return {"runtime": str(run), "memory": str(run / "memory.db"), "floor": floor_price(cfg)}


def status(cfg: Mapping[str, Any]) -> dict[str, Any]:
    with memory(cfg) as mem:
        counts = {row[0]: row[1] for row in mem.execute("SELECT status,COUNT(*) FROM incidents GROUP BY status")}
        latest = [dict(row) for row in mem.execute("SELECT * FROM incidents ORDER BY detected_at DESC LIMIT 20")]
    return {
        "runtime": str(runtime_dir(cfg)),
        "floor": floor_price(cfg),
        "incidents": counts,
        "latest": latest,
        "running": _running(cfg),
        "capabilities": read_json(runtime_dir(cfg) / "capabilities.json", None),
        "provider_backoff": _provider_backoff(cfg),
        "halted": (runtime_dir(cfg) / "HALT").exists(),
    }


def _record_cycle_latency(
    cfg: Mapping[str, Any], *, detector_elapsed: float, total_elapsed: float
) -> None:
    run = runtime_dir(cfg)
    if detector_elapsed * 1000.0 > float(cfg["loop"].get("detector_budget_ms", 200.0)):
        atomic_json(
            run / "detector-budget-breach.json",
            {"at": iso(), "elapsed_ms": detector_elapsed * 1000.0},
        )
    atomic_json(
        run / "cycle-latency.json",
        {"at": iso(), "detector_ms": detector_elapsed * 1000.0, "total_ms": total_elapsed * 1000.0},
    )


def dispatch_once(cfg: Mapping[str, Any]) -> list[str]:
    """Run one bounded dispatch turn without sharing the detector's process."""

    if _startup_debt_pending(cfg):
        return []
    lock = (runtime_dir(cfg) / "dispatch.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return []
    try:
        return dispatch(cfg)
    finally:
        lock.close()


def _dispatch_has_eligible_debt(
    cfg: Mapping[str, Any],
    running: list[Mapping[str, Any]],
) -> bool:
    """Return whether one dispatch child can make durable progress now."""

    if _startup_debt_pending(cfg):
        return False
    if _provider_backoff(cfg) is not None:
        return False
    active_incidents = {str(row.get("incident_id") or "") for row in running}
    by_kind = {
        kind: sum(1 for row in running if str(row.get("kind") or "") == kind)
        for kind in ("hard", "precursor")
    }
    with memory_ro(cfg) as mem:
        blind = mem.execute(
            "SELECT kind FROM incidents WHERE status='queued' AND stage='blind'"
        ).fetchall()
        repair = mem.execute(
            "SELECT kind FROM incidents WHERE status='queued' AND stage='repair_waiting'"
        ).fetchall()
        classification = mem.execute(
            "SELECT incident_id FROM incidents WHERE status='running' AND stage='classification'"
        ).fetchall()
        blocked_evidence = mem.execute(
            "SELECT 1 FROM incidents WHERE status='blocked' AND stage='evidence' LIMIT 1"
        ).fetchone()
        retries = mem.execute(
            "SELECT incident_id,kind FROM incidents WHERE status='retry_pending'"
        ).fetchall()
    if any(
        by_kind.get(str(row[0]), 0) < int(cfg["loop"].get(f"{row[0]}_slots", 1))
        for row in blind
    ):
        return True
    if blocked_evidence is not None and _active_loaded_sha(cfg):
        return True
    for row in classification:
        incident_id = str(row[0])
        if incident_id in active_incidents:
            continue
        incident_dir = runtime_dir(cfg) / "incidents" / incident_id
        if isinstance(read_json(incident_dir / "classification.json", None), Mapping) and isinstance(
            read_json(incident_dir / "diagnosis.json", None), Mapping
        ):
            return True
    if not any(
        str(row.get("stage") or "") in {"repair", "repair_feedback", "review", "delivery", "production"}
        for row in running
    ) and any(
        by_kind.get(str(row[0]), 0) < int(cfg["loop"].get(f"{row[0]}_slots", 1))
        for row in repair
    ):
        return True
    if not retries:
        return False
    retry_delay = float(cfg["loop"].get("stage_retry_seconds", 60))
    records = sorted(
        (runtime_dir(cfg) / "runs").glob("*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for retry in retries:
        incident_id = str(retry["incident_id"])
        kind = str(retry["kind"])
        if incident_id in active_incidents or by_kind.get(kind, 0) >= int(
            cfg["loop"].get(f"{kind}_slots", 1)
        ):
            continue
        prior = next(
            (
                record
                for item in records
                if isinstance((record := read_json(item, {})), dict)
                and record.get("incident_id") == incident_id
            ),
            None,
        )
        if not isinstance(prior, dict) or not isinstance(prior.get("command"), list):
            continue
        stage = str(prior.get("stage") or "")
        cwd = Path(str(prior.get("cwd") or ROOT))
        if _worktree_writer_running(running, stage=stage, cwd=cwd):
            continue
        completed_at = parse_time(str(prior.get("completed_at") or prior.get("started_at") or ""))
        if completed_at is not None and (now() - completed_at).total_seconds() < retry_delay:
            continue
        if prior.get("controller") or Path(str(prior.get("events") or "")).with_suffix(".prompt.md").is_file():
            return True
    return False


def _spawn_dispatch_worker(cfg: Mapping[str, Any]) -> subprocess.Popen[Any]:
    logs = runtime_dir(cfg) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"dispatch-{os.getpid()}-{time.monotonic_ns()}.log"
    handle = log_path.open("wb")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(cfg.get("_config_path") or CONFIG_PATH),
                "dispatch-once",
            ],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        handle.close()


def daemon(cfg: Mapping[str, Any]) -> int:
    run = runtime_dir(cfg)
    run.mkdir(parents=True, exist_ok=True)
    lock = (run / "loop.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 75
    stopping = False
    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bootstrap_complete = False
    atomic_json(
        run / "status.json",
        {
            "alive": True,
            "pid": os.getpid(),
            "at": iso(),
            "phase": "startup",
            "startup_maintenance": "starting",
            "startup_bootstrap": "pending",
            "startup_error": None,
            "created": [],
            "dispatch_worker_pid": None,
            "error": None,
            "dispatch_error": None,
            "provider_backoff": None,
        },
    )
    global _STARTUP_BUDGET
    startup_pending = True
    startup_error: str | None = None
    _STARTUP_BUDGET = _new_startup_budget(cfg)
    try:
        bootstrap(cfg)
        bootstrap_complete = True
        reconcile_orphan_incidents(cfg)
        startup_pending = _startup_reconcile_remaining(cfg)
        if startup_pending:
            startup_error = "startup_maintenance_deferred:batch_remaining"
            _record_startup_debt(cfg, startup_error)
        else:
            _record_startup_debt(cfg, "startup_maintenance_complete", status="resolved")
    except StartupMaintenanceDeferred as exc:
        startup_error = str(exc)
        if bootstrap_complete:
            _shrink_startup_batch(cfg)
        _record_startup_debt(cfg, startup_error)
    finally:
        _STARTUP_BUDGET = None
    poll = max(0.05, float(cfg["loop"].get("poll_ms", 250)) / 1000.0)
    dispatch_worker: subprocess.Popen[Any] | None = None
    dispatch_error: str | None = None
    capabilities_ready = False
    next_debt_check_at = 0.0
    while not stopping and not (run / "HALT").exists():
        cycle_started = time.monotonic()
        detector_elapsed = 0.0
        error = None
        created: list[str] = []
        atomic_json(
            run / "status.json",
            {
                "alive": True,
                "pid": os.getpid(),
                "at": iso(),
                "phase": "startup_maintenance" if startup_pending else "cycle",
                "startup_maintenance": "pending" if startup_pending else "complete",
                "startup_bootstrap": "complete" if bootstrap_complete else "pending",
                "startup_error": startup_error,
                "created": [],
                "evidence_maintenance": "starting",
                "evidence_built": [],
                "evidence_deferred": [],
                "dispatch_worker_pid": None,
                "error": None,
                "dispatch_error": dispatch_error,
                "provider_backoff": None,
            },
        )
        if startup_pending:
            _STARTUP_BUDGET = _new_startup_budget(cfg)
            try:
                if not bootstrap_complete:
                    bootstrap(cfg)
                    bootstrap_complete = True
                reconcile_orphan_incidents(cfg)
                startup_pending = _startup_reconcile_remaining(cfg)
                startup_error = (
                    "startup_maintenance_deferred:batch_remaining"
                    if startup_pending
                    else None
                )
                _record_startup_debt(
                    cfg,
                    startup_error or "startup_maintenance_complete",
                    status="retry_pending" if startup_pending else "resolved",
                )
            except StartupMaintenanceDeferred as exc:
                startup_error = str(exc)
                if bootstrap_complete:
                    _shrink_startup_batch(cfg)
                _record_startup_debt(cfg, startup_error)
            finally:
                _STARTUP_BUDGET = None
        # Publish liveness before detector/evidence maintenance can touch a
        # large historical database.  Operators can distinguish busy from dead.
        atomic_json(
            run / "status.json",
            {
                "alive": True,
                "pid": os.getpid(),
                "at": iso(),
                "phase": "startup_maintenance" if startup_pending else "cycle",
                "startup_maintenance": "pending" if startup_pending else "complete",
                "startup_bootstrap": "complete" if bootstrap_complete else "pending",
                "startup_error": startup_error,
                "created": [],
                "evidence_maintenance": "starting",
                "evidence_built": [],
                "evidence_deferred": [],
                "dispatch_worker_pid": None,
                "error": None,
                "dispatch_error": dispatch_error,
                "provider_backoff": _provider_backoff(cfg),
            },
        )
        try:
            detector_started = time.monotonic()
            created = detect(cfg)
            detector_elapsed = time.monotonic() - detector_started
        except Exception as exc:  # the detector remains restartable and evidence-backed
            error = f"{type(exc).__name__}: {exc}"
        recorded_dispatch_error = dispatch_error
        atomic_json(
            run / "status.json",
            {
                "alive": True,
                "pid": os.getpid(),
                "at": iso(),
                "created": created,
                "evidence_maintenance": "complete" if error is None else "error",
                "evidence_built": _LAST_EVIDENCE_CYCLE.get("built", []),
                "evidence_deferred": _LAST_EVIDENCE_CYCLE.get("deferred", []),
                "evidence_attempted": _LAST_EVIDENCE_CYCLE.get("attempted", 0),
                "evidence_validated": _LAST_EVIDENCE_CYCLE.get("validated", 0),
                "evidence_bytes": _LAST_EVIDENCE_CYCLE.get("bytes", 0),
                "dispatch_worker_pid": (
                    dispatch_worker.pid
                    if dispatch_worker is not None and dispatch_worker.poll() is None
                    else None
                ),
                "error": error,
                "dispatch_error": dispatch_error,
                "provider_backoff": _provider_backoff(cfg),
            },
        )
        if error is None and not startup_pending and not _startup_debt_pending(cfg):
            try:
                running = _running(cfg)
                completed = poll_runs(cfg, running)
                if completed:
                    completed_ids = set(completed)
                    running = [
                        item for item in running
                        if str(item.get("run_id") or "") not in completed_ids
                    ]
                worker_exited = (
                    dispatch_worker is not None and dispatch_worker.poll() is not None
                )
                provider_backoff = _provider_backoff(cfg)
                if provider_backoff is not None:
                    capabilities_ready = False
                elif current_capabilities(cfg) is None:
                    capabilities_ready = False
                    ensure_capability_probe(cfg)
                else:
                    capability_became_ready = not capabilities_ready
                    capabilities_ready = True
                    retry_check_due = time.monotonic() >= next_debt_check_at
                    dispatch_wake = bool(created) or bool(completed) or worker_exited or capability_became_ready or retry_check_due
                    if (
                        dispatch_worker is None or worker_exited
                    ) and dispatch_wake:
                        if _dispatch_has_eligible_debt(cfg, running):
                            dispatch_worker = _spawn_dispatch_worker(cfg)
                        next_debt_check_at = time.monotonic() + 5.0
            except Exception as exc:
                dispatch_error = f"{type(exc).__name__}: {exc}"
            else:
                dispatch_error = None
            if dispatch_error != recorded_dispatch_error:
                atomic_json(
                    run / "status.json",
                    {
                        "alive": True,
                        "pid": os.getpid(),
                        "at": iso(),
                        "created": created,
                        "dispatch_worker_pid": (
                            dispatch_worker.pid
                            if dispatch_worker is not None and dispatch_worker.poll() is None
                            else None
                        ),
                        "error": error,
                        "dispatch_error": dispatch_error,
                        "provider_backoff": _provider_backoff(cfg),
                    },
                )
        elapsed = time.monotonic() - cycle_started
        _record_cycle_latency(cfg, detector_elapsed=detector_elapsed, total_elapsed=elapsed)
        if elapsed < poll:
            time.sleep(poll - elapsed)
    if dispatch_worker is not None and dispatch_worker.poll() is None:
        _terminate_process_group(dispatch_worker.pid)
    terminated: list[str] = []
    for active in _running(cfg):
        _terminate_process_group(int(active["pid"]))
        _finish_run(cfg, active, 143)
        terminated.append(str(active["run_id"]))
    with _probe_lock:
        probe_pids = list(_probe_process_groups)
    for pid in probe_pids:
        _terminate_process_group(pid)
    atomic_json(
        run / "status.json",
        {"alive": False, "pid": os.getpid(), "at": iso(), "terminated_runs": terminated},
    )
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--config", type=Path, default=CONFIG_PATH)
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("probe")
    sub.add_parser("scan-once")
    sub.add_parser("dispatch-once")
    sub.add_parser("daemon")
    sub.add_parser("status")
    evidence = sub.add_parser("build-evidence")
    evidence.add_argument("incident_id")
    deploy = sub.add_parser("deploy-incident")
    deploy.add_argument("incident_id")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "bootstrap":
        print(json.dumps(bootstrap(cfg), ensure_ascii=False, indent=2))
        return 0
    if args.command == "probe":
        bootstrap(cfg)
        result = probe_capabilities(cfg, smoke=True)
        atomic_json(
            runtime_dir(cfg) / "capability-fingerprint.json",
            {"value": _capability_fingerprint(cfg), "at": iso()},
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "scan-once":
        bootstrap(cfg)
        print(json.dumps({"created": detect(cfg)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "dispatch-once":
        bootstrap(cfg)
        print(json.dumps({"launched": dispatch_once(cfg)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-evidence":
        print(build_evidence(cfg, args.incident_id))
        return 0
    if args.command == "deploy-incident":
        return deploy_incident(cfg, args.incident_id)
    if args.command == "status":
        bootstrap(cfg)
        print(json.dumps(status(cfg), ensure_ascii=False, indent=2))
        return 0
    return daemon(cfg)


if __name__ == "__main__":
    sys.exit(main())
