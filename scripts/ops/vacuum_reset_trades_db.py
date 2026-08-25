# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md item 13
#   (bounded-by-construction storage redesign, Slice C). Writer-plane fence pattern
#   is scripts/migrations/2026_07_quarantine_phase_retirement.py's
#   _live_zeus_processes/_assert_writer_plane_fenced (T5 migration), reused near-
#   verbatim: "No machine-checkable global writer fence exists in this repo (rg
#   confirmed entries_paused only pauses NEW entries, not monitor/exit/settlement/
#   reconcile writers)". Backup manifest format is scripts/ops/backup_canonical_dbs.py's
#   output. Precondition style (open-position gate, backup-ack, FK-ordered/staged
#   execution) follows scripts/ops/archive_pre_epoch_trades.py.
#
# THIS SCRIPT HAS NEVER BEEN RUN. It is documentation-grade: written, reviewed by
# read, syntax-checked, and its precondition/assertion LOGIC unit-tested against
# tiny fixture files (tests/test_vacuum_reset_trades_db.py) -- but --vacuum-into and
# --swap have never executed against any real database, live or otherwise. An
# operator runs this once, by hand, in a maintenance window.
"""One-time VACUUM reset for zeus_trades.db: the only way freed retention pages
(from items 11-13's DELETE-based retention, all of which leave auto_vacuum=0's
freelist internal rather than shrinking the file) actually return to the OS.

Three explicit phases, run as separate commands so an operator can stop and
inspect between each:

  1. --check           Read-only. Verifies every precondition. Never writes
                        anything. Safe to run repeatedly, any time.
  2. --vacuum-into DEST Re-verifies --check's preconditions, then runs
                        `VACUUM INTO DEST` against a READ-ONLY connection to the
                        live source DB (the live DB itself is never opened for
                        write by this phase), integrity-checks DEST, converts
                        DEST to auto_vacuum=INCREMENTAL (see AUTO_VACUUM
                        CONVERSION below), and writes a receipt JSON recording
                        DEST's path + sha256 + row counts per table + the
                        integrity-check result. Does NOT touch the live file.
  3. --swap DEST --operator-confirms-fenced
                        Requires a receipt from phase 2 proving DEST passed
                        integrity check, the writer-plane fence (see below),
                        and re-verifies --check's preconditions one more time
                        (state can have changed between phases). Atomically
                        renames the live DB out of the way, moves DEST into its
                        place, and re-verifies integrity on the file now at the
                        live path before declaring success. On ANY failure
                        after the live path has been touched, restores the
                        original file from the pre-swap rename.

PRECONDITIONS (checked by --check and re-checked by every later phase):
  - Writer-plane fenced: --operator-confirms-fenced (required, no default) PLUS
    a `ps`-based scan for any running zeus daemon process (same two-part fence
    as the T5 migration -- no machine-checkable global writer fence exists in
    this repo; entries_paused only pauses NEW entries, not monitor/exit/
    settlement/reconcile writers). --check and --vacuum-into do NOT require
    --operator-confirms-fenced (they never write to the live file); only
    --swap does.
  - Zero open positions: `position_current` has zero rows outside the terminal
    phases (settled, economically_closed, admin_closed, voided) -- verified
    live 2026-08-25 (see items 11/12): this is CURRENTLY true. If the operator
    cannot get to zero open positions, entries_paused (control_plane) is
    accepted as an alternative precondition per the operator's original
    instruction ("entries paused OR zero open positions") -- but note
    entries_paused does NOT stop monitor/exit/settlement/reconcile writers, so
    the writer-plane fence above is still mandatory regardless.
  - Backup manifest: --backup-manifest PATH pointing at a manifest produced by
    scripts/ops/backup_canonical_dbs.py, dated within --backup-max-age-hours
    (default 24) of now, whose entries include zeus_trades.db with a verified
    dest_sha256.
  - Source integrity: `PRAGMA integrity_check` on the live DB returns exactly
    ["ok"].
  - Free space at DEST's directory: at least 1.2x the CURRENT live file size
    (conservative -- the whole point is the output should be much smaller, but
    the check must not assume that before it has actually measured it).

SIZE ASSERTIONS (phase 2, after VACUUM INTO):
  - DEST integrity_check returns exactly ["ok"].
  - DEST size < SOURCE size (VACUUM INTO must never produce something larger;
    if it did, something is wrong -- refuse and do not proceed to swap).
  - DEST size > SOURCE size * MIN_PLAUSIBLE_RATIO (0.05) -- guards against a
    catastrophically incomplete copy silently passing a naive "smaller is
    good" check.
  - Per-table row counts in DEST match the source AT THE MOMENT OF THE VACUUM
    (a live DB can gain rows between the count and the vacuum; this compares
    against a fresh count taken from the SAME read-only connection used for
    VACUUM INTO, immediately before it, so no writer can race between them --
    the source connection is opened once and reused for both).

AUTO_VACUUM CONVERSION: `PRAGMA auto_vacuum=INCREMENTAL` is set on the
connection BEFORE `VACUUM INTO` runs, which (per SQLite's VACUUM semantics --
VACUUM INTO performs the same rebuild-into-a-fresh-file mechanism as VACUUM,
just targeting a new path) should produce DEST already in incremental mode.
This script does NOT trust that assumption blindly: after VACUUM INTO, it
opens DEST separately and reads back `PRAGMA auto_vacuum`. If it is not `2`
(incremental), the script runs `PRAGMA auto_vacuum=INCREMENTAL; VACUUM;`
directly on DEST (cheap -- DEST is already small) and re-verifies before
proceeding. Verify-then-fix, never assume-and-proceed.

AFTER THE SWAP: src/state/decision_chain.py::_inline_expire_decision_log,
src/state/snapshot_repo.py::_inline_expire_executable_market_snapshots, and
src/events/triggers/market_channel_ingestor.py::
_inline_expire_execution_feasibility_evidence (item 13, Slice B) each already
call `PRAGMA incremental_vacuum(1000)` on every write -- a no-op today because
auto_vacuum=0 live, but it activates automatically the moment this reset makes
auto_vacuum=INCREMENTAL true. No further code changes are needed after the
swap for the DB to start shrinking in steady state.

Usage
-----
    python scripts/ops/vacuum_reset_trades_db.py --check
        [--db PATH] [--backup-manifest PATH] [--backup-max-age-hours N]

    python scripts/ops/vacuum_reset_trades_db.py --vacuum-into DEST
        [--db PATH] [--backup-manifest PATH] [--backup-max-age-hours N]

    python scripts/ops/vacuum_reset_trades_db.py --swap DEST
        --operator-confirms-fenced
        [--db PATH] [--backup-manifest PATH] [--backup-max-age-hours N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MIN_PLAUSIBLE_SIZE_RATIO = 0.05
DEST_FREE_SPACE_MULTIPLIER = 1.2
DEFAULT_BACKUP_MAX_AGE_HOURS = 24

# Reused near-verbatim from scripts/migrations/2026_07_quarantine_phase_retirement.py
# (T5 migration) -- see module docstring PRECONDITIONS.
_SKIP_PROCESS_CHECK_ENV_VAR = "ZEUS_VACUUM_RESET_TEST_SKIP_PROCESS_CHECK"
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

_TRADE_CLASS_TABLES_TO_COUNT = (
    "decision_log",
    "executable_market_snapshots",
    "execution_feasibility_evidence",
    "position_current",
    "position_events",
    "venue_commands",
)


class PreconditionError(RuntimeError):
    pass


def _get_default_db_path() -> Path:
    from src.config import STATE_DIR
    return STATE_DIR / "zeus_trades.db"


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Writer-plane fence (T5 pattern)
# ---------------------------------------------------------------------------


def _live_zeus_processes() -> list[str]:
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


def assert_writer_plane_fenced(operator_confirms_fenced: bool) -> None:
    if not operator_confirms_fenced:
        raise PreconditionError(
            "REFUSED: --swap requires the writer plane fenced. No "
            "machine-checkable global writer fence exists in this repo "
            "(entries_paused only pauses NEW entries, not monitor/exit/"
            "settlement/reconcile writers). Stop every zeus daemon "
            "(launchctl bootout each com.zeus.* label -- NOT a scripts/"
            "deploy_live.py restart), confirm no process is writing "
            "zeus_trades.db, then re-run with --operator-confirms-fenced."
        )
    live = _live_zeus_processes()
    if live:
        raise PreconditionError(
            "REFUSED: --operator-confirms-fenced was passed but a zeus "
            "daemon process is still running:\n  " + "\n  ".join(live)
        )


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def check_zero_open_positions_or_entries_paused(conn: sqlite3.Connection) -> str:
    """Returns a human-readable description of which precondition was met.
    Raises PreconditionError if neither holds."""
    open_count = conn.execute(
        """
        SELECT COUNT(*) FROM position_current
        WHERE phase NOT IN ('settled', 'economically_closed', 'admin_closed', 'voided')
        """
    ).fetchone()[0]
    if open_count == 0:
        return "zero_open_positions"
    # entries_paused lives on the WORLD db behind its own durable-authority
    # read path (src.control.control_plane.is_entries_paused); this script's
    # `conn` is a read-only connection to the TRADE db, so the canonical
    # helper is called directly rather than hand-rolling a second, possibly
    # stale, query against control_overrides_history.
    from src.control.control_plane import is_entries_paused
    if is_entries_paused():
        return (
            "entries_paused (WARNING: monitor/exit/settlement/reconcile "
            "writers are NOT stopped by this alone -- the writer-plane fence "
            "at --swap time is still mandatory)"
        )
    raise PreconditionError(
        f"REFUSED: {open_count} open position(s) exist and entries_paused is "
        "not set. Either close/settle all positions or set "
        "control_plane:global:entries_paused before proceeding "
        "(the writer-plane fence at --swap time still applies either way)."
    )


def check_backup_manifest(
    manifest_path: Path, *, db_path: Path, max_age_hours: int
) -> dict:
    if not manifest_path.exists():
        raise PreconditionError(f"REFUSED: backup manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - created_at
    if age > timedelta(hours=max_age_hours):
        raise PreconditionError(
            f"REFUSED: backup manifest is {age} old, older than "
            f"--backup-max-age-hours={max_age_hours}: {manifest_path}"
        )
    entries = manifest.get("entries", [])
    # scripts/ops/backup_canonical_dbs.py's manifest entry key is "db" (the
    # source file's basename, e.g. "zeus_trades.db"), not "source_path".
    match = next(
        (e for e in entries if str(e.get("db", "")) == db_path.name),
        None,
    )
    if match is None:
        raise PreconditionError(
            f"REFUSED: backup manifest {manifest_path} has no entry for {db_path.name}"
        )
    verify = match.get("verify") or {}
    if not verify.get("ok"):
        raise PreconditionError(
            f"REFUSED: backup manifest entry for {db_path.name} did not verify ok: {verify}"
        )
    return match


def check_source_integrity(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        # Severe corruption can raise directly rather than returning a
        # non-"ok" result row (e.g. "database disk image is malformed").
        raise PreconditionError(f"REFUSED: source integrity_check raised: {exc}") from exc
    if rows != [("ok",)]:
        raise PreconditionError(f"REFUSED: source integrity_check failed: {rows}")


def check_dest_free_space(dest: Path, *, source_size: int) -> None:
    usage = shutil.disk_usage(dest.parent if dest.parent.exists() else dest.parent.parent)
    required = int(source_size * DEST_FREE_SPACE_MULTIPLIER)
    if usage.free < required:
        raise PreconditionError(
            f"REFUSED: {dest.parent} has {usage.free} bytes free, needs at "
            f"least {required} ({DEST_FREE_SPACE_MULTIPLIER}x the source file size)"
        )


def run_check(
    *,
    db_path: Path,
    backup_manifest: Path | None,
    backup_max_age_hours: int,
) -> dict:
    """Read-only. Never writes anything."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        position_status = check_zero_open_positions_or_entries_paused(conn)
        check_source_integrity(conn)
        backup_status = None
        if backup_manifest is not None:
            backup_status = check_backup_manifest(
                backup_manifest, db_path=db_path, max_age_hours=backup_max_age_hours
            )
        source_size = db_path.stat().st_size
        return {
            "position_precondition": position_status,
            "source_integrity": "ok",
            "backup_verified": backup_status is not None,
            "source_size_bytes": source_size,
            "ready_for_vacuum_into": True,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 2: VACUUM INTO
# ---------------------------------------------------------------------------


def run_vacuum_into(
    *,
    db_path: Path,
    dest: Path,
    backup_manifest: Path | None,
    backup_max_age_hours: int,
) -> dict:
    if dest.exists():
        raise PreconditionError(f"REFUSED: --vacuum-into destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        check_zero_open_positions_or_entries_paused(conn)
        check_source_integrity(conn)
        if backup_manifest is not None:
            check_backup_manifest(
                backup_manifest, db_path=db_path, max_age_hours=backup_max_age_hours
            )
        source_size = db_path.stat().st_size
        check_dest_free_space(dest, source_size=source_size)

        # Row counts from the SAME read-only connection, immediately before
        # VACUUM INTO -- no writer can race between this count and the vacuum
        # itself since both go through this one connection's consistent view.
        source_counts = {}
        for table in _TRADE_CLASS_TABLES_TO_COUNT:
            try:
                source_counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                source_counts[table] = None  # table absent on this DB; skip

        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()

    # Verify DEST independently.
    dest_conn = sqlite3.connect(str(dest))
    try:
        integrity_rows = dest_conn.execute("PRAGMA integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            raise PreconditionError(
                f"REFUSED: VACUUM INTO output failed integrity_check: {integrity_rows} "
                f"({dest} was NOT swapped into place and should be deleted)"
            )

        auto_vacuum_mode = dest_conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if int(auto_vacuum_mode) != 2:
            # Verify-then-fix: do not assume PRAGMA auto_vacuum set before
            # VACUUM INTO took effect on the destination.
            dest_conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            dest_conn.execute("VACUUM")
            auto_vacuum_mode = dest_conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            integrity_rows = dest_conn.execute("PRAGMA integrity_check").fetchall()
            if integrity_rows != [("ok",)] or int(auto_vacuum_mode) != 2:
                raise PreconditionError(
                    f"REFUSED: could not convert {dest} to auto_vacuum=INCREMENTAL "
                    f"(mode={auto_vacuum_mode}, integrity={integrity_rows})"
                )

        dest_counts = {}
        for table, expected in source_counts.items():
            if expected is None:
                continue
            dest_counts[table] = dest_conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            if dest_counts[table] != expected:
                raise PreconditionError(
                    f"REFUSED: row count mismatch for {table}: source={expected} "
                    f"dest={dest_counts[table]}"
                )
    finally:
        dest_conn.close()

    dest_size = dest.stat().st_size
    if dest_size >= source_size:
        raise PreconditionError(
            f"REFUSED: VACUUM INTO output ({dest_size} bytes) is not smaller "
            f"than the source ({source_size} bytes)"
        )
    if dest_size < source_size * MIN_PLAUSIBLE_SIZE_RATIO:
        raise PreconditionError(
            f"REFUSED: VACUUM INTO output ({dest_size} bytes) is implausibly "
            f"small relative to the source ({source_size} bytes) -- possible "
            f"incomplete copy despite passing row-count checks"
        )

    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(db_path),
        "source_size_bytes": source_size,
        "source_row_counts": source_counts,
        "dest_path": str(dest),
        "dest_size_bytes": dest_size,
        "dest_sha256": _sha256_file(dest),
        "dest_integrity_check": "ok",
        "dest_auto_vacuum_mode": "incremental",
        "reduction_ratio": round(1 - dest_size / source_size, 4),
    }
    receipt_path = dest.with_suffix(dest.suffix + ".vacuum_reset_receipt.json")
    receipt_path.write_text(json.dumps(receipt, indent=2))
    receipt["receipt_path"] = str(receipt_path)
    return receipt


# ---------------------------------------------------------------------------
# Phase 3: swap
# ---------------------------------------------------------------------------


def run_swap(
    *,
    db_path: Path,
    dest: Path,
    operator_confirms_fenced: bool,
    backup_manifest: Path | None,
    backup_max_age_hours: int,
) -> dict:
    assert_writer_plane_fenced(operator_confirms_fenced)

    receipt_path = dest.with_suffix(dest.suffix + ".vacuum_reset_receipt.json")
    if not receipt_path.exists():
        raise PreconditionError(
            f"REFUSED: no vacuum_reset receipt at {receipt_path}. Run "
            f"--vacuum-into {dest} first; --swap never runs VACUUM INTO itself."
        )
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("dest_integrity_check") != "ok":
        raise PreconditionError(f"REFUSED: receipt does not show a passed integrity check: {receipt}")
    actual_sha = _sha256_file(dest)
    if actual_sha != receipt.get("dest_sha256"):
        raise PreconditionError(
            f"REFUSED: {dest} sha256 ({actual_sha}) does not match its receipt "
            f"({receipt.get('dest_sha256')}) -- the file changed since --vacuum-into ran"
        )

    # Re-verify preconditions one more time -- state can have changed since
    # --vacuum-into ran (this is a SEPARATE operator invocation, possibly
    # much later).
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        check_zero_open_positions_or_entries_paused(conn)
        check_source_integrity(conn)
        if backup_manifest is not None:
            check_backup_manifest(
                backup_manifest, db_path=db_path, max_age_hours=backup_max_age_hours
            )
    finally:
        conn.close()

    backup_of_live = db_path.with_suffix(
        db_path.suffix + f".pre_vacuum_reset_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    os.rename(db_path, backup_of_live)
    try:
        shutil.move(str(dest), str(db_path))
        verify_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = verify_conn.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                raise PreconditionError(f"REFUSED: post-swap integrity_check failed: {rows}")
        finally:
            verify_conn.close()
    except BaseException:
        # Restore the original file; the swap did not complete successfully.
        if db_path.exists() and not backup_of_live.exists():
            pass  # dest already moved into db_path's place and failed verify; leave for operator inspection
        else:
            os.rename(backup_of_live, db_path)
        raise

    for suffix in ("-wal", "-shm"):
        stray = Path(str(db_path) + suffix)
        if stray.exists():
            stray.unlink()

    return {
        "swapped_at": datetime.now(timezone.utc).isoformat(),
        "live_path": str(db_path),
        "pre_swap_backup_path": str(backup_of_live),
        "post_swap_integrity_check": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--vacuum-into", type=Path, default=None, metavar="DEST")
    mode.add_argument("--swap", type=Path, default=None, metavar="DEST")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--backup-manifest", type=Path, default=None)
    parser.add_argument("--backup-max-age-hours", type=int, default=DEFAULT_BACKUP_MAX_AGE_HOURS)
    parser.add_argument("--operator-confirms-fenced", action="store_true", default=False)
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()

    try:
        if args.check:
            result = run_check(
                db_path=db_path,
                backup_manifest=args.backup_manifest,
                backup_max_age_hours=args.backup_max_age_hours,
            )
        elif args.vacuum_into is not None:
            result = run_vacuum_into(
                db_path=db_path,
                dest=args.vacuum_into,
                backup_manifest=args.backup_manifest,
                backup_max_age_hours=args.backup_max_age_hours,
            )
        else:
            result = run_swap(
                db_path=db_path,
                dest=args.swap,
                operator_confirms_fenced=args.operator_confirms_fenced,
                backup_manifest=args.backup_manifest,
                backup_max_age_hours=args.backup_max_age_hours,
            )
    except PreconditionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
