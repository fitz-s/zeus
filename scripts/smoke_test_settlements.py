#!/usr/bin/env python3
"""
Smoke-test: compare our observation data against Polymarket settlement outcomes.

Fetches all closed daily-temperature events from Gamma API and checks whether
our recorded high_temp falls within the winning market bin.

Usage:
    python scripts/smoke_test_settlements.py [--verbose] [--city CITY]

Output:
    - Summary: MATCH / MISMATCH / NO_DATA counts
    - Per-city breakdown of mismatches
    - Detailed mismatch lines (always printed)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import cities_by_name as CBN
from src.contracts.settlement_semantics import SettlementSemantics

# ---------------------------------------------------------------------------
# Gamma API helpers
# ---------------------------------------------------------------------------
_ENV = {k: v for k, v in os.environ.items()
        if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')}

GAMMA_BASE = "https://gamma-api.polymarket.com"
TAG_ID = 103040  # daily-temperature


def _curl_json(url: str) -> dict | list | None:
    r = subprocess.run(
        ['curl', '-fsk', '--max-time', '30', url],
        capture_output=True, text=True, env=_ENV,
    )
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def fetch_all_closed_events() -> list[dict]:
    events: list[dict] = []
    offset = 0
    while True:
        batch = _curl_json(
            f"{GAMMA_BASE}/events?tag_id={TAG_ID}&closed=true"
            f"&limit=50&offset={offset}"
        )
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 50:
            break
        offset += 50
        time.sleep(0.15)
    return events


# ---------------------------------------------------------------------------
# Bin-parsing regexes
# ---------------------------------------------------------------------------
# "between 52-53°F"
BIN_RE   = re.compile(r'(\d+)\s*[-–]\s*(\d+)\s*°([CF])', re.I)
# "be 5°C on" (early single-value format)
EXACT_RE = re.compile(r'be\s+(-?\d+)\s*°([CF])\s+on', re.I)
# "15°C or below"
BELOW_RE = re.compile(r'(-?\d+)\s*°([CF])\s+or\s+(?:below|lower)', re.I)
# "28°C or above"
ABOVE_RE = re.compile(r'(-?\d+)\s*°([CF])\s+or\s+(?:above|higher)', re.I)

_PARSERS = [
    (BIN_RE,   lambda m: (float(m.group(1)), float(m.group(2)), m.group(3).upper())),
    (EXACT_RE, lambda m: (float(m.group(1)), float(m.group(1)), m.group(2).upper())),
    (BELOW_RE, lambda m: (-999.0, float(m.group(1)), m.group(2).upper())),
    (ABOVE_RE, lambda m: (float(m.group(1)), 999.0, m.group(2).upper())),
]

MONTHS = {
    'January': '01', 'February': '02', 'March': '03', 'April': '04',
    'May': '05', 'June': '06', 'July': '07', 'August': '08',
    'September': '09', 'October': '10', 'November': '11', 'December': '12',
}


def extract_title_date(title: str, end_date: str) -> str | None:
    """Extract actual weather date from event title (NOT endDate).

    Gamma API endDate = market close date = weather date + 1 day.
    The title contains the real weather date: "...on March 17?"
    """
    m = re.search(
        r'on\s+(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+(\d{1,2})', title,
    )
    if m:
        month = MONTHS[m.group(1)]
        day = int(m.group(2))
        year = end_date[:4] if end_date else '2026'
        return f'{year}-{month}-{day:02d}'
    return end_date[:10] if end_date else None


def resolve_city(raw: str) -> str | None:
    """Match raw city name from title against our city registry."""
    for cn in CBN:
        if cn.lower() == raw.lower():
            return cn
    for cn in CBN:
        if raw.lower() in cn.lower() or cn.lower() in raw.lower():
            return cn
    return None


def parse_winning_bin(markets: list[dict]) -> tuple[float, float, str, str] | None:
    """Find the winning market and parse its temperature bin.

    Returns (bin_lo, bin_hi, unit, question) or None.
    """
    for mkt in (markets or []):
        prices = mkt.get('outcomePrices', '')
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(prices, list) or len(prices) < 1:
            continue
        try:
            if float(prices[0]) <= 0.9:
                continue
        except (ValueError, TypeError):
            continue

        question = mkt.get('question', '')
        for pat, handler in _PARSERS:
            match = pat.search(question)
            if match:
                lo, hi, unit = handler(match)
                return lo, hi, unit, question
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('--city', type=str, default=None,
                        help='Filter to a single city')
    args = parser.parse_args()

    db_path = ROOT / 'state' / 'zeus-world.db'
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print("Fetching closed events from Gamma API...")
    events = fetch_all_closed_events()
    print(f"  Fetched {len(events)} events")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    results: list[tuple] = []  # (city, date, pm_bin, our_val, status, source)

    for event in events:
        title = event.get('title', '')
        end_date = event.get('endDate', '')

        m = re.search(r'[Ii]n\s+(.+?)\s+on\s+', title)
        if not m:
            continue
        city_name = resolve_city(m.group(1).strip())
        if not city_name:
            continue
        if args.city and city_name.lower() != args.city.lower():
            continue

        target_date = extract_title_date(title, end_date)
        if not target_date:
            continue

        parsed = parse_winning_bin(event.get('markets', []))
        if not parsed:
            continue
        bin_lo, bin_hi, unit, winning_q = parsed

        row = conn.execute(
            "SELECT high_temp, unit, source, authority "
            "FROM observations WHERE city=? AND target_date=?",
            (city_name, target_date),
        ).fetchone()

        if not row:
            results.append((city_name, target_date,
                            f'{bin_lo}-{bin_hi}{unit}', None, 'NO_DATA', ''))
            continue

        if row['authority'] == 'QUARANTINED':
            results.append((city_name, target_date,
                            f'{bin_lo}-{bin_hi}{unit}', row['high_temp'],
                            'QUARANTINED', row['source']))
            continue

        sem = SettlementSemantics.for_city(CBN[city_name])
        sv = sem.assert_settlement_value(row['high_temp'], context='smoke')
        status = 'MATCH' if bin_lo <= sv <= bin_hi else 'MISMATCH'
        results.append((city_name, target_date,
                        f'{bin_lo}-{bin_hi}{unit}', sv, status, row['source']))

    conn.close()

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    counts = Counter(r[4] for r in results)
    total = len(results)
    print(f"\nResults: {total} events compared")
    for status in ('MATCH', 'MISMATCH', 'NO_DATA', 'QUARANTINED'):
        n = counts.get(status, 0)
        pct = 100 * n / max(total, 1)
        print(f"  {status:14s}: {n:5d} ({pct:.1f}%)")

    mismatches = [r for r in results if r[4] == 'MISMATCH']
    if mismatches:
        print(f"\nMISMATCHES ({len(mismatches)}):")
        for r in sorted(mismatches, key=lambda x: (x[0], x[1])):
            print(f"  {r[0]:20s} {r[1]}  pm={r[2]:20s}  our={r[3]:7.1f}  src={r[5]}")

        mm_cities = Counter(r[0] for r in mismatches)
        print(f"\nMismatch by city: {dict(sorted(mm_cities.items(), key=lambda x: -x[1]))}")

    if args.verbose:
        no_data = [r for r in results if r[4] == 'NO_DATA']
        if no_data:
            print(f"\nNO_DATA ({len(no_data)}):")
            for r in sorted(no_data, key=lambda x: (x[0], x[1])):
                print(f"  {r[0]:20s} {r[1]}")


if __name__ == '__main__':
    main()
