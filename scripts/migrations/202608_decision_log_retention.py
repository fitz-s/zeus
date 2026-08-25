# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: live incident 2026-08-24/25 (RiskGuard storage_capacity DATA_DEGRADED,
#   61% of ticks since ~06:22Z, gating new entries) + architecture/db_table_ownership.yaml
#   decision_log (trade) note: "Schema-21 global-auction winners bind this exact retained
#   row and its content hashes into ActionableTradeCertificate; command persistence and
#   settlement attribution re-read it, so these referenced rows are durable execution
#   evidence and must not be inferred or dropped." + tier0_candidate_set_provenance schema
#   owner (docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md item 3 /
#   tier0_selection_lift_preregistration_2026-08-24.md).
# WRITER_LOCK: this script performs DML (DELETE), not DDL-only -- writes under
#   db_writer_lock(BULK) per src/state/db_writer_lock.py, precedent
#   scripts/backfill_decision_events_from_artifact_json.py (chunked lock acquire/commit
#   so LIVE writers are never blocked longer than one chunk).
"""Idempotent retention DELETE for decision_log (trade DB, zeus_trades.db).

decision_log is 73GB / 565,879 rows (2026-05-02..present) with no prior retention --
the single largest contributor to the 2026-08-24 storage_capacity DATA_DEGRADED
incident (RiskGuard needs max(64GB, 10%*disk) free; volume was ~85GB free against a
~92.6GB requirement). This migration deletes decision_log rows older than
``--keep-days`` (default 30), EXCEPT rows that anchor the tier0 preregistered
selection-lift study, which must be retained indefinitely for the study's duration.

Consumer-window audit (2026-08-25, law-check before implementation; see
architecture/db_table_ownership.yaml decision_log entry + rg -ln "decision_log"
src/ scripts/ for the full consumer list):

  consumer                                                    window            keep_days=30 safe?
  --------------------------------------------------------------------------------------------------
  decision_chain.query_no_trade_cases                         hours=24 default  yes (LIMIT 200 fallback)
    (src/state/decision_chain.py:222)                         or explicit not_before
  decision_chain.query_settlement_records /                   caller limit/     yes
    query_learning_surface_summary                            not_before
  command_recovery._causal_decision_log_rows                  command_at +/-    yes (idx_decision_log_ts)
    (src/execution/command_recovery.py:4819)                  15 minutes
  event_reactor_adapter.py:1995 (book-native-side lookup)      ORDER BY id DESC  yes
                                                                LIMIT 4
  monitor_cadence.latest_complete_global_auction_receipt       ORDER BY id DESC  yes
    (src/ops/monitor_cadence.py:434)                           LIMIT 8
  live_health.py global-auction component reference chain      caller freshness yes (recent cutoff +
    (src/control/live_health.py:5765)                          cutoff, <=8 hops   bounded hop count)
  command_recovery / venue_command_repo EDLI decision_log_id   keyed to an       yes IF no non-terminal
    verification (src/execution/command_recovery.py:4857,      active/closing    position is older than
    src/state/venue_command_repo.py:2415)                      command           keep_days (verified live
                                                                                  2026-08-25: 0 non-terminal
                                                                                  position_current rows)
  settlement_skill_attribution.load_settled_positions          UNBOUNDED --      CONDITIONAL (see below)
    (src/analysis/settlement_skill_attribution.py:1390,        only_new=True
    _resolve_decision_q_from_certificate:979)                  backfills every
                                                                 un-attributed
                                                                 settled position,
                                                                 no time bound
  tier0 preregistered selection-lift study                     indefinite (study protected via the
    (scripts/selection_lift_report.py,                         duration)         tier0-anchor EXCEPT
    src/engine/global_batch_runtime.py::_persist_tier0_         clause below
    candidate_set)

CONDITIONAL consumer (settlement_skill_attribution): ``run_settlement_skill_attribution``
backfills every settled position lacking a settlement_attribution row, with no age bound
-- in principle an old, still-unattributed position's global-auction certificate could
need to read an old decision_log row via decision_log_id. Live evidence 2026-08-25: 86
settled positions are currently unattributed, 27 of them older than 30 days -- but EVERY
ONE of those 27 already has an unresolvable certificate chain independent of decision_log
(position_decision_attribution has no ATTRIBUTED row, or the VERIFIED
ActionableTradeCertificate it names is absent) -- so none of them would actually read
decision_log even if this migration never ran. This migration does NOT add a cross-DB
(decision_certificates / settlement_attribution live on zeus-world.db) guard against this
residual risk -- flagged for the operator: if the settlement_skill_attribution job falls
behind AND a future certificate resolves cleanly for a position still older than
keep_days, that position would grade UNATTRIBUTABLE_Q_MISSING instead of its true
category. Re-verify this 0-current-risk invariant before lowering --keep-days or
widening the tier0 exception.

Migration semantic policy:
  - DELETE-only; idempotent (re-running with the same --keep-days deletes nothing new
    once the cutoff has been fully drained).
  - Default mode is dry-run (report only). --apply required to write.
  - Protection exception: rows with mode='global_single_order_auction' whose
    artifact_json.summary.selection_epoch_identity matches a row in
    tier0_candidate_set_provenance (same-DB join, both tables live on zeus_trades.db;
    sole writer of tier0_candidate_set_provenance is
    src.engine.global_batch_runtime._persist_tier0_candidate_set, same transaction as
    the receipt write). If tier0_candidate_set_provenance does not exist on the target
    DB, the exception protects nothing (matches the literal spec; the table landed on
    live 2026-08-24, so this only affects older/isolated fixture DBs).
  - --keep-days must be >= MIN_KEEP_DAYS (7 -- a >10x margin over the longest CONFIRMED
    bounded consumer window of 24h) or the script refuses to run at all.
  - Chunked DELETE (default 2000 rows/txn) under db_writer_lock(BULK), lock
    acquired/released per chunk so LIVE writers are never blocked longer than one
    chunk's commit.

VACUUM note (2026-08-25): auto_vacuum=0 (NONE) on the live trade DB. DELETE alone does
NOT shrink the on-disk file or relieve the storage_capacity gate -- freed pages become
internal SQLite freelist space, reused by future INSERTs, not returned to the OS. An
in-place VACUUM needs roughly 1.5-2x the live file size in free space (file is ~198GB;
volume free space was ~80GB as of 2026-08-25) -- infeasible today (chicken-and-egg: the
retention job's own purpose is to relieve a space shortage that also blocks a
same-volume VACUUM). Two documented paths, NEITHER executed by this script:
  1. ``VACUUM INTO`` a path on a volume WITH sufficient free space (only needs room for
     the compacted output file, not 1.5x headroom on the SAME volume), then swap the
     compacted file in during a maintenance window with an open-position precondition +
     backup manifest ack (pattern: scripts/ops/archive_pre_epoch_trades.py). After the
     swap, ``PRAGMA auto_vacuum=INCREMENTAL`` can be set on the fresh file (must
     immediately follow a VACUUM to take effect) so future retention runs can reclaim
     space incrementally via ``PRAGMA incremental_vacuum`` without ever needing another
     full VACUUM.
  2. Repeated retention runs at progressively shorter --keep-days across maintenance
     windows, combined with retention on the OTHER oversized tables
     (executable_market_snapshots 48GB, execution_feasibility_evidence 25GB -- out of
     scope for this migration), until the live file's non-live-data fraction is small
     enough that an in-place VACUUM's headroom requirement drops under available free
     space.

Usage
-----
    python scripts/migrations/202608_decision_log_retention.py [--apply] [--keep-days N]
        [--db PATH] [--chunk-size N]

Default is dry-run. Use --apply to delete rows.
--keep-days: retention window in days (default: 30, minimum: 7).
--db: override trade DB path (default: from src.config STATE_DIR).
--chunk-size: rows deleted per transaction (default: 2000).
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
MIN_KEEP_DAYS = 7
DEFAULT_CHUNK_SIZE = 2000

# Shared with the inline piggybacked expiry in src/state/decision_chain.py
# (2026-08-25 bounded-by-construction redesign) -- single definition, no
# drift between the periodic backlog-drain tool and the inline mechanism.
from src.state.decision_chain import TIER0_EXCEPT_CLAUSE as _TIER0_EXCEPT_CLAUSE


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


def _candidate_select_sql(has_tier0: bool) -> str:
    except_clause = _TIER0_EXCEPT_CLAUSE if has_tier0 else ""
    return f"""
        SELECT id FROM decision_log
        WHERE timestamp < ?
        {except_clause}
        ORDER BY id
        LIMIT ?
    """


def report(db_path: Path, *, keep_days: int) -> dict[str, int]:
    """Dry-run: count total/protected/deletable rows. Never writes."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        has_tier0 = _table_exists(conn, "tier0_candidate_set_provenance")
        cutoff = _cutoff_str(keep_days)
        total_old = conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE timestamp < ?", (cutoff,)
        ).fetchone()[0]
        if has_tier0:
            deletable = conn.execute(
                f"""
                SELECT COUNT(*) FROM decision_log
                WHERE timestamp < ?
                {_TIER0_EXCEPT_CLAUSE}
                """,
                (cutoff,),
            ).fetchone()[0]
        else:
            deletable = total_old
        return {
            "cutoff": cutoff,
            "total_rows_older_than_cutoff": total_old,
            "protected_by_tier0_anchor": total_old - deletable,
            "deletable": deletable,
            "tier0_table_present": int(has_tier0),
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
            f"DECISION_LOG_RETENTION_KEEP_DAYS_BELOW_MINIMUM: --keep-days={keep_days} "
            f"< MIN_KEEP_DAYS={MIN_KEEP_DAYS}. The documented consumer-window audit "
            f"(module docstring) requires at least {MIN_KEEP_DAYS} days of margin over "
            f"the longest confirmed bounded consumer (query_no_trade_cases, 24h)."
        )

    stats = report(db_path, keep_days=keep_days)
    if dry_run:
        return stats

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    has_tier0 = stats["tier0_table_present"] == 1
    cutoff = stats["cutoff"]
    select_sql = _candidate_select_sql(has_tier0)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = None
    total_deleted = 0
    try:
        while True:
            with db_writer_lock(db_path, WriteClass.BULK):
                ids = [
                    row[0]
                    for row in conn.execute(select_sql, (cutoff, chunk_size)).fetchall()
                ]
                if not ids:
                    break
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM decision_log WHERE id IN ({placeholders})", ids
                )
                conn.commit()
                total_deleted += len(ids)
            logger.info("decision_log retention: deleted %d rows so far", total_deleted)
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

    print(f"{'[DRY-RUN] ' if dry_run else ''}decision_log retention on {db_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if dry_run:
        print(f"\n[dry-run] would delete {stats['deletable']} rows (no writes made)")


if __name__ == "__main__":
    main()
