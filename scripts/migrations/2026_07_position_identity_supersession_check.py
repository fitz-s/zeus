#!/usr/bin/env python3
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=2026-07-13
# Purpose: F2 rework of the reverted LX-F packet (f2c50ebd5 / af902a8e4) — the
#   missing live-DB migration lane the wave-1 reviews required (C2 blocker):
#   an already-created zeus_trades.db's position_events.event_type CHECK is
#   frozen at CREATE-TABLE time (CREATE TABLE IF NOT EXISTS never rewrites an
#   existing table) and will not admit POSITION_IDENTITY_SUPERSEDED until this
#   migration rebuilds the table. Follows the T5 migration pattern
#   (scripts/migrations/2026_07_quarantine_phase_retirement.py): writer-plane
#   fence, optional backup, ONE transaction, idempotent re-run.
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2
#   delta (duplicate-position identity BLOCKER, "before read-model backfill,
#   convert prior consolidations into explicit immutable identity-supersession
#   facts") + docs/rebuild/consult_answers/
#   local_ledger_excision_wave1_review_2026-07-13.txt ("the supplied change set
#   does not show a live-database migration for an already-created SQLite
#   event-type CHECK constraint... needs an explicit compatible schema
#   migration, stale-binary fencing, an idempotent backfill").
"""F2 — live-DB migration for the position_events.event_type CHECK.

Narrower than T5 in one respect: `position_events` lives ONLY in
zeus_trades.db (K1 split — see `src.state.db.init_schema_trade_only`). This
migration therefore touches a SINGLE physical file, not three ATTACHed ones,
so it does not need T5's dedicated non-WAL connection + ATTACH dance (that
exotic protocol exists purely to make a 3-file transaction atomic under a
storage engine that only guarantees atomicity per-file; a single file's WAL
commit is already atomic, with or without ATTACH). It still follows T5's
writer-plane-fence + optional-backup + single-transaction + idempotent-re-run
discipline, because a table rebuild takes an exclusive lock on
`position_events` for its duration and a concurrent writer must not be racing
it.

WHY THIS DOES NOT RUN THROUGH THE SHARED scripts/migrations RUNNER
--------------------------------------------------------------------
Same reasoning as T5 (see that module's docstring): the shared runner has no
hook for the writer-plane-fence confirmation or the backup ceremony. `up()`/
`down()` below exist only so this file's `2*.py` name does not silently get
picked up and mis-applied by `python -m scripts.migrations apply` — they
refuse immediately with a pointer to the real entry point, `main()` below.

STALE-BINARY FENCING
---------------------
A running process built from `src/engine/lifecycle_events.py` BEFORE this
migration ships already tolerates the pre-migration CHECK: `_merge_
equivalent_rows` probes `position_events_admits_event_type` on every write
attempt (not a cached startup-only check) and falls back to a
LOCAL_WRITE_FAILURE ReviewWorkItem when the literal is not yet admitted —
never crashes, never loses the void events already appended. A rolling
deploy therefore has no unsafe window: old and new binaries both behave
correctly against both pre- and post-migration schema, and the migration
itself only needs the writer-plane fenced for the DURATION of its own single
transaction (a table rebuild, like T5's, takes an exclusive lock).

Usage
-----
    python scripts/migrations/2026_07_position_identity_supersession_check.py \\
        --operator-confirms-fenced

    # Point at a fixture state dir instead of the live STATE_DIR (tests only):
    python scripts/migrations/2026_07_position_identity_supersession_check.py \\
        --operator-confirms-fenced --state-dir /tmp/fixture_state
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
from dataclasses import dataclass
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

TARGET_LITERAL = "POSITION_IDENTITY_SUPERSEDED"
TABLE_NAME = "position_events"

# Kill-point injection hook (tests only). See _maybe_crash() below.
_KILL_ENV_VAR = "ZEUS_F2_KILL_AT"
# Test-only escape hatch for the process-scan half of the writer-plane fence
# check — NEVER set outside a test fixture. The --operator-confirms-fenced
# flag itself is never bypassable. See _assert_writer_plane_fenced().
_SKIP_PROCESS_CHECK_ENV_VAR = "ZEUS_F2_MIGRATION_TEST_SKIP_PROCESS_CHECK"

KILL_POINTS = ("post_fence_check", "post_backup", "mid_ddl", "pre_commit")

# Mirrors scripts/migrations/2026_07_quarantine_phase_retirement.py's own
# standalone daemon-pattern list (reimplemented here for the same reason:
# never import a module with import-time side effects such as
# deploy_live.py's LIVE_REPO resolution).
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
    """Kill-point injection hook (tests only). If ZEUS_F2_KILL_AT names this
    checkpoint, hard-exit the process with NO Python cleanup — the most
    faithful simulation of a real SIGKILL/power-loss for exercising SQLite's
    rollback-journal recovery path (a raised exception would let a normal
    except/finally run a clean rollback, which is exactly the behavior under
    test)."""
    if os.environ.get(_KILL_ENV_VAR) == checkpoint:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


def _resolve_trade_db_path(state_dir: Optional[str]) -> Path:
    if state_dir:
        return Path(state_dir) / "zeus_trades.db"
    from src.state.db import _zeus_trade_db_path

    return _zeus_trade_db_path()


# ---------------------------------------------------------------------------
# 1) Writer-plane fence
# ---------------------------------------------------------------------------


def _live_zeus_processes() -> list[str]:
    """ps-based scan for any running zeus daemon process — the second half of
    the fence, alongside the mandatory --operator-confirms-fenced flag (no
    machine-checkable global writer fence exists in this repo today)."""
    if os.environ.get(_SKIP_PROCESS_CHECK_ENV_VAR) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        print(
            "WARNING: ps -axo pid,command failed; process-scan half of the "
            "fence check could not run. Relying on --operator-confirms-fenced alone.",
            file=sys.stderr,
        )
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
            "REFUSED: this migration requires the writer plane fenced. Stop "
            "every zeus daemon (scripts/deploy_live.py restart is NOT what you "
            "want here — actually STOP them: launchctl bootout each com.zeus.* "
            "label), confirm no process is writing zeus_trades.db, then re-run "
            "with --operator-confirms-fenced."
        )
    live = _live_zeus_processes()
    if live:
        raise SystemExit(
            "REFUSED: --operator-confirms-fenced was passed but a zeus daemon "
            "process is still running:\n  " + "\n  ".join(live) + "\nStop it "
            "before re-running."
        )


# ---------------------------------------------------------------------------
# 2) WAL checkpoint + truncate, backup
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


def _backup_trade_db(path: Path, backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_root / f"f2_position_events_check_migration_{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    dst = dest / path.name
    shutil.copy2(path, dst)
    for sidecar_suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + sidecar_suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, Path(str(dst) + sidecar_suffix))
    integrity = _integrity_check(dst)
    manifest = {
        "trade": {
            "path": str(dst),
            "size_bytes": dst.stat().st_size,
            "sha256": _sha256(dst),
            "integrity_check": integrity,
        }
    }
    if integrity != "ok":
        raise RuntimeError(
            f"F2 backup integrity check FAILED for trade db ({dst}): {integrity} — "
            "ABORT before touching the live DB"
        )
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return dest


# ---------------------------------------------------------------------------
# Paren-aware CHECK-list literal insertion
# ---------------------------------------------------------------------------


def _find_matching_close_paren(sql: str, open_paren_index: int) -> int:
    """Return the index of the ``)`` that closes the ``(`` at
    ``open_paren_index``, walking forward and skipping over SQL string
    literals (``'...'``, with ``''`` as an escaped quote) and ``--`` line
    comments — so an embedded comment containing a stray ``)`` (a REAL bug
    the original LX-F packet fixed in `src/state/db.py`'s own copy of this
    comment — see tests/state/test_inv_position_event_wire_grammar.py) cannot
    fool a naive ``[^)]+``-style scan. A live production DB's
    ``sqlite_master`` text may still carry the pre-fix comment placement
    FOREVER (CHECK text is frozen at CREATE-TABLE time), so this migration
    cannot assume clean input and must parse it properly.
    """
    assert sql[open_paren_index] == "("
    depth = 1
    i = open_paren_index + 1
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise RuntimeError("F2 migration: unbalanced parentheses scanning CHECK IN(...) list")


def _insert_check_literal(sql: str, *, column: str, literal: str) -> str:
    """Insert ``literal`` as a new member of ``column``'s ``CHECK (... IN
    (...))`` list in ``sql``, robust to embedded comments containing stray
    parens. Raises if the CHECK clause cannot be located."""
    m = re.search(
        rf"{column}\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*{column}\s+IN\s*\(",
        sql,
    )
    if not m:
        raise RuntimeError(
            f"F2 migration: could not locate `{column} ... CHECK ({column} IN (` "
            "in the live position_events DDL — refusing to rebuild blind"
        )
    open_paren_index = m.end() - 1
    close_paren_index = _find_matching_close_paren(sql, open_paren_index)
    insertion = f",\n        '{literal}'"
    return sql[:close_paren_index] + insertion + sql[close_paren_index:]


# ---------------------------------------------------------------------------
# Idempotent single-table rebuild
# ---------------------------------------------------------------------------


@dataclass
class RebuildResult:
    rebuilt: bool
    pre_count: int = 0
    post_count: int = 0


def _rebuild_position_events_check(conn: sqlite3.Connection) -> RebuildResult:
    """Idempotent table rebuild adding TARGET_LITERAL to position_events'
    event_type CHECK. No-op (rebuilt=False) if the live CHECK already admits
    it, or if the table doesn't exist yet. No data is remapped — every
    existing row's column values are copied verbatim; only the CHECK
    constraint text changes."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE_NAME,),
    ).fetchone()
    table_sql = row[0] if row else None
    if table_sql is None:
        return RebuildResult(rebuilt=False)
    if f"'{TARGET_LITERAL}'" in table_sql:
        return RebuildResult(rebuilt=False)

    pre_count = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]

    new_sql = _insert_check_literal(table_sql, column="event_type", literal=TARGET_LITERAL)

    migrated_name = f"{TABLE_NAME}_f2_migrated"
    # sqlite_master may record the identifier bare or double-quoted (a table
    # ever touched by ALTER TABLE gets re-recorded quoted) — accept both.
    for prefix_plain in (f'CREATE TABLE "{TABLE_NAME}"', f"CREATE TABLE {TABLE_NAME}"):
        if new_sql.startswith(prefix_plain):
            new_sql = f"CREATE TABLE {migrated_name}" + new_sql[len(prefix_plain):]
            break
    else:
        raise RuntimeError(
            f"F2 migration: unrecognized CREATE TABLE prefix for {TABLE_NAME} "
            "— refusing to rebuild blind"
        )

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({TABLE_NAME})")]
    col_list = ", ".join(cols)

    _maybe_crash("mid_ddl")

    conn.execute(new_sql)
    conn.execute(
        f"INSERT INTO {migrated_name} ({col_list}) SELECT {col_list} FROM {TABLE_NAME}"
    )

    post_count = conn.execute(f"SELECT COUNT(*) FROM {migrated_name}").fetchone()[0]
    if post_count != pre_count:
        raise RuntimeError(
            f"F2 migration row-count drift on {TABLE_NAME}: pre={pre_count} "
            f"post={post_count} — ABORT to prevent data loss"
        )

    # DROP TABLE cascades removal of indexes/triggers on `table`; capture
    # their SQL first and recreate against the renamed table.
    index_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (TABLE_NAME,),
    ).fetchall()
    trigger_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL",
        (TABLE_NAME,),
    ).fetchall()

    conn.execute(f"DROP TABLE {TABLE_NAME}")
    # ALTER TABLE RENAME re-parses every view in every attached schema; a
    # pre-existing broken view aborts the rename even though it is unrelated
    # to this table (see T5's identical use of this pragma). Defense-in-depth
    # — position_events has no view dependents today.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute(f"ALTER TABLE {migrated_name} RENAME TO {TABLE_NAME}")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")

    for (index_sql,) in index_rows:
        conn.execute(index_sql)
    for (trigger_sql,) in trigger_rows:
        conn.execute(trigger_sql)

    return RebuildResult(rebuilt=True, pre_count=pre_count, post_count=post_count)


def run_migration(conn: sqlite3.Connection) -> RebuildResult:
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _rebuild_position_events_check(conn)
        _maybe_crash("pre_commit")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def up(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "F2 position_events CHECK migration requires the writer-plane-fence + "
        "backup ceremony and CANNOT run through the shared single-DB migration "
        "runner (python -m scripts.migrations apply). Run this script "
        "directly:\n"
        "    python scripts/migrations/2026_07_position_identity_supersession_check.py "
        "--operator-confirms-fenced"
    )


def down(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "F2 rollback is NOT a per-DB down() — restore the trade-DB backup this "
        "migration wrote (or, if --skip-backup was used, forward-fix; the "
        "table rebuild is content-preserving — no live row is ever destroyed "
        "or remapped, only the CHECK constraint text changes)."
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
        help="Directory containing zeus_trades.db. Defaults to the live "
        "STATE_DIR. Tests only.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Directory to write the trade-DB backup into. Defaults to "
        "<state-dir>/backups.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Operator-only: skip the backup. The table rebuild runs in ONE "
        "transaction (BEGIN IMMEDIATE ... COMMIT) so a crash before COMMIT "
        "leaves the pre-migration table untouched and a crash after COMMIT "
        "leaves it fully migrated — never mixed.",
    )
    args = parser.parse_args(argv)

    trade_path = _resolve_trade_db_path(args.state_dir)
    backup_root = Path(args.backup_dir) if args.backup_dir else trade_path.parent / "backups"

    if not trade_path.exists():
        print(f"{trade_path} does not exist yet — nothing to migrate.")
        return 0

    probe_conn = sqlite3.connect(str(trade_path))
    try:
        row = probe_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_NAME,),
        ).fetchone()
    finally:
        probe_conn.close()
    if row is None or not row[0]:
        print(f"{TABLE_NAME} table does not exist yet — nothing to migrate.")
        return 0
    if f"'{TARGET_LITERAL}'" in row[0]:
        print(
            f"F2 migration already applied ({TABLE_NAME}.event_type CHECK "
            f"admits {TARGET_LITERAL!r}) — no-op."
        )
        return 0

    _assert_writer_plane_fenced(args.operator_confirms_fenced)
    _maybe_crash("post_fence_check")

    _checkpoint_truncate(trade_path)

    if args.skip_backup:
        print("backup: SKIPPED (--skip-backup)")
    else:
        backup_dir = _backup_trade_db(trade_path, backup_root)
        print(f"backup: {backup_dir}")
    _maybe_crash("post_backup")

    conn = sqlite3.connect(str(trade_path))
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        result = run_migration(conn)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "finished_at": _now_iso(),
                "rebuilt": result.rebuilt,
                "pre_count": result.pre_count,
                "post_count": result.post_count,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
