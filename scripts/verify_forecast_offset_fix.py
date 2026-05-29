# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Before/after measurement of PR-A forecast selection fix (read-only diagnostic).
# Reuse: Run after PR-A merge to confirm contributing-run selection bias reduction for far-east cities.
"""
PART 1: Before/after measurement of PR-A forecast selection fix.

Compares OLD selection (latest-first) vs NEW selection (contributes-first)
against observed daily-high for far-east cities (Taipei, Seoul, Guangzhou,
Busan, Shenzhen, Qingdao, Tokyo) and controls (Amsterdam, Chicago).

OLD ORDER BY: source_cycle_time DESC, available_at DESC, snapshot_id DESC
NEW ORDER BY: (CASE WHEN contributes_to_target_extrema=1 AND
              attribution IN positive_set AND NOT boundary_ambiguous
              THEN 0 ELSE 1 END) ASC, source_cycle_time DESC, available_at DESC,
              snapshot_id DESC

Read-only: opens both DBs as read-only URIs; writes nothing.

Usage:
    python scripts/verify_forecast_offset_fix.py [--forecasts-db PATH] [--world-db PATH] [--date DATE]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_DATE = "2026-05-22"
TEMP_METRIC = "high"

FAR_EAST_CITIES = ["Taipei", "Seoul", "Guangzhou", "Busan", "Shenzhen", "Qingdao", "Tokyo"]
CONTROL_CITIES = ["Amsterdam", "Chicago"]
ALL_CITIES = FAR_EAST_CITIES + CONTROL_CITIES

# OLD selection: pure latest-first (the bug)
SQL_OLD = """
    SELECT snapshot_id, source_cycle_time, contributes_to_target_extrema,
           forecast_window_attribution_status, boundary_ambiguous, members_json
    FROM ensemble_snapshots
    WHERE city = ?
      AND target_date = ?
      AND temperature_metric = ?
    ORDER BY source_cycle_time DESC, available_at DESC, snapshot_id DESC
    LIMIT 1
"""

# NEW selection: PR-A ranking — contributing runs first, then latest within
SQL_NEW = """
    SELECT snapshot_id, source_cycle_time, contributes_to_target_extrema,
           forecast_window_attribution_status, boundary_ambiguous, members_json
    FROM ensemble_snapshots
    WHERE city = ?
      AND target_date = ?
      AND temperature_metric = ?
    ORDER BY (CASE WHEN COALESCE(contributes_to_target_extrema,0)=1
                        AND COALESCE(forecast_window_attribution_status,'') NOT IN ('UNKNOWN','')
                        AND COALESCE(boundary_ambiguous,0)=0
                   THEN 0 ELSE 1 END) ASC,
             source_cycle_time DESC, available_at DESC, snapshot_id DESC
    LIMIT 1
"""

# Observed daily high: MAX(running_max) over all hourly rows for the day
SQL_OBS = """
    SELECT MAX(running_max) AS obs_high
    FROM observation_instants
    WHERE city = ? AND target_date = ?
"""


def _max_members(members_json: str | None) -> float | None:
    """Return max member value, or None if unparseable."""
    if not members_json:
        return None
    try:
        m = json.loads(members_json)
        return round(max(m), 2) if m else None
    except Exception:
        return None


def _round2(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--forecasts-db", default=str(ZEUS_FORECASTS_DB_PATH),
                   help="Path to zeus-forecasts.db (default: src.state.db.ZEUS_FORECASTS_DB_PATH)")
    p.add_argument("--world-db", default=str(ZEUS_WORLD_DB_PATH),
                   help="Path to zeus-world.db (default: src.state.db.ZEUS_WORLD_DB_PATH)")
    p.add_argument("--date", default=TARGET_DATE,
                   help=f"Target date YYYY-MM-DD (default: {TARGET_DATE})")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    target_date = args.date
    # Open read-only
    fc = sqlite3.connect(f"file:{args.forecasts_db}?mode=ro", uri=True)
    fc.row_factory = sqlite3.Row
    wc = sqlite3.connect(f"file:{args.world_db}?mode=ro", uri=True)
    wc.row_factory = sqlite3.Row

    rows = []
    for city in ALL_CITIES:
        old_row = fc.execute(SQL_OLD, (city, target_date, TEMP_METRIC)).fetchone()
        new_row = fc.execute(SQL_NEW, (city, target_date, TEMP_METRIC)).fetchone()
        obs_row = wc.execute(SQL_OBS, (city, target_date)).fetchone()

        old_fc = _max_members(old_row["members_json"]) if old_row else None
        new_fc = _max_members(new_row["members_json"]) if new_row else None
        obs = _round2(obs_row["obs_high"]) if obs_row else None

        old_bias = _round2(old_fc - obs) if (old_fc is not None and obs is not None) else None
        new_bias = _round2(new_fc - obs) if (new_fc is not None and obs is not None) else None

        old_contrib = old_row["contributes_to_target_extrema"] if old_row else None
        new_contrib = new_row["contributes_to_target_extrema"] if new_row else None
        same_snap = (old_row["snapshot_id"] == new_row["snapshot_id"]) if (old_row and new_row) else None

        rows.append({
            "city": city,
            "old_fc": old_fc,
            "new_fc": new_fc,
            "obs": obs,
            "old_bias": old_bias,
            "new_bias": new_bias,
            "old_contrib": old_contrib,
            "new_contrib": new_contrib,
            "same_snap": same_snap,
        })

    fc.close()
    wc.close()

    # ---------------------------------------------------------------------------
    # Print table
    # ---------------------------------------------------------------------------
    header = (
        f"{'City':<14} {'OldFc':>7} {'NewFc':>7} {'Obs':>6} "
        f"{'OldBias':>8} {'NewBias':>8} {'OldCtrib':>9} {'NewCtrib':>9} {'SameSnap':>9}"
    )
    sep = "-" * len(header)

    print(f"\nForecast Selection Fix: Before/After ({target_date}, metric={TEMP_METRIC})")
    print(sep)
    print(header)
    print(sep)

    for r in rows:
        tag = "  [far-east]" if r["city"] in FAR_EAST_CITIES else "  [control]"
        obs_note = ""
        if r["city"] == "Chicago":
            obs_note = " (°F)"
        elif r["city"] in FAR_EAST_CITIES + ["Amsterdam"]:
            obs_note = " (°C)"
        print(
            f"{r['city']:<14} {str(r['old_fc']):>7} {str(r['new_fc']):>7} {str(r['obs']):>6} "
            f"{str(r['old_bias']):>8} {str(r['new_bias']):>8} "
            f"{str(r['old_contrib']):>9} {str(r['new_contrib']):>9} {str(r['same_snap']):>9}"
            f"{tag}{obs_note}"
        )

    print(sep)

    # ---------------------------------------------------------------------------
    # Summary statistics (far-east only; Chicago is °F so excluded from mean)
    # ---------------------------------------------------------------------------
    fe_rows = [r for r in rows if r["city"] in FAR_EAST_CITIES]
    old_abs_biases = [abs(r["old_bias"]) for r in fe_rows if r["old_bias"] is not None]
    new_abs_biases = [abs(r["new_bias"]) for r in fe_rows if r["new_bias"] is not None]

    def _mean(lst: list[float]) -> str:
        return f"{sum(lst)/len(lst):.2f}" if lst else "N/A"

    print(f"\nFar-east mean |bias| (°C): OLD={_mean(old_abs_biases)}  NEW={_mean(new_abs_biases)}")
    print(f"  Cities with selection change: {sum(1 for r in fe_rows if r['same_snap'] is False)}/{len(fe_rows)}")
    print(f"  Controls unchanged (same snapshot): {all(r['same_snap'] for r in rows if r['city'] in CONTROL_CITIES)}")

    if old_abs_biases and new_abs_biases:
        old_mean = sum(old_abs_biases) / len(old_abs_biases)
        new_mean = sum(new_abs_biases) / len(new_abs_biases)
        if new_mean < old_mean * 0.5:
            print(f"\n  VERDICT: PR-A FIXES the selection bias ({old_mean:.2f} -> {new_mean:.2f}, >{100*(1-new_mean/old_mean):.0f}% reduction).")
        else:
            print(f"\n  VERDICT: bias reduction insufficient ({old_mean:.2f} -> {new_mean:.2f}).")

    print()


if __name__ == "__main__":
    main()
