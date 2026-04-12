"""ETL: Market price history with timing context.

Source: rainstorm.db:token_price_log
       JOIN rainstorm.db:market_events raw_response for token -> event mapping
       Falls back to token_price_log city/date when archived event JSON is absent.
       JOIN rainstorm.db:settlements for resolution time
  — token_price_log schema differs between rainstorm and zeus-shared;
    this script is a no-op when rainstorm.db is absent.
    Data was already ETL'd on initial run.
Target: zeus-shared.db:market_price_history

Computes:
- hours_since_open: (observed_at - market created_at) in hours
- hours_to_resolution: (settled_at - observed_at) in hours

ZEUS_SPEC §14.3 ETL 3:
  "365K prices / 1,643 settlements = 222 price snapshots per settlement.
  Zeus currently uses 0 of these 222 intermediate prices."
"""

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_shared_connection as get_connection, init_schema

logger = logging.getLogger(__name__)

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


@dataclass(frozen=True)
class TokenMarketMeta:
    """Stable mapping from a CLOB token to its Polymarket event/bin context."""

    market_slug: str
    city: str | None = None
    target_date: str | None = None
    range_label: str | None = None
    condition_id: str | None = None
    opened_at: str | None = None


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO8601 timestamp, tolerant of various formats."""
    if not ts:
        return None
    try:
        # Handle both +00:00 and Z suffixes
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        # Strip timezone for consistent comparisons (rainstorm has mixed tz/naive)
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _hours_between(earlier: datetime | None, later: datetime | None) -> float | None:
    """Compute hours between two datetimes, return None if either is missing."""
    if earlier is None or later is None:
        return None
    delta = (later - earlier).total_seconds() / 3600.0
    if delta < 0:
        return None
    return round(delta, 2)


def _json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _same_label(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return " ".join(str(left).split()) == " ".join(str(right).split())


def _extract_token_market_meta(row: sqlite3.Row) -> list[tuple[str, TokenMarketMeta]]:
    """Extract token mappings from one Rainstorm market_events.raw_response row.

    Rainstorm stored the full Gamma event in every market row. The selected bin
    must be matched by the top-level market_id; using markets[0] maps every bin
    in an event to the first market's tokens.
    """
    raw_response = row["raw_response"]
    if not raw_response:
        return []
    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return []

    raw_event = payload.get("raw_event") or {}
    markets = raw_event.get("markets") or []
    if not isinstance(markets, list):
        return []

    market_id = str(payload.get("market_id") or "")
    group_title = payload.get("groupItemTitle") or row["range_label"]

    selected_market = None
    if market_id:
        selected_market = next(
            (market for market in markets if str(market.get("id") or "") == market_id),
            None,
        )
    if selected_market is None and group_title:
        selected_market = next(
            (
                market for market in markets
                if _same_label(market.get("groupItemTitle"), group_title)
            ),
            None,
        )
    if selected_market is None:
        return []

    token_ids = [str(token) for token in _json_list(selected_market.get("clobTokenIds")) if token]
    if not token_ids:
        return []

    event_id = str(row["event_id"] or payload.get("event_id") or raw_event.get("id") or "")
    market_slug = event_id or str(raw_event.get("slug") or selected_market.get("slug") or selected_market.get("id") or "")
    if not market_slug:
        return []

    meta = TokenMarketMeta(
        market_slug=market_slug,
        city=row["city"],
        target_date=row["target_date"],
        range_label=selected_market.get("groupItemTitle") or row["range_label"],
        condition_id=selected_market.get("conditionId") or row["condition_id"],
        opened_at=raw_event.get("createdAt") or row["imported_at"],
    )
    return [(token_id, meta) for token_id in token_ids]


def _merge_token_meta(
    token_market: dict[str, TokenMarketMeta],
    token_id: str,
    meta: TokenMarketMeta,
) -> bool:
    existing = token_market.get(token_id)
    if existing is None:
        token_market[token_id] = meta
        return False
    return existing.market_slug != meta.market_slug


def _load_rainstorm_token_market(rs: sqlite3.Connection) -> tuple[dict[str, TokenMarketMeta], int, int]:
    token_market: dict[str, TokenMarketMeta] = {}
    raw_rows = 0
    conflicts = 0
    for row in rs.execute("""
        SELECT city, target_date, event_id, condition_id, range_label, raw_response, imported_at
        FROM market_events
        WHERE raw_response IS NOT NULL
          AND raw_response != ''
    """):
        raw_rows += 1
        for token_id, meta in _extract_token_market_meta(row):
            if _merge_token_meta(token_market, token_id, meta):
                conflicts += 1
    return token_market, raw_rows, conflicts


def _load_zeus_token_market(zeus: sqlite3.Connection) -> dict[str, TokenMarketMeta]:
    token_market: dict[str, TokenMarketMeta] = {}
    for row in zeus.execute("""
        SELECT market_slug, token_id, city, target_date, range_label, condition_id, created_at
        FROM market_events
        WHERE token_id IS NOT NULL
          AND token_id != ''
    """):
        token_market[row["token_id"]] = TokenMarketMeta(
            market_slug=row["market_slug"],
            city=row["city"],
            target_date=row["target_date"],
            range_label=row["range_label"],
            condition_id=row["condition_id"],
            opened_at=row["created_at"],
        )
    return token_market


def _synthetic_market_slug(city: str | None, target_date: str | None) -> str:
    if not city or not target_date:
        return ""
    city_key = "-".join(str(city).strip().lower().split())
    return f"weather:{city_key}:{target_date}"


def _load_token_price_fallbacks(
    rs: sqlite3.Connection,
    token_market: dict[str, TokenMarketMeta],
) -> tuple[int, int]:
    """Fill token mappings from token_price_log city/date when event JSON is absent.

    These synthetic keys are intentionally event-level, not bin-level. Rainstorm's
    late-March/April token_price_log rows carry city/date but blank range_label,
    while the matching archived market_events rows are absent. A city/date key is
    safer than leaving the price curve unassigned or guessing a Polymarket id.
    """
    added = 0
    conflicts = 0
    for row in rs.execute("""
        SELECT token_id, city, target_date, MIN(observed_at) AS first_observed_at
        FROM token_price_log
        WHERE token_id != 'test-token-123'
          AND price > 0
          AND observed_at IS NOT NULL
          AND city IS NOT NULL
          AND city != ''
          AND target_date IS NOT NULL
          AND target_date != ''
        GROUP BY token_id, city, target_date
    """):
        slug = _synthetic_market_slug(row["city"], row["target_date"])
        if not slug:
            continue
        meta = TokenMarketMeta(
            market_slug=slug,
            city=row["city"],
            target_date=row["target_date"],
            opened_at=row["first_observed_at"],
        )
        if row["token_id"] in token_market:
            if token_market[row["token_id"]].market_slug != slug:
                conflicts += 1
            continue
        token_market[row["token_id"]] = meta
        added += 1
    return added, conflicts


def _market_open_lookup(token_market: dict[str, TokenMarketMeta]) -> dict[str, datetime]:
    market_open: dict[str, datetime] = {}
    for meta in token_market.values():
        opened_at = _parse_iso(meta.opened_at or "")
        if opened_at is None:
            continue
        current = market_open.get(meta.market_slug)
        if current is None or opened_at < current:
            market_open[meta.market_slug] = opened_at
    return market_open


def _prepare_token_market_temp(
    zeus: sqlite3.Connection,
    token_market: dict[str, TokenMarketMeta],
) -> None:
    zeus.execute("DROP TABLE IF EXISTS temp.token_market_map")
    zeus.execute("""
        CREATE TEMP TABLE token_market_map (
            token_id TEXT PRIMARY KEY,
            market_slug TEXT NOT NULL,
            opened_at TEXT
        )
    """)
    zeus.executemany(
        "INSERT OR REPLACE INTO token_market_map (token_id, market_slug, opened_at) VALUES (?, ?, ?)",
        ((token_id, meta.market_slug, meta.opened_at) for token_id, meta in token_market.items()),
    )


def _count_slug_updates(zeus: sqlite3.Connection) -> int:
    return int(zeus.execute("""
        SELECT COUNT(*)
        FROM market_price_history AS mph
        JOIN token_market_map AS tm ON tm.token_id = mph.token_id
        WHERE mph.market_slug != tm.market_slug
    """).fetchone()[0] or 0)


def _apply_slug_updates(zeus: sqlite3.Connection) -> int:
    zeus.execute("""
        UPDATE market_price_history
        SET market_slug = (
            SELECT tm.market_slug
            FROM token_market_map AS tm
            WHERE tm.token_id = market_price_history.token_id
        )
        WHERE EXISTS (
            SELECT 1
            FROM token_market_map AS tm
            WHERE tm.token_id = market_price_history.token_id
              AND tm.market_slug != market_price_history.market_slug
        )
    """)
    return zeus.execute("SELECT changes()").fetchone()[0]


def _count_hours_since_open_updates(zeus: sqlite3.Connection) -> int:
    return int(zeus.execute("""
        SELECT COUNT(*)
        FROM market_price_history AS mph
        JOIN token_market_map AS tm ON tm.token_id = mph.token_id
        WHERE mph.hours_since_open IS NULL
          AND tm.opened_at IS NOT NULL
          AND julianday(mph.recorded_at) >= julianday(tm.opened_at)
    """).fetchone()[0] or 0)


def _apply_hours_since_open_updates(zeus: sqlite3.Connection) -> int:
    zeus.execute("""
        UPDATE market_price_history
        SET hours_since_open = ROUND(
            (
                julianday(recorded_at) - julianday(
                    (SELECT tm.opened_at FROM token_market_map AS tm WHERE tm.token_id = market_price_history.token_id)
                )
            ) * 24.0,
            2
        )
        WHERE hours_since_open IS NULL
          AND EXISTS (
              SELECT 1
              FROM token_market_map AS tm
              WHERE tm.token_id = market_price_history.token_id
                AND tm.opened_at IS NOT NULL
                AND julianday(market_price_history.recorded_at) >= julianday(tm.opened_at)
          )
    """)
    return zeus.execute("SELECT changes()").fetchone()[0]


def run_etl(*, dry_run: bool = False, update_slugs_only: bool = False) -> dict:
    # Source table (token_price_log with observed_at) only exists in rainstorm.db
    if not RAINSTORM_DB.exists():
        msg = "Rainstorm DB not found — market_price_history already ETL'd"
        logger.info(msg)
        print(msg)
        return {"status": "noop", "reason": "rainstorm_db_not_found"}

    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0]
    print(f"market_price_history has {existing} existing rows. Running incremental sync...")

    token_market, raw_market_rows, raw_conflicts = _load_rainstorm_token_market(rs)
    rainstorm_token_count = len(token_market)
    zeus_token_market = _load_zeus_token_market(zeus)
    for token_id, meta in zeus_token_market.items():
        token_market.setdefault(token_id, meta)
    fallback_token_count, fallback_conflicts = _load_token_price_fallbacks(rs, token_market)
    market_open = _market_open_lookup(token_market)
    print(
        "Token mappings loaded: "
        f"{len(token_market)} total "
        f"({rainstorm_token_count} from Rainstorm raw_response, "
        f"{len(zeus_token_market)} from Zeus market_events, "
        f"{fallback_token_count} from token_price_log city/date fallback, "
        f"{raw_conflicts} raw conflicts)"
    )
    if fallback_conflicts:
        print(f"Token price fallback conflicts skipped: {fallback_conflicts}")
    print(f"Raw Rainstorm market rows scanned: {raw_market_rows}")
    print(f"Market open times loaded: {len(market_open)} events")

    # Build settlement time lookup: (city, target_date) → settled_at
    settle_rows = rs.execute("""
        SELECT city, target_date, settled_at
        FROM settlements
        WHERE settled_at IS NOT NULL
    """).fetchall()
    settle_time = {}
    for s in settle_rows:
        key = (s["city"], s["target_date"])
        ts = _parse_iso(s["settled_at"])
        if ts:
            settle_time[key] = ts
    print(f"Settlement times loaded: {len(settle_time)} settlements")

    _prepare_token_market_temp(zeus, token_market)
    slug_updates = _count_slug_updates(zeus)
    hours_since_open_updates = _count_hours_since_open_updates(zeus)
    print(f"Existing market_price_history slug updates pending: {slug_updates:,}")
    print(f"Existing hours_since_open updates pending: {hours_since_open_updates:,}")

    # Read token_price_log
    source_count = rs.execute("""
        SELECT COUNT(*)
        FROM token_price_log
        WHERE token_id != 'test-token-123'
          AND price > 0
          AND observed_at IS NOT NULL
    """).fetchone()[0]
    print(f"Source rows: {source_count:,}")

    imported = 0
    matched_source_rows = 0
    if update_slugs_only:
        print("Skipping source row import (--update-slugs-only).")
    else:
        rows = rs.execute("""
            SELECT token_id, city, target_date, price, observed_at
            FROM token_price_log
            WHERE token_id != 'test-token-123'
              AND price > 0
              AND observed_at IS NOT NULL
        """)

        batch = []
        for r in rows:
            token_id = r["token_id"]
            observed_at = _parse_iso(r["observed_at"])
            if observed_at is None:
                continue

            meta = token_market.get(token_id)
            market_slug = meta.market_slug if meta else ""
            if meta:
                matched_source_rows += 1
            city = r["city"]
            target_date = r["target_date"]

            # Compute timing metrics
            hours_since_open = None
            hours_to_resolution = None

            if market_slug:
                hours_since_open = _hours_between(market_open.get(market_slug), observed_at)
            if city and target_date:
                settle_key = (city, target_date)
                if settle_key in settle_time:
                    hours_to_resolution = _hours_between(observed_at, settle_time[settle_key])

            if dry_run:
                continue

            batch.append((
                market_slug or "", token_id, r["price"],
                r["observed_at"], hours_since_open, hours_to_resolution
            ))

            if len(batch) >= 10000:
                zeus.executemany("""
                    INSERT OR IGNORE INTO market_price_history
                    (market_slug, token_id, price, recorded_at,
                     hours_since_open, hours_to_resolution)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, batch)
                imported += len(batch)
                batch = []

        if batch:
            zeus.executemany("""
                INSERT OR IGNORE INTO market_price_history
                (market_slug, token_id, price, recorded_at,
                 hours_since_open, hours_to_resolution)
                VALUES (?, ?, ?, ?, ?, ?)
            """, batch)
            imported += len(batch)

    applied_slug_updates = 0
    applied_hours_since_open_updates = 0
    if not dry_run:
        applied_slug_updates = _apply_slug_updates(zeus)
        applied_hours_since_open_updates = _apply_hours_since_open_updates(zeus)
        zeus.commit()

    final = zeus.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0]

    # Summary stats
    with_resolution = zeus.execute(
        "SELECT COUNT(*) FROM market_price_history WHERE hours_to_resolution IS NOT NULL"
    ).fetchone()[0]

    rs.close()
    zeus.close()

    print(f"Source rows with token mapping: {matched_source_rows:,}")
    print(f"Slug updates applied: {applied_slug_updates:,}")
    print(f"hours_since_open updates applied: {applied_hours_since_open_updates:,}")
    print(f"Final row count: {final:,}")
    print(f"With hours_to_resolution: {with_resolution:,}")

    return {
        "dry_run": dry_run,
        "update_slugs_only": update_slugs_only,
        "source_rows": source_count,
        "source_rows_with_token_mapping": matched_source_rows,
        "token_mappings": len(token_market),
        "fallback_token_mappings": fallback_token_count,
        "pending_slug_updates": slug_updates,
        "pending_hours_since_open_updates": hours_since_open_updates,
        "applied_slug_updates": applied_slug_updates,
        "applied_hours_since_open_updates": applied_hours_since_open_updates,
        "imported": final,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inspect source/mapping counts without writing")
    parser.add_argument(
        "--update-slugs-only",
        action="store_true",
        help="Backfill market_slug on existing rows without re-importing source prices",
    )
    args = parser.parse_args()

    result = run_etl(dry_run=args.dry_run, update_slugs_only=args.update_slugs_only)
    print(f"\nDone: {result}")
