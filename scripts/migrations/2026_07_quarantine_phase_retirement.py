#!/usr/bin/env python3
# Lifecycle: created=2026-07-12; last_reviewed=2026-07-12; last_reused=2026-07-12
# Purpose: T5 quarantine phase retirement — offline RED-cutover migration (BLOCKER-2
#   protocol). Rewrites legacy 'quarantined'/'QUARANTINED'/'CHAIN_QUARANTINED' literals
#   out of zeus_trades.db (+ the position_lots ghost shell on zeus-world.db) and drops
#   the literals from every carrying CHECK constraint, in ONE crash-safe transaction.
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-2 (offline RED cutover protocol) + conductor log "T5-CORE: LANDED 05a751290"
#   (T5 MIGRATION mapping — the exact value remap this script implements).
"""T5 quarantine phase retirement — offline RED-cutover migration (BLOCKER-2).

T5-CORE (already landed, 05a751290) retired the QUARANTINED/QUARANTINE_EXPIRED/
ENTRY_AUTHORITY_QUARANTINED members from the three lifecycle enums in CODE
(``PositionPhase``, ``LifecycleState``, ``ChainState``) and installed a mixed-epoch
load-time bridge in ``src.state.portfolio`` so a legacy row still carrying one of
these literals on disk does not crash Position construction. This script is the
DATA + SCHEMA half of that migration: it physically rewrites every legacy row and
drops the literal from every SQL CHECK constraint that still names it, so the
load-time bridge can retire in a follow-up packet.

WHY THIS CANNOT RUN THROUGH THE SHARED scripts/migrations RUNNER
------------------------------------------------------------------
``scripts/migrations/__main__.py`` opens ONE canonical DB via ``src.state.db``'s
normal connection factory (``_connect()``), which RE-ENABLES WAL on every open.
BLOCKER-2 (docs/rebuild/quarantine_excision_2026-07-11.md) established that a
three-DB table rebuild is NOT crash-atomic under WAL — an attached-DB commit is
only atomic *per file* under WAL, not across the ATTACH set. This migration
therefore:

  1. requires the writer plane FENCED (no daemon may write any of the three DBs
     while it runs);
  2. WAL-checkpoints + truncates all three DBs first;
  3. takes a synchronized, integrity-verified 3-DB backup set;
  4. opens its OWN dedicated, NON-WAL (``journal_mode=DELETE``) connection —
     never ``src.state.db._connect()`` — and ATTACHes the other two DBs;
  5. does ALL work in ONE transaction on that single connection, so a SIGKILL at
     any point before COMMIT leaves the rollback journal to restore the exact
     pre-migration state on next open, and a SIGKILL after COMMIT leaves the DBs
     fully migrated — never a mixed state;
  6. stamps an identical ``schema_epoch`` on all three DBs in the SAME
     transaction, which ``src.state.db.assert_schema_epoch_not_mixed`` (the
     startup guard, deliverable B of this packet) refuses to boot past if it
     ever disagrees across the three files.

``up(conn)``/``down(conn)`` below exist ONLY so this file's ``2*.py`` name does not
silently corrupt a run of ``python -m scripts.migrations apply`` (which globs
``2*.py`` and would otherwise try to load it): they refuse immediately with a
pointer to the real entry point, ``main()`` below.

VALUE MAPPING (conductor log, "T5 MIGRATION mapping")
-------------------------------------------------------
- ``position_current.phase = 'quarantined'`` -> the position's TRUE current phase
  (REPLACEMENT PHASE LAW, docs/rebuild item T5): ``'active'``, or ``'pending_exit'``
  when the position's own most recent ``position_events`` row is an open exit
  attempt (see ``_infer_true_phase``). The dispute itself moves to an OPEN
  ``review_work_items`` row with ``reason_code='LEGACY_QUARANTINE_MIGRATED'`` —
  never a phase string (I-1/BLOCKER-2 REPLACEMENT PHASE LAW).
- ``position_current.chain_state`` in ``{quarantined, quarantine_expired,
  entry_authority_quarantined}`` -> ``'synced'`` (matches
  ``src.state.portfolio._normalize_runtime_chain_state``'s load-time bridge
  target exactly; plain column, no CHECK, so this is a bare UPDATE).
- ``position_events.phase_before`` / ``phase_after`` = ``'quarantined'`` (historical
  breadcrumbs) -> ``'active'`` (event strings are data, not enum-parsed at rest;
  a uniform, deterministic target keeps the rebuild helper generic/testable).
- ``position_events.event_type`` = ``'CHAIN_QUARANTINED'`` -> ``'REVIEW_REQUIRED'``
  (already a valid event type naming exactly "this needs review", matching
  REPLACEMENT PHASE LAW's own language).
- ``position_lots.state`` = ``'QUARANTINED'`` (world ghost shell + trade
  authoritative copy; 0 live rows per census 2026-07-11, defensive path) ->
  ``'CONFIRMED_EXPOSURE'`` with a loud ERROR log (real exposure stays visible,
  never silently dropped).

EXPLICITLY OUT OF SCOPE (found, not touched — see the runbook/final report)
------------------------------------------------------------------------------
- ``token_suppression`` / ``token_suppression_history.suppression_reason``
  ('operator_quarantine_clear' / 'chain_only_quarantined'): an ACTIVE, live
  ChainOnlyFact-suppression mechanism (T2/T8-B2 territory), not a T5 lifecycle-
  phase literal. Renaming it is a semantic decision outside this packet's scope.
- ``settlements`` / ``observations.authority`` 'QUARANTINED': already migrated to
  'DISPUTED' by T2b (``src.state.db.SCHEMA_VERSION`` 43, boot-time in-code
  migration ``_migrate_authority_tier_disputed``).
- ``decision_integrity_quarantine``: DIQ packet's territory.
- ``market_topology_state`` / ``source_contract_audit_events``: checked via rg on
  every schema source in this repo — carry no quarantine-family literal today
  (already clean via earlier authority-check migrations).

Usage
-----
    python scripts/migrations/2026_07_quarantine_phase_retirement.py \\
        --operator-confirms-fenced

    # Point at a fixture state dir instead of the live STATE_DIR (tests only):
    python scripts/migrations/2026_07_quarantine_phase_retirement.py \\
        --operator-confirms-fenced --state-dir /tmp/fixture_state

See docs/rebuild/t5_migration_runbook.md for the full operator runbook
(preconditions, expected output, verification queries, rollback procedure).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# TARGET_DB metadata purely so the shared runner's cross-DB-write guard (see
# scripts/migrations/__init__.py::apply_migrations) resolves cleanly to a
# RuntimeError from up() below, rather than an unrelated "missing TARGET_DB
# metadata" error that would obscure the real refusal message.
TARGET_DB = "trade"

# Stable version identifier (NOT a timestamp) stamped into schema_epoch on all
# three DBs. src.state.db.assert_schema_epoch_not_mixed only cares that the
# three agree, never about this literal value — but a stable id makes
# idempotency checks (re-run after a partial/complete prior run) unambiguous.
TARGET_SCHEMA_EPOCH = "t5_quarantine_phase_retirement_v1"

# Kill-point injection hook (tests only). See _maybe_crash() below.
_KILL_ENV_VAR = "ZEUS_T5_KILL_AT"
# Test-only escape hatch for the process-scan half of the writer-plane fence
# check — NEVER set outside a test fixture. The --operator-confirms-fenced
# flag itself is never bypassable. See _assert_writer_plane_fenced().
_SKIP_PROCESS_CHECK_ENV_VAR = "ZEUS_T5_MIGRATION_TEST_SKIP_PROCESS_CHECK"

KILL_POINTS = (
    "post_fence_check",
    "post_backup",
    "mid_ddl",
    "mid_copy",
    "post_validate",
    "post_stamp",
    "pre_commit",
)

# Known zeus daemon process patterns (mirrors scripts/deploy_live.py DAEMONS +
# scripts/check_live_restart_preflight.py's _live_main_processes() pattern set,
# reimplemented standalone here so this migration never imports a module with
# import-time side effects such as deploy_live.py's LIVE_REPO resolution).
_ZEUS_DAEMON_PATTERNS = (
    "src.main",
    "src/main.py",
    "src.engine.cycle_runner",
    "src/execution/harvester",
    "src.execution.harvester",
    "price_channel_ingest",
    "riskguard_live",
    "src.riskguard",
    "substrate_observer",
    "post_trade_capital",
    "forecast_live",
    "venue_heartbeat",
    "heartbeat_sensor",
    "data_ingest",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _maybe_crash(checkpoint: str) -> None:
    """Kill-point injection hook (tests only). If ZEUS_T5_KILL_AT names this
    checkpoint, hard-exit the process with NO Python cleanup (no atexit, no
    exception unwind, no rollback) — the most faithful simulation of a real
    SIGKILL/power-loss for exercising SQLite's rollback-journal recovery path,
    since a raised exception would let `except`/`finally` blocks run a clean
    rollback, which is exactly the behavior under test."""
    if os.environ.get(_KILL_ENV_VAR) == checkpoint:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbPaths:
    world: Path
    forecasts: Path
    trade: Path


def _resolve_db_paths(state_dir: Optional[str]) -> DbPaths:
    if state_dir:
        base = Path(state_dir)
        return DbPaths(
            world=base / "zeus-world.db",
            forecasts=base / "zeus-forecasts.db",
            trade=base / "zeus_trades.db",
        )
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH, _zeus_trade_db_path

    return DbPaths(
        world=ZEUS_WORLD_DB_PATH,
        forecasts=ZEUS_FORECASTS_DB_PATH,
        trade=_zeus_trade_db_path(),
    )


# ---------------------------------------------------------------------------
# 1) Writer-plane fence
# ---------------------------------------------------------------------------


def _live_zeus_processes() -> list[str]:
    """ps-based scan for any running zeus daemon process. No machine-checkable
    global writer fence exists in this repo today (rg confirmed: entries_paused
    only pauses NEW entries, not monitor/exit/settlement/reconcile writers) —
    so this scan is the second half of the fence, alongside the mandatory
    --operator-confirms-fenced flag."""
    if os.environ.get(_SKIP_PROCESS_CHECK_ENV_VAR) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        # Fail-closed would be nicer, but a ps failure on a non-POSIX CI box
        # must not block --operator-confirms-fenced entirely; the flag itself
        # remains mandatory. Surface the failure loudly.
        print("WARNING: ps -axo pid,command failed; process-scan half of the "
              "fence check could not run. Relying on --operator-confirms-fenced alone.",
              file=sys.stderr)
        return []
    self_pid = os.getpid()
    hits: list[str] = []
    for line in out.splitlines():
        try:
            pid_str, _, cmd = line.strip().partition(" ")
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        if "python" not in cmd:
            continue
        if any(pattern in cmd for pattern in _ZEUS_DAEMON_PATTERNS):
            hits.append(line.strip())
    return hits


def _assert_writer_plane_fenced(operator_confirms_fenced: bool) -> None:
    if not operator_confirms_fenced:
        raise SystemExit(
            "REFUSED: T5 migration requires the writer plane fenced. No "
            "machine-checkable global writer fence exists in this repo (rg "
            "confirmed entries_paused only pauses NEW entries, not monitor/"
            "exit/settlement/reconcile writers). Stop every zeus daemon "
            "(scripts/deploy_live.py restart is NOT what you want here — "
            "actually STOP them: launchctl bootout each com.zeus.* label), "
            "confirm no process is writing any of the three DBs, then re-run "
            "with --operator-confirms-fenced. See "
            "docs/rebuild/t5_migration_runbook.md."
        )
    live = _live_zeus_processes()
    if live:
        raise SystemExit(
            "REFUSED: --operator-confirms-fenced was passed but a zeus "
            "daemon process is still running:\n  "
            + "\n  ".join(live)
            + "\nStop it before re-running. See docs/rebuild/t5_migration_runbook.md."
        )


# ---------------------------------------------------------------------------
# 2) WAL checkpoint + truncate
# ---------------------------------------------------------------------------


def _checkpoint_truncate(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3) Synchronized 3-DB backup set
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _integrity_check(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    finally:
        conn.close()


def _backup_three(paths: DbPaths, backup_root: Path) -> Path:
    """Copy all three DB files (+ any WAL/SHM sidecars still present) into ONE
    timestamped directory, verify each copy's integrity, and record a manifest
    (path, size, sha256, integrity_check result) so a later restore can prove
    the backup set itself was not corrupt at capture time.

    Rollback law (BLOCKER-2): all three backups restored together or forward-
    fix; never one file alone; never the old binary after any target write.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_root / f"t5_quarantine_migration_{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, dict] = {}
    for label, src in (("world", paths.world), ("forecasts", paths.forecasts), ("trade", paths.trade)):
        if not src.exists():
            manifest[label] = {"path": None, "note": "source db did not exist"}
            continue
        dst = dest / src.name
        shutil.copy2(src, dst)
        for sidecar_suffix in ("-wal", "-shm"):
            sidecar = Path(str(src) + sidecar_suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(dst) + sidecar_suffix))
        integrity = _integrity_check(dst)
        manifest[label] = {
            "path": str(dst),
            "size_bytes": dst.stat().st_size,
            "sha256": _sha256(dst),
            "integrity_check": integrity,
        }
        if integrity != "ok":
            raise RuntimeError(
                f"T5 backup integrity check FAILED for {label} ({dst}): {integrity} — "
                "ABORT before touching any live DB"
            )
    manifest_path = dest / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return dest


# ---------------------------------------------------------------------------
# 4) Dedicated non-WAL connection
# ---------------------------------------------------------------------------


def _open_dedicated_connection(paths: DbPaths) -> sqlite3.Connection:
    """A DEDICATED sqlite3 connection with journal_mode=DELETE (rollback
    journal). NEVER src.state.db._connect() — it re-enables WAL. ATTACHes
    the other two DBs so all work happens in ONE transaction."""
    from src.state.db import _install_connection_functions

    conn = sqlite3.connect(str(paths.trade))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = DELETE")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "delete":
        conn.close()
        raise RuntimeError(
            f"T5 migration requires journal_mode=DELETE on the trade DB connection; "
            f"got {mode!r}. Another connection may still hold the DB in WAL mode — "
            "confirm the writer plane is truly fenced."
        )
    conn.execute("ATTACH DATABASE ? AS world", (str(paths.world),))
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(paths.forecasts),))
    for schema in ("world", "forecasts"):
        conn.execute(f"PRAGMA {schema}.journal_mode = DELETE")
        got = conn.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
        if str(got).lower() != "delete":
            conn.close()
            raise RuntimeError(
                f"T5 migration requires journal_mode=DELETE on attached schema "
                f"{schema!r}; got {got!r}."
            )
    _install_connection_functions(conn)
    return conn


# ---------------------------------------------------------------------------
# Generic CHECK-literal-drop table rebuild
# ---------------------------------------------------------------------------


def _strip_check_literal(sql: str, literal: str) -> str:
    """Remove one quoted literal from a SQL `IN ('a','b',...)` list, robust to
    whether it is first/middle/last in the list and to whitespace/newline
    formatting (sqlite_master echoes the CREATE TABLE text close to verbatim
    but this must not depend on exact indentation)."""
    pattern_not_last = re.compile(r"'" + re.escape(literal) + r"'\s*,\s*")
    if pattern_not_last.search(sql):
        return pattern_not_last.sub("", sql, count=1)
    pattern_last = re.compile(r",\s*'" + re.escape(literal) + r"'")
    if pattern_last.search(sql):
        return pattern_last.sub("", sql, count=1)
    # Sole remaining member of the list (should not happen for our tables —
    # every CHECK we touch has >=1 surviving member) — leave untouched rather
    # than guess.
    return sql


@dataclass
class RebuildResult:
    table: str
    schema: str
    rebuilt: bool
    pre_count: int = 0
    post_count: int = 0
    remapped: dict = field(default_factory=dict)
    pre_hash: str = ""
    post_hash: str = ""


def _table_create_sql(conn: sqlite3.Connection, schema: str, table: str) -> Optional[str]:
    prefix = f"{schema}." if schema else ""
    row = conn.execute(
        f"SELECT sql FROM {prefix}sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row[0] if row else None


def _table_content_hash(conn: sqlite3.Connection, schema: str, table: str, pk: str) -> str:
    prefix = f"{schema}." if schema else ""
    cols = [r[1] for r in conn.execute(f"PRAGMA {prefix}table_info({table})")]
    col_list = ", ".join(cols)
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT {col_list} FROM {prefix}{table} ORDER BY {pk}"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def _rebuild_table_drop_literals(
    conn: sqlite3.Connection,
    *,
    schema: str,
    table: str,
    literals_to_drop: list[str],
    column_remaps: dict[str, str],
    pk_col: str,
    on_checkpoint: Optional[str] = None,
) -> RebuildResult:
    """Idempotent REOPEN-2-style table rebuild (mirrors
    src.state.db._migrate_authority_tier_disputed): reuse the table's OWN
    recorded DDL text with only the named CHECK literals stripped, then
    INSERT...SELECT with per-column CASE remaps, verify row-count parity,
    recreate indexes + triggers against the final name, drop the old table,
    rename the rebuilt one into place.

    ``column_remaps`` maps column name -> a SQL expression (referencing the
    OLD table's column names) used in the SELECT list in place of a bare
    column reference — e.g. ``{"phase": "CASE WHEN phase='quarantined' THEN "
    "'active' ELSE phase END"}``.

    No-op (rebuilt=False) when the table doesn't exist or none of
    ``literals_to_drop`` appear in its current CHECK text (idempotent).
    """
    prefix = f"{schema}." if schema else ""
    table_sql = _table_create_sql(conn, schema, table)
    if table_sql is None:
        return RebuildResult(table=table, schema=schema, rebuilt=False)
    if not any(f"'{lit}'" in table_sql for lit in literals_to_drop):
        return RebuildResult(table=table, schema=schema, rebuilt=False)

    pre_count = conn.execute(f"SELECT COUNT(*) FROM {prefix}{table}").fetchone()[0]
    pre_hash = _table_content_hash(conn, schema, table, pk_col)

    new_sql = table_sql
    for lit in literals_to_drop:
        new_sql = _strip_check_literal(new_sql, lit)

    migrated_name = f"{table}_t5_migrated"
    # sqlite_master may record the identifier bare or double-quoted (a table
    # ever touched by ALTER TABLE gets re-recorded quoted) — accept both.
    for prefix_plain in (f'CREATE TABLE "{table}"', f"CREATE TABLE {table}"):
        if new_sql.startswith(prefix_plain):
            new_sql = f"CREATE TABLE {migrated_name}" + new_sql[len(prefix_plain):]
            break
    else:
        raise RuntimeError(
            f"T5 rebuild: unrecognized CREATE TABLE prefix for {schema}.{table} "
            "— refusing to rebuild blind"
        )
    if schema:
        new_sql = new_sql.replace(
            f"CREATE TABLE {migrated_name}", f"CREATE TABLE {schema}.{migrated_name}", 1
        )

    cols = [r[1] for r in conn.execute(f"PRAGMA {prefix}table_info({table})")]
    col_list = ", ".join(cols)
    select_list = ", ".join(column_remaps.get(c, c) for c in cols)

    if on_checkpoint:
        _maybe_crash(on_checkpoint)

    conn.execute(new_sql)
    conn.execute(
        f"INSERT INTO {prefix}{migrated_name} ({col_list}) "
        f"SELECT {select_list} FROM {prefix}{table}"
    )

    post_count = conn.execute(f"SELECT COUNT(*) FROM {prefix}{migrated_name}").fetchone()[0]
    if post_count != pre_count:
        raise RuntimeError(
            f"T5 rebuild row-count drift on {schema}.{table}: "
            f"pre={pre_count} post={post_count} — ABORT to prevent data loss"
        )

    remapped: dict[str, int] = {}
    for col, expr in column_remaps.items():
        if "CASE" not in expr.upper():
            continue
        # Best-effort per-value remap counts for the operator receipt: compare
        # old vs new value per row. Only meaningful for the columns we remap.
        changed = conn.execute(
            f"SELECT COUNT(*) FROM {prefix}{table} t, {prefix}{migrated_name} m "
            f"WHERE t.{pk_col} = m.{pk_col} AND t.{col} IS NOT m.{col}"
        ).fetchone()[0]
        if changed:
            remapped[col] = changed

    index_rows = conn.execute(
        f"SELECT sql FROM {prefix}sqlite_master WHERE type='index' AND tbl_name=? "
        "AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    trigger_rows = conn.execute(
        f"SELECT sql FROM {prefix}sqlite_master WHERE type='trigger' AND tbl_name=? "
        "AND sql IS NOT NULL",
        (table,),
    ).fetchall()

    conn.execute(f"DROP TABLE {prefix}{table}")
    # ALTER TABLE RENAME re-parses every view in every attached schema; a
    # pre-existing broken view (e.g. a ghost view referencing a table that
    # lives in another physical DB) aborts the rename even though it is
    # unrelated to this table. legacy_alter_table skips that revalidation —
    # the rename itself never rewrites view SQL anyway.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute(f"ALTER TABLE {prefix}{migrated_name} RENAME TO {table}")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
    def _schema_qualify(sql: str) -> str:
        # Recorded DDL never carries the schema; on this ATTACHed connection an
        # unqualified CREATE INDEX/TRIGGER would land in main (the trade DB).
        if not schema:
            return sql
        for kw in ("CREATE UNIQUE INDEX ", "CREATE INDEX ", "CREATE TRIGGER "):
            if sql.startswith(kw):
                return f"{kw}{schema}." + sql[len(kw):]
        raise RuntimeError(
            f"T5 rebuild: unrecognized DDL prefix while re-creating on "
            f"{schema}.{table}: {sql[:60]!r}"
        )

    for (index_sql,) in index_rows:
        conn.execute(_schema_qualify(index_sql))
    for (trigger_sql,) in trigger_rows:
        conn.execute(_schema_qualify(trigger_sql))

    post_hash = _table_content_hash(conn, schema, table, pk_col)
    return RebuildResult(
        table=table,
        schema=schema,
        rebuilt=True,
        pre_count=pre_count,
        post_count=post_count,
        remapped=remapped,
        pre_hash=pre_hash,
        post_hash=post_hash,
    )


# ---------------------------------------------------------------------------
# Phase-inference heuristic (REPLACEMENT PHASE LAW)
# ---------------------------------------------------------------------------

# Open exit-attempt event types: the position has expressed exit intent that
# has not yet resolved to a terminal exit outcome. Deliberately narrower than
# src.state.db._EXIT_LIFECYCLE_EVENT_TYPES (which also includes terminal exit
# outcomes) — we only want the "still trying to exit" subset here.
_OPEN_EXIT_ATTEMPT_EVENT_TYPES = frozenset(
    {"EXIT_INTENT", "EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED", "EXIT_RETRY_SCHEDULED"}
)


def _infer_true_phase(conn: sqlite3.Connection, position_id: str) -> str:
    """REPLACEMENT PHASE LAW (docs/rebuild/quarantine_excision_2026-07-11.md,
    T5): a formerly-quarantined position keeps its TRUE lifecycle phase —
    'active', or 'pending_exit' when the position's own most recent
    position_events row is an open (unresolved) exit attempt."""
    row = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? "
        "ORDER BY sequence_no DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    if row and str(row[0]) in _OPEN_EXIT_ATTEMPT_EVENT_TYPES:
        return "pending_exit"
    return "active"


# ---------------------------------------------------------------------------
# Migration body (runs inside the ONE transaction)
# ---------------------------------------------------------------------------


@dataclass
class MigrationReceipt:
    started_at: str
    finished_at: str = ""
    rebuilds: list = field(default_factory=list)
    chain_state_rows_updated: int = 0
    review_work_items_opened: int = 0
    position_lots_world_flagged: int = 0
    position_lots_trade_flagged: int = 0


def _mint_legacy_quarantine_review_item(
    conn: sqlite3.Connection, *, position_id: str, exposure_bound_usd: Optional[float]
) -> None:
    from src.contracts.review_work_item import ReviewReasonCode
    from src.state.review_work_items import open_work_item

    unbounded = exposure_bound_usd is None or exposure_bound_usd < 0
    open_work_item(
        conn,
        owner_domain="trade",
        owner_table="position_current",
        subject_id=position_id,
        reason_code=ReviewReasonCode.LEGACY_QUARANTINE_MIGRATED,
        evidence_refs=("docs/rebuild/quarantine_excision_2026-07-11.md#T5",),
        exposure_bound_usd=None if unbounded else exposure_bound_usd,
        unbounded=unbounded,
        priority=100,
        last_error_class="LEGACY_QUARANTINED_ROW",
        last_error_detail=(
            "pre-T5-migration row carried phase='quarantined'; remapped to its "
            "true phase by the T5 offline migration"
        ),
    )


def _migrate_position_current(conn: sqlite3.Connection, receipt: MigrationReceipt) -> None:
    quarantined_rows = conn.execute(
        "SELECT position_id, cost_basis_usd FROM position_current WHERE phase = 'quarantined'"
    ).fetchall()
    true_phase_by_id = {
        pid: _infer_true_phase(conn, pid) for (pid, _cost) in quarantined_rows
    }

    if true_phase_by_id:
        clauses = " ".join(
            f"WHEN position_id = '{pid}' THEN '{phase}'"
            for pid, phase in true_phase_by_id.items()
        )
        phase_expr = f"CASE {clauses} ELSE phase END"
    else:
        phase_expr = "phase"

    result = _rebuild_table_drop_literals(
        conn,
        schema="",
        table="position_current",
        literals_to_drop=["quarantined"],
        column_remaps={"phase": phase_expr},
        pk_col="position_id",
        on_checkpoint="mid_ddl",
    )
    receipt.rebuilds.append(result)

    for pid, cost_basis in quarantined_rows:
        _mint_legacy_quarantine_review_item(
            conn,
            position_id=pid,
            exposure_bound_usd=(float(cost_basis) if cost_basis is not None else None),
        )
        receipt.review_work_items_opened += 1

    _maybe_crash("mid_copy")

    chain_updated = conn.execute(
        "UPDATE position_current SET chain_state = 'synced' "
        "WHERE chain_state IN ('quarantined', 'quarantine_expired', 'entry_authority_quarantined')"
    ).rowcount
    receipt.chain_state_rows_updated = int(chain_updated or 0)


def _migrate_position_events(conn: sqlite3.Connection, receipt: MigrationReceipt) -> None:
    phase_case = (
        "CASE WHEN phase_before = 'quarantined' THEN 'active' ELSE phase_before END"
    )
    phase_after_case = (
        "CASE WHEN phase_after = 'quarantined' THEN 'active' ELSE phase_after END"
    )
    event_type_case = (
        "CASE WHEN event_type = 'CHAIN_QUARANTINED' THEN 'REVIEW_REQUIRED' ELSE event_type END"
    )
    result = _rebuild_table_drop_literals(
        conn,
        schema="",
        table="position_events",
        literals_to_drop=["quarantined", "CHAIN_QUARANTINED"],
        column_remaps={
            "phase_before": phase_case,
            "phase_after": phase_after_case,
            "event_type": event_type_case,
        },
        pk_col="event_id",
    )
    receipt.rebuilds.append(result)


def _migrate_position_lots(conn: sqlite3.Connection, receipt: MigrationReceipt, *, schema: str) -> None:
    state_case = "CASE WHEN state = 'QUARANTINED' THEN 'CONFIRMED_EXPOSURE' ELSE state END"
    prefix = f"{schema}." if schema else ""
    flagged = conn.execute(
        f"SELECT lot_id, position_id FROM {prefix}position_lots WHERE state = 'QUARANTINED'"
    ).fetchall()
    for lot_id, position_id in flagged:
        import logging

        logging.getLogger(__name__).error(
            "T5_LEGACY_POSITION_LOTS_QUARANTINED_REMAPPED: %s.position_lots lot_id=%s "
            "position_id=%s state='QUARANTINED' -> 'CONFIRMED_EXPOSURE' — manual "
            "operator review recommended (this path is defensive; 0 live rows "
            "expected per 2026-07-11 census)",
            schema or "trade",
            lot_id,
            position_id,
        )
    if schema == "world":
        receipt.position_lots_world_flagged = len(flagged)
    else:
        receipt.position_lots_trade_flagged = len(flagged)

    result = _rebuild_table_drop_literals(
        conn,
        schema=schema,
        table="position_lots",
        literals_to_drop=["QUARANTINED"],
        column_remaps={"state": state_case},
        pk_col="lot_id",
    )
    receipt.rebuilds.append(result)


def _stamp_schema_epoch(conn: sqlite3.Connection) -> None:
    from src.state.db import SCHEMA_EPOCH_TABLE_DDL

    now = _now_iso()
    for schema in ("", "world.", "forecasts."):
        conn.execute(SCHEMA_EPOCH_TABLE_DDL.replace("schema_epoch", f"{schema}schema_epoch"))
        conn.execute(
            f"INSERT INTO {schema}schema_epoch (id, epoch, stamped_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET epoch=excluded.epoch, stamped_at=excluded.stamped_at",
            (TARGET_SCHEMA_EPOCH, now),
        )


def _validate_no_remaining_literals(conn: sqlite3.Connection) -> None:
    checks = [
        ("", "position_current", "phase", "quarantined"),
        ("", "position_events", "phase_before", "quarantined"),
        ("", "position_events", "phase_after", "quarantined"),
        ("", "position_events", "event_type", "CHAIN_QUARANTINED"),
        ("", "position_lots", "state", "QUARANTINED"),
        ("world.", "position_lots", "state", "QUARANTINED"),
    ]
    for schema, table, col, literal in checks:
        exists = conn.execute(
            f"SELECT name FROM {schema}sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        remaining = conn.execute(
            f"SELECT COUNT(*) FROM {schema}{table} WHERE {col} = ?", (literal,)
        ).fetchone()[0]
        if remaining:
            raise RuntimeError(
                f"T5 post-migration validation FAILED: {schema}{table}.{col} still "
                f"has {remaining} row(s) carrying {literal!r} — ABORT before commit"
            )


def run_migration(conn: sqlite3.Connection) -> MigrationReceipt:
    receipt = MigrationReceipt(started_at=_now_iso())
    conn.execute("BEGIN IMMEDIATE")
    try:
        _migrate_position_current(conn, receipt)
        _migrate_position_events(conn, receipt)
        _migrate_position_lots(conn, receipt, schema="")
        _migrate_position_lots(conn, receipt, schema="world")

        _maybe_crash("post_validate")
        _validate_no_remaining_literals(conn)

        _stamp_schema_epoch(conn)
        _maybe_crash("post_stamp")

        receipt.finished_at = _now_iso()
        _maybe_crash("pre_commit")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return receipt


# ---------------------------------------------------------------------------
# Idempotency / mixed-epoch pre-check
# ---------------------------------------------------------------------------


def _classify_epoch_state(paths: DbPaths) -> tuple[str, dict]:
    from src.state.db import read_schema_epoch

    epochs = {}
    for label, path in (("world", paths.world), ("forecasts", paths.forecasts), ("trade", paths.trade)):
        if not path.exists():
            epochs[label] = None
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            epochs[label] = read_schema_epoch(conn)
        except sqlite3.OperationalError:
            epochs[label] = None
        finally:
            conn.close()
    values = set(epochs.values())
    if values == {None}:
        return "none", epochs
    if len(values) == 1:
        return "complete", epochs
    return "mixed", epochs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def up(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "T5 quarantine phase retirement CANNOT run through the shared "
        "single-DB migration runner (python -m scripts.migrations apply). "
        "BLOCKER-2 requires an offline RED cutover: writer-plane fence -> "
        "WAL checkpoint+truncate -> synchronized 3-DB backup -> a dedicated "
        "non-WAL connection ATTACHing all three DBs -> ONE transaction. Run "
        "this script directly:\n"
        "    python scripts/migrations/2026_07_quarantine_phase_retirement.py "
        "--operator-confirms-fenced\n"
        "See docs/rebuild/t5_migration_runbook.md."
    )


def down(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "T5 rollback is NOT a per-DB down() — restore the synchronized 3-DB "
        "backup set together (never one file alone; never the old binary "
        "after any target write). See docs/rebuild/t5_migration_runbook.md."
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--operator-confirms-fenced",
        action="store_true",
        help="Required. Confirms the operator has stopped every zeus daemon "
        "before running this migration.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Directory containing zeus-world.db / zeus-forecasts.db / "
        "zeus_trades.db. Defaults to the live STATE_DIR. Tests only.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Directory to write the synchronized 3-DB backup set into. "
        "Defaults to <state-dir>/backups.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Operator-only: skip the synchronized 3-DB backup set. The "
        "operator explicitly waived DB backups (2026-07-12 directive; the "
        "agent-side DB-backup guard also forbids whole-DB copies). Rollback "
        "after a crash then relies on the rollback journal alone — the ONE "
        "attached transaction still guarantees untouched-or-complete.",
    )
    args = parser.parse_args(argv)

    paths = _resolve_db_paths(args.state_dir)
    backup_root = Path(args.backup_dir) if args.backup_dir else paths.trade.parent / "backups"

    epoch_state, epochs = _classify_epoch_state(paths)
    if epoch_state == "complete":
        print(f"T5 migration already applied (schema_epoch={epochs['trade']!r} on all "
              "three DBs) — no-op.")
        return 0
    if epoch_state == "mixed":
        print(
            f"REFUSED: mixed schema_epoch across the three DBs: {epochs!r}. This "
            "indicates a partially-applied migration or a crash mid-run. DO NOT "
            "re-run blind — restore the synchronized 3-DB backup set together, or "
            "forward-fix under operator supervision. See "
            "docs/rebuild/t5_migration_runbook.md rollback procedure.",
            file=sys.stderr,
        )
        return 1

    _assert_writer_plane_fenced(args.operator_confirms_fenced)
    _maybe_crash("post_fence_check")

    for path in (paths.world, paths.forecasts, paths.trade):
        _checkpoint_truncate(path)

    if args.skip_backup:
        print("backup set: SKIPPED (--skip-backup, operator directive 2026-07-12)")
    else:
        backup_dir = _backup_three(paths, backup_root)
        print(f"backup set: {backup_dir}")
    _maybe_crash("post_backup")

    conn = _open_dedicated_connection(paths)
    try:
        receipt = run_migration(conn)
    finally:
        conn.close()

    print(json.dumps(
        {
            "started_at": receipt.started_at,
            "finished_at": receipt.finished_at,
            "chain_state_rows_updated": receipt.chain_state_rows_updated,
            "review_work_items_opened": receipt.review_work_items_opened,
            "position_lots_world_flagged": receipt.position_lots_world_flagged,
            "position_lots_trade_flagged": receipt.position_lots_trade_flagged,
            "rebuilds": [
                {
                    "table": r.table,
                    "schema": r.schema or "trade",
                    "rebuilt": r.rebuilt,
                    "pre_count": r.pre_count,
                    "post_count": r.post_count,
                    "remapped": r.remapped,
                }
                for r in receipt.rebuilds
            ],
        },
        indent=2,
        sort_keys=True,
    ))
    if not args.skip_backup:
        (backup_dir / "receipt.json").write_text(
            json.dumps({"finished_at": receipt.finished_at, "epoch": TARGET_SCHEMA_EPOCH}, indent=2)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
