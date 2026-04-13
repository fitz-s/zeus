#!/usr/bin/env python3
"""Offline script: build 46x46 Pearson city temperature correlation matrix.

Reads production state/zeus-world.db ensemble_snapshots, computes per-city
daily mean temperature time series, outputs config/city_correlation_matrix.json.
Pairs with < 30 overlapping days are dropped; runtime falls back to haversine.

Usage:
    python scripts/build_correlation_matrix.py
    python scripts/build_correlation_matrix.py --db /path/to/zeus-world.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "state" / "zeus-world.db"
OUTPUT_PATH = PROJECT_ROOT / "config" / "city_correlation_matrix.json"
MIN_OVERLAP_DAYS = 30


def load_city_series(db_path: Path) -> pd.DataFrame:
    """Return DataFrame of (city, target_date, daily_mean_temp_members)."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT city, target_date, members_json FROM ensemble_snapshots ORDER BY city, target_date"
    ).fetchall()
    conn.close()

    records = []
    for city, target_date, members_json in rows:
        try:
            members = json.loads(members_json)
            if not members:
                continue
            daily_mean = float(np.mean(members))
            records.append((city, target_date, daily_mean))
        except Exception:
            continue

    df = pd.DataFrame(records, columns=["city", "target_date", "daily_mean"])
    # Keep one row per (city, target_date) — average if duplicates
    df = df.groupby(["city", "target_date"], as_index=False)["daily_mean"].mean()
    return df


def build_matrix(df: pd.DataFrame) -> dict:
    """Pivot to wide form and compute pairwise Pearson correlations."""
    wide = df.pivot(index="target_date", columns="city", values="daily_mean")
    cities = list(wide.columns)

    matrix = {}
    pair_count = 0
    for i, city_a in enumerate(cities):
        matrix[city_a] = {}
        for j, city_b in enumerate(cities):
            if j <= i:
                continue
            # Align on common dates
            common = wide[[city_a, city_b]].dropna()
            if len(common) < MIN_OVERLAP_DAYS:
                continue  # Insufficient overlap — haversine fallback at runtime
            corr = float(common[city_a].corr(common[city_b]))
            if np.isnan(corr):
                continue
            matrix[city_a][city_b] = round(corr, 4)
            pair_count += 1

    return matrix, pair_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Build city correlation matrix from TIGGE data")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to zeus-world.db")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"[WARN] DB not found: {args.db} -- writing empty matrix (haversine fallback will handle runtime)")
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "ecmwf_tigge ensemble_snapshots daily mean",
            "pair_count": 0,
            "note": "DB not found at build time; runtime uses haversine fallback",
            "matrix": {},
        }
        OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
        print(f"Wrote empty matrix to {OUTPUT_PATH}")
        return

    print(f"Loading ensemble_snapshots from {args.db}...")
    df = load_city_series(args.db)
    print(f"  {len(df)} (city, date) rows across {df.city.nunique()} cities")

    print("Computing pairwise Pearson correlations...")
    matrix, pair_count = build_matrix(df)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "ecmwf_tigge ensemble_snapshots daily mean",
        "pair_count": pair_count,
        "min_overlap_days": MIN_OVERLAP_DAYS,
        "matrix": matrix,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote {pair_count} pairs to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
