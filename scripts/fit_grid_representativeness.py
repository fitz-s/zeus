# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator "finish v3" (2026-06-17) — walk-forward fit of the v3
#   zeus_grid_coordinate_precision_upgrade_v3.md rule 5 (sigma_repr^2 = g(d_eff,|dz|,regime))
#   and rule 4 (station/grid shift beta_alt/b_grid) from VERIFIED settled residuals.
#   Operator law: sigma_repr fitted (MLE/walk-forward), NEVER cold-start-forever. Read-only DB.
"""Fit the v3 grid-representativeness coefficients OUT-OF-SAMPLE.

For every VERIFIED settled (city, metric, target_date) lead-1 cell in the FIT window
[-fit_lo d, -holdout d] (default [-60, -7]) we build, per model present in the grid table:

  settlement_residual = settled_truth_C - model_forecast_value_C   (rule 4/5 convention:
      settled - x_station; for rule 5 only the SQUARE enters, so the sign is immaterial there)
  d_eff_m, dz_m       = the model's native-cell distance + delta_z from
      config/grid_representativeness.json (the SAME table the live loader reads)

Then:
  * rule 5: fit_representativeness_variance(rows) -> ReprVarianceFit (a0, a_d, a_z)
  * rule 4: fit_station_shift(rows)             -> StationShiftFit  (beta_alt, b_grid)

Both fits are POOLED across cities/models (a single global stratum) because the grid
features (d_eff, dz) already carry the per-cell variation; per-(city,model) strata are far
too thin for an honest slope (the live loader passes the pooled fit + per-cell d_eff/dz).
The holdout window [-holdout, now] is RESERVED for scripts/validate_grid_representativeness_fusion.py
(no overlap -> no leakage between fit and validation).

Writes state/repr_variance_fit.json + state/station_shift_fit.json (the fitted dataclass
fields + n_train + fitted flag). Read-only on the live DB (?mode=ro).

Usage: python scripts/fit_grid_representativeness.py [--fit-lo 60] [--holdout 7] [--lead 1]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forecast.grid_representativeness_loader import load_grid_representativeness
from src.forecast.representativeness_variance import (
    ReprResidualRow,
    StationShiftResidualRow,
    fit_representativeness_variance,
    fit_station_shift,
)

REPO = Path(__file__).resolve().parents[1]
FORECASTS_DB = REPO / "state" / "zeus-forecasts.db"
REPR_FIT_OUT = REPO / "state" / "repr_variance_fit.json"
SHIFT_FIT_OUT = REPO / "state" / "station_shift_fit.json"


def _settle_to_c(value: float, unit: str | None) -> float:
    """Settlement -> degC (forecast_value_c is always degC). F settlement converts first."""
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def build_residual_rows(
    con: sqlite3.Connection,
    *,
    fit_lo: int,
    holdout: int,
    lead: int,
) -> tuple[list[ReprResidualRow], list[StationShiftResidualRow], dict]:
    """Build pooled rule-5 + rule-4 residual rows over the FIT window [-fit_lo, -holdout].

    Uses endpoint='previous_runs' (fixed-lead train product, the SAME source the live
    history provider trains on) joined to VERIFIED settlement, strictly inside the fit
    window so the holdout stays untouched. Each row is keyed to its (city, model) grid cell.
    """
    grid = load_grid_representativeness()
    repr_rows: list[ReprResidualRow] = []
    shift_rows: list[StationShiftResidualRow] = []
    diag = {"n_join": 0, "n_in_grid": 0, "models": {}, "cities": set()}

    rows = con.execute(
        """
        SELECT r.city AS city, r.model AS model, r.target_date AS target_date,
               r.forecast_value_c AS fv,
               s.settlement_value AS sv, s.settlement_unit AS unit
        FROM raw_model_forecasts AS r
        JOIN settlement_outcomes AS s
          ON s.city = r.city AND s.target_date = r.target_date
         AND s.temperature_metric = r.metric
        WHERE r.endpoint = 'previous_runs'
          AND r.lead_days = ?
          AND s.authority = 'VERIFIED'
          AND s.settlement_value IS NOT NULL
          AND r.forecast_value_c IS NOT NULL
          AND r.target_date >= date('now', ?)
          AND r.target_date <  date('now', ?)
        """,
        (int(lead), f"-{fit_lo} day", f"-{holdout} day"),
    ).fetchall()

    for row in rows:
        diag["n_join"] += 1
        city, model = row["city"], row["model"]
        cell = ((grid.get(city) or {}).get("models") or {}).get(model)
        if not isinstance(cell, dict):
            continue
        d_eff = cell.get("d_eff_m")
        if d_eff is None:
            continue
        dz = cell.get("delta_z_m")
        try:
            settled_c = _settle_to_c(row["sv"], row["unit"])
            fv = float(row["fv"])
            d_eff_m = float(d_eff)
            dz_m = float(dz) if dz is not None else 0.0
        except (TypeError, ValueError):
            continue
        # rule 4/5 convention: settled - x_station (sign immaterial for rule-5 square).
        resid = settled_c - fv
        repr_rows.append(
            ReprResidualRow(
                settlement_residual=resid,
                d_eff_m=d_eff_m,
                dz_m=dz_m,
                # Regime flags carried from the cold-start coarse stratification (the fit
                # interface refines a0/a_d/a_z; regime multipliers ride the base_fit). Not
                # set per-row here -> all False (the conservative pooled fit).
            )
        )
        shift_rows.append(
            StationShiftResidualRow(settlement_residual=resid, dz=dz_m)
        )
        diag["n_in_grid"] += 1
        diag["models"][model] = diag["models"].get(model, 0) + 1
        diag["cities"].add(city)

    diag["cities"] = sorted(diag["cities"])
    return repr_rows, shift_rows, diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit-lo", type=int, default=60, help="fit window lower bound (days back)")
    ap.add_argument("--holdout", type=int, default=7, help="reserve last N days for validation")
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--min-train", type=int, default=25)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{FORECASTS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    repr_rows, shift_rows, diag = build_residual_rows(
        con, fit_lo=args.fit_lo, holdout=args.holdout, lead=args.lead
    )
    con.close()

    repr_fit = fit_representativeness_variance(repr_rows, min_train=args.min_train)
    shift_fit = fit_station_shift(shift_rows, min_train=args.min_train)

    REPR_FIT_OUT.write_text(json.dumps(asdict(repr_fit), indent=2))
    SHIFT_FIT_OUT.write_text(json.dumps(asdict(shift_fit), indent=2))

    print(f"=== Grid-representativeness FIT (fit window [-{args.fit_lo}d, -{args.holdout}d], lead-{args.lead}) ===")
    print(f"  joinable previous_runs rows in window : {diag['n_join']}")
    print(f"  rows with a grid-table cell           : {diag['n_in_grid']}  ({len(diag['cities'])} cities)")
    print(f"  per-model row counts                  : "
          + ", ".join(f"{m}={n}" for m, n in sorted(diag["models"].items(), key=lambda kv: -kv[1])))
    print()
    print("  rule 5 (repr variance) ReprVarianceFit:")
    print(f"    a0={repr_fit.a0:.6f}  a_d={repr_fit.a_d:.6f}  a_z={repr_fit.a_z:.3e}")
    print(f"    regime mults coastal={repr_fit.coastal_mult} orography={repr_fit.orography_mult} urban={repr_fit.urban_mult}")
    print(f"    n_train={repr_fit.n_train}  fitted={repr_fit.fitted}")
    print(f"    (cold-start was a0=0.25 a_d=0.04 a_z=2.5e-05)")
    print()
    print("  rule 4 (station shift) StationShiftFit:")
    print(f"    beta_alt={shift_fit.beta_alt:.6f}  b_grid={shift_fit.b_grid:.6f}")
    print(f"    n_train={shift_fit.n_train}  fitted={shift_fit.fitted}")
    print()
    print(f"  wrote {REPR_FIT_OUT.relative_to(REPO)}")
    print(f"  wrote {SHIFT_FIT_OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
