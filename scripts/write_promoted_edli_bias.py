# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: A4 per-city EDLI bias correction. Writes model_bias_ens rows the LIVE
#   _snapshot_p_raw reads when edli_v1.edli_bias_correction_enabled is ON.
"""Write per-city EDLI bias rows to model_bias_ens (zeus-world.db) — ALL cities, no exception.

Writes one row per (city, month) for the active trading months (default 5,6 = late-May +
June) so the season/month boundary (MAM->JJA) is covered and the live read (which keys on
season=season_from_date(target_date) AND month=int(target_date[5:7])) matches.

effective_bias_c = mean(forecast - observed) from the canonical settled measurement
(/tmp/canonical_bias_rows.json). Same numeric value the live helper subtracts (train==serve).
NOTE: the canonical bias is fit on MAY (MAM) settled data; applying it to JUNE assumes the
bias persists across the season step (operator-directed; ECMWF bias is seasonal so refit on
JJA settled data when it accumulates).

DRY-RUN by default; --commit writes. Default authority VERIFIED (live-readable).
"""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path
import numpy as np
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.calibration.ens_bias_repo import write_bias_model, init_ens_bias_schema
from src.calibration.manager import season_from_date
from src.config import cities_by_name

LIVE_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max"
PRIOR_DATA_VERSION = "tigge_mx2t6_local_calendar_day_max"
FAMILY = "edli_per_city_v1"
METRIC = "high"
GATE_SET_HASH = "a4_canonical_2026_05_31"

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--authority", default="VERIFIED", choices=["STAGING", "VERIFIED"])
    ap.add_argument("--months", default="5,6", help="active trading months to write rows for")
    ap.add_argument("--rows", default="/tmp/canonical_bias_rows.json")
    args = ap.parse_args()
    months = [int(m) for m in args.months.split(",")]

    canon = json.loads(Path(args.rows).read_text())
    by_city: dict[str, list] = {}
    for r in canon:
        by_city.setdefault(r["city"], []).append(r["err"])

    rows_out = []
    print(f"ALL cities, months={months}, authority={args.authority}, commit={args.commit}\n")
    for city in sorted(by_city):
        cobj = cities_by_name.get(city)
        if cobj is None:
            print(f"  {city}: no city config, skip"); continue
        errs = np.array(by_city[city], dtype=float)
        eff = float(errs.mean())
        sd = float(errs.std(ddof=1)) if len(errs) > 1 else float(errs.std())
        for mo in months:
            season = season_from_date(f"2026-{mo:02d}-15", lat=cobj.lat)
            rows_out.append(dict(
                city=city, season=season, month=mo, metric=METRIC,
                live_data_version=LIVE_DATA_VERSION, prior_data_version=PRIOR_DATA_VERSION,
                posterior_bias_c=eff, posterior_sd_c=sd, n_live=len(errs), n_prior=0,
                weight_live=1.0, estimator="a4_canonical_per_city_settled",
                error_model_family=FAMILY, error_model_key=f"{city}|{season}|{mo}|{METRIC}",
                bias_c=eff, bias_sd_c=sd, residual_sd_c=sd, effective_bias_c=eff,
                total_residual_sd_c=sd, correction_strength=1.0, authority=args.authority,
                gate_set_hash=GATE_SET_HASH, coverage_months=str(mo), month_alias=mo,
                training_cutoff="2026-05-29", recorded_at="2026-05-31",
            ))
        print(f"  {city:13s} eff_bias_c={eff:+.2f}C n={len(errs)} months={months}")

    if not args.commit:
        print(f"\nDRY-RUN: {len(rows_out)} rows. Re-run with --commit.")
        return 0
    from src.state.db import get_world_connection
    conn = get_world_connection()
    init_ens_bias_schema(conn)
    for row in rows_out:
        row.pop("month_alias", None)
        write_bias_model(conn, **row)
    conn.commit()
    print(f"\nWROTE {len(rows_out)} rows ({len(by_city)} cities x {len(months)} months, authority={args.authority}).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
