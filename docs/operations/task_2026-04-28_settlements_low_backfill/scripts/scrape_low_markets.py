#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
"""Scrape closed Polymarket LOW (mn2t6) markets.

Scope (verified 2026-04-28 via gamma-api.polymarket.com):
- 48 closed historical LOW events + 18 active
- 8 cities: london, seoul, nyc, tokyo, shanghai, paris, miami, hong-kong
- date range: 2026-04-15 .. 2026-04-29

Output: a JSON manifest of (city, target_date, winning_bin, pm_bin_lo, pm_bin_hi,
unit, resolution_source) for closed-and-resolved events.

This script ONLY reads from Polymarket and writes a JSON file. It does not
touch any DB. The DB write happens in a separate `--apply` script gated by
operator approval.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
TAG_SLUG_LOW = "daily-temperature"

# city slug → (canonical zeus city display name, settlement_unit, settlement_source_type)
CITY_SLUG_MAP = {
    "london":    ("London",    "C", "wu_icao"),
    "seoul":     ("Seoul",     "C", "wu_icao"),
    "nyc":       ("NYC",       "F", "wu_icao"),
    "tokyo":     ("Tokyo",     "C", "wu_icao"),
    "shanghai":  ("Shanghai",  "C", "wu_icao"),
    "paris":     ("Paris",     "C", "wu_icao"),
    "miami":     ("Miami",     "F", "wu_icao"),
    "hong-kong": ("Hong Kong", "C", "hko"),
}


def list_low_events(closed: str = "true") -> list[dict[str, Any]]:
    r = httpx.get(f"{GAMMA_BASE}/tags/slug/{TAG_SLUG_LOW}", timeout=20)
    r.raise_for_status()
    tag_id = r.json()["id"]

    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        er = httpx.get(
            f"{GAMMA_BASE}/events",
            params={"tag_id": tag_id, "closed": closed, "limit": 100, "offset": offset},
            timeout=20,
        )
        er.raise_for_status()
        events = er.json()
        if not events:
            break
        for ev in events:
            title = (ev.get("title") or "").lower()
            slug = (ev.get("slug") or "").lower()
            if any(kw in title or kw in slug for kw in
                   ("lowest temp", "low temperature", "daily low", "overnight low")):
                out.append(ev)
        if len(events) < 100:
            break
        offset += 100
        if offset > 5000:
            break
    return out


def parse_target_date(slug: str) -> str | None:
    """slug = lowest-temperature-in-shanghai-on-april-15-2026 → '2026-04-15'."""
    m = re.search(r"-on-([a-z]+)-(\d{1,2})-(\d{4})", slug)
    if not m:
        return None
    month_name, day, year = m.groups()
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    month = months.get(month_name)
    if month is None:
        return None
    try:
        return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    except Exception:
        return None


def parse_city_from_slug(slug: str) -> str | None:
    m = re.match(r"^lowest-temperature-in-([a-z\-]+)-on-", slug)
    if not m:
        return None
    return m.group(1)


def parse_bin_label(group_item_title: str, market_question: str | None = None) -> tuple[str, float | None, float | None, str]:
    """Parse Polymarket LOW bin into (label, pm_bin_lo, pm_bin_hi, bin_kind).

    bin_kind ∈ {"point", "finite_range", "lower_shoulder", "upper_shoulder"}.
    Polymarket weather grammar (per zeus_market_settlement_reference.md):
      - point:           "10°C"            resolves on {10}            cardinality 1
      - finite_range:    "50-51°F"         resolves on {50, 51}        cardinality 2
      - lower_shoulder:  "9°C or below"    resolves on (-∞, 9]         cardinality unbounded
      - upper_shoulder:  "19°C or higher"  resolves on [19, +∞)        cardinality unbounded
    """
    s = (group_item_title or "").strip()
    # lower shoulder: "9°C or below" / "9°F or below"
    m = re.match(r"^(-?\d+)\s*°\s*([CFcf])\s*or\s*below$", s, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return s, None, v, "lower_shoulder"
    # upper shoulder: "19°C or higher"
    m = re.match(r"^(-?\d+)\s*°\s*([CFcf])\s*or\s*higher$", s, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return s, v, None, "upper_shoulder"
    # finite range: "68-69°F" or "50-51°C"
    m = re.match(r"^(-?\d+)\s*-\s*(-?\d+)\s*°\s*([CFcf])$", s, re.IGNORECASE)
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2))
        return s, lo, hi, "finite_range"
    # point: "15°C"
    m = re.match(r"^(-?\d+)\s*°\s*([CFcf])$", s, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return s, v, v, "point"
    return s, None, None, "unknown"


def find_winning_bin(markets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the child market with outcomePrices = ["1", "0"] (Yes won)."""
    for m in markets:
        op = m.get("outcomePrices")
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except Exception:
                continue
        if isinstance(op, list) and len(op) == 2:
            try:
                yes_p = float(op[0])
                no_p = float(op[1])
            except Exception:
                continue
            if yes_p == 1.0 and no_p == 0.0 and m.get("umaResolutionStatus") == "resolved":
                return m
    return None


def build_record(event: dict[str, Any]) -> dict[str, Any] | None:
    slug = event.get("slug") or ""
    city_slug = parse_city_from_slug(slug)
    target_date = parse_target_date(slug)
    if city_slug is None or target_date is None:
        return None
    if city_slug not in CITY_SLUG_MAP:
        return {"slug": slug, "skipped_reason": f"city_slug_unmapped: {city_slug}"}
    canonical_city, expected_unit, settlement_source_type = CITY_SLUG_MAP[city_slug]

    markets = event.get("markets") or []
    if not markets:
        return {"slug": slug, "skipped_reason": "no_markets"}
    winner = find_winning_bin(markets)
    if winner is None:
        return {"slug": slug, "skipped_reason": "no_resolved_winner"}

    label, lo, hi, kind = parse_bin_label(
        winner.get("groupItemTitle") or "",
        winner.get("question"),
    )
    # Detect unit from the label
    label_unit = "C" if "°C" in (winner.get("groupItemTitle") or "") else (
        "F" if "°F" in (winner.get("groupItemTitle") or "") else None
    )
    return {
        "slug": slug,
        "event_id": event.get("id"),
        "city_slug": city_slug,
        "city": canonical_city,
        "target_date": target_date,
        "winning_bin_label": label,
        "winning_bin_kind": kind,
        "pm_bin_lo": lo,
        "pm_bin_hi": hi,
        "unit": label_unit or expected_unit,
        "settlement_source_type": settlement_source_type,
        "resolution_source": event.get("resolutionSource"),
        "uma_resolved_at": winner.get("closedTime"),
        "winner_market_id": winner.get("id"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Scrape Polymarket LOW (mn2t6) closed markets")
    p.add_argument("--out", required=True, help="Output JSON path for the manifest")
    p.add_argument("--include-active", action="store_true",
                   help="Also include active (unresolved) events for inspection")
    args = p.parse_args()

    print(f"[{datetime.utcnow().isoformat()}Z] fetching closed LOW events...")
    events = list_low_events("true")
    if args.include_active:
        active = list_low_events("false")
        events += active
    print(f"  raw event count: {len(events)}")

    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ev in events:
        rec = build_record(ev)
        if rec is None:
            continue
        if "skipped_reason" in rec:
            skipped.append(rec)
        else:
            records.append(rec)
    print(f"  resolved + parseable: {len(records)}")
    print(f"  skipped: {len(skipped)}")
    for s in skipped[:5]:
        print(f"    - {s}")

    # City × date matrix
    by_city: dict[str, list[str]] = {}
    for r in records:
        by_city.setdefault(r["city"], []).append(r["target_date"])
    print("\n  records by city:")
    for c, ds in sorted(by_city.items()):
        print(f"    {c:12s} {len(ds):3d} dates: {min(ds)} .. {max(ds)}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": "gamma-api.polymarket.com",
        "tag_slug": TAG_SLUG_LOW,
        "n_records": len(records),
        "n_skipped": len(skipped),
        "records": records,
        "skipped": skipped,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[ok] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
