# Lifecycle: created=2026-06-18; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Build the generated side-aware OOF q_lcb reliability artifact for live guard serving.
# Reuse: Run when rebuilding the q_lcb OOF reliability artifact or changing guard cell schema.
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §"THE q_lcb RELIABILITY GUARD" + src/decision/qlcb_reliability_guard.py (the LIVE guard:
#   cell scheme, QLCB_BUCKET_EDGES, lead_bucket, cell_key). Operator task ("两个都做"): build the
#   OFFLINE OOF reliability table that ACTIVATES the RAW q_lcb reliability guard — across BOTH
#   settlement metrics (high/low) and ALL lead buckets (L1/L2_3/L4P), keyed by the REFINED 0.05
#   bucket grid. RAW no-de-bias law: the center is the live RAW diagonal 1/E[r^2] 2nd-moment
#   center (src/forecast/center.py: raw_second_moment_weights + weighted_huber_location) over the
#   LIVE per-city select_models set + ECMWF anchor (src/forecast/model_selection.select_models —
#   so the coarse globals / jma / icon_seamless dropped from the live fusion are NEVER centred);
#   q_lcb reproduces the production build_joint_q_band draw loop over the production settlement-
#   preimage integrator (src/calibration/emos.bin_probability_settlement, vectorized + validated
#   byte-equal to the scalar primitive at startup) threaded with each city's production
#   rounding_rule (src/contracts/settlement_semantics). The emitted cells are side-aware:
#   YES grades bin hit-rate; NO grades complement hit-rate for the exact NO claim.
#
# This script WRITES ONLY state/qlcb_oof_reliability.json (a generated artifact, NOT a *.db).
# It opens state/zeus-forecasts.db READ-ONLY immutable. It modifies NO src/ code.
"""Build the OOF q_lcb reliability table (rolling-origin, strictly-prior training only).

For each (metric, lead, city, target_date) prediction in /tmp/multilead_forecasts.json:
  1. Resolve the LIVE fusion member set: select_models(present, lat, lon, lead).used_models
     (decorrelated globals + in-domain regional reps + the ECMWF anchor). Coarse globals /
     jma_seamless / icon_seamless are absent from the live set and so are never centred.
  2. Build the RAW diagonal-precision center over those members using ONLY strictly-PRIOR
     (target_date - 1d and earlier) per-model residual history -> production center
     (raw_second_moment_weights + weighted_huber_location). sigma = the train-RMSE of that center
     over the strictly-prior window (the realized-floor width).
  3. Build a complete integer-degree MECE Omega around the member envelope, integrated under the
     city's production rounding_rule settlement preimage.
  4. Reproduce build_joint_q_band: N_DRAWS parameter-posterior draws (mu ~ N(mu*, center_se),
     sigma floored), per-draw simplex bin integration, marginal alpha-quantile -> q_lcb per
     YES bin and q_lcb over each NO complement. The point joint q gives the modal
     (argmax-mass) bin.
  5. For each bin and executable side: cell = cell_key(metric, lead_days, side, bin_position,
     q_lcb[side, bin]); tally n += 1. YES hits when settlement is in the bin; NO hits when
     settlement is outside the bin. Cells MERGE across leads by lead_bucket (L1/L2_3/L4P) and
     across the two source aggregations by metric.

Production-equivalence relied on (documented in the report): the full PredictiveDistribution /
OutcomeSpace / venue-family stack is NOT wired standalone; instead the SAME integrator, the SAME
center weights, the SAME rounding-rule preimage, the SAME select_models set, and the SAME
draw_mu/draw_sigma/marginal-quantile algebra the live band uses are driven directly over a
reconstructed complete Omega.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np
from scipy.stats import norm as _norm

# --- production primitives (imported, never re-implemented) -------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.calibration.emos import bin_probability_settlement  # settlement-preimage integrator
from src.contracts.settlement_semantics import settlement_preimage_offsets
from src.forecast.center import raw_second_moment_weights, weighted_huber_location
from src.forecast.model_selection import select_models
from src.decision.qlcb_reliability_guard import cell_key, lead_bucket  # the LIVE guard cell scheme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORECASTS_PATH = "/tmp/multilead_forecasts.json"
DB_PATH = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_PATH = os.path.join(REPO, "state", "qlcb_oof_reliability.json")

# Band reproduction constants — byte-identical to src/probability/joint_q_band.py.
N_DRAWS = 4000
ALPHA = 0.05
_SIGMA_POSITIVE_FLOOR = 1e-6
MIN_PRIOR_FOR_RMSE = 3   # need a few prior days before a center RMSE is meaningful
OMEGA_PAD = 12           # degrees of integer bins beyond the member envelope each side

# Metrics and the leads fetched (previous_dayN -> lead_bucket via the guard's lead_bucket()).
METRICS = ("high", "low")
LEADS = (1, 2, 3, 5, 7)


def _c_to_native(value_c: float, unit: str) -> float:
    return value_c * 9.0 / 5.0 + 32.0 if unit == "F" else value_c


def _center_se_native(members_native: np.ndarray) -> float:
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    spread = float(np.std(arr, ddof=1))
    se = spread / math.sqrt(arr.size)
    return se if math.isfinite(se) and se > 0.0 else 0.0


def _model_disp_native(members_native: np.ndarray) -> float:
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    s = float(np.std(arr, ddof=1))
    return s if math.isfinite(s) and s > 0.0 else 0.0


def load_settlements(con: sqlite3.Connection, metric: str):
    """{(city, date): (settled_native, unit, source_type)} for VERIFIED settled rows of metric."""
    rows = con.execute(
        "SELECT city, target_date, settlement_value, unit, settlement_source_type "
        "FROM settlements WHERE temperature_metric=? AND settlement_value IS NOT NULL "
        "AND unit IS NOT NULL",
        (metric,),
    ).fetchall()
    return {(c, td): (float(v), u, (s or "")) for c, td, v, u, s in rows}


def rounding_rule_for(source_type: str) -> str:
    return "oracle_truncate" if str(source_type).lower() == "hko" else "wmo_half_up"


def build_omega(members_native: np.ndarray):
    """Complete integer-degree MECE partition: open-low shoulder, single-integer interior, open-high."""
    lo_i = int(math.floor(float(np.min(members_native)))) - OMEGA_PAD
    hi_i = int(math.ceil(float(np.max(members_native)))) + OMEGA_PAD
    bins = [(None, float(lo_i))]
    for t in range(lo_i + 1, hi_i):
        bins.append((float(t), float(t)))
    bins.append((float(hi_i), None))
    return bins


def _edges_for(bins, rounding_rule: str):
    """Pre-resolve per-bin (lower, upper) integration edges in native units. None shoulder -> +-inf."""
    low_off, high_off = settlement_preimage_offsets(rounding_rule, half_step=0.5)
    lower = np.empty(len(bins), dtype=float)
    upper = np.empty(len(bins), dtype=float)
    for i, (lo, hi) in enumerate(bins):
        lower[i] = -np.inf if lo is None else (float(lo) + low_off)
        upper[i] = np.inf if hi is None else (float(hi) + high_off)
    return lower, upper


def integrate_q_vec(mu_arr: np.ndarray, sigma_arr: np.ndarray, lower: np.ndarray, upper: np.ndarray):
    """Vectorized settlement-preimage integration + per-row simplex renorm.

    mass[k,i] = Phi((upper[i]-mu_k)/sig_k) - Phi((lower[i]-mu_k)/sig_k), clip>=0, then q = q/q.sum
    per row.  Byte-equivalent (validated < 1e-9 at startup) to looping bin_probability_settlement
    per (draw, bin); rows whose mass sums to <= 0 are returned as all-NaN (caller drops them).
    """
    mu = np.asarray(mu_arr, dtype=float).reshape(-1, 1)
    sig = np.asarray(sigma_arr, dtype=float).reshape(-1, 1)
    zu = (upper.reshape(1, -1) - mu) / sig
    zl = (lower.reshape(1, -1) - mu) / sig
    mass = _norm.cdf(zu) - _norm.cdf(zl)
    np.clip(mass, 0.0, None, out=mass)
    totals = mass.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        q = mass / totals
    bad = (~np.isfinite(totals.ravel())) | (totals.ravel() <= 0.0)
    q[bad, :] = np.nan
    return q


def _validate_vectorized_integrator():
    """Assert integrate_q_vec == the scalar production primitive (both rules) before any build."""
    for rule in ("wmo_half_up", "oracle_truncate"):
        bins = [(None, 60.0)] + [(float(t), float(t)) for t in range(61, 80)] + [(80.0, None)]
        lower, upper = _edges_for(bins, rule)
        mus = np.array([62.3, 70.0, 75.9])
        sigs = np.array([0.7, 2.4, 1.1])
        vec = integrate_q_vec(mus, sigs, lower, upper)
        for r, (mu, sig) in enumerate(zip(mus, sigs)):
            probs = np.array(
                [bin_probability_settlement(mu, sig, lo, hi, rounding_rule=rule) for lo, hi in bins]
            )
            probs = np.clip(probs, 0.0, None)
            ref = probs / probs.sum()
            diff = float(np.max(np.abs(vec[r] - ref)))
            assert diff < 1e-9, f"vectorized integrator diverges ({rule}, row {r}): {diff:g}"
    print("vectorized integrator validated == scalar bin_probability_settlement (<1e-9)", flush=True)


def bin_of_value(settled_native: float, bins, rounding_rule: str) -> int:
    if rounding_rule in ("oracle_truncate", "floor"):
        label = math.floor(settled_native + 1e-9)
    elif rounding_rule == "ceil":
        label = math.ceil(settled_native - 1e-9)
    else:
        label = math.floor(settled_native + 0.5 + 1e-9)
    for i, (lo, hi) in enumerate(bins):
        if lo is None:
            if label <= int(hi):
                return i
        elif hi is None:
            if label >= int(lo):
                return i
        else:
            if int(lo) == label:
                return i
    return -1


def _raw_m2_for(city, model, td, by_date, settlements, unit):
    """Strictly-prior mean squared RAW residual (native) + n for one model at origin td."""
    sq = []
    for qtd in sorted(by_date.keys()):
        if qtd >= td:
            break
        if (city, qtd) not in settlements:
            continue
        qm = by_date.get(qtd, {})
        if model not in qm:
            continue
        qset_native = settlements[(city, qtd)][0]
        qunit = settlements[(city, qtd)][1]
        r = _c_to_native(float(qm[model]), qunit) - qset_native
        sq.append(r * r)
    return (float(np.mean(sq)), len(sq)) if sq else (None, 0)


def _live_member_ids(present, lat, lon, lead):
    """The LIVE fusion member set: select_models(...).used_models (anchor + globals + regionals).

    used_models already includes the ECMWF anchor iff it is present (anchor_present), matching the
    materializer's "anchor as a member" center. Raises loudly on a genuine select_models failure
    (never swallows -> a silent member-set drop would miscalibrate the cell); an empty set (no
    anchor + no eligible model present that day) returns [] and the caller skips the cell as thin.
    """
    sel = select_models(present_models=present, lat=lat, lon=lon, lead_days=int(lead))
    return [m for m in sel.used_models if m in present]


def process_metric_lead(metric, lead, fc_block, settlements, cities_cfg, cells, ctr):
    """Walk every (city, target_date); tally q_lcb reliability cells for this metric+lead."""
    rng_seed = ctr["rng_seed"]
    for city in sorted(fc_block.keys()):
        cfg = cities_cfg.get(city)
        if cfg is None:
            continue
        lat, lon = float(cfg.lat), float(cfg.lon)
        by_date = {d: dict(ms) for d, ms in fc_block[city].items()}
        dates = sorted(by_date.keys())
        for td in dates:
            if (city, td) not in settlements:
                ctr["skipped_no_settle"] += 1
                continue
            settled_native, unit, stype = settlements[(city, td)]
            rule = rounding_rule_for(stype)

            present = {m: float(v) for m, v in by_date.get(td, {}).items()}
            model_ids = _live_member_ids(present, lat, lon, lead)
            if len(model_ids) < 1:
                ctr["skipped_thin"] += 1
                continue

            # center weights from strictly-prior raw 2nd moments over the LIVE member set
            raw_m2 = {m: _raw_m2_for(city, m, td, by_date, settlements, unit) for m in model_ids}
            members_native = np.asarray([_c_to_native(present[m], unit) for m in model_ids], dtype=float)
            weights_map = raw_second_moment_weights(raw_m2, unit=unit)
            weights = np.asarray([weights_map.get(m, 0.0) for m in model_ids], dtype=float)
            if weights.sum() <= 0.0:
                ctr["skipped_thin"] += 1
                continue
            mu_star = float(weighted_huber_location(members_native, weights))

            # sigma = strictly-prior train-RMSE of the SAME (re-derived prior-of-prior) center
            prior_sq = []
            for ptd in dates:
                if ptd >= td:
                    break
                if (city, ptd) not in settlements:
                    continue
                pres_p = {m: float(v) for m, v in by_date.get(ptd, {}).items()}
                pmodels = _live_member_ids(pres_p, lat, lon, lead)
                if not pmodels:
                    continue
                pset_native, punit, _ = settlements[(city, ptd)]
                pmembers = np.asarray([_c_to_native(pres_p[m], punit) for m in pmodels], dtype=float)
                pm2 = {m: _raw_m2_for(city, m, ptd, by_date, settlements, unit) for m in pmodels}
                pw_map = raw_second_moment_weights(pm2, unit=unit)
                pw = np.asarray([pw_map.get(m, 0.0) for m in pmodels], dtype=float)
                if pw.sum() <= 0.0:
                    continue
                pmu = float(weighted_huber_location(pmembers, pw))
                prior_sq.append((pmu - pset_native) ** 2)
            if len(prior_sq) < MIN_PRIOR_FOR_RMSE:
                ctr["skipped_thin"] += 1
                continue
            sigma = float(math.sqrt(np.mean(prior_sq)))
            if not math.isfinite(sigma) or sigma <= 0.0:
                ctr["skipped_thin"] += 1
                continue

            center_se = _center_se_native(members_native)
            model_disp = _model_disp_native(members_native)

            bins = build_omega(members_native)
            settled_bin = bin_of_value(settled_native, bins, rule)
            if settled_bin < 0:
                ctr["skipped_thin"] += 1
                continue
            lower, upper = _edges_for(bins, rule)

            # point joint q (served distribution; modal = argmax mass)
            point = integrate_q_vec(np.array([mu_star]), np.array([max(sigma, _SIGMA_POSITIVE_FLOOR)]),
                                    lower, upper)[0]
            if not np.all(np.isfinite(point)):
                ctr["skipped_thin"] += 1
                continue
            modal_idx = int(np.argmax(point))

            # draws: mu_k ~ N(mu*, center_se); sigma_k floored at the realized sigma (band law)
            rng = np.random.default_rng(rng_seed)
            rng_seed += 1
            disp = min(0.25 * abs(model_disp), 0.25 * abs(sigma)) if sigma > 0 else 0.0
            mu_k = (np.full(N_DRAWS, mu_star) if center_se <= 0.0
                    else rng.normal(mu_star, center_se, N_DRAWS))
            if disp <= 0.0:
                sig_k = np.full(N_DRAWS, max(sigma, _SIGMA_POSITIVE_FLOOR))
            else:
                sig_k = np.maximum(rng.normal(sigma, disp, N_DRAWS), _SIGMA_POSITIVE_FLOOR)
            qmat = integrate_q_vec(mu_k, sig_k, lower, upper)
            good = np.isfinite(qmat).all(axis=1)
            if int(good.sum()) < int(0.5 * N_DRAWS):
                ctr["skipped_thin"] += 1
                continue
            q_lcb = np.quantile(qmat[good], ALPHA, axis=0)  # marginal alpha-quantile per YES bin
            q_lcb_no = np.quantile(1.0 - qmat[good], ALPHA, axis=0)

            ctr["n_predictions"] += 1
            for i in range(len(bins)):
                bin_position = "modal" if i == modal_idx else "nonmodal"
                yes_key = cell_key(
                    metric=metric,
                    lead_days=float(lead),
                    side="YES",
                    bin_position=bin_position,
                    q_lcb=float(q_lcb[i]),
                )
                cells[yes_key][0] += 1
                if i == settled_bin:
                    cells[yes_key][1] += 1

                no_key = cell_key(
                    metric=metric,
                    lead_days=float(lead),
                    side="NO",
                    bin_position=bin_position,
                    q_lcb=float(q_lcb_no[i]),
                )
                cells[no_key][0] += 1
                if i != settled_bin:
                    cells[no_key][1] += 1
    ctr["rng_seed"] = rng_seed


def main() -> int:
    _validate_vectorized_integrator()

    with open(FORECASTS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    fc = data["forecasts"]

    from src.config import cities_by_name
    cities_cfg = dict(cities_by_name)

    con = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True)
    settlements_by_metric = {m: load_settlements(con, m) for m in METRICS}
    con.close()

    cells = defaultdict(lambda: [0, 0])
    ctr = {"n_predictions": 0, "skipped_no_settle": 0, "skipped_thin": 0, "rng_seed": 0}

    for metric in METRICS:
        sett = settlements_by_metric[metric]
        for lead in LEADS:
            block = fc.get(metric, {}).get(str(lead))
            if not block:
                continue
            before = ctr["n_predictions"]
            process_metric_lead(metric, lead, block, sett, cities_cfg, cells, ctr)
            print(f"  {metric} lead={lead} ({lead_bucket(float(lead))}): "
                  f"+{ctr['n_predictions']-before} predictions", flush=True)

    out_cells = {k: {"n": int(n), "hit_rate": float(h) / float(n)}
                 for k, (n, h) in cells.items() if n > 0}
    artifact = {
        "meta": {
            "schema_version": 2,
            "built_at": "2026-06-18",
            "n_predictions": int(ctr["n_predictions"]),
            "n_cells": len(out_cells),
            "metrics": list(METRICS),
            "leads": list(LEADS),
            "center_method": "RAW_DIAGONAL_2ND_MOMENT over select_models().used_models + ECMWF "
            "anchor (raw_second_moment_weights + weighted_huber_location); sigma=strictly-prior "
            "center train-RMSE",
            "band": f"build_joint_q_band-equivalent: n_draws={N_DRAWS}, alpha={ALPHA}, "
            "draw_mu~N(mu*,center_se), draw_sigma floored, per-draw simplex integ (vectorized, "
            "validated ==scalar), marginal alpha-quantile",
            "bucket_grid": "QLCB_BUCKET_EDGES (refined uniform 0.05) imported from the live guard",
            "cell_key_schema": "metric|lead_bucket|side|bin_position|q_lcb_bucket",
            "side_semantics": "YES hit = settled in bin; NO hit = settled outside bin, with NO q_lcb computed as alpha-quantile of 1-q_bin draws",
            "source": "/tmp/multilead_forecasts.json (land-coord previous-runs corpus) + "
            "state/zeus-forecasts.db (immutable RO); strictly-prior rolling-origin training",
            "n_skipped_no_settle": int(ctr["skipped_no_settle"]),
            "n_skipped_thin": int(ctr["skipped_thin"]),
        },
        "cells": out_cells,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)

    total_n = sum(c["n"] for c in out_cells.values())
    print(f"WROTE {OUT_PATH}")
    print(f"n_predictions={ctr['n_predictions']} cells={len(out_cells)} total_n={total_n}")
    print(f"skipped_no_settle={ctr['skipped_no_settle']} skipped_thin={ctr['skipped_thin']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
