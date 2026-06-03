# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: D-7 rolling per-city EDLI bias producer. REPLACES the stale static-May
#   fit (scripts/write_promoted_edli_bias.py, frozen /tmp/canonical_bias_rows.json) with a
#   causal trailing-7-distinct-settled-day rolling bias. Walk-forward OOS (this session):
#   bias_d7 MAE 1.882 < static 2.098 < grid_rep 2.193 < emos 2.881 < raw 3.036 (settlement
#   degrees). D-7 is the validated winner; MIN_N=3 (raw fallback below).
#
#   SAME write shape/keying as write_promoted_edli_bias.py so the LIVE reader
#   (event_reactor_adapter._maybe_apply_edli_bias_correction -> read_bias_model) is
#   unchanged: model_bias_ens VERIFIED, family='edli_per_city_v1', keyed (city, season,
#   month, metric, live_data_version), lead_bucket='LEGACY_POOLED', coverage_months=month,
#   gate_set_hash=a4_canonical_2026_05_31, effective_bias_c in degC (reader x1.8 for F).
"""Daily-recomputed D-7 rolling per-city EDLI bias -> model_bias_ens (zeus-world.db).

For each city, over the TRAILING window of the last ``window_days`` DISTINCT settled days
strictly before ``now`` (settled_at <= now — strict causality, only settlements that have
actually settled), compute the residual in CANONICAL degC:

    residual_degC = degC(mean(latest_causal_snapshot_members)) - degC(settlement_value)

(members + settlement normalized to degC via members_unit / settlement_unit; require
members_unit present and equal-family to settlement_unit). effective_bias_c =
mean(residuals). If fewer than ``min_n`` (default 3) settled days are in the window, NO row
is written for that city (the live reader falls back to raw members).

Convention: ``effective_bias_c`` is stored in degC EXACTLY as the static producer did
(SF static eff=-4.682 is the degC residual, validated this session: SF degC residual
-5.04 vs degF -9.07 — the store is degC, the reader re-scales x1.8 for F cities). Computing
the residual in degC and storing degC makes the bias+grid 1.8x-mismatch category impossible.

DRY-RUN by default; --commit writes to state/zeus-world.db. Idempotent daily upsert
(INSERT OR REPLACE on the PK), so re-running the same day overwrites in place.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.calibration.ens_bias_repo import _to_c, init_ens_bias_schema, write_bias_model
from src.calibration.manager import season_from_date

# Keep these IDENTICAL to write_promoted_edli_bias.py so the live read key matches.
LIVE_DATA_VERSION_HIGH = "ecmwf_opendata_mx2t3_local_calendar_day_max"
LIVE_DATA_VERSION_LOW = "ecmwf_opendata_mn2t3_local_calendar_day_min"
FAMILY = "edli_per_city_v1"
GATE_SET_HASH = "a4_canonical_2026_05_31"
METHOD = "d7_rolling"
DEFAULT_WINDOW_DAYS = 7
MIN_N = 3
LEAD_BUCKET = "LEGACY_POOLED"  # matches static producer rows (reader does not bucket-filter)


def _unit_family(unit: str | None) -> str:
    """Return 'F' or 'C' family for a unit string, or raise on unknown."""
    u = (unit or "").strip().lower()
    if u in {"f", "degf", "fahrenheit"} or (u and u.endswith("f")):
        return "F"
    if u in {"c", "degc", "celsius"} or (u and u.endswith("c")):
        return "C"
    raise ValueError(f"unknown temperature unit: {unit!r}")


def _latest_causal_snapshot_mean_c(
    conn, *, city: str, target_date: str, data_version: str, metric: str,
    settlement_unit: str,
) -> float | None:
    """Mean (degC) of the latest-available causal snapshot's non-null members for
    (city, target_date). Requires members_unit present and same family as the
    settlement unit (no cross-unit residual). Returns None if no usable snapshot.
    """
    rows = conn.execute(
        """
        SELECT members_json AS mj, members_unit AS mu, available_at AS av
        FROM ensemble_snapshots
        WHERE city = ? AND target_date = ? AND dataset_id = ?
          AND temperature_metric = ? AND authority = 'VERIFIED'
          AND members_json IS NOT NULL AND members_unit IS NOT NULL
        ORDER BY available_at DESC
        """,
        (city, target_date, data_version, metric),
    ).fetchall()
    for r in rows:
        mu = r["mu"]
        try:
            # members and settlement must share the same unit family (no Kelvin leak /
            # cross-unit residual). Mismatch -> skip this snapshot (fail-closed).
            if _unit_family(mu) != _unit_family(settlement_unit):
                continue
            parsed = json.loads(r["mj"])
            vals = [
                float(x)
                for x in (parsed.values() if isinstance(parsed, dict) else parsed)
                if x is not None
            ]
            if not vals:
                continue
            return _to_c(statistics.fmean(vals), mu)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def compute_city_bias(
    conn,
    *,
    city: str,
    metric: str,
    data_version: str,
    now_iso: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_n: int = MIN_N,
) -> dict | None:
    """Compute the D-7 rolling effective_bias_c (degC) for one city, or None (raw fallback).

    CAUSALITY: only settlements with settled_at <= now_iso enter the window (strictly the
    settlements that have actually settled as of the decision/run time — a settlement
    settled in the future is invisible, no look-ahead). The window is the last
    ``window_days`` DISTINCT settled target_dates before ``now``.

    Residuals are in CANONICAL degC: degC(mean members) - degC(settlement_value), both
    normalized from the city's native unit. effective_bias_c = mean(residuals).
    Returns None if fewer than ``min_n`` settled days are in the window (raw fallback).
    """
    settled_rows = conn.execute(
        """
        SELECT target_date AS td, settlement_value AS sv, settlement_unit AS su,
               settled_at AS sat
        FROM settlement_outcomes
        WHERE city = ? AND temperature_metric = ? AND authority = 'VERIFIED'
          AND settled_at IS NOT NULL AND settled_at <= ?
        ORDER BY target_date DESC
        """,
        (city, metric, now_iso),
    ).fetchall()

    # Last `window_days` DISTINCT settled target_dates (most recent first), keeping the
    # row whose settled_at is the latest for each date (defensive against dupes).
    by_date: dict[str, dict] = {}
    for r in settled_rows:
        td = str(r["td"])
        prev = by_date.get(td)
        if prev is None or str(r["sat"]) > str(prev["sat"]):
            by_date[td] = {"sv": r["sv"], "su": r["su"], "sat": r["sat"]}
    window_dates = sorted(by_date.keys(), reverse=True)[:window_days]

    residuals: list[float] = []
    used_dates: list[str] = []
    for td in window_dates:
        rec = by_date[td]
        sv, su = rec["sv"], rec["su"]
        if sv is None or su is None:
            continue
        try:
            fc_mean_c = _latest_causal_snapshot_mean_c(
                conn, city=city, target_date=td, data_version=data_version,
                metric=metric, settlement_unit=su,
            )
            if fc_mean_c is None:
                continue
            settle_c = _to_c(float(sv), su)
        except (ValueError, TypeError):
            continue
        residuals.append(fc_mean_c - settle_c)
        used_dates.append(td)

    if len(residuals) < min_n:
        return None

    eff = statistics.fmean(residuals)
    sd = statistics.stdev(residuals) if len(residuals) > 1 else 0.0
    used_sorted = sorted(used_dates)
    return {
        "effective_bias_c": float(eff),
        "sd_c": float(sd),
        "n_window": len(residuals),
        "window_dates": used_sorted,
        "window_start": used_sorted[0],
        "window_end": used_sorted[-1],
    }


def write_city_bias(
    world_conn,
    *,
    city,
    metric: str,
    data_version: str,
    bias: dict,
    now_iso: str,
    months: tuple[int, ...] | None = None,
    lat: float = 90.0,
    authority: str = "VERIFIED",
) -> list[dict]:
    """Upsert the D-7 bias row(s) into model_bias_ens, same shape/key as the static
    producer. Writes one row per active month (default: the month of window_end) so the
    season/month boundary the live read keys on is covered. Idempotent (INSERT OR REPLACE).
    Returns the list of row dicts written.
    """
    # Accept either a City dataclass (from main()) or a plain city-name str (from tests).
    city_name = getattr(city, "name", city)
    if lat == 90.0:
        lat = float(getattr(city, "lat", 90.0))
    eff = float(bias["effective_bias_c"])
    sd = float(bias["sd_c"])
    n = int(bias["n_window"])
    cov_end_month = int(str(bias["window_end"])[5:7])
    if months is None:
        months = (cov_end_month,)

    written: list[dict] = []
    for mo in months:
        season = season_from_date(f"2026-{mo:02d}-15", lat=lat)
        write_bias_model(
            world_conn,
            city=city_name,
            season=season,
            month=mo,
            metric=metric,
            live_data_version=data_version,
            prior_data_version=None,
            posterior_bias_c=eff,
            posterior_sd_c=sd,
            n_live=n,
            n_prior=0,
            weight_live=1.0,
            estimator="d7_rolling_per_city_settled",
            bias_unit="C",
            bias_c=eff,
            bias_sd_c=sd,
            residual_sd_c=sd,
            effective_bias_c=eff,
            total_residual_sd_c=sd,
            correction_strength=1.0,
            error_model_family=FAMILY,
            error_model_key=f"{city_name}|{season}|{mo}|{metric}|{METHOD}",
            authority=authority,
            gate_set_hash=GATE_SET_HASH,
            coverage_months=str(mo),
            lead_bucket=LEAD_BUCKET,
            transport_delta_policy=json.dumps(
                {
                    "method": METHOD,
                    "n_window": n,
                    "window_start": bias["window_start"],
                    "window_end": bias["window_end"],
                    "recorded_at": now_iso,
                }
            ),
            training_cutoff=now_iso,
            recorded_at=now_iso,
        )
        written.append({"city": city_name, "season": season, "month": mo, "eff_bias_c": eff, "n": n})
    return written


def _old_static_bias(world_conn, city: str, metric: str, data_version: str) -> float | None:
    """Best-effort lookup of the current stored eff_bias_c (for the comparison table)."""
    try:
        r = world_conn.execute(
            "SELECT effective_bias_c FROM model_bias_ens "
            "WHERE city=? AND metric=? AND live_data_version=? AND error_model_family=? "
            "AND authority='VERIFIED' ORDER BY recorded_at DESC LIMIT 1",
            (city, metric, data_version, FAMILY),
        ).fetchone()
        return float(r[0]) if r and r[0] is not None else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write to zeus-world.db")
    ap.add_argument("--metric", default="high", choices=["high", "low"])
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--months", default="", help="CSV of months to write (default: window_end month)")
    ap.add_argument(
        "--now",
        default="",
        help="ISO decision time (default: now UTC). Strict causality cutoff for settled_at.",
    )
    args = ap.parse_args()

    now_iso = args.now or datetime.now(timezone.utc).isoformat()
    data_version = LIVE_DATA_VERSION_HIGH if args.metric == "high" else LIVE_DATA_VERSION_LOW
    months = tuple(int(m) for m in args.months.split(",") if m.strip()) or None

    from src.config import cities_by_name
    from src.state.db import get_forecasts_connection_read_only, get_world_connection

    import sqlite3 as _sqlite3
    fc = get_forecasts_connection_read_only()
    fc.row_factory = _sqlite3.Row

    world = get_world_connection()
    world.row_factory = _sqlite3.Row
    init_ens_bias_schema(world)

    print(
        f"D-7 rolling EDLI bias | metric={args.metric} dv={data_version} "
        f"now={now_iso} window={args.window_days}d min_n={args.min_n} commit={args.commit}\n"
    )
    header = f"{'city':16s} {'n':>3s} {'window':23s} {'d7_bias_c':>10s} {'old_static':>11s} {'delta':>8s}"
    print(header)
    print("-" * len(header))

    written_total = 0
    thin_cities: list[str] = []
    moved_1c: list[tuple[str, float]] = []
    for name in sorted(cities_by_name):
        city = cities_by_name[name]
        bias = compute_city_bias(
            fc, city=city.name, metric=args.metric, data_version=data_version,
            now_iso=now_iso, window_days=args.window_days, min_n=args.min_n,
        )
        old = _old_static_bias(world, city.name, args.metric, data_version)
        if bias is None:
            thin_cities.append(city.name)
            print(f"{city.name:16s} {'--':>3s} {'(thin-n raw fallback)':23s} "
                  f"{'--':>10s} {('%+.2f' % old) if old is not None else '--':>11s} {'--':>8s}")
            continue
        eff = bias["effective_bias_c"]
        win = f"{bias['window_start']}..{bias['window_end']}"
        delta = (eff - old) if old is not None else None
        if delta is not None and abs(delta) > 1.0:
            moved_1c.append((city.name, delta))
        print(f"{city.name:16s} {bias['n_window']:>3d} {win:23s} {eff:>+10.2f} "
              f"{('%+.2f' % old) if old is not None else '--':>11s} "
              f"{('%+.2f' % delta) if delta is not None else '--':>8s}")
        if args.commit:
            rows = write_city_bias(
                world, city=city, metric=args.metric, data_version=data_version,
                bias=bias, now_iso=now_iso, months=months, lat=city.lat,
            )
            written_total += len(rows)

    print()
    if thin_cities:
        print(f"THIN-N (raw fallback, no row): {', '.join(thin_cities)}")
    if moved_1c:
        print("MOVED >1C vs old static: " + ", ".join(f"{c}({d:+.2f})" for c, d in moved_1c))

    if args.commit:
        world.commit()
        print(f"\nWROTE {written_total} rows (authority=VERIFIED). Daily idempotent upsert.")
    else:
        print("\nDRY-RUN: no rows written. Re-run with --commit.")
    fc.close()
    world.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
