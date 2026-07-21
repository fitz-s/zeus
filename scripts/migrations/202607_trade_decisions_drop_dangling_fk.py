#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: W0-a — rebuild trade_decisions (zeus_trades.db) to drop the dangling
#   inline FK `forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id)`.
#   Local ensemble_snapshots was dropped 2026-05-18 by the K1 split (canonical table
#   moved to zeus-forecasts.db); with foreign_keys=ON the FK target is unreachable in
#   main schema, so every INSERT/UPDATE errors `no such table: main.ensemble_snapshots`
#   and the table has been FROZEN since 2026-07-02. This restores it. SINGLE-DB,
#   crash-atomic in one WAL transaction.
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   implementation/W0_RUNBOOK.md + consult_W0_verdict.md (GPT-5.6 round-3 adversarial
#   pass, "NO-GO as specified -> GO after the 5 corrections"). Fence/kill-point pattern
#   mirrors scripts/migrations/2026_07_quarantine_phase_retirement.py (T5).
"""W0-a live-money schema migration: drop trade_decisions' dangling FK by table rebuild.

WHY A REBUILD (not ALTER): SQLite has no `ALTER TABLE ... DROP CONSTRAINT`; a
column-level FK is removed only by the documented generalized procedure —
create-new, copy, drop-old, rename-new — in one transaction.

WHY SINGLE-DB WAL (diverges from T5's dedicated journal_mode=DELETE connection):
the consult verdict is explicit — a single modified WAL database is crash-atomic
for `BEGIN IMMEDIATE ... COMMIT`; readers continue on their old snapshot and a
crash exposes either the old-committed or the new-committed schema, never the
intermediate DROP/RENAME state. Changing journal mode adds risk without adding
safety. T5 needed DELETE only because it spanned THREE files (ATTACH is not
cross-file atomic under WAL). W0-a touches ONE file, so it stays WAL and never
ATTACHes forecasts.

THE FIVE CONSULT CORRECTIONS THIS SCRIPT ENCODES (W0_RUNBOOK.md §blocker table):
  B1 fence      — the app writer-lock is NOT fleet-complete (4 non-mutually-exclusive
                  write schemes). trade_decisions being frozen only means writes to
                  THAT table fail; other tables in the same file are still written.
                  Require an ALL-WRITER operator fence (--operator-confirms-fenced +
                  ps process-scan), NOT just a table lock. No reader fence needed.
  B2 SELECT *   — explicit, pinned column lists on both sides (SELECT * would silently
                  misplace values on any future physical column-order drift; the live
                  p_calibrated column already proves the physical schema drifted from
                  db.py's intended CREATE).
  B3 sequence   — `seq >= 4645` is the WRONG invariant. AUTOINCREMENT promises new ids
                  exceed the largest EVER committed (stored in sqlite_sequence). DROP
                  deletes that row; copying ids rebuilds seq from the copied max. If a
                  row above the current max was ever inserted-then-deleted, a naive
                  rebuild LOWERS the high-water and REUSES a consumed id -> aliases a
                  deleted historical decision to a new one. Save the exact live seq and
                  restore it explicitly after RENAME.
  B4 checks     — unqualified PRAGMA integrity_check / foreign_key_check scan the whole
                  93.9 GiB DB. Use table-scoped `PRAGMA main.integrity_check('trade_decisions')`
                  and bounded FK checks only.
  B5 (W0-c)     — not this script. W0-c (fill-to-lot reconcile) is a separate, gated step
                  and is NO-GO until position_lots has a per-fill idempotency key.

CRASH-ATOMICITY: one BEGIN IMMEDIATE ... COMMIT. Any failed assertion before COMMIT
=> explicit ROLLBACK (the transaction itself IS the primary rollback; no table
restore needed). A SIGKILL at any point before COMMIT leaves the pre-migration
table intact (WAL discards the uncommitted frames on next open); after COMMIT the
table is fully rebuilt — never a `_new`-residue / mixed state. The kill-point matrix
(tests/test_trade_decisions_fk_rebuild.py) proves this. kill -9 proves crash
behavior; genuine power-loss durability additionally relies on synchronous=FULL +
macOS fullfsync=ON, both set below.

up()/down() exist ONLY so the shared `python -m scripts.migrations apply` glob
(2*.py) refuses to run this file silently — the real entry point is main().

Usage:
    python scripts/migrations/202607_trade_decisions_drop_dangling_fk.py \\
        --operator-confirms-fenced [--capsule-dir DIR] [--dry-run]
    # tests point at a fixture:
    python scripts/migrations/202607_trade_decisions_drop_dangling_fk.py \\
        --operator-confirms-fenced --state-dir /tmp/fixture_state
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Pinned identity of the table we are migrating (asserted at runtime; a drift
#     since authoring ABORTS before any write). Captured live 2026-07-21. -------
EXPECTED_TABLE = "trade_decisions"
EXPECTED_CREATE_SHA256 = "6a637b7e6ef3f690276c899c96a5deb89d7931aa07bfd3ec5f48a39fd6621c55"
EXPECTED_COLUMNS = (
    "trade_id", "market_id", "bin_label", "direction", "size_usd", "price",
    "timestamp", "forecast_snapshot_id", "calibration_model_version", "p_raw",
    "p_calibrated", "p_posterior", "edge", "ci_lower", "ci_upper", "kelly_fraction",
    "status", "filled_at", "fill_price", "runtime_trade_id", "order_id",
    "order_status_text", "order_posted_at", "entered_at_ts", "chain_state",
    "strategy", "edge_source", "bin_type", "discovery_mode", "market_hours_open",
    "fill_quality", "entry_method", "selected_method", "applied_validations_json",
    "exit_trigger", "exit_reason", "admin_exit_reason", "exit_divergence_score",
    "exit_market_velocity_1h", "exit_forward_edge", "settlement_semantics_json",
    "epistemic_context_json", "edge_context_json", "entry_alpha_usd",
    "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd",
    "settlement_edge_usd", "env",
)
# The exact dangling FK clause to surgically remove (must occur exactly once).
FK_CLAUSE = "REFERENCES ensemble_snapshots(snapshot_id)"
APPROVED_SOURCE_IDS = (
    # venv SQLite 3.53.2 (probe-A / G1): NOT in the 3.51.2 WAL-reset corruption window.
    "2026-06-03 19:12:13 d6e03d8c777cfa2d35e3b60d8ec3e0187f3e9f99d8e2ee9cac695fd6fcdf1a24",
)

_KILL_ENV = "ZEUS_W0A_KILL_AT"          # tests only: hard-exit at a named checkpoint
_SKIP_PROC_ENV = "ZEUS_W0A_SKIP_PROCESS_CHECK"  # tests only: skip the ps scan half


# Zeus daemon process patterns (mirrors T5 _ZEUS_DAEMON_PATTERNS — every process
# that can hold a WRITABLE trades handle; a read-only process may remain).
_ZEUS_DAEMON_PATTERNS = (
    "src.main", "src/main.py", "src.engine.cycle_runner",
    "src/execution/harvester", "src.execution.harvester",
    "price_channel_ingest", "riskguard_live", "src.riskguard",
    "substrate_observer", "post_trade_capital", "forecast_live",
    "venue_heartbeat", "heartbeat_sensor", "data_ingest",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _maybe_crash(checkpoint: str) -> None:
    """Kill-point hook (tests only). If ZEUS_W0A_KILL_AT names this checkpoint,
    hard-exit with NO cleanup — the faithful SIGKILL/power-loss simulation, since a
    raised exception would let except/finally run a clean rollback (not under test)."""
    if os.environ.get(_KILL_ENV) == checkpoint:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


# ---------------------------------------------------------------------------
# 1) All-writer fence  (consult B1)
# ---------------------------------------------------------------------------

def _live_zeus_processes() -> list[str]:
    """ps-based scan for any running zeus daemon that could hold a writable trades
    handle. No machine-checkable global writer fence exists in this repo (T5 proved:
    entries_paused pauses only NEW entries, not monitor/exit/settlement/reconcile
    writers) — so this scan is the second half of the fence alongside the mandatory
    --operator-confirms-fenced flag."""
    if os.environ.get(_SKIP_PROC_ENV) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        print("WARNING: `ps -axo pid,command` failed; the process-scan half of the "
              "fence could not run. Relying on --operator-confirms-fenced alone.",
              file=sys.stderr)
        return []
    self_pid = os.getpid()
    hits: list[str] = []
    for line in out.splitlines():
        pid_str, _, cmd = line.strip().partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == self_pid or "python" not in cmd:
            continue
        if any(p in cmd for p in _ZEUS_DAEMON_PATTERNS):
            hits.append(line.strip())
    return hits


def _assert_writer_plane_fenced(operator_confirms_fenced: bool) -> None:
    if not operator_confirms_fenced:
        raise SystemExit(
            "REFUSED: W0-a requires the ALL-WRITER plane fenced (consult B1: the app "
            "writer-lock is not fleet-complete; other tables in zeus_trades.db are "
            "still written even though trade_decisions is frozen). STOP every zeus "
            "daemon (launchctl bootout each com.zeus.* label — a deploy_live.py restart "
            "is NOT a stop), confirm nothing holds a writable trades handle, then re-run "
            "with --operator-confirms-fenced. Runbook: W0_RUNBOOK.md."
        )
    live = _live_zeus_processes()
    if live:
        raise SystemExit(
            "REFUSED: --operator-confirms-fenced was passed but a zeus daemon is still "
            "running:\n  " + "\n  ".join(live) + "\nStop it before re-running.")


# ---------------------------------------------------------------------------
# DB path + connection
# ---------------------------------------------------------------------------

def _trade_db_path(state_dir: Optional[str]) -> Path:
    if state_dir:
        return Path(state_dir) / "zeus_trades.db"
    from src.state.db import _zeus_trade_db_path
    return _zeus_trade_db_path()


def _open_migration_connection(path: Path) -> sqlite3.Connection:
    """Fresh, dedicated mode=rw connection (never src.state.db._connect(), never rwc,
    never ATTACH forecasts). rw (not rwc) so a wrong/missing path fails loudly instead
    of silently creating an empty DB (the dash/underscore stray-DB hazard)."""
    if not path.exists():
        raise SystemExit(f"REFUSED: trades DB not found at {path} (mode=rw, not rwc).")
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0.0, isolation_level=None)
    # busy_timeout=0: failure to acquire BEGIN IMMEDIATE is an ABORT (a concurrent
    # writer means the fence failed), never a 30s wait (consult B1).
    conn.execute("PRAGMA busy_timeout = 0")
    return conn


def _assert_connection_preconditions(conn: sqlite3.Connection, path: Path) -> None:
    src_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
    if src_id not in APPROVED_SOURCE_IDS:
        raise SystemExit(f"REFUSED: unapproved sqlite_source_id() {src_id!r}. "
                         "W0-a must run under the approved venv 3.53.2 build (G1); "
                         "the system 3.51.2 CLI is in the WAL-reset corruption window.")
    dbs = [r[1] for r in conn.execute("PRAGMA database_list").fetchall()]
    if dbs != ["main"]:
        raise SystemExit(f"REFUSED: database_list is {dbs!r}, expected ['main'] only "
                         "(no ATTACH permitted for W0-a).")
    jm = conn.execute("PRAGMA main.journal_mode").fetchone()[0]
    if jm.lower() != "wal":
        raise SystemExit(f"REFUSED: journal_mode is {jm!r}, expected wal.")


def _set_durability(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA fullfsync = ON")            # macOS F_FULLFSYNC (probe-A G3)
    conn.execute("PRAGMA wal_autocheckpoint = 0")    # don't turn this commit into a big checkpoint
    conn.execute("PRAGMA legacy_alter_table = OFF")


# ---------------------------------------------------------------------------
# Schema derivation (consult B2)
# ---------------------------------------------------------------------------

def _live_create_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (EXPECTED_TABLE,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"REFUSED: table {EXPECTED_TABLE} not present.")
    return row[0]


def _assert_schema_pinned(conn: sqlite3.Connection) -> str:
    """Assert the physical schema matches exactly what this script was authored
    against, and return the live CREATE sql. Any drift ABORTS before any write."""
    sql = _live_create_sql(conn)
    got = hashlib.sha256(sql.encode()).hexdigest()
    if got != EXPECTED_CREATE_SHA256:
        raise SystemExit(
            f"REFUSED: trade_decisions CREATE sql sha256 {got} != pinned "
            f"{EXPECTED_CREATE_SHA256}. The physical schema drifted since this "
            "migration was authored; re-review and re-pin before running.")
    cols = tuple(c[1] for c in conn.execute("PRAGMA table_xinfo(trade_decisions)").fetchall()
                 if (len(c) < 7 or c[6] == 0))
    if cols != EXPECTED_COLUMNS:
        raise SystemExit(f"REFUSED: live column list {cols} != pinned {EXPECTED_COLUMNS}.")
    fks = conn.execute("PRAGMA foreign_key_list(trade_decisions)").fetchall()
    if not any(r[2] == "ensemble_snapshots" for r in fks):
        raise SystemExit(f"REFUSED: expected an ensemble_snapshots FK edge; found {fks}. "
                         "Either already migrated, or an unexpected schema.")
    if conn.execute("SELECT count(*) FROM sqlite_master WHERE name='trade_decisions_new'").fetchone()[0]:
        raise SystemExit("REFUSED: trade_decisions_new already exists (prior aborted run?).")
    # No indexes/triggers on trade_decisions (census); assert still true.
    extra = conn.execute(
        "SELECT type,name FROM sqlite_master WHERE tbl_name='trade_decisions' AND type IN ('index','trigger')"
    ).fetchall()
    if extra:
        raise SystemExit(f"REFUSED: unexpected index/trigger appeared on trade_decisions: {extra}. "
                         "Generalized rebuild must recreate dependents — re-review.")
    return sql


def _new_table_ddl(live_sql: str) -> str:
    """Build the trade_decisions_new DDL: rename the table and strip ONLY the one FK
    clause. Both edits are asserted to apply exactly once (never a silent no-op)."""
    if FK_CLAUSE not in live_sql or live_sql.count(FK_CLAUSE) != 1:
        raise SystemExit(f"REFUSED: expected exactly one {FK_CLAUSE!r} in CREATE sql; "
                         f"found {live_sql.count(FK_CLAUSE)}.")
    # strip " REFERENCES ensemble_snapshots(snapshot_id)" -> leave the soft-ref column.
    stripped = live_sql.replace(" " + FK_CLAUSE, "").replace(FK_CLAUSE, "")
    if FK_CLAUSE in stripped:
        raise SystemExit("REFUSED: FK clause survived the strip.")
    # rename CREATE TABLE [ "]trade_decisions[" ] ( -> ..._new (
    for needle in ('CREATE TABLE trade_decisions (', 'CREATE TABLE "trade_decisions" ('):
        if needle in stripped:
            ddl = stripped.replace(needle, 'CREATE TABLE trade_decisions_new (', 1)
            break
    else:
        raise SystemExit("REFUSED: could not locate the CREATE TABLE trade_decisions ( header to rename.")
    if ddl.count("trade_decisions_new") != 1 or "ensemble_snapshots" in ddl:
        raise SystemExit("REFUSED: new DDL failed post-conditions (rename/strip).")
    return ddl


# ---------------------------------------------------------------------------
# Rollback capsule (consult §7 — scoped single-table, respects the DB-backup guard)
# ---------------------------------------------------------------------------

def _typed_row_digest(conn: sqlite3.Connection, table: str) -> str:
    cols = ",".join(EXPECTED_COLUMNS)
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT {cols} FROM {table} ORDER BY trade_id"):
        h.update(repr(tuple((type(v).__name__, v) for v in row)).encode())
    return h.hexdigest()


def _write_capsule(conn: sqlite3.Connection, live_sql: str, old_seq: int, capsule_dir: Path) -> Path:
    """Export the single table + metadata to a standalone SQLite file (NOT a whole-DB
    copy — respects the DB-backup guard; NOT a CSV — preserves storage classes)."""
    capsule_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_iso().replace(":", "").replace("-", "")[:15]
    cap = capsule_dir / f"trade_decisions_capsule_{stamp}.sqlite"
    if cap.exists():
        raise SystemExit(f"REFUSED: capsule {cap} already exists.")
    cnt, mn, mx = conn.execute("SELECT count(*), min(trade_id), max(trade_id) FROM trade_decisions").fetchone()
    digest = _typed_row_digest(conn, "trade_decisions")
    cxn = sqlite3.connect(str(cap))
    try:
        cxn.execute("PRAGMA journal_mode=DELETE")
        cxn.execute(live_sql)  # exact original DDL, FK and all
        cols = ",".join(EXPECTED_COLUMNS)
        ph = ",".join(["?"] * len(EXPECTED_COLUMNS))
        cxn.executemany(
            f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
            conn.execute(f"SELECT {cols} FROM trade_decisions ORDER BY trade_id").fetchall(),
        )
        cxn.execute("CREATE TABLE _capsule_meta (k TEXT PRIMARY KEY, v TEXT)")
        meta = {
            "created_at": _now_iso(), "source": "W0-a trade_decisions FK rebuild",
            "original_create_sql": live_sql, "original_create_sha256": EXPECTED_CREATE_SHA256,
            "row_count": cnt, "min_trade_id": mn, "max_trade_id": mx,
            "old_sqlite_sequence_seq": old_seq, "typed_row_digest": digest,
        }
        cxn.executemany("INSERT INTO _capsule_meta VALUES (?,?)",
                        [(k, json.dumps(v)) for k, v in meta.items()])
        cxn.commit()
    finally:
        cxn.close()
    # fsync file + parent dir, then read-only.
    fd = os.open(str(cap), os.O_RDONLY)
    os.fsync(fd); os.close(fd)
    dfd = os.open(str(capsule_dir), os.O_RDONLY)
    os.fsync(dfd); os.close(dfd)
    cap_sha = hashlib.sha256(cap.read_bytes()).hexdigest()
    (capsule_dir / (cap.name + ".sha256")).write_text(cap_sha + "\n")
    os.chmod(cap, 0o444)
    print(f"CAPSULE {cap} rows={cnt} min={mn} max={mx} seq={old_seq} sha256={cap_sha}")
    return cap


# ---------------------------------------------------------------------------
# The migration (single WAL transaction)
# ---------------------------------------------------------------------------

def run_migration(path: Path, *, operator_confirms_fenced: bool, capsule_dir: Path,
                  dry_run: bool) -> None:
    _assert_writer_plane_fenced(operator_confirms_fenced)
    conn = _open_migration_connection(path)
    try:
        _assert_connection_preconditions(conn, path)
        _set_durability(conn)
        # foreign_keys OFF must be set + verified BEFORE BEGIN (a change inside a txn
        # is a no-op) — consult.
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("PRAGMA foreign_keys = OFF")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise SystemExit("REFUSED: could not disable foreign_keys before BEGIN.")

        live_sql = _assert_schema_pinned(conn)
        new_ddl = _new_table_ddl(live_sql)
        old_count, old_min, old_max = conn.execute(
            "SELECT count(*), min(trade_id), max(trade_id) FROM trade_decisions").fetchone()
        seq_rows = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchall()
        if len(seq_rows) != 1 or not isinstance(seq_rows[0][0], int):
            raise SystemExit(f"REFUSED: expected exactly one integer sqlite_sequence row; got {seq_rows}.")
        old_seq = seq_rows[0][0]
        if old_seq < (old_max or 0):
            raise SystemExit(f"REFUSED: sqlite_sequence.seq {old_seq} < max(trade_id) {old_max}.")
        old_digest = _typed_row_digest(conn, "trade_decisions")

        cap = _write_capsule(conn, live_sql, old_seq, capsule_dir)

        if dry_run:
            print(f"DRY-RUN OK: would rebuild {old_count} rows (min={old_min} max={old_max} "
                  f"seq={old_seq}); capsule at {cap}; new DDL:\n{new_ddl}")
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-assert every precondition INSIDE the write transaction.
            if hashlib.sha256(_live_create_sql(conn).encode()).hexdigest() != EXPECTED_CREATE_SHA256:
                raise SystemExit("ABORT: schema changed between check and BEGIN.")
            _maybe_crash("after_begin")

            conn.execute(new_ddl)
            _maybe_crash("after_create")

            cols = ",".join(EXPECTED_COLUMNS)
            conn.execute(f"INSERT INTO trade_decisions_new ({cols}) "
                         f"SELECT {cols} FROM trade_decisions ORDER BY trade_id")
            _maybe_crash("after_copy")

            n_count, n_min, n_max = conn.execute(
                "SELECT count(*), min(trade_id), max(trade_id) FROM trade_decisions_new").fetchone()
            if (n_count, n_min, n_max) != (old_count, old_min, old_max):
                raise SystemExit(f"ABORT: copy count/min/max {(n_count,n_min,n_max)} != "
                                 f"{(old_count,old_min,old_max)}.")
            if _typed_row_digest(conn, "trade_decisions_new") != old_digest:
                raise SystemExit("ABORT: typed row digest mismatch after copy.")
            # two-way typed EXCEPT over explicit columns
            diff = conn.execute(
                f"SELECT count(*) FROM (SELECT {cols} FROM trade_decisions "
                f"EXCEPT SELECT {cols} FROM trade_decisions_new "
                f"UNION ALL SELECT {cols} FROM trade_decisions_new "
                f"EXCEPT SELECT {cols} FROM trade_decisions)").fetchone()[0]
            if diff != 0:
                raise SystemExit(f"ABORT: two-way row diff is {diff}, expected 0.")

            conn.execute("DROP TABLE trade_decisions")
            _maybe_crash("after_drop")
            conn.execute("ALTER TABLE trade_decisions_new RENAME TO trade_decisions")
            _maybe_crash("after_rename")

            # B3: preserve the exact historical AUTOINCREMENT high-water.
            post = conn.execute(
                "SELECT count(*) FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()[0]
            if post != 1:
                raise SystemExit(f"ABORT: expected exactly 1 sqlite_sequence row post-rename; got {post}.")
            conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='trade_decisions'", (old_seq,))
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise SystemExit("ABORT: sqlite_sequence UPDATE did not affect exactly 1 row.")
            if conn.execute("SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()[0] != old_seq:
                raise SystemExit("ABORT: sqlite_sequence.seq not restored to old_seq.")
            if conn.execute("SELECT count(*) FROM sqlite_sequence WHERE name='trade_decisions_new'").fetchone()[0]:
                raise SystemExit("ABORT: stray trade_decisions_new sequence row.")

            # Final fingerprints + table-scoped checks (consult B4).
            final_fks = conn.execute("PRAGMA foreign_key_list(trade_decisions)").fetchall()
            if any(r[2] == "ensemble_snapshots" for r in final_fks):
                raise SystemExit(f"ABORT: ensemble_snapshots FK still present: {final_fks}.")
            final_cols = tuple(c[1] for c in conn.execute("PRAGMA table_xinfo(trade_decisions)").fetchall()
                               if (len(c) < 7 or c[6] == 0))
            if final_cols != EXPECTED_COLUMNS:
                raise SystemExit(f"ABORT: final columns {final_cols} != expected.")
            ic = conn.execute("PRAGMA main.integrity_check('trade_decisions')").fetchone()[0]
            if ic != "ok":
                raise SystemExit(f"ABORT: table-scoped integrity_check returned {ic!r}.")
            _maybe_crash("before_commit")

            conn.execute("COMMIT")
        except BaseException:
            # Some SQLite errors roll back only the failing statement; only ROLLBACK
            # if still in a transaction. "an exception occurred" != "txn is gone".
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

        conn.execute("PRAGMA foreign_keys = ON")
        if conn.in_transaction:
            raise SystemExit("POST: connection unexpectedly still in a transaction after COMMIT.")
        print(f"W0-a COMMITTED: trade_decisions rebuilt, {old_count} rows preserved, "
              f"seq={old_seq}, dangling ensemble_snapshots FK removed. Capsule: {cap}")
    finally:
        conn.close()

    _verify_fresh(path)


def _verify_fresh(path: Path) -> None:
    """Reopen on a fresh connection and confirm the committed end-state."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.25, isolation_level=None)
    try:
        conn.execute("PRAGMA query_only=ON")
        fks = conn.execute("PRAGMA foreign_key_list(trade_decisions)").fetchall()
        if any(r[2] == "ensemble_snapshots" for r in fks):
            raise SystemExit(f"VERIFY FAILED: ensemble_snapshots FK still present: {fks}.")
        if conn.execute("SELECT count(*) FROM sqlite_master WHERE name='trade_decisions_new'").fetchone()[0]:
            raise SystemExit("VERIFY FAILED: trade_decisions_new residue present.")
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()
        print(f"VERIFY OK (fresh conn): no dangling FK, no _new residue, sqlite_sequence={seq}.")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Shared-runner guard + entry point
# ---------------------------------------------------------------------------

def up(conn):  # noqa: ANN001
    raise SystemExit("202607_trade_decisions_drop_dangling_fk is NOT a shared-runner "
                     "migration (it needs an all-writer fence + a scoped connection). "
                     "Run its main() directly with --operator-confirms-fenced.")


def down(conn):  # noqa: ANN001
    raise SystemExit("No auto-down: reverse only via the scoped rollback capsule "
                     "(reverse single-table rebuild under the same fence). See W0_RUNBOOK.md.")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="W0-a: drop trade_decisions dangling FK (live-money).")
    ap.add_argument("--operator-confirms-fenced", action="store_true",
                    help="Operator asserts every zeus writer daemon is STOPPED (not just paused).")
    ap.add_argument("--state-dir", default=None, help="Fixture state dir (tests only).")
    ap.add_argument("--capsule-dir", default=None,
                    help="Where to write the rollback capsule (default: <state>/../w0a_capsules).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run all checks + write the capsule, but do not modify the table.")
    a = ap.parse_args(argv)
    path = _trade_db_path(a.state_dir)
    capsule_dir = Path(a.capsule_dir) if a.capsule_dir else path.parent / "w0a_capsules"
    run_migration(path, operator_confirms_fenced=a.operator_confirms_fenced,
                  capsule_dir=capsule_dir, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
