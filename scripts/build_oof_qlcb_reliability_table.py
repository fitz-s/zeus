# Created: 2026-06-18
# Last audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §"THE q_lcb RELIABILITY GUARD" + src/decision/qlcb_reliability_guard.py (the LIVE guard:
#   cell scheme, QLCB_BUCKET_EDGES, lead_bucket, cell_key). Operator task: build the OFFLINE
#   OOF reliability table that ACTIVATES the RAW q_lcb reliability guard. RAW no-de-bias law:
#   the center is the live RAW diagonal 1/E[r^2] 2nd-moment center (src/forecast/center.py:
#   raw_second_moment_weights + weighted_huber_location); q_lcb reproduces the production
#   build_joint_q_band draw loop (src/probability/joint_q_band.py) over the production
#   settlement-preimage integrator (src/calibration/emos.bin_probability_settlement) threaded
#   with each city's production rounding_rule (src/contracts/settlement_semantics).
#
# This script WRITES ONLY state/qlcb_oof_reliability.json (a generated artifact, NOT a *.db).
# It opens state/zeus-forecasts.db READ-ONLY immutable. It modifies NO src/ code.
"""Build the OOF q_lcb reliability table (rolling-origin, strictly-prior training only).

For each (city, target_date) lead-1 prediction in /tmp/unbiased_test_forecasts.json:
  1. Build the RAW diagonal-precision center over the present raw members using ONLY
     strictly-PRIOR (target_date - 1d and earlier) per-model residual history -> the same
     production center (raw_second_moment_weights + weighted_huber_location). sigma = the
     train-RMSE of that center over the strictly-prior window (the realized-floor width).
  2. Build a complete integer-degree MECE Omega around the member envelope, integrated under
     the city's production rounding_rule settlement preimage.
  3. Reproduce build_joint_q_band: 4000 parameter-posterior draws (mu ~ N(mu*, center_se),
     sigma floored), per-draw simplex bin integration, marginal alpha=0.05 quantile -> q_lcb
     per bin. The point joint q gives the modal (argmax-mass) bin.
  4. For each bin: cell = cell_key(metric, lead_days=1, bin_position, q_lcb[bin]); tally
     n += 1, hits += (settled_value in this bin). hit_rate = hits / n per cell.

Production-equivalence relied on (documented in the report): the full PredictiveDistribution /
OutcomeSpace / venue-family stack is NOT wired standalone; instead the SAME integrator, the
SAME center weights, the SAME rounding-rule preimage, and the SAME draw_mu/draw_sigma/marginal-
quantile algebra the live band uses are driven directly over a reconstructed complete Omega.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

# --- production primitives (imported, never re-implemented) -------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.calibration.emos import bin_probability_settlement  # settlement-preimage integrator
from src.forecast.center import raw_second_moment_weights, weighted_huber_location
from src.decision.qlcb_reliability_guard import (  # the LIVE guard cell scheme
    cell_key,
    lead_bucket,
    qlcb_bucket,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORECASTS_PATH = "/tmp/unbiased_test_forecasts.json"
DB_PATH = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_PATH = os.path.join(REPO, "state", "qlcb_oof_reliability.json")

# These forecasts are lead-1 daily-HIGH forecasts (forecast made 1 day before target).
METRIC = "high"
LEAD_DAYS = 1.0

# Band reproduction constants — byte-identical to src/probability/joint_q_band.py.
N_DRAWS = 4000
ALPHA = 0.05
_SIGMA_POSITIVE_FLOOR = 1e-6
# draw_sigma dispersion: 0.25 * model_dispersion capped at 0.25 * served (joint_q_band).
# Standalone we set model_dispersion = the strictly-prior member spread (weighted), matching
# sigma_authority.model_dispersion_sigma's _weighted_spread basis; the cap keeps it modest.

# Minimum strictly-prior training rows before a model carries a raw-2nd-moment signal.
# The center helper already shrinks thin models toward equal weight (n<MIN_TRAIN), so we keep
# ALL models with >=1 prior obs but pass their true (raw_m2, n) so the production EB shrink runs.
MIN_PRIOR_FOR_RMSE = 3  # need a few prior days before a center RMSE is meaningful

# Omega construction: integer bins from (floor(min_member)-PAD) to (ceil(max_member)+PAD),
# open shoulders at each end so the partition is complete over (-inf, +inf).
OMEGA_PAD = 12  # degrees of integer bins beyond the member envelope each side


def _c_to_native(value_c: float, unit: str) -> float:
    return value_c * 9.0 / 5.0 + 32.0 if unit == "F" else value_c


def _center_se_native(members_native: np.ndarray) -> float:
    """Center-parameter SE = spread / sqrt(n) (sigma_authority.center_parameter_se_sigma,
    the no-fused-sd branch: standard error of the member mean)."""
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    spread = float(np.std(arr, ddof=1))
    se = spread / math.sqrt(arr.size)
    return se if math.isfinite(se) and se > 0.0 else 0.0


def _model_disp_native(members_native: np.ndarray) -> float:
    """Member dispersion (weighted spread proxy) = sample std, native unit."""
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    s = float(np.std(arr, ddof=1))
    return s if math.isfinite(s) and s > 0.0 else 0.0


def load_settlements(con: sqlite3.Connection):
    """{(city, date): (settled_native, unit, source_type)} for metric=high, settled rows."""
    cur = con.cursor()
    rows = cur.execute(
        "SELECT city, target_date, settlement_value, unit, settlement_source_type "
        "FROM settlements WHERE temperature_metric=? AND settlement_value IS NOT NULL "
        "AND unit IS NOT NULL",
        (METRIC,),
    ).fetchall()
    out = {}
    for city, td, val, unit, stype in rows:
        out[(city, td)] = (float(val), unit, (stype or ""))
    return out


def rounding_rule_for(source_type: str) -> str:
    """Production rule (settlement_semantics.for_city): hko -> oracle_truncate, else wmo."""
    return "oracle_truncate" if str(source_type).lower() == "hko" else "wmo_half_up"


def build_omega(members_native: np.ndarray, rounding_rule: str):
    """A complete integer-degree MECE partition (list of (lo, hi) native, None=open shoulder).

    Interior bins are single-integer bins (lo==hi==t); the two ends are open shoulders so the
    partition covers (-inf, +inf). Integrated under bin_probability_settlement with the city
    rounding_rule, so the preimage (symmetric WMO vs asymmetric HK truncate) matches production.
    """
    lo_i = int(math.floor(float(np.min(members_native)))) - OMEGA_PAD
    hi_i = int(math.ceil(float(np.max(members_native)))) + OMEGA_PAD
    bins = []
    # open-low shoulder: (None, lo_i)
    bins.append((None, float(lo_i)))
    for t in range(lo_i + 1, hi_i):
        bins.append((float(t), float(t)))  # interior single-integer bin
    # open-high shoulder: (hi_i, None)
    bins.append((float(hi_i), None))
    return bins


def integrate_q(mu: float, sigma: float, bins, rounding_rule: str) -> np.ndarray:
    """One simplex row: integrate every bin over its settlement preimage, clip>=0, q/q.sum().

    Byte-equivalent to build_joint_q's single transform (clip then ONE normalization)."""
    probs = []
    for lo, hi in bins:
        p = bin_probability_settlement(mu, sigma, lo, hi, rounding_rule=rounding_rule)
        probs.append(p)
    q = np.clip(np.asarray(probs, dtype=float), 0.0, None)
    total = float(q.sum())
    if not math.isfinite(total) or total <= 0.0:
        return None
    return q / total


def bin_of_value(settled_native: float, bins, rounding_rule: str) -> int:
    """Index of the bin whose SETTLEMENT label the settled value rounds into.

    The settled value is the realized integer label; we find the interior bin (lo==hi==label)
    or the shoulder that contains it. Settlement rounding: wmo_half_up=floor(x+0.5),
    oracle_truncate/floor=floor(x)."""
    if rounding_rule in ("oracle_truncate", "floor"):
        label = math.floor(settled_native + 1e-9)
    elif rounding_rule == "ceil":
        label = math.ceil(settled_native - 1e-9)
    else:  # wmo_half_up
        label = math.floor(settled_native + 0.5 + 1e-9)
    for i, (lo, hi) in enumerate(bins):
        if lo is None:  # open-low shoulder: label <= hi
            if label <= int(hi):
                return i
        elif hi is None:  # open-high shoulder: label >= lo
            if label >= int(lo):
                return i
        else:
            if int(lo) == label:
                return i
    return -1


def main() -> int:
    with open(FORECASTS_PATH, "r", encoding="utf-8") as fh:
        forecasts = json.load(fh)["forecasts"]

    con = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True)
    settlements = load_settlements(con)
    con.close()

    # cell -> [n, hits]
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    n_predictions = 0
    n_skipped_no_settle = 0
    n_skipped_thin = 0

    rng_seed_base = 0

    for city in sorted(forecasts.keys()):
        by_date = forecasts[city]
        dates = sorted(by_date.keys())
        # Per-model strictly-prior residual history accumulator: model -> list of (sq_resid)
        # We rebuild residuals walk-forward: for each target_date, train = all PRIOR dates with
        # a settlement, residual = forecast(model, prior_date) - settled_native(prior_date).
        for td in dates:
            if (city, td) not in settlements:
                n_skipped_no_settle += 1
                continue
            settled_native, unit, stype = settlements[(city, td)]
            rule = rounding_rule_for(stype)

            members_today = by_date[td]  # {model: value_c}
            if not members_today:
                continue

            # --- strictly-prior raw 2nd-moment per model (target_date and later EXCLUDED) ----
            raw_m2_and_n: dict[str, tuple[float | None, int]] = {}
            for model, val_c in members_today.items():
                sq = []
                for ptd in dates:
                    if ptd >= td:
                        break  # strictly-prior only
                    if (city, ptd) not in settlements:
                        continue
                    pmembers = by_date.get(ptd, {})
                    if model not in pmembers:
                        continue
                    pset_native, punit, _ = settlements[(city, ptd)]
                    # forecast is degC; convert to the settlement native unit for the residual.
                    fc_native = _c_to_native(float(pmembers[model]), punit)
                    r = fc_native - pset_native
                    sq.append(r * r)
                if sq:
                    raw_m2_and_n[model] = (float(np.mean(sq)), len(sq))
                else:
                    raw_m2_and_n[model] = (None, 0)

            # Member values in the settlement native unit (forecast degC -> native).
            model_ids = list(members_today.keys())
            members_native = np.asarray(
                [_c_to_native(float(members_today[m]), unit) for m in model_ids], dtype=float
            )

            # --- RAW diagonal-precision center (production helper) -------------------------
            w_by_model = raw_second_moment_weights(
                {m: raw_m2_and_n[m] for m in model_ids}, unit=unit
            )
            weights = np.asarray([w_by_model[m] for m in model_ids], dtype=float)
            mu_star = weighted_huber_location(members_native, weights)

            # --- sigma = train-RMSE of the center over the strictly-prior window ----------
            # The realized-floor width: RMSE of (center_prior - settled_prior) across prior days
            # where the center is the SAME weighted-Huber center recomputed on prior members.
            prior_sq = []
            for ptd in dates:
                if ptd >= td:
                    break
                if (city, ptd) not in settlements:
                    continue
                pmembers = by_date.get(ptd, {})
                pmodels = [m for m in pmembers if m in raw_m2_and_n]
                if not pmodels:
                    continue
                pset_native, punit, _ = settlements[(city, ptd)]
                pmembers_native = np.asarray(
                    [_c_to_native(float(pmembers[m]), punit) for m in pmodels], dtype=float
                )
                # strictly-prior 2nd moments for the center AT ptd (re-derive prior-of-prior)
                pm2 = {}
                for m in pmodels:
                    psq = []
                    for qtd in dates:
                        if qtd >= ptd:
                            break
                        if (city, qtd) not in settlements:
                            continue
                        qm = by_date.get(qtd, {})
                        if m not in qm:
                            continue
                        qset_native, qunit, _ = settlements[(city, qtd)]
                        rr = _c_to_native(float(qm[m]), qunit) - qset_native
                        psq.append(rr * rr)
                    pm2[m] = (float(np.mean(psq)), len(psq)) if psq else (None, 0)
                pw = raw_second_moment_weights(pm2, unit=unit)
                pweights = np.asarray([pw[m] for m in pmodels], dtype=float)
                pmu = weighted_huber_location(pmembers_native, pweights)
                prior_sq.append((pmu - pset_native) ** 2)

            if len(prior_sq) < MIN_PRIOR_FOR_RMSE:
                n_skipped_thin += 1
                continue
            sigma = float(math.sqrt(np.mean(prior_sq)))
            if not math.isfinite(sigma) or sigma <= 0.0:
                n_skipped_thin += 1
                continue

            center_se = _center_se_native(members_native)
            model_disp = _model_disp_native(members_native)

            # --- build Omega + the band (reproduce build_joint_q_band) ---------------------
            bins = build_omega(members_native, rule)
            settled_bin = bin_of_value(settled_native, bins, rule)
            if settled_bin < 0:
                # settled value outside the padded Omega -> widen would be needed; skip rare case
                n_skipped_thin += 1
                continue

            # point joint q (the served distribution; modal = argmax mass).
            point_q = integrate_q(mu_star, sigma, bins, rule)
            if point_q is None:
                n_skipped_thin += 1
                continue
            modal_idx = int(np.argmax(point_q))

            # draw matrix: mu_k ~ N(mu*, center_se); sigma_k floored. Deterministic seed.
            rng = np.random.default_rng(rng_seed_base)
            rng_seed_base += 1
            disp = min(0.25 * abs(model_disp), 0.25 * abs(sigma)) if sigma > 0 else 0.0
            floor = max(_SIGMA_POSITIVE_FLOOR, 0.0)  # realized floor == sigma already (no sub)
            n_bins = len(bins)
            samples = np.empty((N_DRAWS, n_bins), dtype=float)
            kept = 0
            for k in range(N_DRAWS):
                mu_k = mu_star if center_se <= 0.0 else float(rng.normal(mu_star, center_se))
                if disp <= 0.0:
                    sigma_k = max(sigma, floor)
                else:
                    sigma_k = max(float(rng.normal(sigma, disp)), max(sigma * 0.0, floor))
                    # floor at the realized sigma so no draw goes sub-realized (band law).
                    sigma_k = max(sigma_k, _SIGMA_POSITIVE_FLOOR)
                q_k = integrate_q(mu_k, sigma_k, bins, rule)
                if q_k is None:
                    continue
                samples[kept, :] = q_k
                kept += 1
            if kept < int(0.5 * N_DRAWS):
                n_skipped_thin += 1
                continue
            samples = samples[:kept, :]
            q_lcb = np.quantile(samples, ALPHA, axis=0)  # marginal 5th percentile per bin

            # --- tally each bin into its reliability cell ----------------------------------
            n_predictions += 1
            for i, (lo, hi) in enumerate(bins):
                band_q = float(q_lcb[i])
                bin_position = "modal" if i == modal_idx else "nonmodal"
                key = cell_key(
                    metric=METRIC,
                    lead_days=LEAD_DAYS,
                    bin_position=bin_position,
                    q_lcb=band_q,
                )
                cells[key][0] += 1
                if i == settled_bin:
                    cells[key][1] += 1

    # --- write the artifact -------------------------------------------------------------
    out_cells = {}
    for key, (n, hits) in cells.items():
        if n <= 0:
            continue
        out_cells[key] = {"n": int(n), "hit_rate": float(hits) / float(n)}

    artifact = {
        "meta": {
            "built_at": "2026-06-18",
            "n_predictions": int(n_predictions),
            "n_cells": len(out_cells),
            "center_method": "RAW_DIAGONAL_2ND_MOMENT (raw_second_moment_weights + "
            "weighted_huber_location); sigma=strictly-prior center train-RMSE",
            "band": f"build_joint_q_band-equivalent: n_draws={N_DRAWS}, alpha={ALPHA}, "
            "draw_mu~N(mu*,center_se), draw_sigma floored, per-draw simplex integ, "
            "marginal alpha-quantile",
            "metric": METRIC,
            "lead_days": LEAD_DAYS,
            "source": "/tmp/unbiased_test_forecasts.json + state/zeus-forecasts.db "
            "(immutable RO); strictly-prior rolling-origin training",
            "n_skipped_no_settle": int(n_skipped_no_settle),
            "n_skipped_thin": int(n_skipped_thin),
        },
        "cells": out_cells,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)

    total_n = sum(c["n"] for c in out_cells.values())
    print(f"WROTE {OUT_PATH}")
    print(f"n_predictions={n_predictions} cells={len(out_cells)} total_n={total_n}")
    print(f"skipped_no_settle={n_skipped_no_settle} skipped_thin={n_skipped_thin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
