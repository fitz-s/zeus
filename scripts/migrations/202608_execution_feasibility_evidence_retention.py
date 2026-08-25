# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: live incident 2026-08-24/25 (RiskGuard storage_capacity DATA_DEGRADED)
#   slice 2 of 2 (paired with 202608_decision_log_retention.py, PR #510) — the
#   second-largest oversized trade-DB table (execution_feasibility_evidence, 25GB,
#   28,277,164 rows as of 2026-08-25, 2026-06-19..present, no prior retention).
# WRITER_LOCK: DML DELETE, writes under db_writer_lock(BULK) per
#   src/state/db_writer_lock.py, same pattern as 202608_decision_log_retention.py.
"""Idempotent retention DELETE for execution_feasibility_evidence (trade DB).

execution_feasibility_evidence is the EDLI pre-submit executable quote/book
feasibility evidence log. Unlike decision_log and executable_market_snapshots,
this table has NO append-only DB trigger (verified: no CREATE TRIGGER references
this table anywhere in src/) and NO external FK-style pointer into it from any
other table (verified: no column named evidence_id exists outside this table's
own PRIMARY KEY) -- so this migration is a plain time-window DELETE, no
trigger-drop-recreate dance and no anchor-protection exception needed.

Consumer-window audit (2026-08-25, law-check before implementation; grep
`execution_feasibility_evidence\\b` across src/ and scripts/, excluding the
separate execution_feasibility_latest compact mirror which is UPSERT-keyed by
(token_id, direction) and needs no retention of its own):

  consumer                                                    window            keep_days=30 safe?
  --------------------------------------------------------------------------------------------------
  cycle_runtime.py:5318 (freshness admission)                 quote_seen_at >=  yes
                                                                julianday(now -
                                                                FRESHNESS_WINDOW_
                                                                DEFAULT) (minutes)
  monitor_refresh.py:919 (latest-key join)                     LIMIT 1, latest   yes
                                                                 key join
  probe_full_live_path_to_submit.py:579                        MAX(quote_seen_at) yes (aggregate over
                                                                 full table         whatever rows remain,
                                                                                    unaffected by DELETE
                                                                                    of OLDER rows)
  check_live_restart_preflight.py:3920                         ORDER BY created_at yes
                                                                DESC LIMIT 1
  orderbook_execution_feasibility_report.py,                   COUNT(*) full-table yes (aggregate stat,
    event_opportunity_report.py                                                    not a specific-row
                                                                                    dependency)
  evaluate_current_regime_capital_advantage.py:1216            operator-supplied   yes (caller picks the
                                                                 date range          range; older range
                                                                                     just returns fewer
                                                                                     rows post-retention,
                                                                                     fails soft not closed)

No consumer reads a specific old evidence_id by external reference and no
consumer has an unbounded historical backfill dependency (unlike
settlement_skill_attribution's decision_log usage in slice 1) -- the longest
confirmed bounded window is a caller-supplied operator date range with no
fixed default, so MIN_KEEP_DAYS here is set from the same floor as slice 1
(7 days, ample margin over the FRESHNESS_WINDOW_DEFAULT-scale live consumers).

Migration semantic policy:
  - DELETE-only; idempotent.
  - Default mode is dry-run (report only). --apply required to write.
  - No protection exception (see above); every row older than --keep-days is
    deletable.
  - One-time prerequisite index: neither of this table's two existing indexes
    (idx_execution_feasibility_evidence_token_time, ..._token_created) leads
    with quote_seen_at alone, so a chunked `WHERE quote_seen_at < cutoff` scan
    against 28M+ rows would degrade to a near-full-table scan per chunk. --apply
    creates `idx_execution_feasibility_evidence_quote_seen_at_only` (plain,
    single-column, IF NOT EXISTS) before the first delete chunk. Building this
    index on 28M rows needs a modest amount of temporary disk itself -- run the
    FIRST --apply pass when a few GB of headroom exists.
  - --keep-days must be >= MIN_KEEP_DAYS (7) or the script refuses to run.
  - Chunked DELETE (default 20000 rows/txn -- larger than decision_log's 2000
    given the 28M-row scale) under db_writer_lock(BULK), lock acquired/released
    per chunk.

VACUUM note: identical to 202608_decision_log_retention.py -- auto_vacuum=0 on
the live trade DB, DELETE alone does not shrink the file or relieve the
storage_capacity gate. See that script's docstring for the full VACUUM path
discussion (external-volume VACUUM INTO / incremental-vacuum conversion).

Usage
-----
    python scripts/migrations/202608_execution_feasibility_evidence_retention.py
        [--apply] [--keep-days N] [--db PATH] [--chunk-size N]

Default is dry-run. Use --apply to delete rows.
--keep-days: retention window in days (default: 30, minimum: 7).
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
MIN_KEEP_DAYS = 7
DEFAULT_CHUNK_SIZE = 20000
_CUTOFF_INDEX_NAME = "idx_execution_feasibility_evidence_quote_seen_at_only"
_CUTOFF_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {_CUTOFF_INDEX_NAME} "
    "ON execution_feasibility_evidence(quote_seen_at)"
)


def _get_default_db_path() -> Path:
    from src.config import STATE_DIR
    return STATE_DIR / "zeus_trades.db"


def _cutoff_str(keep_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S")


def report(db_path: Path, *, keep_days: int) -> dict[str, int]:
    """Dry-run: count deletable rows. Never writes."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cutoff = _cutoff_str(keep_days)
        deletable = conn.execute(
            "SELECT COUNT(*) FROM execution_feasibility_evidence WHERE quote_seen_at < ?",
            (cutoff,),
        ).fetchone()[0]
        return {"cutoff": cutoff, "deletable": deletable}
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
            f"EXECUTION_FEASIBILITY_EVIDENCE_RETENTION_KEEP_DAYS_BELOW_MINIMUM: "
            f"--keep-days={keep_days} < MIN_KEEP_DAYS={MIN_KEEP_DAYS}."
        )

    stats = report(db_path, keep_days=keep_days)
    if dry_run:
        return stats

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    cutoff = stats["cutoff"]
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
                    for row in conn.execute(
                        """
                        SELECT evidence_id FROM execution_feasibility_evidence
                        WHERE quote_seen_at < ?
                        ORDER BY evidence_id
                        LIMIT ?
                        """,
                        (cutoff, chunk_size),
                    ).fetchall()
                ]
                if not ids:
                    break
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM execution_feasibility_evidence WHERE evidence_id IN ({placeholders})",
                    ids,
                )
                conn.commit()
                total_deleted += len(ids)
            logger.info(
                "execution_feasibility_evidence retention: deleted %d rows so far",
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

    print(f"{'[DRY-RUN] ' if dry_run else ''}execution_feasibility_evidence retention on {db_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if dry_run:
        print(f"\n[dry-run] would delete {stats['deletable']} rows (no writes made)")


if __name__ == "__main__":
    main()
