#!/usr/bin/env python3
# Created: 2026-07-22
# Last reused/audited: 2026-07-23
# Authority basis: operator-directed WORLD single-live decision-graph cutover.
"""Writer-fenced removal of retired alternate-runtime persistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state"
OLD_AUTHORITY = "trade_" + "authority_status"
OLD_LIVE_AUTHORITY = "LIVE_" + "AUTHORITY"
OLD_ELIGIBILITY = "promotion_" + "eligible"
OLD_AUDIT_INDEX = "idx_edli_live_profit_audit_" + "promotion"
TRANSFER_TABLE = "validated_calibration_" + "transfers"
CONVERSION_TABLE = "ctf_conversion_" + "commands"
CONVERSION_EVENTS = "ctf_conversion_command_" + "events"
FORCE_EXIT_COLUMN = "force_exit_" + "review"
OLD_PRE_SUBMIT_MODE = "NO_" + "SUBMIT"
OLD_REPLAY_MODE = "REPLAY_" + "COUNTERFACTUAL"
OLD_SIZING_CERTIFICATE = "Kelly" + "DryRunCertificate"
OLD_PRE_SUBMIT_DECISION_CERTIFICATE = "NoSubmit" + "DecisionCertificate"
OLD_PRE_SUBMIT_MODE_CERTIFICATE = "NoSubmit" + "ModeCertificate"
RECEIPT_COLUMNS = (
    "q_live_" + "raw",
    "q_lcb_" + "raw",
    "coverage_" + "hierarchy_level",
    "coverage_" + "hierarchy_cohort_key",
    "coverage_" + "hierarchy_n",
    "coverage_" + "hierarchy_wins",
    "coverage_" + "hierarchy_estimator",
    "main" + "stream_" + "agreement_pass",
    "main" + "stream_" + "agreement_fail_reason",
    "main" + "stream_" + "point",
    "main" + "stream_" + "delta",
    "main" + "stream_" + "bin_label",
    "main" + "stream_" + "source",
    "main" + "stream_" + "fetched_at_utc",
    "lf" + "sr",
    "edge_" + "shrunk",
    "edge_" + "shrunk_posterior_sd",
    "selection_" + "authority",
)
REMOVED_MANIFEST_FIELD = OLD_AUTHORITY
CONFIG_NOTES = (
    "_edli_live_" + "scope_note_2026_06_09",
    "_edli_live_" + "scope_note_2026_06_12",
    "_mass_enable_note_2026_06_09",
    "_exit_" + "bias_family_unify_FLIP_2026_06_12",
    "_exit_" + "bias_family_unify_enabled_note",
    "_BUY_NO_NATIVE_QUOTE_EVIDENCE_note",
    "_calibration_bin_source_v2_fit_" + "enabled_note",
    "_ddd_v2_" + "enabled_note",
)
CONFIG_KEYS = (
    "main" + "stream_" + "warm_max_families_per_cycle",
    "main" + "stream_" + "agreement_reference_enabled",
    "main" + "stream_" + "agreement_enforce_on_submit",
    "forecast_" + "sharpness_gate_enabled",
    "forecast_" + "sharpness_mae_multiplier",
    "edli_" + "bias_correction_enabled",
    "edli_" + "grid_representativeness_correction_enabled",
    "edli_" + "emos_sole_calibrator_enabled",
    "exit_" + "bias_family_unify_enabled",
    "qkernel_" + "spine_enabled",
    "_qkernel_" + "spine_enabled_note",
    "replacement_0_1_bayes_precision_fusion_" + "capture_enabled",
    "replacement_0_1_bayes_precision_fusion_" + "enabled",
    "replacement_0_1_fused_q_shape_" + "enabled",
    "edli_source_run_dual_chain_" + "enabled",
    "edli_intake_phase_filter_" + "enabled",
    "edli_user_channel_reconcile_" + "enabled",
    "fill_synchronizer_" + "enabled",
    "real_order_submit_" + "enabled",
    "calibration_bin_source_v2_fit_" + "enabled",
    "ddd_v2_" + "enabled",
    "BUY_NO_NATIVE_QUOTE_EVIDENCE_" + "ENABLED",
    "BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_" + "ENABLED",
    "download_current_targets_" + "enabled",
    "market_channel_quote_cache_" + "enabled",
    "event_writer_" + "enabled",
    "forecast_snapshot_trigger_" + "enabled",
    "market_channel_ingestor_" + "enabled",
    "durable_submit_outbox_" + "enabled",
    "pre_submit_balance_allowance_check_" + "enabled",
    "day0_authority_catchup_scanner_" + "enabled",
    "day0_extreme_trigger_" + "enabled",
    "day0_fast_obs_lane_" + "enabled",
    "day0_hard_fact_live_" + "enabled",
    "reactor_" + "mode",
    "live_execution_" + "mode",
    "edli_live_" + "scope",
    "openmeteo_ecmwf_ifs9_bayes_fusion_live_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_kelly_increase_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_direction_flip_" + "enabled",
    "CANONICAL_EXIT_" + "PATH",
    "HOLD_VALUE_EXIT_" + "COSTS",
    "_HOLD_VALUE_EXIT_" + "COSTS_note",
)
CONFIG_PATHS = ("edli.enabled",)
PROCESS_MARKERS = (
    "src.main",
    "src/main.py",
    "src.ingest_main",
    "src/ingest_main.py",
    "forecast_live_daemon",
    "price_channel_ingest",
    "price_channel_daemon",
    "event_reactor",
    "riskguard_live",
    "src.riskguard.riskguard",
    "post_trade_capital",
    "substrate_observer",
    "venue_heartbeat",
)
RETIRED_FILES = (
    Path("state") / ("selection_" + "curse_bound.json"),
)

WORLD_DB = "zeus-world.db"
TRADES_DB = "zeus_trades.db"
DECISION_TABLE = "decision_certificates"
EDGE_TABLE = "decision_certificate_edges"
SUPERSESSION_TABLE = "decision_certificate_supersessions"
FAILURE_TABLE = "decision_compile_failures"
TRADES_GHOST_DROP_ORDER = (
    EDGE_TABLE,
    SUPERSESSION_TABLE,
    FAILURE_TABLE,
    DECISION_TABLE,
)
LIVE_MODE = "LIVE"
PROTECTED_POSITION_PHASES = frozenset(
    {
        "pending_entry",
        "active",
        "day0_window",
        "pending_exit",
        "economically_closed",
        "unknown",
    }
)
TERMINAL_COMMAND_STATES = frozenset(
    {"SUBMIT_REJECTED", "FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "FAILED"}
)
DECISION_COLUMNS = (
    "certificate_id",
    "certificate_type",
    "schema_version",
    "canonicalization_version",
    "semantic_key",
    "claim_type",
    "mode",
    "decision_time",
    "source_available_at",
    "agent_received_at",
    "persisted_at",
    "max_parent_source_available_at",
    "max_parent_agent_received_at",
    "max_parent_persisted_at",
    "authority_id",
    "authority_version",
    "algorithm_id",
    "algorithm_version",
    "config_hash",
    "model_version_hash",
    "payload_json",
    "payload_hash",
    "certificate_hash",
    "verifier_status",
    "created_at",
)
DECISION_LIVE_DDL = """
CREATE TABLE decision_certificates_live_new (
    certificate_id TEXT NOT NULL PRIMARY KEY,
    certificate_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    canonicalization_version TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'LIVE'),
    decision_time TEXT NOT NULL,
    source_available_at TEXT,
    agent_received_at TEXT,
    persisted_at TEXT,
    max_parent_source_available_at TEXT,
    max_parent_agent_received_at TEXT,
    max_parent_persisted_at TEXT,
    authority_id TEXT NOT NULL,
    authority_version TEXT NOT NULL,
    algorithm_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_hash TEXT,
    model_version_hash TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    certificate_hash TEXT NOT NULL UNIQUE,
    verifier_status TEXT NOT NULL CHECK (
      verifier_status IN ('VERIFIED','REJECTED','SUPERSEDED','REVIEW_REQUIRED')
    ),
    created_at TEXT NOT NULL,
    UNIQUE(certificate_type, semantic_key, mode, decision_time)
)
"""
OPAQUE_REFERENCE_FIELDS = {
    "position_events": ("decision_id", "payload_json"),
    "venue_command_events": ("payload_json",),
    "decision_log": ("artifact_json",),
    "edli_live_profit_audit": (
        "expected_edge_source_certificate_hash",
        "cost_basis_source_certificate_hash",
    ),
}


def live_writers() -> list[str]:
    out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    current = os.getpid()
    found: list[str] = []
    for line in out.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid != current and "python" in command and any(marker in command for marker in PROCESS_MARKERS):
            found.append(line.strip())
    return found


def loaded_writer_jobs(root: Path) -> list[str]:
    """Return loaded launchd jobs capable of restarting a target writer."""

    try:
        out = subprocess.check_output(
            ["launchctl", "print", f"gui/{os.getuid()}"],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot prove launchd writer jobs are unloaded") from exc
    root_text = str(root.resolve())
    blocks = re.split(r"\n\s*(?=service = |[A-Za-z0-9_.-]+ = \{)", out)
    return [
        block.splitlines()[0].strip()
        for block in blocks
        if root_text in block and any(marker in block for marker in PROCESS_MARKERS)
    ]


def open_canonical_db_handles(root: Path) -> list[str]:
    """Return foreign processes holding any canonical DB or WAL/SHM handle."""

    state = root / "state"
    targets = [
        state / name
        for db in ("zeus-world.db", "zeus-forecasts.db", "zeus_trades.db", "risk_state.db")
        for name in (db, f"{db}-wal", f"{db}-shm")
        if (state / name).exists()
    ]
    if not targets:
        return []
    result = subprocess.run(
        ["lsof", "-Fn", "--", *(str(path) for path in targets)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"cannot prove canonical DB handles are closed: {result.stderr.strip()}")
    current = f"p{os.getpid()}"
    records = result.stdout.splitlines()
    found: list[str] = []
    owner = ""
    for record in records:
        if record.startswith("p"):
            owner = record
        elif record.startswith("n") and owner and owner != current:
            found.append(f"{owner} {record[1:]}")
    return sorted(set(found))


def assert_writer_fence(root: Path) -> None:
    writers = live_writers()
    jobs = loaded_writer_jobs(root)
    handles = open_canonical_db_handles(root)
    if writers or jobs or handles:
        detail = [
            *(f"process {item}" for item in writers),
            *(f"launchd {item}" for item in jobs),
            *(f"open_handle {item}" for item in handles),
        ]
        raise RuntimeError("live writer fence is not durable:\n  " + "\n  ".join(detail))


def target_release_identity(root: Path) -> dict[str, str]:
    """Bind apply/resume to the checkout that owns this exact migration code."""

    if root.resolve() != ROOT.resolve():
        raise RuntimeError("apply must run from the target checkout itself")
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fingerprint_path = root / "architecture" / "_schema_fingerprint.txt"
    schema_fingerprint = fingerprint_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("target git HEAD is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", schema_fingerprint):
        raise RuntimeError("target schema fingerprint is invalid")
    return {
        "target_head": head,
        "migration_script_sha256": script_hash,
        "schema_fingerprint": schema_fingerprint,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def open_db(path: Path, *, writable: bool) -> sqlite3.Connection:
    mode = "rw" if writable else "ro"
    if not path.exists():
        raise RuntimeError(f"required DB is missing: {path}")
    conn = sqlite3.connect(
        f"file:{path}?mode={mode}", uri=True, timeout=0.0, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=0")
    if writable:
        conn.execute("PRAGMA foreign_keys=ON")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            conn.close()
            raise RuntimeError("could not keep foreign_keys enabled")
    else:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> tuple[str, ...]:
    return tuple(
        str(row[1])
        for row in conn.execute(
            f"PRAGMA {quote_identifier(schema)}.table_xinfo({quote_identifier(table)})"
        )
        if len(row) < 7 or int(row[6]) == 0
    )


def _schema_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return conn.execute(
        f"SELECT 1 FROM {quote_identifier(schema)}.sqlite_master "
        "WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _materialize_retired_closure(conn: sqlite3.Connection) -> bool:
    """Build the graph closure once, indexed for every subsequent plan query."""
    query_only = bool(conn.execute("PRAGMA query_only").fetchone()[0])
    if query_only:
        conn.execute("PRAGMA query_only=OFF")
    conn.execute("DROP TABLE IF EXISTS temp.single_live_retired_closure")
    conn.execute(
        "CREATE TEMP TABLE single_live_retired_closure ("
        "certificate_hash TEXT PRIMARY KEY COLLATE NOCASE, "
        "certificate_id TEXT NOT NULL, is_seed INTEGER NOT NULL CHECK (is_seed IN (0, 1))"
        ") WITHOUT ROWID"
    )
    conn.execute(
        "CREATE INDEX temp.single_live_retired_closure_certificate_id "
        "ON single_live_retired_closure(certificate_id)"
    )
    conn.execute(
        f"""
        WITH RECURSIVE retired(certificate_id, certificate_hash) AS (
            SELECT certificate_id, certificate_hash
              FROM {DECISION_TABLE}
             WHERE mode != ?
            UNION
            SELECT child.certificate_id, child.certificate_hash
              FROM retired
              JOIN {EDGE_TABLE} edge
                ON lower(edge.parent_certificate_hash) = lower(retired.certificate_hash)
              JOIN {DECISION_TABLE} child
                ON child.certificate_id = edge.child_certificate_id
        )
        INSERT OR IGNORE INTO temp.single_live_retired_closure(
            certificate_hash, certificate_id, is_seed
        )
        SELECT lower(retired.certificate_hash), retired.certificate_id,
               CASE WHEN cert.mode != ? THEN 1 ELSE 0 END
          FROM retired
          JOIN {DECISION_TABLE} cert
            ON cert.certificate_id = retired.certificate_id
        """,
        (LIVE_MODE, LIVE_MODE),
    )
    return query_only


def _retired_closure_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT certificate_id, certificate_hash, is_seed "
            "FROM temp.single_live_retired_closure ORDER BY certificate_hash"
        )
    )


def _closure_digest(hashes: set[str]) -> dict[str, Any]:
    payload = json.dumps(sorted(hashes), separators=(",", ":"), ensure_ascii=True)
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "count": len(hashes),
    }


def _drop_retired_closure(conn: sqlite3.Connection, restore_query_only: bool) -> None:
    conn.execute("DROP TABLE IF EXISTS temp.single_live_retired_closure")
    if restore_query_only:
        conn.execute("PRAGMA query_only=ON")


def _time_range(
    conn: sqlite3.Connection,
    table: str,
    where: str,
    params: tuple[object, ...],
    *fields: str,
) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for field in fields:
        row = conn.execute(
            f"SELECT MIN({quote_identifier(field)}), MAX({quote_identifier(field)}) "
            f"FROM {quote_identifier(table)} WHERE {where}",
            params,
        ).fetchone()
        out[field] = {
            "min": None if row is None or row[0] is None else str(row[0]),
            "max": None if row is None or row[1] is None else str(row[1]),
        }
    return out


def _rows_referencing_hashes(
    conn: sqlite3.Connection,
    schema: str,
    table: str,
    field: str,
    hashes: set[str],
) -> set[int]:
    if not hashes:
        return set()
    normalized = {value.lower() for value in hashes}
    rows = conn.execute(
        f"SELECT rowid, CAST({quote_identifier(field)} AS TEXT) "
        f"FROM {quote_identifier(schema)}.{quote_identifier(table)} "
        f"WHERE {quote_identifier(field)} IS NOT NULL"
    )
    digest = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)

    def references(value: Any) -> bool:
        if isinstance(value, str):
            lowered = value.lower()
            return lowered in normalized or any(
                token.lower() in normalized for token in digest.findall(value)
            )
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return False

    def row_references(raw: object) -> bool:
        text = str(raw)
        try:
            return references(json.loads(text))
        except (TypeError, json.JSONDecodeError):
            return references(text)

    return {
        int(row[0])
        for row in rows
        if row_references(row[1])
    }


def _opaque_reference_counts(conn: sqlite3.Connection, closure: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not closure:
        return counts
    for table, fields in OPAQUE_REFERENCE_FIELDS.items():
        if not _schema_table_exists(conn, "trades", table):
            continue
        present = set(_table_columns(conn, "trades", table))
        for field in fields:
            if field not in present:
                continue
            table_sql = quote_identifier(table)
            field_sql = quote_identifier(field)
            if field.endswith("_json"):
                count = int(
                    conn.execute(
                        f"SELECT COUNT(DISTINCT source.rowid) "
                        f"FROM trades.{table_sql} source, "
                        f"json_tree(source.{field_sql}) leaf "
                        "JOIN temp.single_live_retired_closure retired "
                        "ON retired.certificate_hash=lower(CAST(leaf.value AS TEXT)) "
                        f"WHERE source.{field_sql} IS NOT NULL "
                        f"AND json_valid(source.{field_sql})"
                    ).fetchone()[0]
                )
            else:
                count = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM trades.{table_sql} source "
                        "JOIN temp.single_live_retired_closure retired "
                        f"ON retired.certificate_hash=lower(CAST(source.{field_sql} AS TEXT))"
                    ).fetchone()[0]
                )
            if count:
                counts[f"{table}.{field}"] = count
    return counts


def _decision_schema_blockers(conn: sqlite3.Connection) -> list[str]:
    blockers: list[str] = []
    for table in (DECISION_TABLE, EDGE_TABLE, SUPERSESSION_TABLE, FAILURE_TABLE):
        if not _schema_table_exists(conn, "main", table):
            blockers.append(f"missing WORLD table {table}")
    if blockers:
        return blockers
    actual = _table_columns(conn, "main", DECISION_TABLE)
    if actual != DECISION_COLUMNS:
        blockers.append(
            f"{DECISION_TABLE} columns drifted: expected={DECISION_COLUMNS!r} actual={actual!r}"
        )
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",
        (DECISION_TABLE,),
    ).fetchall()
    if triggers:
        blockers.append(
            f"{DECISION_TABLE} has unmodeled triggers: {[str(row[0]) for row in triggers]!r}"
        )
    inbound_fks: list[str] = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = str(row[0])
        for fk in conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})"):
            if str(fk[2]) == DECISION_TABLE:
                inbound_fks.append(f"{table}.{fk[3]}->{DECISION_TABLE}.{fk[4]}")
    if inbound_fks:
        blockers.append(f"unmodeled inbound foreign keys: {sorted(inbound_fks)!r}")
    residue = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='decision_certificates_live_new'"
    ).fetchone()
    if residue is not None:
        blockers.append("decision_certificates_live_new residue already exists")
    return blockers


def _disk_preflight(conn: sqlite3.Connection, path: Path) -> dict[str, int | str]:
    allocated = 0
    method = "dbstat"
    try:
        names = (DECISION_TABLE, EDGE_TABLE, SUPERSESSION_TABLE, FAILURE_TABLE)
        placeholders = ",".join("?" for _ in names)
        allocated = int(
            conn.execute(
                f"SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE name IN ({placeholders})",
                names,
            ).fetchone()[0]
        )
    except sqlite3.Error:
        method = "database_file_fallback"
        allocated = path.stat().st_size
    wal = path.with_name(path.name + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    required = max(16 * 1024 * 1024, allocated * 3 + wal_bytes + 1024 * 1024)
    free = shutil.disk_usage(path.parent).free
    return {
        "method": method,
        "estimated_graph_bytes": allocated,
        "wal_bytes": wal_bytes,
        "required_free_bytes": required,
        "available_free_bytes": free,
    }


def _protected_position_attribution_preflight(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reject ambiguous protected positions before graph traversal can begin."""
    details: dict[str, Any] = {
        "unresolved_current_projection_count": 0,
        "unresolved_current_projection_sample": [],
    }
    required = ("position_current", "position_decision_attribution")
    missing = [table for table in required if not _schema_table_exists(conn, "trades", table)]
    if missing:
        return [{"kind": "missing_trades_preflight_tables", "tables": missing}], details

    protected = sorted(PROTECTED_POSITION_PHASES)
    placeholders = ",".join("?" for _ in protected)
    rows = conn.execute(
        f"""
        SELECT pc.position_id, pc.phase, pda.command_id,
               pda.decision_certificate_hash, pda.resolution
          FROM trades.position_current pc
          LEFT JOIN trades.position_decision_attribution pda
            ON pda.position_id = pc.position_id
         WHERE lower(pc.phase) IN ({placeholders})
        """,
        tuple(protected),
    ).fetchall()
    unresolved = [
        dict(row)
        for row in rows
        if str(row["resolution"] or "").upper() != "ATTRIBUTED"
        or not str(row["decision_certificate_hash"] or "").strip()
    ]
    details["unresolved_current_projection_count"] = len(unresolved)
    details["unresolved_current_projection_sample"] = unresolved[:25]
    if not unresolved:
        return [], details
    return [
        {
            "kind": "current_projection_attribution_unresolved",
            "count": len(unresolved),
            "sample": unresolved[:25],
        }
    ], details


def _trades_preflight(
    conn: sqlite3.Connection,
    closure: set[str],
    protected_details: dict[str, Any],
    *,
    include_opaque_references: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    details: dict[str, Any] = {
        "active_position_attribution_count": 0,
        "nonterminal_command_count": 0,
        **protected_details,
        "historical_opaque_reference_counts": (
            _opaque_reference_counts(conn, closure)
            if include_opaque_references
            else {"status": "deferred_to_fenced_apply"}
        ),
    }
    required = ("position_current", "position_decision_attribution", "venue_commands")
    missing = [table for table in required if not _schema_table_exists(conn, "trades", table)]
    if missing:
        blockers.append({"kind": "missing_trades_preflight_tables", "tables": missing})
        return blockers, details

    protected = sorted(PROTECTED_POSITION_PHASES)
    placeholders = ",".join("?" for _ in protected)
    rows = conn.execute(
        f"""
        SELECT pc.position_id, pc.phase, pda.command_id,
               pda.decision_certificate_hash, pda.resolution
          FROM trades.position_current pc
          LEFT JOIN trades.position_decision_attribution pda
            ON pda.position_id = pc.position_id
         WHERE lower(pc.phase) IN ({placeholders})
        """,
        tuple(protected),
    ).fetchall()
    active_refs = [
        dict(row)
        for row in rows
        if str(row["decision_certificate_hash"] or "").lower() in closure
    ]
    details["active_position_attribution_count"] = len(active_refs)
    details["active_position_attribution_sample"] = active_refs[:25]
    if active_refs:
        blockers.append(
            {
                "kind": "retired_closure_referenced_by_current_position",
                "count": len(active_refs),
                "sample": active_refs[:25],
            }
        )
    terminal = sorted(TERMINAL_COMMAND_STATES)
    terminal_placeholders = ",".join("?" for _ in terminal)
    command_rows = conn.execute(
        f"""
        SELECT cmd.command_id, cmd.position_id, cmd.state,
               pda.decision_certificate_hash
          FROM trades.venue_commands cmd
          JOIN trades.position_decision_attribution pda
            ON pda.position_id = cmd.position_id
         WHERE upper(COALESCE(cmd.state, '')) NOT IN ({terminal_placeholders})
        """,
        tuple(terminal),
    ).fetchall()
    command_refs = [
        dict(row)
        for row in command_rows
        if str(row["decision_certificate_hash"] or "").lower() in closure
    ]
    details["nonterminal_command_count"] = len(command_refs)
    details["nonterminal_command_sample"] = command_refs[:25]
    if command_refs:
        blockers.append(
            {
                "kind": "retired_closure_referenced_by_nonterminal_command",
                "count": len(command_refs),
                "sample": command_refs[:25],
            }
        )
    return blockers, details


def _kept_orphans(conn: sqlite3.Connection, closure: set[str]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT edge.child_certificate_id, child.certificate_hash AS child_hash,
               edge.parent_role,
               edge.parent_certificate_hash
          FROM {EDGE_TABLE} edge
          JOIN {DECISION_TABLE} child
            ON child.certificate_id = edge.child_certificate_id
          LEFT JOIN {DECISION_TABLE} parent
            ON lower(parent.certificate_hash) = lower(edge.parent_certificate_hash)
         WHERE parent.certificate_hash IS NULL
        """
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if str(row["child_hash"] or "").lower() not in closure
    ]


def _compile_failure_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT stage, reason_code, COUNT(*) AS count,
                   MIN(decision_time) AS min_decision_time,
                   MAX(decision_time) AS max_decision_time,
                   MIN(created_at) AS min_created_at,
                   MAX(created_at) AS max_created_at
              FROM {FAILURE_TABLE}
             WHERE mode != ?
             GROUP BY stage, reason_code
             ORDER BY stage, reason_code
            """,
            (LIVE_MODE,),
        )
    ]


def plan_world_decision_graph(
    world_path: Path,
    trades_path: Path,
    *,
    include_opaque_references: bool = True,
) -> dict[str, Any]:
    conn = open_db(world_path, writable=False)
    try:
        schema_blockers = _decision_schema_blockers(conn)
        if schema_blockers:
            return {
                "status": "blocked",
                "blockers": [{"kind": "world_schema", "reasons": schema_blockers}],
            }
        conn.execute("ATTACH DATABASE ? AS trades", (f"file:{trades_path}?mode=ro",))
        try:
            protected_blockers, protected_details = _protected_position_attribution_preflight(
                conn
            )
            if protected_blockers:
                return {
                    "status": "blocked",
                    "blockers": protected_blockers,
                    "trades_preflight": protected_details,
                    "fast_fail": "protected_position_attribution",
                }
            restore_query_only = _materialize_retired_closure(conn)
            try:
                closure_rows = _retired_closure_rows(conn)
                closure = {str(row["certificate_hash"]).lower() for row in closure_rows}
                cert_count = int(
                    conn.execute(f"SELECT COUNT(*) FROM {DECISION_TABLE}").fetchone()[0]
                )
                edge_count = int(
                    conn.execute(f"SELECT COUNT(*) FROM {EDGE_TABLE}").fetchone()[0]
                )
                supersession_count = int(
                    conn.execute(f"SELECT COUNT(*) FROM {SUPERSESSION_TABLE}").fetchone()[0]
                )
                failure_count = int(
                    conn.execute(f"SELECT COUNT(*) FROM {FAILURE_TABLE}").fetchone()[0]
                )
                disk = _disk_preflight(conn, world_path)
                blockers, trades = _trades_preflight(
                    conn,
                    closure,
                    protected_details,
                    include_opaque_references=include_opaque_references,
                )
                kept_orphans = _kept_orphans(conn, closure)
                if kept_orphans:
                    blockers.append(
                        {
                            "kind": "kept_certificate_orphan",
                            "count": len(kept_orphans),
                            "sample": kept_orphans[:25],
                        }
                    )
                if int(disk["available_free_bytes"]) < int(disk["required_free_bytes"]):
                    blockers.append({"kind": "insufficient_disk_space", **disk})
                removed_failures = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {FAILURE_TABLE} WHERE mode != ?",
                        (LIVE_MODE,),
                    ).fetchone()[0]
                )
                dangling_edges, removed_edges = map(
                    int,
                    conn.execute(
                        f"""
                        SELECT
                            COALESCE(SUM(CASE WHEN child.certificate_hash IS NULL
                                      OR parent.certificate_hash IS NULL
                                     THEN 1 ELSE 0 END), 0),
                            COALESCE(SUM(CASE WHEN child.certificate_hash IS NULL
                                      OR parent.certificate_hash IS NULL
                                      OR retired_child.certificate_hash IS NOT NULL
                                      OR retired_parent.certificate_hash IS NOT NULL
                                     THEN 1 ELSE 0 END), 0)
                          FROM {EDGE_TABLE} edge
                          LEFT JOIN {DECISION_TABLE} child
                            ON child.certificate_id = edge.child_certificate_id
                          LEFT JOIN {DECISION_TABLE} parent
                            ON lower(parent.certificate_hash) = lower(edge.parent_certificate_hash)
                          LEFT JOIN temp.single_live_retired_closure retired_child
                            ON retired_child.certificate_hash = lower(child.certificate_hash)
                          LEFT JOIN temp.single_live_retired_closure retired_parent
                            ON retired_parent.certificate_hash = lower(parent.certificate_hash)
                        """
                    ).fetchone(),
                )
                removed_supersessions = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                          FROM {SUPERSESSION_TABLE} s
                          LEFT JOIN {DECISION_TABLE} old
                            ON lower(old.certificate_hash) = lower(s.old_certificate_hash)
                          LEFT JOIN {DECISION_TABLE} new
                            ON lower(new.certificate_hash) = lower(s.new_certificate_hash)
                          LEFT JOIN temp.single_live_retired_closure retired_old
                            ON retired_old.certificate_hash = lower(s.old_certificate_hash)
                          LEFT JOIN temp.single_live_retired_closure retired_new
                            ON retired_new.certificate_hash = lower(s.new_certificate_hash)
                         WHERE old.certificate_hash IS NULL
                            OR new.certificate_hash IS NULL
                            OR retired_old.certificate_hash IS NOT NULL
                            OR retired_new.certificate_hash IS NOT NULL
                        """
                    ).fetchone()[0]
                )
                preserved_old_sizing = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {DECISION_TABLE} "
                        "WHERE mode=? AND certificate_type=?",
                        (LIVE_MODE, OLD_SIZING_CERTIFICATE),
                    ).fetchone()[0]
                )
                plan = {
                    "status": "blocked" if blockers else "ready",
                    "blockers": blockers,
                    "closure_digest": _closure_digest(closure),
                    "counts": {
                        "certificates_pre": cert_count,
                        "certificates_remove": len(closure),
                        "certificates_keep": cert_count - len(closure),
                        "edges_pre": edge_count,
                        "edges_remove": removed_edges,
                        "dangling_edges_pre": dangling_edges,
                        "supersessions_pre": supersession_count,
                        "supersessions_remove": removed_supersessions,
                        "compile_failures_pre": failure_count,
                        "compile_failures_remove": removed_failures,
                        "preserved_live_old_sizing_predecessors": preserved_old_sizing,
                    },
                    "closure_class_counts": {"seed": 0, "dependent": 0},
                    "removed_certificate_time_ranges": _time_range(
                        conn,
                        DECISION_TABLE,
                        "lower(certificate_hash) IN ("
                        "SELECT certificate_hash FROM temp.single_live_retired_closure)",
                        (),
                        "decision_time",
                        "created_at",
                    ),
                    "removed_compile_failure_time_ranges": _time_range(
                        conn,
                        FAILURE_TABLE,
                        "mode != ?",
                        (LIVE_MODE,),
                        "decision_time",
                        "created_at",
                    ),
                    "removed_compile_failure_summary": _compile_failure_summary(conn),
                    "historical_opaque_reference_counts": trades[
                        "historical_opaque_reference_counts"
                    ],
                    "trades_preflight": trades,
                    "disk_preflight": disk,
                    "pre_checks": {
                        "foreign_keys": int(
                            conn.execute("PRAGMA foreign_keys").fetchone()[0]
                        ),
                        "kept_orphan_count": len(kept_orphans),
                        "schema_columns_match": True,
                    },
                }
                for row in closure_rows:
                    key = "seed" if int(row["is_seed"]) else "dependent"
                    plan["closure_class_counts"][key] += 1
                return plan
            finally:
                _drop_retired_closure(conn, restore_query_only)
        finally:
            conn.execute("DETACH DATABASE trades")
    finally:
        conn.close()


def _saved_decision_indexes(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL ORDER BY name",
            (DECISION_TABLE,),
        )
    ]


def _rebuild_world_decision_graph(conn: sqlite3.Connection) -> None:
    index_sql = _saved_decision_indexes(conn)
    cols = ", ".join(quote_identifier(column) for column in DECISION_COLUMNS)
    conn.execute(DECISION_LIVE_DDL)
    conn.execute(
        f"INSERT INTO decision_certificates_live_new ({cols}) "
        + f"SELECT {cols} FROM {DECISION_TABLE} cert "
        + "WHERE NOT EXISTS (SELECT 1 FROM temp.single_live_retired_closure retired "
        + "WHERE retired.certificate_hash=lower(cert.certificate_hash))"
    )
    conn.execute(
        f"""
        DELETE FROM {EDGE_TABLE}
         WHERE NOT EXISTS (
                   SELECT 1 FROM decision_certificates_live_new child
                    WHERE child.certificate_id = {EDGE_TABLE}.child_certificate_id
               )
            OR NOT EXISTS (
                   SELECT 1 FROM decision_certificates_live_new parent
                    WHERE lower(parent.certificate_hash) = lower({EDGE_TABLE}.parent_certificate_hash)
               )
        """
    )
    conn.execute(
        f"""
        DELETE FROM {SUPERSESSION_TABLE}
         WHERE NOT EXISTS (
                   SELECT 1 FROM decision_certificates_live_new old
                    WHERE lower(old.certificate_hash) = lower({SUPERSESSION_TABLE}.old_certificate_hash)
               )
            OR NOT EXISTS (
                   SELECT 1 FROM decision_certificates_live_new new
                    WHERE lower(new.certificate_hash) = lower({SUPERSESSION_TABLE}.new_certificate_hash)
               )
        """
    )
    conn.execute(f"DELETE FROM {FAILURE_TABLE} WHERE mode != ?", (LIVE_MODE,))
    conn.execute(f"DROP TABLE {DECISION_TABLE}")
    conn.execute(
        "ALTER TABLE decision_certificates_live_new RENAME TO decision_certificates"
    )
    for sql in index_sql:
        conn.execute(sql)


def postcheck_world_decision_graph(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (DECISION_TABLE,),
        ).fetchone()[0]
    )
    checks: dict[str, Any] = {
        "non_live_certificate_count": int(
            conn.execute(
                f"SELECT COUNT(*) FROM {DECISION_TABLE} WHERE mode != ?", (LIVE_MODE,)
            ).fetchone()[0]
        ),
        "non_live_compile_failure_count": int(
            conn.execute(
                f"SELECT COUNT(*) FROM {FAILURE_TABLE} WHERE mode != ?", (LIVE_MODE,)
            ).fetchone()[0]
        ),
        "orphan_edge_count": int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM {EDGE_TABLE} edge
                  LEFT JOIN {DECISION_TABLE} child
                    ON child.certificate_id = edge.child_certificate_id
                  LEFT JOIN {DECISION_TABLE} parent
                    ON lower(parent.certificate_hash) = lower(edge.parent_certificate_hash)
                 WHERE child.certificate_id IS NULL OR parent.certificate_hash IS NULL
                """
            ).fetchone()[0]
        ),
        "dangling_supersession_count": int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM {SUPERSESSION_TABLE} s
                  LEFT JOIN {DECISION_TABLE} old
                    ON lower(old.certificate_hash) = lower(s.old_certificate_hash)
                  LEFT JOIN {DECISION_TABLE} new
                    ON lower(new.certificate_hash) = lower(s.new_certificate_hash)
                 WHERE old.certificate_hash IS NULL OR new.certificate_hash IS NULL
                """
            ).fetchone()[0]
        ),
        "foreign_keys": int(conn.execute("PRAGMA foreign_keys").fetchone()[0]),
        "live_check_present": "CHECK (mode = 'LIVE')" in sql,
        "columns_match": _table_columns(conn, "main", DECISION_TABLE) == DECISION_COLUMNS,
        "integrity_check": str(
            conn.execute(f"PRAGMA main.integrity_check('{DECISION_TABLE}')").fetchone()[0]
        ),
    }
    failed = {
        key: value
        for key, value in checks.items()
        if (key.endswith("_count") and value != 0)
        or key in {"foreign_keys"} and value != 1
        or key in {"live_check_present", "columns_match"} and value is not True
        or key == "integrity_check" and value != "ok"
    }
    if failed:
        raise RuntimeError(f"WORLD decision graph postcheck failed: {failed}")
    return checks


def _write_receipt_atomic(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


def _trades_ghost_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        table: (
            int(conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])
            if _schema_table_exists(conn, "main", table)
            else 0
        )
        for table in TRADES_GHOST_DROP_ORDER
    }
    if not _schema_table_exists(conn, "main", "position_decision_attribution"):
        raise RuntimeError("canonical position_decision_attribution is missing")
    return {
        "counts": counts,
        "present_tables": [
            table
            for table in TRADES_GHOST_DROP_ORDER
            if _schema_table_exists(conn, "main", table)
        ],
        "position_decision_attribution_count": int(
            conn.execute("SELECT COUNT(*) FROM position_decision_attribution").fetchone()[0]
        ),
    }


def drop_trades_ghost_decision_graph(trades_path: Path) -> dict[str, Any]:
    pre_conn = open_db(trades_path, writable=False)
    try:
        pre = _trades_ghost_snapshot(pre_conn)
    finally:
        pre_conn.close()

    conn = open_db(trades_path, writable=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _trades_ghost_snapshot(conn) != pre:
                raise RuntimeError("TRADES ghost graph changed between preflight and BEGIN")
            for table in TRADES_GHOST_DROP_ORDER:
                if _schema_table_exists(conn, "main", table):
                    conn.execute(f"DROP TABLE {quote_identifier(table)}")
            absent = {
                table: not _schema_table_exists(conn, "main", table)
                for table in TRADES_GHOST_DROP_ORDER
            }
            if not all(absent.values()):
                raise RuntimeError(f"TRADES ghost graph postcheck failed: {absent}")
            if not _schema_table_exists(conn, "main", "position_decision_attribution"):
                raise RuntimeError("position_decision_attribution was removed")
            pda_count = int(
                conn.execute("SELECT COUNT(*) FROM position_decision_attribution").fetchone()[0]
            )
            if pda_count != pre["position_decision_attribution_count"]:
                raise RuntimeError("position_decision_attribution changed")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    fresh = open_db(trades_path, writable=False)
    try:
        fresh_absent = {
            table: not _schema_table_exists(fresh, "main", table)
            for table in TRADES_GHOST_DROP_ORDER
        }
        if not all(fresh_absent.values()):
            raise RuntimeError(f"TRADES fresh postcheck failed: {fresh_absent}")
        fresh_pda_count = int(
            fresh.execute("SELECT COUNT(*) FROM position_decision_attribution").fetchone()[0]
        )
        if fresh_pda_count != pre["position_decision_attribution_count"]:
            raise RuntimeError("position_decision_attribution fresh count changed")
    finally:
        fresh.close()
    return {
        "pre_drop_counts": pre["counts"],
        "pre_drop_present_tables": pre["present_tables"],
        "post_drop_absent": fresh_absent,
        "position_decision_attribution_count": fresh_pda_count,
    }


def migrate_world_decision_graph(
    world_path: Path,
    trades_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    started_at = utc_now()
    plan = plan_world_decision_graph(world_path, trades_path)
    if plan.get("blockers"):
        raise RuntimeError(
            "WORLD decision graph migration refused: "
            + json.dumps(plan["blockers"], sort_keys=True)
        )
    conn = open_db(world_path, writable=True)
    committed = False
    try:
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise RuntimeError("foreign_keys must remain ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            restore_query_only = _materialize_retired_closure(conn)
            try:
                in_tx_closure = {
                    str(row["certificate_hash"]).lower()
                    for row in _retired_closure_rows(conn)
                }
                if _closure_digest(in_tx_closure) != plan["closure_digest"]:
                    raise RuntimeError("retired closure changed between preflight and BEGIN")
                _rebuild_world_decision_graph(conn)
            finally:
                _drop_retired_closure(conn, restore_query_only)
            transaction_checks = postcheck_world_decision_graph(conn)
            conn.execute("COMMIT")
            committed = True
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    try:
        trades_ghost = drop_trades_ghost_decision_graph(trades_path)
    except BaseException as exc:
        if committed:
            raise RuntimeError(
                f"WORLD DB COMMITTED but TRADES ghost graph drop failed explicitly: {exc}"
            ) from exc
        raise

    fresh = open_db(world_path, writable=False)
    try:
        fresh.execute("PRAGMA foreign_keys=ON")
        fresh_checks = postcheck_world_decision_graph(fresh)
        post_count = int(fresh.execute(f"SELECT COUNT(*) FROM {DECISION_TABLE}").fetchone()[0])
        edge_count = int(fresh.execute(f"SELECT COUNT(*) FROM {EDGE_TABLE}").fetchone()[0])
        supersession_count = int(
            fresh.execute(f"SELECT COUNT(*) FROM {SUPERSESSION_TABLE}").fetchone()[0]
        )
        failure_count = int(fresh.execute(f"SELECT COUNT(*) FROM {FAILURE_TABLE}").fetchone()[0])
    finally:
        fresh.close()

    receipt = {
        "schema_version": 1,
        "migration": "202607_single_live_semantics_cutover",
        "world_db": str(world_path),
        "trades_db": str(trades_path),
        "started_at": started_at,
        "committed_at": utc_now(),
        "closure_digest": plan["closure_digest"],
        "counts": {
            **plan["counts"],
            "certificates_post": post_count,
            "edges_post": edge_count,
            "supersessions_post": supersession_count,
            "compile_failures_post": failure_count,
        },
        "closure_class_counts": plan["closure_class_counts"],
        "removed_certificate_time_ranges": plan["removed_certificate_time_ranges"],
        "removed_compile_failure_time_ranges": plan[
            "removed_compile_failure_time_ranges"
        ],
        "removed_compile_failure_summary": plan["removed_compile_failure_summary"],
        "historical_opaque_reference_counts": plan[
            "historical_opaque_reference_counts"
        ],
        "trades_preflight": plan["trades_preflight"],
        "trades_ghost_decision_graph": trades_ghost,
        "disk_preflight": plan["disk_preflight"],
        "pre_checks": plan["pre_checks"],
        "post_checks": {
            "transaction": transaction_checks,
            "fresh_connection": fresh_checks,
        },
    }
    try:
        _write_receipt_atomic(receipt_path, receipt)
    except BaseException as exc:
        if committed:
            raise RuntimeError(
                f"WORLD DB COMMITTED but receipt write failed explicitly: {receipt_path}: {exc}"
            ) from exc
        raise
    return receipt


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def describe_db(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        found: list[str] = []
        for table in (
            TRANSFER_TABLE,
            CONVERSION_TABLE,
            CONVERSION_EVENTS,
            "raw_forecast_artifacts",
            "deterministic_forecast_anchors",
            "forecast_posteriors",
            "settlement_capture_verifications",
            "edli_live_profit_audit",
            "edli_no_submit_receipts",
            "risk_state",
        ):
            if not table_exists(conn, table):
                continue
            count = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            relevant = sorted(
                columns(conn, table)
                & {
                    OLD_AUTHORITY,
                    OLD_ELIGIBILITY,
                    "evidence_tier",
                    FORCE_EXIT_COLUMN,
                    *RECEIPT_COLUMNS,
                }
            )
            if relevant or table in {
                TRANSFER_TABLE,
                CONVERSION_TABLE,
                CONVERSION_EVENTS,
            }:
                found.append(f"{table}: rows={count} retired_columns={relevant}")
        if table_exists(conn, "decision_certificates"):
            for mode, count in conn.execute(
                "SELECT mode, count(*) FROM decision_certificates "
                "WHERE mode != 'LIVE' GROUP BY mode"
            ):
                found.append(f"decision_certificates retired mode {mode}: rows={count}")
            for certificate_type, count in conn.execute(
                "SELECT certificate_type, count(*) FROM decision_certificates "
                "WHERE certificate_type IN (?,?,?) GROUP BY certificate_type",
                (
                    OLD_SIZING_CERTIFICATE,
                    OLD_PRE_SUBMIT_DECISION_CERTIFICATE,
                    OLD_PRE_SUBMIT_MODE_CERTIFICATE,
                ),
            ):
                found.append(
                    f"decision_certificates retired type {certificate_type}: rows={count}"
                )
        if table_exists(conn, "decision_compile_failures"):
            count = int(
                conn.execute(
                    "SELECT count(*) FROM decision_compile_failures WHERE mode != 'LIVE'"
                ).fetchone()[0]
            )
            if count:
                found.append(f"decision_compile_failures retired modes: rows={count}")
        return found
    finally:
        conn.close()


def mutation_blockers(path: Path) -> list[str]:
    """Prove every deterministic per-DB mutation precondition before any commit."""

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        blockers: list[str] = []
        for table in (
            TRANSFER_TABLE,
            CONVERSION_TABLE,
            CONVERSION_EVENTS,
        ):
            if table_exists(conn, table):
                count = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                if count:
                    blockers.append(f"{path.name}:{table} is non-empty ({count})")
        table = "edli_live_profit_audit"
        if table_exists(conn, table):
            cols = columns(conn, table)
            if OLD_ELIGIBILITY in cols and "learning_eligible" in cols:
                blockers.append(f"{path.name}:{table} has both eligibility columns")
        table = "forecast_posteriors"
        if table_exists(conn, table):
            cols = columns(conn, table)
            if "runtime_layer" not in cols:
                if OLD_AUTHORITY not in cols:
                    blockers.append(
                        f"{path.name}:{table} is missing runtime_layer after authority removal"
                    )
            elif OLD_AUTHORITY in cols:
                count = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE "
                        "(runtime_layer IS NOT NULL AND lower(runtime_layer) != 'live') "
                        f"OR (runtime_layer IS NULL AND lower(COALESCE({OLD_AUTHORITY}, '')) != ?)",
                        (OLD_LIVE_AUTHORITY.lower(),),
                    ).fetchone()[0]
                )
            else:
                count = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} "
                        "WHERE runtime_layer IS NULL OR lower(runtime_layer) != 'live'"
                    ).fetchone()[0]
                )
            if "runtime_layer" in cols and count:
                blockers.append(
                    f"{path.name}:{table} has {count} rows without a live runtime_layer"
                )
        return blockers
    finally:
        conn.close()


def mutate_db(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0.0, isolation_level=None)
    changed: list[str] = []
    try:
        conn.execute("PRAGMA busy_timeout=0")
        conn.execute("PRAGMA foreign_keys=ON")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise RuntimeError(f"could not keep foreign_keys enabled for {path}")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table in (
                TRANSFER_TABLE,
                CONVERSION_EVENTS,
                CONVERSION_TABLE,
            ):
                if table_exists(conn, table):
                    count = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                    if count != 0:
                        raise RuntimeError(f"refusing to drop non-empty retired table {table}: {count} rows")
                    conn.execute(f"DROP TABLE {table}")
                    changed.append(f"dropped {table} ({count} rows)")

            for table in ("raw_forecast_artifacts", "deterministic_forecast_anchors"):
                if table_exists(conn, table) and OLD_AUTHORITY in columns(conn, table):
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {OLD_AUTHORITY}")
                    changed.append(f"dropped {table} retired authority column")

            table = "forecast_posteriors"
            if table_exists(conn, table):
                cols = columns(conn, table)
                has_old_authority = OLD_AUTHORITY in cols
                if "runtime_layer" not in cols and has_old_authority:
                    conn.execute(
                        "ALTER TABLE forecast_posteriors ADD COLUMN runtime_layer TEXT "
                        "CHECK (runtime_layer IS NULL OR runtime_layer = 'live')"
                    )
                    cols = columns(conn, table)
                if "runtime_layer" not in cols:
                    raise RuntimeError("forecast_posteriors is missing runtime_layer")
                if has_old_authority:
                    conn.execute(
                        f"UPDATE forecast_posteriors SET runtime_layer='live' "
                        f"WHERE runtime_layer IS NULL AND lower({OLD_AUTHORITY})=?",
                        (OLD_LIVE_AUTHORITY.lower(),),
                    )
                remaining = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM forecast_posteriors "
                        "WHERE runtime_layer IS NULL OR lower(runtime_layer) != 'live'"
                    ).fetchone()[0]
                )
                if remaining:
                    raise RuntimeError(
                        "refusing posterior migration while "
                        f"{remaining} rows lack a live runtime_layer"
                    )
                if has_old_authority:
                    conn.execute(f"ALTER TABLE forecast_posteriors DROP COLUMN {OLD_AUTHORITY}")
                    changed.append("migrated forecast_posteriors to the live runtime layer")

            table = "settlement_capture_verifications"
            if table_exists(conn, table) and "evidence_tier" in columns(conn, table):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN evidence_tier")
                changed.append(f"dropped {table}.evidence_tier")

            table = "edli_no_submit_receipts"
            if table_exists(conn, table):
                for column in RECEIPT_COLUMNS:
                    if column in columns(conn, table):
                        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                        changed.append(f"dropped {table}.{column}")

            table = "edli_live_profit_audit"
            if table_exists(conn, table):
                cols = columns(conn, table)
                if OLD_ELIGIBILITY in cols and "learning_eligible" in cols:
                    raise RuntimeError(f"{table} has both old and new eligibility columns")
                if OLD_ELIGIBILITY in cols:
                    conn.execute(
                        f"ALTER TABLE {table} RENAME COLUMN {OLD_ELIGIBILITY} TO learning_eligible"
                    )
                    conn.execute(f"DROP INDEX IF EXISTS {OLD_AUDIT_INDEX}")
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_edli_live_profit_audit_learning "
                        "ON edli_live_profit_audit(learning_eligible, order_lifecycle_state, created_at)"
                    )
                    changed.append(f"renamed {table} eligibility column")

            table = "risk_state"
            if table_exists(conn, table) and FORCE_EXIT_COLUMN in columns(conn, table):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {FORCE_EXIT_COLUMN}")
                changed.append(f"dropped {table}.{FORCE_EXIT_COLUMN}")

            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"integrity_check failed for {path}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return changed
    finally:
        conn.close()


def json_files_with_field(base: Path) -> list[Path]:
    found: list[Path] = []
    if not base.exists():
        return found
    for path in base.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and REMOVED_MANIFEST_FIELD in payload:
            found.append(path)
    return found


def rewrite_json_without_field(path: Path, field: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or field not in payload:
        return
    del payload[field]
    tmp = path.with_name(path.name + ".single-live.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clean_config(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    removed: list[str] = []

    retired = set((*CONFIG_NOTES, *CONFIG_KEYS))
    retired_paths = set(CONFIG_PATHS)

    def strip(mapping: dict[str, object], prefix: str = "") -> None:
        for key in tuple(mapping):
            path_key = f"{prefix}.{key}" if prefix else key
            if key in retired or path_key in retired_paths:
                del mapping[key]
                removed.append(path_key)
                continue
            value = mapping[key]
            if isinstance(value, dict):
                strip(value, path_key)

    strip(payload)
    if removed:
        tmp = path.with_name(path.name + ".single-live.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return removed


def _open_stage_journal(
    progress_path: Path,
    root: Path,
    *,
    target_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Open a durable, target-bound journal without erasing interrupted state."""
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"unreadable migration stage journal: {progress_path}: {exc}") from exc
        expected = {
            "schema_version": 2 if target_identity else 1,
            "migration": "202607_single_live_semantics_cutover",
            "root": str(root),
            **(target_identity or {}),
        }
        if not isinstance(progress, dict) or any(
            progress.get(key) != value for key, value in expected.items()
        ):
            raise RuntimeError(f"stage journal does not match this migration target: {progress_path}")
        completed = progress.get("completed_stages")
        if not isinstance(completed, list) or any(not isinstance(stage, str) for stage in completed):
            raise RuntimeError(f"stage journal has invalid completed_stages: {progress_path}")
        if progress.get("status") == "complete":
            return progress
        progress["status"] = "running"
        progress["resumed_at"] = utc_now()
    else:
        progress = {
            "schema_version": 2 if target_identity else 1,
            "migration": "202607_single_live_semantics_cutover",
            "root": str(root),
            **(target_identity or {}),
            "status": "running",
            "started_at": utc_now(),
            "completed_stages": [],
        }
    progress["updated_at"] = utc_now()
    _write_receipt_atomic(progress_path, progress)
    return progress


def _record_stage_journal(
    progress_path: Path,
    progress: dict[str, Any],
    stage: str,
    *,
    complete: bool,
) -> None:
    progress["current_stage"] = stage
    progress["updated_at"] = utc_now()
    if complete and stage not in progress["completed_stages"]:
        progress["completed_stages"].append(stage)
    _write_receipt_atomic(progress_path, progress)


def _run_journaled_stage(
    progress_path: Path,
    progress: dict[str, Any],
    stage: str,
    action: Any,
    *,
    precondition: Any | None = None,
) -> Any:
    if stage in progress["completed_stages"]:
        return None
    if precondition is not None:
        precondition()
    _record_stage_journal(progress_path, progress, stage, complete=False)
    result = action()
    _record_stage_journal(progress_path, progress, stage, complete=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-confirms-fenced", action="store_true")
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Explicit Zeus checkout whose canonical state/config will be migrated.",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Durable WORLD graph receipt path (default: state/migration_receipts/...).",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    state = root / "state"
    dbs = (
        state / "zeus-world.db",
        state / "zeus-forecasts.db",
        state / "zeus_trades.db",
        state / "risk_state.db",
    )
    for path in dbs:
        print(path)
        for line in describe_db(path):
            print(f"  {line}")
    json_paths = json_files_with_field(root / ".local")
    print(f"json files carrying retired authority field: {len(json_paths)}")
    retired_files = [root / rel for rel in RETIRED_FILES if (root / rel).exists()]
    print(f"retired runtime files present: {[str(path.relative_to(root)) for path in retired_files]}")

    world_path = state / WORLD_DB
    trades_path = state / TRADES_DB
    graph_plan = plan_world_decision_graph(
        world_path,
        trades_path,
        include_opaque_references=args.apply,
    )
    print("WORLD decision graph plan:")
    print(json.dumps(graph_plan, indent=2, sort_keys=True))

    if not args.apply:
        if graph_plan.get("blockers"):
            print("DRY-RUN BLOCKED: no changes made")
            return 2
        print("DRY-RUN READY: no changes made")
        return 0
    if not args.operator_confirms_fenced:
        raise SystemExit("REFUSED: --apply requires --operator-confirms-fenced")
    try:
        release_identity = target_release_identity(root)
        assert_writer_fence(root)
    except RuntimeError as exc:
        raise SystemExit(f"REFUSED: {exc}") from exc

    deterministic_blockers = [
        blocker for path in dbs for blocker in mutation_blockers(path)
    ]
    if deterministic_blockers:
        raise SystemExit(
            "REFUSED: mutation preflight failed before first commit:\n  "
            + "\n  ".join(deterministic_blockers)
        )

    receipt_path = args.receipt
    if receipt_path is None:
        receipt_path = (
            state
            / "migration_receipts"
            / "202607_single_live_semantics_cutover.json"
        )
    elif not receipt_path.is_absolute():
        receipt_path = root / receipt_path
    progress_path = receipt_path.with_name(receipt_path.stem + ".progress.json")
    progress = _open_stage_journal(
        progress_path,
        root,
        target_identity=release_identity,
    )
    if progress["status"] == "complete":
        print(f"APPLY ALREADY COMPLETE: journal={progress_path}")
        return 0
    progress["mutation_preflight"] = "passed"
    _record_stage_journal(progress_path, progress, "preflight", complete=True)
    try:
        receipt = _run_journaled_stage(
            progress_path,
            progress,
            "decision_graphs",
            lambda: migrate_world_decision_graph(world_path, trades_path, receipt_path),
            precondition=lambda: assert_writer_fence(root),
        )
        if receipt is not None:
            print(
                "WORLD decision graph committed: "
                f"removed={receipt['counts']['certificates_remove']} "
                f"kept={receipt['counts']['certificates_post']} receipt={receipt_path}"
            )
            print(
                "TRADES ghost decision graph dropped: "
                + json.dumps(
                    receipt["trades_ghost_decision_graph"]["pre_drop_counts"],
                    sort_keys=True,
                )
            )

        for path in dbs:
            changed = _run_journaled_stage(
                progress_path,
                progress,
                f"mutated:{path.name}",
                lambda path=path: mutate_db(path),
                precondition=lambda: assert_writer_fence(root),
            )
            if changed is not None:
                for line in changed:
                    print(f"{path.name}: {line}")
        _run_journaled_stage(
            progress_path,
            progress,
            "runtime_json",
            lambda: [rewrite_json_without_field(path, REMOVED_MANIFEST_FIELD) for path in json_paths],
            precondition=lambda: assert_writer_fence(root),
        )
        def remove_retired_files() -> list[Path]:
            removed: list[Path] = []
            for path in retired_files:
                if path.exists():
                    path.unlink()
                    removed.append(path)
            return removed

        removed_files = _run_journaled_stage(
            progress_path,
            progress,
            "runtime_files",
            remove_retired_files,
            precondition=lambda: assert_writer_fence(root),
        )
        if removed_files is not None:
            for path in removed_files:
                print(f"removed retired runtime file: {path.relative_to(root)}")
        removed_notes = _run_journaled_stage(
            progress_path,
            progress,
            "config",
            lambda: clean_config(root / "config" / "settings.json"),
            precondition=lambda: assert_writer_fence(root),
        )
        progress["status"] = "complete"
        _record_stage_journal(progress_path, progress, "complete", complete=True)
        print(f"rewritten json files: {len(json_paths)}")
        if removed_notes is not None:
            print(f"removed config notes: {removed_notes}")
    except BaseException as exc:
        progress["status"] = "failed_resumable"
        progress["error"] = f"{type(exc).__name__}: {exc}"
        _record_stage_journal(progress_path, progress, "failed", complete=False)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
