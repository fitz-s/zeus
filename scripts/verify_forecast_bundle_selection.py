# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Read-only operator diagnostic — bundle-layer selection vs latest-cycle for each city/date.
# Reuse: Run after the bundle-layer fix to confirm the SELECTED bundle is a contributing
#   cycle (not the latest post-peak cycle) and to measure forecast-vs-observed bias.
"""Verify forecast bundle-layer selection (P0 follow-up §7).

Read-only operator tool (NOT a CI test).  For each city/date it prints:
  - the SELECTED bundle (contributor-first ranking across all forecast cycles)
  - the LATEST snapshot (the single-path "latest cycle" the old reader locked to)
  - observed daily high + forecast-vs-observed bias

Selection rule (mirrors src.data.forecast_extrema_authority + _bundle_rank):
  rank = (0 if FULL_CONTRIBUTOR else 1, -source_cycle_time, -available_at, -snapshot_id)
A 00Z FULL_CONTRIBUTOR outranks a later 12Z NON_CONTRIBUTOR; recency breaks ties
only within an equal contributor class.  "SameSnap=False" means the bundle layer
chose a DIFFERENT (earlier, contributing) snapshot than the latest-cycle path — the
P0 cold-bias fix in action.

Read-only: opens both DBs as read-only URIs; writes nothing.

Usage:
    python scripts/verify_forecast_bundle_selection.py \
        [--forecasts-db PATH] [--world-db PATH] [--date YYYY-MM-DD] [--metric high|low]

Default DB paths come from ZEUS_FORECASTS_DB_PATH / ZEUS_WORLD_DB_PATH
(src.state.db), like verify_forecast_offset_fix.py.
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

from src.data.forecast_extrema_authority import (  # noqa: E402
    ForecastExtremaEligibility,
    classify_forecast_extrema_authority,
)
from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH  # noqa: E402

TARGET_DATE = "2026-05-22"
DEFAULT_METRIC = "high"

# Far-east cities are the ones the P0 cold-bias hit hardest (timezone-correlated
# post-peak 12Z selection); controls have aligned cycles.
FAR_EAST_CITIES = ["Taipei", "Seoul", "Guangzhou", "Busan", "Shenzhen", "Qingdao", "Tokyo"]
CONTROL_CITIES = ["Amsterdam", "Chicago"]
ALL_CITIES = FAR_EAST_CITIES + CONTROL_CITIES

# All cycles for the scope (no LIMIT) — ranking is applied in Python so it stays
# byte-for-byte aligned with _bundle_rank / classify_forecast_extrema_authority.
SQL_ALL_CYCLES = """
    SELECT snapshot_id, source_run_id, source_cycle_time, available_at,
           contributes_to_target_extrema, forecast_window_attribution_status,
           boundary_ambiguous, members_json, dataset_id
    FROM ensemble_snapshots
    WHERE city = ?
      AND target_date = ?
      AND temperature_metric = ?
"""

# Observed daily extremum from the world-DB observations table.  The column is
# high_temp / low_temp keyed by city + target_date.  MAX over rows handles
# multiple source rows for the same city/date.
SQL_OBS_HIGH = """
    SELECT MAX(high_temp) AS obs FROM observations WHERE city = ? AND target_date = ?
"""
SQL_OBS_LOW = """
    SELECT MIN(low_temp) AS obs FROM observations WHERE city = ? AND target_date = ?
"""


def _max_members(members_json: str | None) -> float | None:
    if not members_json:
        return None
    try:
        members = [m for m in json.loads(members_json) if m is not None]
    except (json.JSONDecodeError, TypeError):
        return None
    return round(max(members), 2) if members else None


def _round2(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


class _NegStr:
    """Wrap a string so that min() picks the lexicographically LARGEST (latest).

    ISO-8601 timestamps sort lexicographically == chronologically, so reversing
    the comparison makes the LATEST cycle/available time sort FIRST under min().
    """

    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s

    def __lt__(self, other: "_NegStr") -> bool:
        return self.s > other.s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NegStr) and self.s == other.s


def _contributor_rank(row: sqlite3.Row) -> int:
    auth = classify_forecast_extrema_authority(dict(row))
    return 0 if auth.eligibility == ForecastExtremaEligibility.FULL_CONTRIBUTOR else 1


def _select_best(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    """Mirror _bundle_rank: contributor class ASC (0 first), then recency DESC."""
    if not rows:
        return None
    return min(
        rows,
        key=lambda r: (
            _contributor_rank(r),
            _NegStr(str(r["source_cycle_time"] or "")),
            _NegStr(str(r["available_at"] or "")),
            -int(r["snapshot_id"] or 0),
        ),
    )


def _latest(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda r: (
            str(r["source_cycle_time"] or ""),
            str(r["available_at"] or ""),
            int(r["snapshot_id"] or 0),
        ),
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--forecasts-db", default=str(ZEUS_FORECASTS_DB_PATH),
                   help="Path to zeus-forecasts.db (default: src.state.db.ZEUS_FORECASTS_DB_PATH)")
    p.add_argument("--world-db", default=str(ZEUS_WORLD_DB_PATH),
                   help="Path to zeus-world.db (default: src.state.db.ZEUS_WORLD_DB_PATH)")
    p.add_argument("--date", default=TARGET_DATE,
                   help=f"Target date YYYY-MM-DD (default: {TARGET_DATE})")
    p.add_argument("--metric", default=DEFAULT_METRIC, choices=("high", "low"),
                   help=f"Temperature metric (default: {DEFAULT_METRIC})")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    fc = sqlite3.connect(f"file:{args.forecasts_db}?mode=ro", uri=True)
    fc.row_factory = sqlite3.Row
    wc = sqlite3.connect(f"file:{args.world_db}?mode=ro", uri=True)
    wc.row_factory = sqlite3.Row

    out_rows = []
    for city in ALL_CITIES:
        cycles = fc.execute(SQL_ALL_CYCLES, (city, args.date, args.metric)).fetchall()
        best = _select_best(cycles)
        latest = _latest(cycles)
        obs_sql = SQL_OBS_HIGH if args.metric == "high" else SQL_OBS_LOW
        try:
            obs_row = wc.execute(obs_sql, (city, args.date)).fetchone()
            obs = _round2(obs_row["obs"]) if obs_row else None
        except sqlite3.OperationalError:
            obs = None

        sel_max = _max_members(best["members_json"]) if best else None
        latest_max = _max_members(latest["members_json"]) if latest else None
        bias = _round2(sel_max - obs) if (sel_max is not None and obs is not None) else None
        sel_auth = (
            classify_forecast_extrema_authority(dict(best)).eligibility.value if best else None
        )
        same_snap = (
            best["snapshot_id"] == latest["snapshot_id"] if (best and latest) else None
        )

        out_rows.append({
            "city": city,
            "selected_snapshot_id": best["snapshot_id"] if best else None,
            "selected_source_run_id": best["source_run_id"] if best else None,
            "selected_source_cycle_time": best["source_cycle_time"] if best else None,
            "selected_contributes": best["contributes_to_target_extrema"] if best else None,
            "selected_attribution": best["forecast_window_attribution_status"] if best else None,
            "selected_eligibility": sel_auth,
            "selected_member_max": sel_max,
            "observed_high": obs,
            "bias": bias,
            "latest_snapshot_id": latest["snapshot_id"] if latest else None,
            "latest_contributes": latest["contributes_to_target_extrema"] if latest else None,
            "latest_member_max": latest_max,
            "same_snapshot": same_snap,
        })

    fc.close()
    wc.close()

    header = (
        f"{'City':<12} {'SelSnap':>8} {'SelCtrb':>8} {'SelMax':>7} "
        f"{'Obs':>6} {'Bias':>7} {'LatSnap':>8} {'LatCtrb':>8} {'LatMax':>7} {'Same':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in out_rows:
        print(
            f"{r['city']:<12} {str(r['selected_snapshot_id']):>8} "
            f"{str(r['selected_contributes']):>8} {str(r['selected_member_max']):>7} "
            f"{str(r['observed_high']):>6} {str(r['bias']):>7} "
            f"{str(r['latest_snapshot_id']):>8} {str(r['latest_contributes']):>8} "
            f"{str(r['latest_member_max']):>7} {str(r['same_snapshot']):>5}"
        )
    print()
    print(json.dumps(out_rows, indent=2, default=str))


if __name__ == "__main__":
    main()
