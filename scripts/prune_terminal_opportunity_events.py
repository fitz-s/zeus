#!/usr/bin/env python3
# Created: 2026-06-16
# Last audited: 2026-07-24
# Authority basis: GOAL #83 (continuous settlement-graded alpha) + RULE 1 (a "no fresh
#   candidates / EXECUTABLE_SNAPSHOT_BLOCKED" symptom is OUR defect). Twin gap to the
#   trades_wal_checkpoint backstop (2026-06-16): the EDLI prune organ
#   (_edli_prune_pending_working_set -> store.archive_*) only MARKS rows
#   processing_status='expired'/'ignored'/'dead_letter' — it NEVER physically deletes
#   them. So opportunity_events / opportunity_event_processing grow UNBOUNDED:
#   measured 2026-06-16 = 7,050,590 terminal rows, zeus-world.db = 42 GB. Writers then
#   hold the single WAL write lock 14 s+ per txn, so the executable-market-snapshot
#   capture loses every write-lock race ('database is locked') -> executable_market_snapshots
#   stays EMPTY -> every forecast family fails _executable_snapshot_gate
#   (EXECUTABLE_SNAPSHOT_BLOCKED) -> the spine never prices a forecast family -> zero
#   forecast-lane crosses. This is the missing PHYSICAL-retention organ.
#
# What it does (reversible-in-spirit: only deletes UNAMBIGUOUSLY-dead queue rows):
#   * KEEP  = events with a live processing row (pending/processing/claimed, ALL consumers)
#             UNION events created within --keep-hours (default 3h, covers anything mid-flight).
#   * DELETE every opportunity_event_processing row NOT in KEEP, and every opportunity_events
#             row NOT in KEEP, in small batches under a long busy_timeout so it COOPERATES
#             with the live daemons (live-trading / venue-heartbeat / data-ingest) instead of
#             starving them. FK enforcement is off in this DB (verified) so order is free; we
#             delete processing first, then events.
#   It does NOT touch settlements / calibration / ENS / risk_state / positions (separate
#   tables and DBs). It does NOT VACUUM by default (auto_vacuum=0, disk tight): freed pages
#   are reused by future inserts so the DB stops GROWING and the active B-trees shrink, which
#   is what restores write speed. Pass --vacuum to reclaim file space (needs a quiet window).
#
# Safety: the KEEP event_ids are written to --keep-out BEFORE any delete (audit/rollback ref).
import argparse
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.state.db_writer_lock import (
    WriteClass,
    connect_with_cutover_lease,
    db_writer_lock,
)

DEFAULT_DB = "/Users/leofitz/zeus/state/zeus-world.db"
LIVE_STATUSES = ("pending", "processing", "claimed")


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


@contextmanager
def _prune_connection(
    db_path: Path,
    *,
    busy_timeout_ms: int,
) -> Iterator[sqlite3.Connection]:
    """Hold the shared cutover lease and BULK writer lock until close."""

    conn = connect_with_cutover_lease(
        db_path,
        canonical_db_path=db_path,
        timeout=busy_timeout_ms / 1000.0,
    )
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    try:
        with db_writer_lock(db_path, WriteClass.BULK):
            yield conn
    finally:
        conn.close()


def _prune(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    """Run the retention sweep inside the caller-held writer critical section."""

    fk = _count(conn, "PRAGMA foreign_keys")
    print(f"foreign_keys={fk} (expect 0)", flush=True)

    # 1) Build the KEEP set as a real (persistent for this connection) temp table so every
    #    batch's NOT IN (SELECT ... FROM keep) is an indexed lookup, not a correlated rescan.
    print("building KEEP set (live processing rows + recent events)...", flush=True)
    conn.execute("DROP TABLE IF EXISTS _keep_event_ids")
    conn.execute("CREATE TEMP TABLE _keep_event_ids (event_id TEXT PRIMARY KEY)")
    conn.execute(
        "INSERT OR IGNORE INTO _keep_event_ids (event_id) "
        "SELECT DISTINCT event_id FROM opportunity_event_processing "
        f"WHERE processing_status IN ({','.join('?'*len(LIVE_STATUSES))})",
        LIVE_STATUSES,
    )
    conn.execute(
        "INSERT OR IGNORE INTO _keep_event_ids (event_id) "
        "SELECT event_id FROM opportunity_events "
        "WHERE created_at >= datetime('now', ?)",
        (f"-{args.keep_hours} hours",),
    )
    conn.commit()
    keep_n = _count(conn, "SELECT COUNT(*) FROM _keep_event_ids")
    print(f"KEEP event_ids = {keep_n}", flush=True)
    with open(args.keep_out, "w") as fh:
        for (eid,) in conn.execute("SELECT event_id FROM _keep_event_ids"):
            fh.write(f"{eid}\n")
    print(f"KEEP set written to {args.keep_out}", flush=True)

    start = time.monotonic()

    def _delete_one_batch(table: str, batch: int, where_extra: str) -> int:
        # Retry on transient lock/busy: a live daemon (emit archive-UPDATE, checkpoint)
        # can hold the single WAL write lock past our busy_timeout. Back off and retry the
        # SAME batch rather than crashing — the prune is idempotent (rowid set re-derived).
        backoff = 1.0
        while True:
            try:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE rowid IN ("
                    f"  SELECT rowid FROM {table} t "
                    f"  WHERE t.event_id NOT IN (SELECT event_id FROM _keep_event_ids){where_extra} "
                    f"  LIMIT ?)",
                    (batch,),
                )
                n = cur.rowcount
                conn.commit()
                return n
            except sqlite3.OperationalError as ex:
                msg = str(ex).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                try:
                    conn.rollback()
                except Exception:
                    pass
                if time.monotonic() - start > args.max_seconds:
                    print(f"[{table}] max-seconds during lock backoff, stopping", flush=True)
                    return 0
                print(f"[{table}] lock/busy, backoff {backoff:.1f}s and retry", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 30.0)

    def _batched_delete(table: str, batch: int, where_extra: str = "") -> int:
        total = 0
        rounds = 0
        while True:
            if time.monotonic() - start > args.max_seconds:
                print(f"[{table}] max-seconds reached, stopping at {total} deleted", flush=True)
                break
            n = _delete_one_batch(table, batch, where_extra)
            total += n
            rounds += 1
            if rounds % 10 == 0 or n == 0:
                print(f"[{table}] deleted={total} (last batch={n}, {time.monotonic()-start:.0f}s)", flush=True)
            if n == 0:
                break
            time.sleep(args.sleep)
        return total

    # 2) processing rows first (smaller), then the big payload_json events.
    print("deleting terminal opportunity_event_processing rows...", flush=True)
    p = _batched_delete(
        "opportunity_event_processing",
        args.proc_batch,
        where_extra=" AND t.processing_status NOT IN ('pending','processing','claimed')",
    )
    print(f"processing rows deleted: {p}", flush=True)

    # opportunity_events is APPEND-ONLY provenance: it carries trg_opportunity_events_no_delete
    # (BEFORE DELETE -> RAISE(ABORT)) AND trg_opportunity_events_no_update. It CANNOT be pruned
    # by DELETE and we do NOT attempt it. This is fine for the write-lock unblock: the emit's
    # INSERT into opportunity_events is a cheap append (O(log n) at the B-tree tail, size-
    # independent), while the lock-HOLDING cost is the archive UPDATEs + fetch_pending JOIN that
    # scan the MUTABLE opportunity_event_processing table — which this prune shrinks. The 42 GB
    # append-only events log needs a SEPARATE cold-storage rotation (drop trigger -> archive ->
    # truncate -> re-add trigger, in a quiet window), tracked as a follow-up, NOT done here.
    e = 0
    print("opportunity_events: append-only (no-DELETE trigger) — skipped by design", flush=True)

    if args.vacuum:
        print("VACUUM (reclaim file space)...", flush=True)
        conn.execute("VACUUM")
        print("VACUUM done", flush=True)

    print(f"DONE in {time.monotonic()-start:.0f}s. proc_deleted={p} events_deleted={e} keep={keep_n}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--keep-hours", type=float, default=3.0,
                    help="also keep opportunity_events created within this many hours")
    ap.add_argument("--proc-batch", type=int, default=20000)
    ap.add_argument("--event-batch", type=int, default=5000)
    ap.add_argument("--busy-timeout-ms", type=int, default=60000)
    ap.add_argument("--sleep", type=float, default=0.05, help="seconds between batches")
    ap.add_argument("--max-seconds", type=float, default=3600.0)
    ap.add_argument("--keep-out", default="/tmp/prune_keep_event_ids_2026-06-16.txt")
    ap.add_argument("--vacuum", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 2

    db_path = Path(args.db).resolve()
    with _prune_connection(
        db_path,
        busy_timeout_ms=args.busy_timeout_ms,
    ) as conn:
        return _prune(args, conn)


if __name__ == "__main__":
    raise SystemExit(main())
