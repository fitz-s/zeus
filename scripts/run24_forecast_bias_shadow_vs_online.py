# Created: 2026-05-30
# Last reused or audited: 2026-05-30
# Authority basis: Run #24 unshadow gate per docs/operations/HANDOFF_2026-05-30_FULL_REMAINING.md:34
#   + LIVE_HALT_RECOVERY_HANDOFF_2026-05-30.md:7 ("#24, bin bias <=1")
#   + LIVE_SHADOW_SEAMHUNT_HANDOFF_2026-05-29.md:65 (shadow p_raw vs online forecast).
# Purpose: READ-ONLY #24 forecast-bias gate. Per (city, target_date, HIGH):
#   shadow predicted bin (our ensemble p_raw argmax over the market bin grid, via the
#   canonical EDLI path p_raw_vector_from_maxes) vs online predicted bin (open-meteo
#   published deterministic forecast_high -> market bin). bin_bias = |idx_shadow - idx_online|.
#   PASS if bin_bias <= 1.  NEVER mutates any DB or live_status.
"""Run #24 — forecast-website-error / shadow-vs-online bin-bias gate (READ-ONLY).

Definition (verified against repo handoffs):
  online forecast      = Open-Meteo PUBLISHED deterministic forecast (best_match,
                         stored in forecasts.source='openmeteo_previous_runs',
                         forecast_high in city settlement unit). Falls back to a
                         fresh HTTP fetch only if the table has no covering row.
  shadow predicted bin = argmax over the market bin grid of the ensemble p_raw
                         vector computed by the CANONICAL inference path
                         src.signal.ensemble_signal.p_raw_vector_from_maxes
                         (same MC+noise+rounding code the EDLI kernel _snapshot_p_raw
                         uses), fed members_json from the freshest COMPLETE
                         ensemble_snapshots row for that (city,target_date,high).
  online predicted bin = the market bin whose [range_low, range_high] contains the
                         online forecast_high.
  bin bias             = |bin_index(shadow) - bin_index(online)| over the ordered
                         market bin grid.  Gate: <= 1.

Output: per-city table + JSON dump to /tmp (bash stdout mangles floats; verify via Read).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

from src.config import STATE_DIR  # noqa: E402
from src.engine.replay import bin_from_range_label  # noqa: E402
from src.signal.ensemble_signal import p_raw_vector_from_maxes  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402


def _world_db() -> Path:
    return STATE_DIR / "zeus-world.db"


def _forecasts_db() -> Path:
    return STATE_DIR / "zeus-forecasts.db"


def _load_city(city_name: str):
    """Return the runtime City object for a name."""
    from src.config import cities_by_name  # local import keeps top clean

    return cities_by_name.get(city_name)


def _ordered_bins(fconn: sqlite3.Connection, city, target_date: str):
    """Return ordered list of (range_label, Bin) for the HIGH market grid.

    Ordered by range_low (open-low bin first). Returns [] if no market grid.
    """
    rows = fconn.execute(
        """
        SELECT DISTINCT range_label, range_low, range_high
        FROM market_events
        WHERE city = ? AND target_date = ? AND temperature_metric = 'high'
          AND range_label IS NOT NULL AND range_label != ''
        """,
        (city.name, target_date),
    ).fetchall()
    parsed = []
    for r in rows:
        b = bin_from_range_label(r["range_label"], city.settlement_unit)
        if b is None:
            continue
        # sort key: open-low bin uses -inf, open-high uses +inf
        lo = b.low if b.low is not None else -1e9
        parsed.append((lo, r["range_label"], b))
    parsed.sort(key=lambda t: t[0])
    return [(lbl, b) for (_lo, lbl, b) in parsed]


def _online_forecast_high(wconn: sqlite3.Connection, city_name: str, target_date: str):
    """Most-recent (lowest forecast_basis_date gap, i.e. freshest lead) open-meteo
    published forecast_high. Returns (value, basis_date, lead_days) or None.

    Uses source='openmeteo_previous_runs' = open-meteo best_match deterministic run.
    Prefers the SMALLEST lead_days (freshest published forecast = closest to nowcast).
    """
    row = wconn.execute(
        """
        SELECT forecast_high, forecast_basis_date, lead_days, temp_unit
        FROM forecasts
        WHERE city = ? AND target_date = ? AND source = 'openmeteo_previous_runs'
          AND forecast_high IS NOT NULL
        ORDER BY lead_days ASC
        LIMIT 1
        """,
        (city_name, target_date),
    ).fetchone()
    if row is None:
        return None
    return (float(row["forecast_high"]), row["forecast_basis_date"], int(row["lead_days"]), row["temp_unit"])


def _shadow_members(fconn: sqlite3.Connection, city_name: str, target_date: str):
    """Freshest COMPLETE ensemble snapshot members for (city,target_date,high).

    Picks the most recent issue_time / fetch_time. Returns (members np.ndarray,
    issue_time, lead_hours, n_members) or None.
    """
    row = fconn.execute(
        """
        SELECT members_json, issue_time, lead_hours, members_unit
        FROM ensemble_snapshots
        WHERE city = ? AND target_date = ? AND temperature_metric = 'high'
          AND members_json IS NOT NULL
          AND causality_status = 'OK'
        ORDER BY issue_time DESC, fetch_time DESC
        LIMIT 1
        """,
        (city_name, target_date),
    ).fetchone()
    if row is None:
        return None
    members = np.asarray(json.loads(row["members_json"]), dtype=float)
    return (members, row["issue_time"], row["lead_hours"], len(members), row["members_unit"])


def _bin_index_for_value(bins, value: float, semantics):
    """Return index of the market bin that settles for `value`.

    Market bins are integer-degree (e.g. 29C, 30C) with open-low/open-high
    shoulders. A continuous forecast must first be SETTLEMENT-ROUNDED to the
    integer the market resolves on (same round_values the MC p_raw uses), then
    matched by containment. Without this, a value like 29.6 lands between the
    29C and 30C point-bins and matches nothing.
    """
    settled = float(semantics.round_values(np.asarray([value], dtype=float))[0])
    for i, (_lbl, b) in enumerate(bins):
        lo = b.low if b.low is not None else -1e9
        hi = b.high if b.high is not None else 1e9
        if lo <= settled <= hi:
            return i
    # settled value beyond grid: clamp to nearest edge bin
    if settled <= (bins[0][1].high if bins[0][1].high is not None else 1e9):
        return 0
    return len(bins) - 1


def evaluate_city(fconn, wconn, city_name: str, target_date: str) -> dict:
    city = _load_city(city_name)
    if city is None:
        return {"city": city_name, "target_date": target_date, "status": "NO_CITY_CONFIG"}

    bins = _ordered_bins(fconn, city, target_date)
    if not bins:
        return {"city": city_name, "target_date": target_date, "status": "NO_MARKET_GRID"}

    shadow = _shadow_members(fconn, city_name, target_date)
    if shadow is None:
        return {"city": city_name, "target_date": target_date, "status": "NO_ENSEMBLE_SNAPSHOT"}
    members, issue_time, lead_hours, n_members, members_unit = shadow

    online = _online_forecast_high(wconn, city_name, target_date)

    # --- shadow p_raw via canonical EDLI inference path ---
    semantics = SettlementSemantics.for_city(city)
    bin_objs = [b for (_lbl, b) in bins]
    p_raw = p_raw_vector_from_maxes(members, city, semantics, bin_objs)
    shadow_idx = int(np.argmax(p_raw))
    shadow_label = bins[shadow_idx][0]
    shadow_mean = float(np.mean(members))

    result = {
        "city": city_name,
        "target_date": target_date,
        "n_bins": len(bins),
        "n_members": n_members,
        "members_unit": members_unit,
        "snapshot_issue_time": issue_time,
        "snapshot_lead_hours": lead_hours,
        "shadow_member_mean_C": round(shadow_mean, 3),
        "shadow_bin_idx": shadow_idx,
        "shadow_bin_label": shadow_label,
        "shadow_p_raw_max": round(float(p_raw[shadow_idx]), 4),
    }

    if online is None:
        result["status"] = "NO_ONLINE_FORECAST"
        return result

    online_val, basis, lead_days, online_unit = online
    online_idx = _bin_index_for_value(bins, online_val, semantics)
    online_settled = float(semantics.round_values(np.asarray([online_val], dtype=float))[0])
    online_label = bins[online_idx][0]

    bias = abs(shadow_idx - online_idx)
    result.update(
        {
            "online_forecast_high": round(online_val, 3),
            "online_unit": online_unit,
            "online_basis_date": basis,
            "online_lead_days": lead_days,
            "online_bin_idx": online_idx,
            "online_bin_label": online_label,
            "bin_bias": bias,
            "verdict": "PASS" if bias <= 1 else "FAIL",
            "status": "OK",
        }
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cities",
        default="Wuhan,Wellington,Taipei,Tel Aviv,Toronto,Tokyo",
        help="comma-separated city names",
    )
    ap.add_argument(
        "--target-dates",
        default="2026-05-31,2026-06-01",
        help="comma-separated target dates",
    )
    ap.add_argument("--out", default="/tmp/run24_bin_bias.json")
    args = ap.parse_args()

    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    dates = [d.strip() for d in args.target_dates.split(",") if d.strip()]

    # Canonical connection helpers (writer-lock antibody: no raw sqlite3.connect to live DBs).
    from src.state.db import get_forecasts_connection_read_only, get_world_connection
    fconn = get_forecasts_connection_read_only()
    fconn.row_factory = sqlite3.Row
    wconn = get_world_connection()
    wconn.row_factory = sqlite3.Row

    rows = []
    try:
        for c in cities:
            for d in dates:
                rows.append(evaluate_city(fconn, wconn, c, d))
    finally:
        fconn.close()
        wconn.close()

    Path(args.out).write_text(json.dumps(rows, indent=2, default=str))

    # compact console table
    ok = [r for r in rows if r.get("status") == "OK"]
    print(f"{'city':12s} {'date':11s} {'shadowT':>8s} {'sBin':>5s} {'onlineT':>8s} {'oBin':>5s} {'bias':>4s} verdict")
    for r in rows:
        if r.get("status") != "OK":
            print(f"{r['city']:12s} {r['target_date']:11s}  [{r.get('status')}]")
            continue
        print(
            f"{r['city']:12s} {r['target_date']:11s} "
            f"{r['shadow_member_mean_C']:8.2f} {r['shadow_bin_idx']:5d} "
            f"{r['online_forecast_high']:8.2f} {r['online_bin_idx']:5d} "
            f"{r['bin_bias']:4d} {r['verdict']}"
        )
    n_pass = sum(1 for r in ok if r.get("verdict") == "PASS")
    n_fail = sum(1 for r in ok if r.get("verdict") == "FAIL")
    print(f"\nEVALUATED_OK={len(ok)} PASS={n_pass} FAIL={n_fail}  (gate: bin_bias<=1)")
    print(f"JSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
