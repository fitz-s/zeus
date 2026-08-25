# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: live incident 2026-08-24/25 (RiskGuard storage_capacity DATA_DEGRADED)
#   slice 2 of 2 (paired with 202608_decision_log_retention.py, PR #510) — the
#   largest oversized trade-DB table (executable_market_snapshots, 48GB,
#   11,532,310 rows as of 2026-08-25, 2026-05-15..present, no prior retention).
# WRITER_LOCK: DML DELETE, writes under db_writer_lock(BULK) per
#   src/state/db_writer_lock.py. Precedent for the append-only trigger
#   drop-recreate-in-one-transaction dance:
#   scripts/repair_executable_snapshot_corruption.py (reads the trigger's own
#   sqlite_master.sql text, drops it, deletes, verifies changes() == expected,
#   re-executes the SAME sql, all inside one BEGIN IMMEDIATE). This migration
#   mirrors that exact mechanism per chunk.
"""Idempotent retention DELETE for executable_market_snapshots (trade DB).

executable_market_snapshots is APPEND-ONLY by DB trigger (NC-NEW-B):
`no_update_executable_market_snapshots` / `no_delete_executable_market_snapshots`
(src/state/snapshot_repo.py) RAISE(ABORT) on any UPDATE/DELETE. This migration
DELETEs rows older than --keep-days (default 30, floor 7) EXCEPT rows whose
snapshot_id is still referenced by venue_commands.snapshot_id or
position_events.snapshot_id (INV-NEW-E: "every persisted venue command cites
an executable-market snapshot" -- src/state/db.py venue_commands DDL comment).
Both anchor tables live on the SAME trade DB -- no cross-DB ATTACH needed.
Live evidence 2026-08-25: only 3337 distinct venue_commands.snapshot_id +
1642 distinct position_events.snapshot_id (small overlap likely) out of
11,532,310 total rows -- the anchor set is <0.05% of the table, so retention
reclaims nearly everything outside the window while never touching a row any
FK-referencing record still points at, regardless of that record's own age
(this also means scripts/ops/archive_pre_epoch_trades.py -- which DOES prune
old venue_commands/position_events rows under its own preconditions and
explicitly excludes executable_market_snapshots from its own scope, "market
snapshot data whose rotation is a separate op" -- naturally shrinks the anchor
set further over time; this migration always joins against whatever anchor
rows currently exist, so it stays correct regardless of that script's cadence).

SAFETY OF THE TRIGGER WINDOW: `DROP TRIGGER` + `DELETE` + verify + re-`CREATE
TRIGGER` all execute inside one `BEGIN IMMEDIATE` transaction per chunk.
BEGIN IMMEDIATE acquires SQLite's own RESERVED lock, which blocks any other
writer's BEGIN at the engine level for the transaction's duration --
independent of and in addition to db_writer_lock(BULK)'s flock-based
mutual exclusion. A concurrent LIVE INSERT attempting to write during the
brief trigger-absent window simply blocks until this chunk's COMMIT, then
proceeds against the (already re-created) trigger -- there is never a window
where a live writer can observe the trigger missing. Unlike
scripts/repair_executable_snapshot_corruption.py's physical B-tree surgery,
this migration does no raw page manipulation and therefore does not need that
script's writer-fence precondition (`--operator-confirms-fenced` / stopped
daemon) -- logical DELETE-by-predicate inside one transaction is sufficient.

Consumer-window audit (2026-08-25, law-check before implementation; grep
`executable_market_snapshots\\b` across src/ and scripts/ -- ~107 call sites
across 47 files, categorized below; full per-file:line detail was gathered via
two parallel locate passes and is summarized here, not reproduced verbatim):

  category                                                     window            keep_days=30 safe?
  --------------------------------------------------------------------------------------------------
  "latest row per condition/token" (ORDER BY captured_at DESC   trivially bounded  yes
    LIMIT 1, or joins through executable_market_snapshot_latest)
  "latest N rows" (LIMIT 4/8/12/100)                            trivially bounded  yes
  "latest snapshot per group" via ROW_NUMBER()..PARTITION BY    trivially bounded  yes -- needs only
    ...ORDER BY captured_at DESC, filtered to rn=1 in Python                       ANY recent snapshot
    (market_scanner.py, harvester_pnl_resolver.py, staleness_                      to exist for a
    cancel.py, main.py:8098) -- confirmed by direct read: these                    currently-relevant
    resolve identity/metadata for CURRENTLY ACTIVE conditions/                     condition, which it
    tokens, never a specific historical entry-time snapshot                        always will
  point lookup by snapshot_id (`WHERE snapshot_id = ?` / `IN (...)`)  UNBOUNDED in   PROTECTED by the
    -- command_recovery.py (7 sites), venue_command_repo.py            principle --   FK-anchor exception
    (4 sites), exchange_reconcile.py, exit_lifecycle.py,                the id comes   above (verified:
    family_exclusive_dedup.py, global_batch_runtime.py,                 from a caller-  command_recovery.py's
    event_reactor_adapter.py, reactor.py, market_channel_               supplied        _command_snapshot()
    ingestor.py -- traced command_recovery.py's _command_snapshot()     command/        reads
    to `command.get("snapshot_id")`, i.e. sourced from a                position        command["snapshot_id"],
    venue_commands row                                                  record          sourced from
                                                                                          venue_commands)
  explicit fixed historical replay windows (qkernel_settlement_        one-time,       yes -- these are
    ev_replay.py, percity_after_cost_ev_gate.py, modal_buyyes_          date-literal    frozen backtest
    drag_analysis.py: 2026-06-09..2026-06-15 or 2026-06-10..            operator tools  scripts against a
    2026-06-28)                                                                        specific archived
                                                                                        study window, not
                                                                                        live-daemon reads
  bounded rolling-window operator tools (qkernel_arm_replay.py,        now - 16 days   yes -- longest
    qkernel_settlement_graded_ev.py: `captured_at >= datetime('now',                    CONFIRMED fixed
    '-16 days')`)                                                                       rolling window;
                                                                                         sets MIN_KEEP_DAYS
  freshness-scan/audit tools (fit_sigma_tau_calibration.py --since      operator-       yes
    default 2026-07-11, check_live_restart_preflight.py, cycle_        supplied or
    phase_offline_study.py, probe_favorite_capture.py)                 small LIMIT
  a handful of genuinely UNBOUNDED scans with NO time filter at all    UNBOUNDED       see below
    (data/market_scanner.py:4305 ORDER BY captured_at DESC no LIMIT;
    execution/exchange_reconcile.py:943 full-table COUNT(*);
    runtime/bankroll_provider.py:343 full-table COUNT(*);
    observability/price_evidence_report.py:183,322 EXISTS/NOT EXISTS
    linkage integrity checks; audit_trade_db_growth.py rowid-tail
    diagnostics)

The genuinely unbounded scans are all either (a) full-table COUNT(*)
telemetry/diagnostics -- unaffected by deleting OLDER rows, the count just
reflects current state, not a specific-row dependency; or (b) linkage-integrity
audit tools (price_evidence_report.py, audit_trade_db_growth.py) whose PURPOSE
is auditing the snapshot substrate itself -- they will legitimately report
fewer historical rows post-retention, which is the intended effect of this
migration, not a correctness violation of a live money-path consumer.

Migration semantic policy:
  - DELETE-only; idempotent.
  - Default mode is dry-run (report only). --apply required to write.
  - Protection exception: rows whose snapshot_id appears in
    venue_commands.snapshot_id OR position_events.snapshot_id (see above).
  - One-time prerequisite index: none of this table's four existing indexes
    lead with captured_at alone (all are (other_col, captured_at DESC)), so a
    chunked `WHERE captured_at < cutoff` scan against 11.5M+ rows would degrade
    badly. --apply creates `idx_executable_market_snapshots_captured_at_only`
    (plain, single-column, IF NOT EXISTS) before the first delete chunk --
    CREATE INDEX is DDL, not blocked by the DELETE/UPDATE triggers. Building
    this index needs modest temporary disk -- run the FIRST --apply pass when
    a few GB of headroom exists.
  - --keep-days must be >= MIN_KEEP_DAYS (17 -- one day of margin over the
    longest CONFIRMED fixed rolling-window consumer, qkernel_arm_replay.py's
    "now - 16 days") or the script refuses to run.
  - Chunked DELETE (default 20000 rows/txn) under db_writer_lock(BULK) PLUS a
    per-chunk BEGIN IMMEDIATE trigger drop/verify/recreate (see SAFETY OF THE
    TRIGGER WINDOW above).

VACUUM note: identical to 202608_decision_log_retention.py -- auto_vacuum=0 on
the live trade DB, DELETE alone does not shrink the file or relieve the
storage_capacity gate. See that script's docstring for the full VACUUM path
discussion.

Usage
-----
    python scripts/migrations/202608_executable_market_snapshots_retention.py
        [--apply] [--keep-days N] [--db PATH] [--chunk-size N]

Default is dry-run. Use --apply to delete rows.
--keep-days: retention window in days (default: 30, minimum: 17).
--db: override trade DB path (default: from src.config STATE_DIR).
--chunk-size: rows deleted per transaction (default: 20000).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_KEEP_DAYS = 30
MIN_KEEP_DAYS = 17
DEFAULT_CHUNK_SIZE = 20000
_DELETE_TRIGGER_NAME = "no_delete_executable_market_snapshots"
_CUTOFF_INDEX_NAME = "idx_executable_market_snapshots_captured_at_only"
_CUTOFF_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {_CUTOFF_INDEX_NAME} "
    "ON executable_market_snapshots(captured_at)"
)

_ANCHOR_EXCEPT_CLAUSE = """
      AND snapshot_id NOT IN (
        SELECT snapshot_id FROM venue_commands WHERE snapshot_id IS NOT NULL
        UNION
        SELECT snapshot_id FROM position_events WHERE snapshot_id IS NOT NULL
      )
"""


def _get_default_db_path() -> Path:
    from src.config import STATE_DIR
    return STATE_DIR / "zeus_trades.db"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _cutoff_str(keep_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S")


def _has_both_anchor_tables(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "venue_commands") and _table_exists(conn, "position_events")


def report(db_path: Path, *, keep_days: int) -> dict[str, int]:
    """Dry-run: count total/protected/deletable rows. Never writes."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        has_anchors = _has_both_anchor_tables(conn)
        cutoff = _cutoff_str(keep_days)
        total_old = conn.execute(
            "SELECT COUNT(*) FROM executable_market_snapshots WHERE captured_at < ?",
            (cutoff,),
        ).fetchone()[0]
        if has_anchors:
            deletable = conn.execute(
                f"""
                SELECT COUNT(*) FROM executable_market_snapshots
                WHERE captured_at < ?
                {_ANCHOR_EXCEPT_CLAUSE}
                """,
                (cutoff,),
            ).fetchone()[0]
        else:
            deletable = total_old
        return {
            "cutoff": cutoff,
            "total_rows_older_than_cutoff": total_old,
            "protected_by_anchor": total_old - deletable,
            "deletable": deletable,
            "anchor_tables_present": int(has_anchors),
        }
    finally:
        conn.close()


def run(
    db_path: Path,
    *,
    keep_days: int,
    dry_run: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, int]:
    if keep_days < MIN_KEEP_DAYS:
        raise ValueError(
            f"EXECUTABLE_MARKET_SNAPSHOTS_RETENTION_KEEP_DAYS_BELOW_MINIMUM: "
            f"--keep-days={keep_days} < MIN_KEEP_DAYS={MIN_KEEP_DAYS}. The documented "
            f"consumer-window audit (module docstring) requires at least "
            f"{MIN_KEEP_DAYS} days of margin over the longest confirmed rolling-window "
            f"consumer (qkernel_arm_replay.py, now - 16 days)."
        )

    stats = report(db_path, keep_days=keep_days)
    if dry_run:
        return stats

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    has_anchors = stats["anchor_tables_present"] == 1
    cutoff = stats["cutoff"]
    except_clause = _ANCHOR_EXCEPT_CLAUSE if has_anchors else ""
    select_sql = f"""
        SELECT snapshot_id FROM executable_market_snapshots
        WHERE captured_at < ?
        {except_clause}
        ORDER BY snapshot_id
        LIMIT ?
    """

    conn = sqlite3.connect(str(db_path))
    total_deleted = 0
    try:
        with db_writer_lock(db_path, WriteClass.BULK):
            conn.execute(_CUTOFF_INDEX_SQL)
            conn.commit()

        while True:
            with db_writer_lock(db_path, WriteClass.BULK):
                ids = [
                    row[0]
                    for row in conn.execute(select_sql, (cutoff, chunk_size)).fetchall()
                ]
                if not ids:
                    break
                placeholders = ",".join("?" for _ in ids)

                trigger_row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name = ?",
                    (_DELETE_TRIGGER_NAME,),
                ).fetchone()
                if trigger_row is None or not trigger_row[0]:
                    raise RuntimeError(
                        "REFUSED: append-only delete trigger "
                        f"{_DELETE_TRIGGER_NAME} is missing"
                    )
                trigger_sql = trigger_row[0]

                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(f"DROP TRIGGER {_DELETE_TRIGGER_NAME}")
                    conn.execute(
                        f"DELETE FROM executable_market_snapshots "
                        f"WHERE snapshot_id IN ({placeholders})",
                        ids,
                    )
                    changed = conn.execute("SELECT changes()").fetchone()[0]
                    if int(changed) != len(ids):
                        raise RuntimeError(
                            f"REFUSED: expected to delete {len(ids)} rows, "
                            f"changes() reported {changed}"
                        )
                    conn.execute(trigger_sql)
                    conn.execute("COMMIT")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                total_deleted += len(ids)
            logger.info(
                "executable_market_snapshots retention: deleted %d rows so far",
                total_deleted,
            )
    finally:
        conn.close()

    stats["rows_deleted"] = total_deleted
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete rows (default: dry-run report only).")
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()
    dry_run = not args.apply

    stats = run(
        db_path,
        keep_days=args.keep_days,
        dry_run=dry_run,
        chunk_size=args.chunk_size,
    )

    print(f"{'[DRY-RUN] ' if dry_run else ''}executable_market_snapshots retention on {db_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if dry_run:
        print(f"\n[dry-run] would delete {stats['deletable']} rows (no writes made)")


if __name__ == "__main__":
    main()
