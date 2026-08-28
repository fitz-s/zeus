#!/usr/bin/env python3
# Created: 2026-08-28
# Last reused or audited: 2026-08-28
# Authority basis: external methodology-review requirement (winner's-curse /
#   selection-effect calibration test over the pre-admission universe) —
#   no_trade_regret_events carries later_outcome/would_have_won/
#   would_have_filled columns and a writer (NoTradeRegretLedger
#   .enrich_after_settlement, src/strategy/live_inference/no_trade_regret.py:162)
#   but that writer has NO caller, so all three columns are NULL on every
#   live row. This is the missing one-shot backfill caller.
# WRITER_LOCK: --apply performs DML (UPDATE) only, under db_writer_lock(BULK)
#   per src/state/db_writer_lock.py, precedent scripts/migrations/
#   202608_decision_log_retention.py (chunked lock acquire/commit/release so
#   LIVE writers are never blocked longer than one chunk). Registered in
#   SQLITE_CONNECT_ALLOWLIST (src/state/db_writer_lock.py).
"""Backfill later_outcome / would_have_won / would_have_filled on
``no_trade_regret_events`` for rows joinable to a VERIFIED settlement.

WHY THIS IS NARROWER THAN "every joinable row"
------------------------------------------------
A row is only *gradeable* when it names a concrete traded instrument: a
``condition_id`` (the specific bin/token the candidate was priced against)
and a ``direction`` (``buy_yes``/``buy_no``). Most EDLI rejection stages
(EXECUTOR_EXPRESSIBILITY family-level "no candidate selected", most
TRADE_SCORE auction-exhaustion rows) never reached a specific-bin decision,
so they carry no condition_id/direction — there is no bin to grade a win or
loss against, and this script correctly leaves them alone (WHERE direction
IN ('buy_yes','buy_no') AND condition_id IS NOT NULL). Live evidence
2026-08-28: of 102,348 rows joinable to a VERIFIED settlement by
(city, target_date, metric) since 2026-08-14, only ~1,689 also carry a
condition_id + buy_yes/buy_no direction and are therefore gradeable.

THE JOIN
--------
    no_trade_regret_events   forecasts.market_events      forecasts.settlement_outcomes
    (condition_id, direction) -condition_id-> (range_low,  -(city,target_date,metric)->  grade_receipt
                                                 range_high)  settlement_value,              (Direction Law)
                                                               settlement_unit (VERIFIED)

market_events.outcome (venue-declared YES/NO per condition_id) is NOT used:
live evidence shows it lags settlement_outcomes by days for recent dates (it
still holds the unresolved question-text sentinel for markets whose weather
settlement is already VERIFIED), so joining on it would silently grade zero
rows. range_low/range_high + the VERIFIED settlement_value is the same
input grade_receipt's other production callers already trust
(src/analysis/settlement_guard_report.py, src/cron/settlement_attribution.py,
src/analysis/settlement_skill_attribution.py) — this script reuses their
exact ``grade_receipt`` truth function and their ``_bin_from_market_event``
bin constructor rather than inventing a second win/loss law.

K1 DB SPLIT
-----------
No cross-DB transaction: forecasts.settlement_outcomes/market_events are
read via a read-only ATTACH on the world connection (same sanctioned
pattern as ``open_world_with_forecasts`` in src/cron/settlement_attribution
.py) purely for SELECT; every WRITE is a single-table UPDATE on
no_trade_regret_events through the world connection, chunked under
db_writer_lock(WORLD, BULK) acquired/released per chunk.

Idempotent: only rows with ``would_have_won IS NULL`` are selected; a
second run over the same window enriches 0 rows.

Usage:
    python3 scripts/enrich_no_trade_regret_outcomes.py [--since ISO] [--limit N]
        [--chunk-size N] [--db PATH] [--fcst-db PATH] [--apply]

Default is dry-run (report only, no writes). --apply performs the UPDATEs.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500

_CANDIDATE_SQL = """
    SELECT r.rowid AS rowid, r.regret_event_id, r.event_id, r.rejection_stage,
           r.rejection_reason, r.condition_id, r.direction,
           r.city, r.target_date, r.metric, r.hypothetical_fill_status,
           me.range_low AS range_low, me.range_high AS range_high,
           so.settlement_value AS settlement_value,
           so.settlement_unit AS settlement_unit,
           so.settled_at AS settled_at
    FROM no_trade_regret_events r
    JOIN forecasts.settlement_outcomes so
      ON so.city = r.city AND so.target_date = r.target_date
     AND so.temperature_metric = r.metric AND so.authority = 'VERIFIED'
    JOIN forecasts.market_events me ON me.condition_id = r.condition_id
    WHERE r.would_have_won IS NULL
      AND r.direction IN ('buy_yes', 'buy_no')
      AND r.condition_id IS NOT NULL AND r.condition_id != ''
      AND r.rowid > ?
      {since_clause}
    ORDER BY r.rowid
    LIMIT ?
"""


def _attach_forecasts(conn: sqlite3.Connection, fcst_db_path: Path) -> None:
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "forecasts" not in attached:
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(fcst_db_path),))


class _SettlementStandIn:
    """Minimal settlement stand-in for grade_receipt (same shape used by
    settlement_guard_report.py / settlement_attribution.py / settlement_skill_attribution.py)."""

    __slots__ = ("settlement_value", "settlement_unit")

    def __init__(self, settlement_value: float, settlement_unit: str) -> None:
        self.settlement_value = settlement_value
        self.settlement_unit = settlement_unit


def grade_candidate_row(row: sqlite3.Row) -> tuple[bool, bool, str, str] | None:
    """Grade one candidate row. Returns (won, filled, later_outcome, settlement_proof)
    or None when the row cannot be turned into a gradeable Bin / unit-mismatches."""
    from src.analysis.settlement_guard_report import _bin_from_market_event
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError
    from src.strategy.live_inference.no_trade_regret import classify_fillable_bucket

    bin_obj = _bin_from_market_event(
        row["range_low"], row["range_high"], row["settlement_unit"]
    )
    if bin_obj is None:
        return None
    settlement = _SettlementStandIn(
        float(row["settlement_value"]), str(row["settlement_unit"])
    )
    try:
        graded = grade_receipt(bin_obj, row["direction"], settlement)
    except (UnitMismatchError, ValueError):
        return None

    filled = row["hypothetical_fill_status"] == "EXECUTABLE_AT_DECISION"
    later_outcome = classify_fillable_bucket(
        would_have_won=graded.won, would_have_filled=filled
    )
    proof = (
        f"settlement_outcomes:VERIFIED:{row['city']}:{row['target_date']}:"
        f"{row['metric']}:{row['settled_at']}"
    )
    return graded.won, filled, later_outcome, proof


def run(
    *,
    world_db_path: Path,
    fcst_db_path: Path,
    since: str | None,
    limit: int | None,
    chunk_size: int,
    apply: bool,
) -> dict[str, int]:
    from src.state.db_writer_lock import WriteClass, db_writer_lock
    from src.strategy.live_inference.no_trade_regret import (
        NoTradeRegretHindsightError,
        NoTradeRegretLedger,
    )

    since_clause = "AND r.created_at >= ?" if since else ""
    sql = _CANDIDATE_SQL.format(since_clause=since_clause)

    stats = {
        "candidates_seen": 0,
        "won": 0,
        "lost": 0,
        "filled": 0,
        "not_bin_gradeable": 0,
        "enrich_conflict": 0,
        "enriched": 0,
    }

    if apply:
        conn = sqlite3.connect(str(world_db_path))
    else:
        conn = sqlite3.connect(f"file:{world_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        _attach_forecasts(conn, fcst_db_path)
        ledger = NoTradeRegretLedger(conn)

        last_rowid = 0
        while True:
            remaining = None if limit is None else limit - stats["candidates_seen"]
            if remaining is not None and remaining <= 0:
                break
            fetch_n = chunk_size if remaining is None else min(chunk_size, remaining)
            params: list[object] = [last_rowid]
            if since:
                params.append(since)
            params.append(fetch_n)
            rows = conn.execute(sql, params).fetchall()
            if not rows:
                break

            enrich_batch: list[tuple[str, str, str, str, bool, bool, str]] = []
            for row in rows:
                stats["candidates_seen"] += 1
                last_rowid = row["rowid"]
                graded = grade_candidate_row(row)
                if graded is None:
                    stats["not_bin_gradeable"] += 1
                    continue
                won, filled, later_outcome, proof = graded
                if won:
                    stats["won"] += 1
                else:
                    stats["lost"] += 1
                if filled:
                    stats["filled"] += 1
                enrich_batch.append(
                    (
                        row["event_id"],
                        row["rejection_stage"],
                        row["rejection_reason"],
                        later_outcome,
                        won,
                        filled,
                        proof,
                    )
                )

            if apply and enrich_batch:
                with db_writer_lock(world_db_path, WriteClass.BULK):
                    for (
                        event_id,
                        rejection_stage,
                        rejection_reason,
                        later_outcome,
                        won,
                        filled,
                        proof,
                    ) in enrich_batch:
                        try:
                            ledger.enrich_after_settlement(
                                event_id=event_id,
                                rejection_stage=rejection_stage,
                                rejection_reason=rejection_reason,
                                later_outcome=later_outcome,
                                would_have_won=won,
                                would_have_filled=filled,
                                settlement_proof=proof,
                            )
                            stats["enriched"] += 1
                        except NoTradeRegretHindsightError:
                            stats["enrich_conflict"] += 1
                    conn.commit()
            elif enrich_batch:
                stats["enriched"] += len(enrich_batch)

            logger.info(
                "chunk done: candidates_seen=%d enriched=%d (last_rowid=%d)",
                stats["candidates_seen"], stats["enriched"], last_rowid,
            )
    finally:
        conn.close()

    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write UPDATEs (default: dry-run report only).")
    parser.add_argument("--since", default=None, help="Only rows with created_at >= this ISO timestamp.")
    parser.add_argument("--limit", type=int, default=None, help="Max candidate rows to process.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--db", type=Path, default=None, help="World DB path (default: canonical zeus-world.db).")
    parser.add_argument("--fcst-db", type=Path, default=None, help="Forecasts DB path (default: canonical zeus-forecasts.db).")
    args = parser.parse_args()

    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH

    world_db_path = args.db or ZEUS_WORLD_DB_PATH
    fcst_db_path = args.fcst_db or ZEUS_FORECASTS_DB_PATH

    logger.info(
        "world_db=%s fcst_db=%s apply=%s since=%s limit=%s chunk_size=%d",
        world_db_path, fcst_db_path, args.apply, args.since, args.limit, args.chunk_size,
    )

    stats = run(
        world_db_path=world_db_path,
        fcst_db_path=fcst_db_path,
        since=args.since,
        limit=args.limit,
        chunk_size=args.chunk_size,
        apply=args.apply,
    )

    print(f"{'[APPLY] ' if args.apply else '[DRY-RUN] '}no_trade_regret_events outcome enrichment")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if not args.apply:
        print(f"\n[dry-run] would enrich {stats['enriched']} rows (no writes made)")


if __name__ == "__main__":
    main()
