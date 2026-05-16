# Created: 2026-05-11
# Last reused or audited: 2026-05-16
# Authority basis: PLAN.md §10, critic v4 ACCEPT 2026-05-11; docs/operations/task_2026-05-16_deep_alignment_audit/REPORT.md Finding #4
"""Operator-invoked backfill for harvester settlements outside the 30-day live window.

Usage:
    python -m scripts.backfill_harvester_settlements [--days N] [--max-wall-seconds N]

This script fetches ALL closed Polymarket events (no 30-day cutoff) and writes
settlement truth rows for any that are missing.  It is the ONLY sanctioned
unbounded paginator variant.  The live harvester ticks (src/ingest/harvester_truth_writer.py
and src/execution/harvester.py) are intentionally capped at 30 days; this script
handles the "stuck market" recovery case (PLAN §10, INV-Harvester-Liveness).

Physical isolation guarantee (PLAN §10 antibody):
- This script does NOT import _fetch_open_settling_markets from
  src.ingest.harvester_truth_writer or _fetch_settled_events from
  src.execution.harvester.  The paginate loop below is hand-written.
- Downstream write surfaces (write_settlement_truth_for_open_markets,
  _extract_resolved_market_outcomes) are imported from the ingest twin
  because they sit *below* the paginator and are correctness-load-bearing.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import httpx

# Downstream write surfaces — importable (sit below the paginator antibody surface)
from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets
from src.data.market_scanner import GAMMA_BASE
from src.state.db import get_forecasts_connection

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Standalone backfill constants — NOT shared with live harvester ticks.
# The live ticks have _CLOSED_EVENTS_MAX_WALL_SECONDS = 120 (2 min).
# Backfill runs operator-attended with a longer ceiling.
_BACKFILL_MAX_WALL_SECONDS = 900    # 15 min operator-attended wall-cap
_BACKFILL_PAGE_LIMIT = 100


def _fetch_all_closed_events(
    *,
    cutoff_days: int | None,
    max_wall_seconds: int,
) -> list[dict]:
    """Standalone paginator — does NOT call live harvester paginator functions.

    Parameters
    ----------
    cutoff_days:
        If None, fetch all closed events (unbounded by date).
        If set, stop when oldest endDate in a batch < now - cutoff_days.
    max_wall_seconds:
        Hard wall-cap; logs a warning and truncates if exceeded.
    """
    cutoff_iso: str | None = None
    if cutoff_days is not None:
        from datetime import timedelta
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=cutoff_days)
        ).isoformat()

    start_wall = time.monotonic()
    results: list[dict] = []
    offset = 0

    while True:
        elapsed = time.monotonic() - start_wall
        if elapsed > max_wall_seconds:
            logger.warning(
                "backfill paginator: wall-cap %ds hit at offset=%d after %.0fs; truncating",
                max_wall_seconds,
                offset,
                elapsed,
            )
            break

        try:
            resp = httpx.get(
                f"{GAMMA_BASE}/events",
                params={
                    "closed": "true",
                    "limit": _BACKFILL_PAGE_LIMIT,
                    "offset": offset,
                    "order": "endDate",
                    "ascending": "false",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            batch = resp.json()
        except httpx.HTTPError as exc:
            logger.error("backfill: Gamma API error at offset=%d: %s", offset, exc)
            break

        if not batch:
            break

        results.extend(batch)
        logger.info("backfill: fetched %d events (total so far: %d)", len(batch), len(results))

        if cutoff_iso:
            oldest_end = min(
                (m.get("endDate", "") for m in batch if m.get("endDate")),
                default="",
            )
            if oldest_end and oldest_end < cutoff_iso:
                logger.info("backfill: reached cutoff (%s); stopping pagination", cutoff_iso)
                break

        if len(batch) < _BACKFILL_PAGE_LIMIT:
            break
        offset += _BACKFILL_PAGE_LIMIT

    # Dedup by (conditionId or id)
    seen: set[str] = set()
    deduped: list[dict] = []
    for ev in results:
        key = str(ev.get("conditionId") or ev.get("id") or "")
        if not key:
            deduped.append(ev)
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(ev)

    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill harvester settlements")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="Only backfill events closed within the last N days (default: all)",
    )
    parser.add_argument(
        "--max-wall-seconds",
        type=int,
        default=_BACKFILL_MAX_WALL_SECONDS,
        metavar="N",
        help=f"Wall-cap in seconds (default: {_BACKFILL_MAX_WALL_SECONDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process but do not write to DB",
    )
    args = parser.parse_args()

    logger.info(
        "backfill_harvester_settlements starting: days=%s max_wall=%ds dry_run=%s",
        args.days,
        args.max_wall_seconds,
        args.dry_run,
    )

    events = _fetch_all_closed_events(
        cutoff_days=args.days,
        max_wall_seconds=args.max_wall_seconds,
    )
    logger.info("backfill: fetched %d unique events total", len(events))

    if not events:
        logger.info("backfill: no events to process")
        return 0

    forecasts_conn = get_forecasts_connection()
    try:
        result = write_settlement_truth_for_open_markets(
            forecasts_conn,
            dry_run=args.dry_run,
        )
        logger.info("backfill: write_settlement_truth result: %s", result)
    finally:
        forecasts_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
