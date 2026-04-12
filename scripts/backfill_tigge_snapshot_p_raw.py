#!/usr/bin/env python3
"""Materialize replay-compatible p_raw_json for TIGGE ensemble snapshots.

This is a derived-field repair for `ensemble_snapshots`: raw TIGGE members are
already canonical forecast evidence, while `p_raw_json` is the bin-aligned vector
needed by replay/backtest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import City, cities_by_name
from src.contracts import SettlementSemantics
from src.data.market_scanner import _parse_temp_range
from src.state.db import get_world_connection, init_schema
from src.types import Bin


def typed_bins_for_city_date(conn, city_name: str, target_date: str, unit: str) -> list[Bin]:
    """Return replay-bin order used by `ReplayContext` for a city/date."""
    queries = [
        (
            """
            SELECT DISTINCT range_label
            FROM calibration_pairs
            WHERE city = ? AND target_date = ?
            ORDER BY range_label
            """,
            (city_name, target_date),
        ),
        (
            """
            SELECT DISTINCT range_label
            FROM market_events
            WHERE city = ?
              AND target_date = ?
              AND range_label IS NOT NULL
              AND range_label != ''
            ORDER BY range_label
            """,
            (city_name, target_date),
        ),
    ]
    for query, params in queries:
        bins: list[Bin] = []
        for row in conn.execute(query, params).fetchall():
            label = row["range_label"]
            try:
                low, high = _parse_temp_range(label)
                bins.append(Bin(low=low, high=high, label=label, unit=unit))
            except Exception:
                continue
        if bins:
            return bins
    return []


def p_raw_from_member_values(member_values: list[float] | np.ndarray, bins: list[Bin], city: City) -> list[float]:
    """Build a normalized p_raw vector from native-unit TIGGE member maxes."""
    if not bins:
        return []
    values = np.asarray(member_values, dtype=np.float64)
    if values.size == 0:
        return []
    measured = SettlementSemantics.for_city(city).round_values(values)
    probs = []
    for bin in bins:
        if bin.is_open_low:
            prob = float(np.mean(measured <= bin.high))
        elif bin.is_open_high:
            prob = float(np.mean(measured >= bin.low))
        else:
            prob = float(np.mean((measured >= bin.low) & (measured <= bin.high)))
        probs.append(prob)
    total = float(sum(probs))
    if total > 0:
        probs = [float(prob / total) for prob in probs]
    return probs


def materialize_snapshot_row(conn, row, *, overwrite: bool = False) -> str:
    """Update one snapshot row. Returns status string."""
    if row["p_raw_json"] not in (None, "") and not overwrite:
        return "already_has_p_raw"
    city = cities_by_name.get(row["city"])
    if city is None:
        return "unknown_city"
    try:
        members = json.loads(row["members_json"])
    except Exception:
        return "bad_members_json"
    bins = typed_bins_for_city_date(conn, row["city"], row["target_date"], city.settlement_unit)
    if not bins:
        return "no_bins"
    p_raw = p_raw_from_member_values(members, bins, city)
    if not p_raw:
        return "empty_p_raw"
    conn.execute(
        """
        UPDATE ensemble_snapshots
        SET p_raw_json = ?
        WHERE snapshot_id = ?
        """,
        (json.dumps(p_raw), row["snapshot_id"]),
    )
    return "updated"


def run_backfill(
    *,
    city: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    conn = get_world_connection()
    init_schema(conn)
    clauses = ["members_json IS NOT NULL", "members_json != ''"]
    params: list[object] = []
    if not overwrite:
        clauses.append("(p_raw_json IS NULL OR p_raw_json = '')")
    if city:
        clauses.append("city = ?")
        params.append(city)
    if date_from:
        clauses.append("target_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("target_date <= ?")
        params.append(date_to)
    query = (
        "SELECT snapshot_id, city, target_date, members_json, p_raw_json "
        "FROM ensemble_snapshots WHERE "
        + " AND ".join(clauses)
        + " ORDER BY target_date, city, snapshot_id"
    )
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(query, params).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        status = materialize_snapshot_row(conn, row, overwrite=overwrite)
        counts[status] = counts.get(status, 0) + 1
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()
    return {
        "dry_run": dry_run,
        "selected_rows": len(rows),
        "counts": dict(sorted(counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        run_backfill(
            city=args.city,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        ),
        indent=2,
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
