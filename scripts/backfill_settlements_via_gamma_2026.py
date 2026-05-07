#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: operator authorization 2026-05-07 — Gamma backfill re-enabled
#                  for LOW markets (always Gamma-internal) and HIGH post-2026-02-20
#                  (Polymarket switched from UMA OO to internal automatic resolver).
#                  Ref: architecture/fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15
"""Backfill settlements_v2 from Polymarket Gamma API for two gaps:

1. HIGH post-2026-02-20: After block 83,275,667 Polymarket moved from UMA OO V2 to
   internal automatic resolver (0x69c47De9D4D3Dad79590d61b9e05918E03775f24).
   These markets are closed+resolved in Gamma but have zero chain-level UMA events.

2. LOW all dates (2026-04-15+): LOW markets never used UMA; always Gamma-internal.

Resolution mechanism: outcomePrices=["1","0"] on the YES outcome = YES won.
Winning temperature = parsed from the question text via _parse_temp_range.

Idempotency: INSERT OR IGNORE on (city, target_date, temperature_metric) —
existing rows (from UMA backfill or live harvester) are never overwritten.

Run:
    source .venv/bin/activate
    python scripts/backfill_settlements_via_gamma_2026.py [--dry-run]
    python scripts/backfill_settlements_via_gamma_2026.py --from-date 2026-02-21 --to-date 2026-05-07
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_gamma")

GAMMA_BASE = "https://gamma-api.polymarket.com"
WEATHER_TAG_ID = 84
# Polymarket internal automatic resolver (confirmed 2026-05-07 probe)
POLYMARKET_INTERNAL_RESOLVER = "0x69c47De9D4D3Dad79590d61b9e05918E03775f24"

# Gap 1: HIGH markets switch to Gamma after last UMA block 83,275,667 ≈ 2026-02-20
HIGH_GAMMA_CUTOVER = "2026-02-21"
# Gap 2: LOW markets — first closed LOW event was 2026-04-15
LOW_GAMMA_START = "2026-04-15"

DEFAULT_FROM_DATE = "2026-02-21"  # covers both gaps


def _gamma_get(path: str, params: dict | None = None, *, retries: int = 3) -> list | dict:
    import httpx
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.get(f"{GAMMA_BASE}{path}", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _fetch_all_closed_weather_events(from_date: str, to_date: str) -> list[dict]:
    """Fetch all closed weather temperature events in [from_date, to_date]."""
    all_events: list[dict] = []
    offset = 0
    limit = 100
    while True:
        batch = _gamma_get("/events", {
            "tag_id": WEATHER_TAG_ID,
            "closed": "true",
            "limit": limit,
            "offset": offset,
        })
        if not batch:
            break
        # Filter to temperature events in date range
        for e in batch:
            ed = (e.get("endDate") or "")[:10]
            if not ed:
                continue
            if ed < from_date or ed > to_date:
                continue
            title = (e.get("title") or "").lower()
            if "temperature" not in title:
                continue
            all_events.append(e)
        if len(batch) < limit:
            break
        offset += limit
    logger.info("Fetched %d closed weather events in %s..%s", len(all_events), from_date, to_date)
    return all_events


def _find_winning_market(event: dict) -> dict | None:
    """Return the YES-winning market bin for a resolved event, or None."""
    markets = event.get("markets") or []
    for m in markets:
        prices_raw = m.get("outcomePrices")
        if not prices_raw:
            continue
        try:
            import ast
            prices = ast.literal_eval(str(prices_raw)) if isinstance(prices_raw, str) else list(prices_raw)
            yes_price = float(prices[0]) if prices else None
        except Exception:
            continue
        # Resolved YES: outcomePrices[0] == "1" (YES outcome)
        if yes_price is not None and yes_price >= 0.99:
            return m
    return None


def _parse_event_date(event: dict) -> str | None:
    """Extract YYYY-MM-DD from event endDate."""
    ed = event.get("endDate") or ""
    return ed[:10] if len(ed) >= 10 else None


def run_backfill(*, from_date: str, to_date: str, dry_run: bool = False) -> dict:
    from src.config import cities_by_name
    from src.state.db import get_world_connection
    from src.data.market_scanner import _match_city, _parse_temp_range, infer_temperature_metric
    from src.ingest.harvester_truth_writer import _write_settlement_truth, _lookup_settlement_obs
    from src.contracts.settlement_semantics import SettlementSemantics

    conn = get_world_connection()
    conn.row_factory = None  # use plain tuples for lightweight queries

    # Pre-fetch existing (city, date, metric) keys to enforce idempotency
    existing: set[tuple[str, str, str]] = set()
    for row in conn.execute(
        "SELECT city, target_date, temperature_metric FROM settlements_v2"
    ).fetchall():
        existing.add((row[0], row[1], row[2]))
    logger.info("Pre-existing settlements_v2 rows: %d", len(existing))

    events = _fetch_all_closed_weather_events(from_date, to_date)

    total = processed = skipped_no_city = skipped_no_winner = 0
    skipped_exists = skipped_no_obs = errors = 0
    written_high = written_low = 0

    for event in events:
        total += 1
        title = event.get("title") or ""
        slug = event.get("slug") or ""
        target_date = _parse_event_date(event)
        if not target_date:
            continue

        # Determine metric (high/low) from event title
        metric = infer_temperature_metric(title)

        # Skip HIGH events that are already covered by UMA backfill
        # (before the Gamma cutover date)
        if metric == "high" and target_date < HIGH_GAMMA_CUTOVER:
            continue
        if metric == "low" and target_date < LOW_GAMMA_START:
            continue

        # Match city
        city = _match_city(title, slug)
        if city is None:
            skipped_no_city += 1
            continue

        # Check idempotency
        key = (city.name, target_date, metric)
        if key in existing:
            skipped_exists += 1
            continue

        # Find YES-winning market bin
        winner_market = _find_winning_market(event)
        if winner_market is None:
            skipped_no_winner += 1
            continue

        # Parse bin bounds from winning market question
        question = (winner_market.get("question") or
                    winner_market.get("groupItemTitle") or "")
        pm_bin_lo, pm_bin_hi = _parse_temp_range(question)

        # Need at least one bound to write a meaningful settlement
        if pm_bin_lo is None and pm_bin_hi is None:
            skipped_no_winner += 1
            continue

        # Derive event slug
        city_slug = city.name.lower().replace(" ", "_")
        event_slug = f"gamma_backfill_{city_slug}_{target_date}_{metric}"

        if dry_run:
            logger.info(
                "DRY-RUN: %s %s %s bin=[%s,%s] q=%s",
                city.name, target_date, metric,
                pm_bin_lo, pm_bin_hi, question[:40],
            )
            processed += 1
            if metric == "high":
                written_high += 1
            else:
                written_low += 1
            continue

        # Look up source-family observation
        obs_row = _lookup_settlement_obs(conn, city, target_date,
                                         temperature_metric=metric)
        if obs_row is None:
            logger.debug("No obs: %s %s %s", city.name, target_date, metric)
            skipped_no_obs += 1
            # Write QUARANTINED row so the gap is recorded
            _write_quarantined_gamma_row(
                conn, city, target_date, metric,
                pm_bin_lo, pm_bin_hi, event_slug,
            )
            existing.add(key)
            continue

        try:
            result = _write_settlement_truth(
                conn, city, target_date, pm_bin_lo, pm_bin_hi,
                event_slug=event_slug,
                obs_row=obs_row,
                resolved_market_outcomes=None,
                temperature_metric=metric,
            )
            # Patch provenance to note gamma_backfill as source
            _patch_provenance_reconstruction_method(conn, city.name, target_date, metric)

            existing.add(key)
            processed += 1
            if metric == "high":
                written_high += 1
            else:
                written_low += 1
        except Exception as exc:
            logger.warning("Write failed %s %s %s: %s", city.name, target_date, metric, exc)
            errors += 1
            try:
                conn.rollback()
            except Exception:
                pass
            continue

        try:
            conn.commit()
        except Exception as exc:
            logger.error("Commit failed: %s", exc)

    # Final commit
    if not dry_run:
        try:
            conn.commit()
        except Exception as exc:
            logger.error("Final commit failed: %s", exc)

    # Distribution query
    dist = []
    try:
        rows = conn.execute(
            "SELECT temperature_metric, COUNT(*), MIN(target_date), MAX(target_date) "
            "FROM settlements_v2 GROUP BY 1"
        ).fetchall()
        dist = [(r[0], r[1], r[2], r[3]) for r in rows]
    except Exception:
        pass

    conn.close()

    return {
        "dry_run": dry_run,
        "from_date": from_date,
        "to_date": to_date,
        "total_gamma_events": total,
        "processed": processed,
        "written_high": written_high,
        "written_low": written_low,
        "skipped_exists": skipped_exists,
        "skipped_no_city": skipped_no_city,
        "skipped_no_winner": skipped_no_winner,
        "skipped_no_obs": skipped_no_obs,
        "errors": errors,
        "settlements_v2_distribution": dist,
    }


def _write_quarantined_gamma_row(
    conn,
    city,
    target_date: str,
    metric: str,
    pm_bin_lo,
    pm_bin_hi,
    event_slug: str,
) -> None:
    """Write a QUARANTINED placeholder row for Gamma events with no local obs."""
    from src.state.db import log_settlement_v2
    settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    provenance = {
        "writer": "backfill_gamma_2026",
        "writer_script": "scripts/backfill_settlements_via_gamma_2026.py",
        "reconstruction_method": "gamma_backfill",
        "quarantine_reason": "gamma_backfill_no_local_obs",
        "pm_bin_lo": pm_bin_lo,
        "pm_bin_hi": pm_bin_hi,
        "temperature_metric": metric,
        "unit": city.settlement_unit,
        "reconstructed_at": settled_at,
        "operator_authorization": "2026-05-07",
    }
    log_settlement_v2(
        conn,
        city=city.name,
        target_date=target_date,
        temperature_metric=metric,
        market_slug=event_slug,
        winning_bin=None,
        settlement_value=None,
        settlement_source=city.settlement_source,
        settled_at=settled_at,
        authority="QUARANTINED",
        provenance=provenance,
        recorded_at=settled_at,
    )


def _patch_provenance_reconstruction_method(
    conn,
    city_name: str,
    target_date: str,
    metric: str,
) -> None:
    """Update provenance_json to set reconstruction_method=gamma_backfill."""
    try:
        row = conn.execute(
            "SELECT settlement_id, provenance_json FROM settlements_v2 "
            "WHERE city=? AND target_date=? AND temperature_metric=?",
            (city_name, target_date, metric),
        ).fetchone()
        if row is None:
            return
        prov = json.loads(row[1] if row[1] else "{}")
        prov["reconstruction_method"] = "gamma_backfill"
        prov["operator_authorization"] = "2026-05-07"
        conn.execute(
            "UPDATE settlements_v2 SET provenance_json=? WHERE settlement_id=?",
            (json.dumps(prov, sort_keys=True, default=str), row[0]),
        )
    except Exception as exc:
        logger.debug("provenance patch failed %s %s %s: %s", city_name, target_date, metric, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill settlements_v2 via Gamma API")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing")
    parser.add_argument("--from-date", default=DEFAULT_FROM_DATE,
                        help=f"Start date YYYY-MM-DD (default: {DEFAULT_FROM_DATE})")
    parser.add_argument("--to-date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    logger.info("Gamma backfill: %s → %s  dry_run=%s", args.from_date, args.to_date, args.dry_run)
    summary = run_backfill(
        from_date=args.from_date,
        to_date=args.to_date,
        dry_run=args.dry_run,
    )

    print("\n=== Gamma Backfill Summary ===")
    for k, v in summary.items():
        if k == "settlements_v2_distribution":
            print(f"  {k}:")
            for row in v:
                print(f"    metric={row[0]} count={row[1]} range={row[2]}..{row[3]}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
