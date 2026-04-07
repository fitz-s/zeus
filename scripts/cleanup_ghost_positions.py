#!/usr/bin/env python3
"""
Ghost position cleanup script.

Finds trade_decisions with status='entered' whose target market date has passed,
cross-references with settlements, and updates their status accordingly.

DRY RUN by default. Pass --apply to make changes.

Usage:
    python scripts/cleanup_ghost_positions.py           # dry run
    python scripts/cleanup_ghost_positions.py --apply   # apply changes
"""

import argparse
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ZEUS_ROOT / "state" / "zeus.db"

# City name normalization: trade_decisions uses full names,
# settlements uses abbreviated names
CITY_NORMALIZE = {
    "New York City": "NYC",
    "New York": "NYC",
}


def normalize_city(raw_city: str) -> str:
    """Normalize city name to match settlements table convention."""
    return CITY_NORMALIZE.get(raw_city, raw_city)


def parse_bin_label(bin_label: str):
    """Extract city and target_date from bin_label text.

    Examples:
        'Will the highest temperature in New York City be between 86-87°F on April 1?'
        'Will the highest temperature in Chicago be 48°F or higher on April 2?'
        'Will the highest temperature in Seattle be 55°F or below on April 5?'
    Returns: ('New York City', date(2026, 4, 1)) or (None, None)
    """
    # Match both "be between X-Y°F" and "be X°F or higher/below" formats
    city_m = re.search(r"temperature in (.+?) be ", bin_label or "")
    date_m = re.search(r"on (\w+ \d+)\?", bin_label or "")

    city = city_m.group(1) if city_m else None
    target = None
    if date_m:
        try:
            target = datetime.strptime(date_m.group(1) + ", 2026", "%B %d, %Y").date()
        except ValueError:
            pass

    return city, target


def find_ghosts(cur, today: date):
    """Find entered trade_decisions where target_date is in the past."""
    cur.execute(
        "SELECT trade_id, market_id, bin_label, timestamp "
        "FROM trade_decisions WHERE status='entered' ORDER BY trade_id"
    )
    ghosts = []
    for row in cur.fetchall():
        trade_id, market_id, bin_label, timestamp = row
        city, target = parse_bin_label(bin_label)
        if target is None or target >= today:
            continue

        # Cross-reference with settlements
        normalized_city = normalize_city(city) if city else None
        settled = False
        if normalized_city and target:
            cur.execute(
                "SELECT COUNT(*) FROM settlements WHERE city = ? AND target_date = ?",
                (normalized_city, str(target)),
            )
            settled = cur.fetchone()[0] > 0

        ghosts.append({
            "trade_id": trade_id,
            "market_id": market_id,
            "bin_label": bin_label,
            "city": city,
            "normalized_city": normalized_city,
            "target_date": target,
            "timestamp": timestamp,
            "settled": settled,
        })

    return ghosts


def main():
    parser = argparse.ArgumentParser(description="Clean up ghost positions in trade_decisions")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry run)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    today = date.today()

    print(f"Ghost Position Cleanup — {'APPLY MODE' if args.apply else 'DRY RUN'}")
    print(f"Database: {DB_PATH}")
    print(f"Today: {today}")
    print()

    ghosts = find_ghosts(cur, today)

    if not ghosts:
        print("No ghost positions found.")
        return

    settled_ghosts = [g for g in ghosts if g["settled"]]
    unresolved_ghosts = [g for g in ghosts if not g["settled"]]

    print(f"Found {len(ghosts)} ghost positions:")
    print(f"  Market settled: {len(settled_ghosts)}")
    print(f"  Unresolved (no settlement row): {len(unresolved_ghosts)}")
    print()

    if settled_ghosts:
        print("=== SETTLED GHOSTS (will mark 'settled_ghost') ===")
        for g in settled_ghosts:
            print(f"  trade_id={g['trade_id']:4d}  city={g['city']:20s}  target={g['target_date']}  {g['bin_label'][:60]}")
            if args.apply:
                cur.execute(
                    "UPDATE trade_decisions SET status='settled_ghost' WHERE trade_id=?",
                    (g["trade_id"],),
                )
        print()

    if unresolved_ghosts:
        print("=== UNRESOLVED GHOSTS (will mark 'unresolved_ghost') ===")
        print(f"  NOTE: These markets have no settlement rows. Possible causes:")
        print(f"    - Settlement harvester hasn't run for dates after 2026-03-30")
        print(f"    - City name mismatch (trade uses full name, settlements uses abbreviation)")
        print()
        for g in unresolved_ghosts:
            city_note = ""
            if g["city"] != g["normalized_city"]:
                city_note = f" (normalized: {g['normalized_city']})"
            print(f"  trade_id={g['trade_id']:4d}  city={g['city'] or 'UNKNOWN':20s}{city_note}  target={g['target_date']}")
            if args.apply:
                cur.execute(
                    "UPDATE trade_decisions SET status='unresolved_ghost' WHERE trade_id=?",
                    (g["trade_id"],),
                )
        print()

    if args.apply:
        conn.commit()
        print(f"APPLIED: {len(settled_ghosts)} marked settled_ghost, {len(unresolved_ghosts)} marked unresolved_ghost")
    else:
        print("DRY RUN complete. Pass --apply to make changes.")

    conn.close()


if __name__ == "__main__":
    main()
