#!/usr/bin/env python3
# Created: 2026-07-28
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. An OOS walk-forward
#   bakeoff (M0..M4, calib_curves/bakeoff.py) selected M2 -- per-(unit_family, metric) k(tau) times
#   per-city variance shrinkage -- as the best legal form for the CURRENT-EVIDENCE (Day0) shape:
#   no city bias term, no market-price anchor, no historical floor. Precedent pattern:
#   scripts/fit_sigma_scale.py (the HISTORICAL-path sibling artifact) -- this script is the new
#   artifact's ONLY writer; the materializer reads it fail-soft.
"""Walk-forward fit of lead-time-indexed sigma calibration for the CURRENT-EVIDENCE path.

MODEL
  Per (unit_family in {C,F}, metric in {high,low}):
    z = settled_c - mu        (mu = bayes_precision_fusion.anchor_value_c, RAW, never refit)
    sig = bayes_precision_fusion.predictive_sigma_c
    tau = lead_target_h = hours from computed_at to target_date+1day 00:00 UTC
    k(tau) = std(z, ddof=1) / sqrt(mean(sig^2))     spread-skill scale, per tau bucket:
      [0,6),[6,12),[12,24),[24,36),[36,48),[48,72),[72,inf)
    This is the SAME estimator as calib_curves/fit_inputs.py::table3_k_fit (verified upstream
    against this exact live DB -- reproduces the cited evidence-basis ranges to 3 sig figs: C/high
    ~1.07-1.18 rising with tau, F/high ~0.80-0.97 shrinking, C/low and F/low ~1 with wide CIs on
    small n). The naive per-observation ratio sqrt(mean((z/sig)^2)) (bakeoff.py's fit_k_by_tau) was
    tried first and REJECTED: it is dominated by a small number of near-zero predictive-sigma rows
    (~1.5% of C/high rows have sig<0.3) and inflates global k to ~1.7 with no tau trend, which does
    not match the verified evidence. std(z)/rms(sig) is the classical ensemble spread-skill ratio:
    robust to per-row sigma heterogeneity because it normalizes the AGGREGATE spread rather than
    each observation individually.
    A bucket with n < MIN_BUCKET_N is UNFITTED and inherits the family/metric-global pooled k
    (computed the same way, pooled over ALL tau in the group -- train-only, never touches
    validation rows). A whole group with n < MIN_GROUP_N is REFUSED: k=1.0 everywhere,
    fitted=False (fail-closed, same law as fit_sigma_scale.py's MIN_CELLS refusal).

  Per-city variance correction (cities with n >= MIN_CITY_N pooled across tau within the group),
  same estimator applied to the k(tau)-scaled sigma:
    c_raw = std(z, ddof=1) / sqrt(mean((k(tau)*sig)^2))
    c_shrunk^2 = (n_c * c_raw^2 + CITY_SHRINKAGE_N0) / (n_c + CITY_SHRINKAGE_N0)
  This shrinks c TOWARD 1 (not toward 0): n_c=0 -> c_shrunk=1 exactly; n_c -> inf -> c_shrunk -> c_raw.

  Served (by the materializer): k_eff = k(tau) * c_shrunk(city). w and floor_steps are NOT part of
  this artifact -- the consumer holds them at exactly 0.0 (k-only calibration for this path).

DATA
  forecast_posteriors (mode=ro) joined to settlements on (city, target_date, temperature_metric).
  mu/sig read from provenance_json '$.bayes_precision_fusion.anchor_value_c' /
  '$.bayes_precision_fusion.predictive_sigma_c'. Settlement F-unit rows convert to Celsius via
  (v-32)*5/9 before computing z. Dedup keeps the LAST posterior per
  (city, target_date, temperature_metric, computed_at floored to the hour) -- mirrors the reference
  extraction in calib_curves/fit_inputs.py::dedup. Rows with lead_target_h < 0 (a retroactive
  posterior recorded after the target date already ended -- no trading-lead meaning) are dropped.

READ-ONLY over --fcst (opened ?mode=ro). Writes ONLY the path given by --out; there is no default
under state/ -- the operator decides if/when to place the artifact where the materializer reads it.

--validate CUTOFF: fits on target_date < CUTOFF, validates on target_date >= CUTOFF, and prints the
OOS mean log-likelihood ladder (k=1 baseline vs fitted k(tau)*c_shrunk) and coverage@68.3 per
(unit_family, metric) group. This is a read-only diagnostic mode; it does not write --out.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
SINCE_DEFAULT = "2026-07-11"  # live current-evidence era start (matches calib_curves reference)

TAU_EDGES = [0.0, 6.0, 12.0, 24.0, 36.0, 48.0, 72.0, math.inf]
TAU_LABELS = ["[0,6)", "[6,12)", "[12,24)", "[24,36)", "[36,48)", "[48,72)", "[72,inf)"]
GROUPS = [("C", "high"), ("C", "low"), ("F", "high"), ("F", "low")]

MIN_BUCKET_N = 60
MIN_GROUP_N = 60
MIN_CITY_N = 30
CITY_SHRINKAGE_N0 = 100

_POST_QUERY = """
    SELECT city, target_date, temperature_metric, computed_at,
           json_extract(provenance_json,'$.bayes_precision_fusion.anchor_value_c') AS mu,
           json_extract(provenance_json,'$.bayes_precision_fusion.predictive_sigma_c') AS sig
    FROM forecast_posteriors
    WHERE computed_at >= ?
      AND json_extract(provenance_json,'$.bayes_precision_fusion.anchor_value_c') IS NOT NULL
      AND json_extract(provenance_json,'$.bayes_precision_fusion.predictive_sigma_c') IS NOT NULL
"""
_SETT_QUERY = """
    SELECT city, target_date, temperature_metric, settlement_value, unit
    FROM settlements
    WHERE settlement_value IS NOT NULL AND unit IS NOT NULL
"""


def tau_bucket(lead_h: "pd.Series") -> "pd.Series":
    return pd.cut(lead_h, bins=TAU_EDGES, labels=TAU_LABELS, right=False)


def load(fcst_path: str, since: str) -> tuple["pd.DataFrame", "pd.DataFrame"]:
    conn = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        post = pd.read_sql_query(_POST_QUERY, conn, params=(since,))
        sett = pd.read_sql_query(_SETT_QUERY, conn)
    finally:
        conn.close()
    return post, sett


def build_frame(post: "pd.DataFrame", sett: "pd.DataFrame") -> "pd.DataFrame":
    post = post.copy()
    post["mu"] = post["mu"].astype(float)
    post["sig"] = post["sig"].astype(float)
    post["computed_at_dt"] = pd.to_datetime(post["computed_at"], utc=True)
    post["hour_key"] = post["computed_at_dt"].dt.floor("h")

    df = post.merge(sett, on=["city", "target_date", "temperature_metric"], how="inner")

    target_end = pd.to_datetime(df["target_date"], utc=True) + pd.Timedelta(days=1)
    df["lead_target_h"] = (target_end - df["computed_at_dt"]).dt.total_seconds() / 3600.0

    is_f = df["unit"] == "F"
    df["settled_c"] = np.where(is_f, (df["settlement_value"] - 32.0) * 5.0 / 9.0, df["settlement_value"])
    df["z"] = df["settled_c"] - df["mu"]
    df["unit_family"] = df["unit"]
    return df


def dedup(df: "pd.DataFrame") -> "pd.DataFrame":
    """Keep only the LAST posterior per (city, target_date, temperature_metric, hour_key)."""
    return df.sort_values("computed_at_dt").drop_duplicates(
        subset=["city", "target_date", "temperature_metric", "hour_key"], keep="last"
    )


def prep(fcst_path: str, since: str) -> tuple["pd.DataFrame", dict]:
    post, sett = load(fcst_path, since)
    df = build_frame(post, sett)
    d = dedup(df)
    n_before = len(d)
    d = d[d["lead_target_h"] >= 0.0].copy()
    n_dropped_negative_lead = n_before - len(d)
    d["taut"] = tau_bucket(d["lead_target_h"])
    stats = dict(
        n_posteriors_read=len(post),
        n_settlements_read=len(sett),
        n_joined_raw=len(df),
        n_joined_dedup=n_before,
        n_dropped_negative_lead=n_dropped_negative_lead,
        n_final=len(d),
    )
    return d, stats


def _spread_skill_k(z: "np.ndarray", sig: "np.ndarray") -> float:
    """std(z, ddof=1) / sqrt(mean(sig^2)) -- verified-upstream estimator (see module docstring);
    matches calib_curves/fit_inputs.py::table3_k_fit, not bakeoff.py's per-observation ratio."""
    return float(np.std(z, ddof=1) / np.sqrt(np.mean(np.asarray(sig, dtype=float) ** 2)))


def fit_k_by_tau(z: "pd.Series", sig: "pd.Series", taut: "pd.Series") -> tuple[dict, dict, float, int]:
    """k(tau) = spread-skill ratio per tau bucket; n<MIN_BUCKET_N inherits the group-global pooled k
    (which is itself None/inert if the WHOLE group is below MIN_GROUP_N)."""
    n_group = len(z)
    global_k = _spread_skill_k(z.values, sig.values) if n_group > 1 else None
    tmp = pd.DataFrame({"taut": taut, "z": z.values, "sig": sig.values})
    buckets: dict = {}
    for label in TAU_LABELS:
        g = tmp[tmp["taut"] == label]
        n = len(g)
        if n >= MIN_BUCKET_N:
            buckets[label] = {"k": _spread_skill_k(g["z"].values, g["sig"].values), "n": int(n), "fitted": True}
        else:
            buckets[label] = {"k": global_k, "n": int(n), "fitted": False}
    return buckets, {}, global_k, n_group


def fit_city_shrinkage(train: "pd.DataFrame", k_by_bucket: dict) -> dict:
    """c_raw = spread-skill ratio of z against the k(tau)-scaled sigma, per city (n>=MIN_CITY_N,
    pooled over tau), shrunk toward 1 via c_shrunk^2 = (n_c*c_raw^2 + N0) / (n_c + N0)."""
    k_row = train["taut"].map(lambda lab: (k_by_bucket.get(lab) or {}).get("k")).astype(float)
    valid = k_row.notna() & (k_row > 0.0)
    scaled_sig = k_row[valid] * train["sig"][valid]
    tmp = pd.DataFrame({"city": train["city"][valid], "z": train["z"][valid], "scaled_sig": scaled_sig})
    cities: dict = {}
    for city, g in tmp.groupby("city"):
        n_c = len(g)
        if n_c < MIN_CITY_N:
            continue
        c_raw = _spread_skill_k(g["z"].values, g["scaled_sig"].values)
        c_raw2 = c_raw ** 2
        c_shrunk2 = (n_c * c_raw2 + CITY_SHRINKAGE_N0) / (n_c + CITY_SHRINKAGE_N0)
        cities[str(city)] = {
            "c_raw": round(c_raw, 6),
            "c_shrunk": round(math.sqrt(c_shrunk2), 6),
            "n": int(n_c),
        }
    return cities


def fit_group(train: "pd.DataFrame") -> dict:
    n_group = len(train)
    if n_group < MIN_GROUP_N:
        return {
            "fitted": False,
            "global_k": 1.0,
            "n": int(n_group),
            "refusal_reason": f"INSUFFICIENT_N:{n_group}<{MIN_GROUP_N}",
            "buckets": {},
            "cities": {},
        }
    buckets, _counts, global_k, _n = fit_k_by_tau(train["z"], train["sig"], train["taut"])
    cities = fit_city_shrinkage(train, buckets)
    return {
        "fitted": True,
        "global_k": round(float(global_k), 6),
        "n": int(n_group),
        "buckets": {lab: {**b, "k": round(float(b["k"]), 6) if b["k"] is not None else None} for lab, b in buckets.items()},
        "cities": cities,
    }


def _k_eff_row(group: dict, taut: "pd.Series", city: "pd.Series") -> "pd.Series":
    bucket_k = taut.map(lambda lab: (group["buckets"].get(lab) or {}).get("k") if group.get("buckets") else None)
    bucket_k = bucket_k.fillna(group.get("global_k", 1.0)).astype(float)
    cities = group.get("cities") or {}
    c_shrunk = city.map(lambda c: (cities.get(str(c)) or {}).get("c_shrunk", 1.0)).astype(float)
    return bucket_k * c_shrunk


def normal_logpdf(z, sigma):
    return -0.5 * np.log(2 * np.pi) - np.log(sigma) - (z ** 2) / (2 * sigma ** 2)


def validate(d: "pd.DataFrame", cutoff: str) -> None:
    train_mask = d["target_date"] < cutoff
    val_mask = d["target_date"] >= cutoff
    print(f"[sigma-tau] validate cutoff={cutoff}  n_train={int(train_mask.sum())}  n_val={int(val_mask.sum())}")
    for unit, metric in GROUPS:
        train = d[train_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        val = d[val_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        if len(train) < MIN_GROUP_N or len(val) < 10:
            print(f"  {unit}/{metric}: SKIP (n_train={len(train)}, n_val={len(val)})")
            continue
        group = fit_group(train)
        k_eff = _k_eff_row(group, val["taut"], val["city"])
        ll_base = normal_logpdf(val["z"].values, val["sig"].values)
        ll_fit = normal_logpdf(val["z"].values, (k_eff.values * val["sig"].values))
        cov_base = float((val["z"].abs() <= val["sig"]).mean())
        cov_fit = float((val["z"].abs() <= (k_eff * val["sig"])).mean())
        print(
            f"  {unit}/{metric}: n_train={len(train)} n_val={len(val)} "
            f"global_k={group.get('global_k')} "
            f"oos_mean_loglik k=1:{ll_base.mean():.5f} fitted:{ll_fit.mean():.5f} "
            f"delta:{(ll_fit.mean() - ll_base.mean()):+.5f}  "
            f"coverage@68.3 k=1:{cov_base:.4f} fitted:{cov_fit:.4f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlements), opened ?mode=ro.")
    ap.add_argument("--since", default=SINCE_DEFAULT, help="ISO date/datetime floor on computed_at (live current-evidence era start).")
    ap.add_argument("--out", default=None, help="output sigma_tau_calibration.json path (REQUIRED unless --validate).")
    ap.add_argument("--validate", default=None, metavar="CUTOFF", help="read-only OOS diagnostic: fit on target_date<CUTOFF, validate on target_date>=CUTOFF. Does not write --out.")
    args = ap.parse_args()

    if args.out is None and args.validate is None:
        ap.error("must supply --out (to write the artifact) or --validate CUTOFF (to report OOS numbers only)")

    d, stats = prep(args.fcst, args.since)
    print(f"[sigma-tau] {stats}")

    if args.validate is not None:
        validate(d, args.validate)
        if args.out is None:
            return 0

    families: dict = {}
    for unit, metric in GROUPS:
        sub = d[(d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        families.setdefault(unit, {})[metric] = fit_group(sub)

    source_query_hash = hashlib.sha256((_POST_QUERY + _SETT_QUERY).encode("utf-8")).hexdigest()[:16]
    data_window = f"since={args.since}"
    if len(d):
        data_window = f"{d['target_date'].min()}..{d['target_date'].max()} (since={args.since})"
    artifact = {
        "_meta": {
            "authority": "sigma_tau_calibration_v1_mle",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "method": "tau_bucketed_normal_scale_mle_plus_city_variance_shrinkage",
            "tau_buckets": {lab: [lo if math.isfinite(lo) else None, hi if math.isfinite(hi) else None]
                            for lab, lo, hi in zip(TAU_LABELS, TAU_EDGES, TAU_EDGES[1:])},
            "min_bucket_n": MIN_BUCKET_N,
            "min_group_n": MIN_GROUP_N,
            "min_city_n": MIN_CITY_N,
            "city_shrinkage_n0": CITY_SHRINKAGE_N0,
            "data_window": data_window,
            "row_counts": stats,
            "source_query_hash": source_query_hash,
        },
        "families": families,
    }

    tmp_path = args.out + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True, default=str)
    os.replace(tmp_path, args.out)
    print(f"[sigma-tau] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
