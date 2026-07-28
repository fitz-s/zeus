#!/usr/bin/env python3
# Created: 2026-07-28
# Last reused or audited: 2026-07-28 (design-review corrections applied same day)
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. An OOS walk-forward
#   bakeoff (calib_curves/bakeoff.py) selected per-(unit_family, metric) FITTED k(tau) times
#   per-city variance shrinkage as the best legal sigma correction for the CURRENT-EVIDENCE (Day0)
#   shape. Precedent pattern: scripts/fit_sigma_scale.py (the HISTORICAL-path sibling artifact) --
#   this script is the new artifact's ONLY writer; the materializer reads it fail-soft.
#
# 2026-07-28 DESIGN-REVIEW CORRECTIONS (applied same day as the first implementation, before any
# artifact was ever placed under state/ -- these supersede that first cut, not a later revision):
#   1. TAU CLOCK: tau is now hours from the posterior's ISSUE clock (the top-level
#      forecast_posteriors.source_cycle_time column -- 100% populated, unlike
#      $.bayes_precision_fusion.current_evidence_shape.source_cycle_time at ~96.5%) to end of
#      target_date UTC, NOT from computed_at (decision time). Live evidence: Hong Kong 2026-07-20
#      HIGH has 247 distinct computed_at values (many recomputes/hour) collapsing to only 4
#      distinct source_cycle_time values on the SAME day -- a computed_at-anchored tau shrinks on
#      every wall-clock recompute with NO new provider issue, a look-ahead-adjacent defect. The
#      materializer's serving-side lookup uses the SAME clock (request.source_cycle_time). The old
#      computed_at-anchored tau is kept ONLY as a --validate comparison column, never for fitting.
#   2. EVENT WEIGHTING: multiple posteriors for one (city, target_date, metric) settlement event
#      are correlated (they share the SAME outcome). Each row is weighted 1/n_e where n_e is that
#      event's row count within the (unit_family, metric) group being fit (so the event's rows sum
#      to weight 1 across the WHOLE group, not renormalized per bucket -- an event whose lifetime
#      spans two tau buckets contributes a fraction of its unit weight to each). MIN_BUCKET_N /
#      MIN_GROUP_N / MIN_CITY_N are now compared against the number of UNIQUE EVENTS (nunique
#      (city, target_date) pairs) touching the cell, not the raw row count; both are reported.
#   3. LIKELIHOOD: the PRIMARY estimator is now interval-censored MLE over the settled integer's
#      actual bin [v-0.5, v+0.5) in native settlement units (F bins converted to Celsius edges,
#      matching the settlement value's own unit), maximizing the (event-)weighted sum of
#      log[Phi((U-mu)/(k*sig)) - Phi((L-mu)/(k*sig))] -- this fits the integer-bin probability
#      directly (the thing actually traded), not a Normal density on the point residual. The
#      earlier closed-form spread-skill ratio (std(z,ddof=1)/sqrt(mean(sig^2)), event-weighted) is
#      retained as a REPORTED cross-check column (k_normal_crosscheck) but is not served.
#   4. DATA FENCE: current_evidence_shape's within/between/delta COMPONENT fields were rolled out
#      in two stages and are only 100% populated for computed_at >= 2026-07-15T22:32:31Z (86.6%
#      before). This fitter reads ONLY anchor_value_c/predictive_sigma_c (always populated, not a
#      component field) so no fence is applied to the training query; the boundary is still
#      recorded in _meta for audit (components_fence_applied=False + the reference timestamp) so a
#      future reader who adds a component-field read here is forced to confront the boundary
#      rather than silently training across it.
"""Walk-forward fit of lead-time-indexed sigma calibration for the CURRENT-EVIDENCE path.

MODEL
  Per (unit_family in {C,F}, metric in {high,low}):
    tau = lead_issue_h = hours from source_cycle_time to target_date+1day 00:00 UTC (the PRIMARY,
      served clock); lead_decision_h = the same measured from computed_at is a --validate-only
      comparison column.
    Bucketed on tau: [0,6),[6,12),[12,24),[24,36),[36,48),[48,72),[72,inf).
    Per row: mu = bayes_precision_fusion.anchor_value_c (RAW, never refit), sig =
      bayes_precision_fusion.predictive_sigma_c, [L,U) = the settled integer's native-unit bin
      [v-0.5, v+0.5), converted to Celsius edges for F-unit settlements.
    event_weight = 1 / (row count of this (city, target_date) pair within the group).
    k(tau) = argmax_k sum(event_weight * log[Phi((U-mu)/(k*sig)) - Phi((L-mu)/(k*sig))]), a 1-D
      bounded MLE (scipy.optimize.minimize_scalar). A bucket with n_events < MIN_BUCKET_N is
      UNFITTED and inherits the group-global pooled k (same estimator, all tau pooled, train-only).
      A whole group with n_events < MIN_GROUP_N is REFUSED: k=1.0 everywhere, fitted=False.
    Per-city variance correction (cities with n_events >= MIN_CITY_N, pooled across tau within the
    group): c_raw = argmax_c of the SAME interval-censored likelihood with sigma = k(tau)*c*sig
    (k(tau) already fixed per-row), then shrunk c_shrunk^2 = (n_e*c_raw^2 + CITY_SHRINKAGE_N0) /
    (n_e + CITY_SHRINKAGE_N0), n_e = the city's unique-event count -- shrinks TOWARD 1.
  Served (by the materializer): k_eff = k(tau) * c_shrunk(city). w and floor_steps are NOT part of
  this artifact -- the consumer holds them at exactly 0.0 (k-only calibration for this path).

DATA
  forecast_posteriors (mode=ro) joined to settlements on (city, target_date, temperature_metric).
  mu/sig read from provenance_json '$.bayes_precision_fusion.anchor_value_c' /
  '$.bayes_precision_fusion.predictive_sigma_c'; source_cycle_time from the top-level DB column.
  Settlement F-unit rows convert to Celsius via (v-32)*5/9 before computing z/bin edges. Dedup
  keeps the LAST posterior per (city, target_date, temperature_metric, computed_at floored to the
  hour) -- mirrors calib_curves/fit_inputs.py::dedup. Rows with lead_issue_h < 0 (source_cycle_time
  after the target date already ended -- no trading-lead meaning) are dropped.

READ-ONLY over --fcst (opened ?mode=ro). Writes ONLY the path given by --out; there is no default
under state/ -- the operator decides if/when to place the artifact where the materializer reads it.

--validate CUTOFF: fits on target_date < CUTOFF, validates on target_date >= CUTOFF, and prints the
event-weighted OOS mean log-likelihood ladder (k=1 baseline vs fitted, under BOTH the primary
interval-censored likelihood and the Normal-density cross-check) and coverage@68.3, per
(unit_family, metric) group. Runs once bucketed on the issue clock (PRIMARY) and once on the
decision clock (COMPARISON ONLY) so the clock choice's effect on OOS numbers is visible. Read-only;
does not write --out.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
SINCE_DEFAULT = "2026-07-11"  # live current-evidence era start (matches calib_curves reference)
COMPONENTS_FENCE_TS = "2026-07-15T22:32:31+00:00"  # current_evidence_shape within/between/delta
                                                     # fields are 100% populated only from here on;
                                                     # NOT applied as a filter -- this fitter never
                                                     # reads those component fields (see module
                                                     # docstring correction 4). Recorded for audit.

TAU_EDGES = [0.0, 6.0, 12.0, 24.0, 36.0, 48.0, 72.0, math.inf]
TAU_LABELS = ["[0,6)", "[6,12)", "[12,24)", "[24,36)", "[36,48)", "[48,72)", "[72,inf)"]
GROUPS = [("C", "high"), ("C", "low"), ("F", "high"), ("F", "low")]

MIN_BUCKET_N = 60   # in UNIQUE EVENTS (nunique (city,target_date)), not rows
MIN_GROUP_N = 60     # in UNIQUE EVENTS
MIN_CITY_N = 30       # in UNIQUE EVENTS
CITY_SHRINKAGE_N0 = 100
K_BOUNDS = (0.05, 8.0)
C_BOUNDS = (0.05, 8.0)
_LOG_EPS = 1e-12

_POST_QUERY = """
    SELECT city, target_date, temperature_metric, computed_at, source_cycle_time,
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
    post["source_cycle_time_dt"] = pd.to_datetime(post["source_cycle_time"], utc=True)
    post["hour_key"] = post["computed_at_dt"].dt.floor("h")

    df = post.merge(sett, on=["city", "target_date", "temperature_metric"], how="inner")

    target_end = pd.to_datetime(df["target_date"], utc=True) + pd.Timedelta(days=1)
    # PRIMARY (served) clock: issue time -> end of target_date.
    df["lead_issue_h"] = (target_end - df["source_cycle_time_dt"]).dt.total_seconds() / 3600.0
    # Comparison-only clock (never used for fitting): decision time -> end of target_date.
    df["lead_decision_h"] = (target_end - df["computed_at_dt"]).dt.total_seconds() / 3600.0

    is_f = df["unit"] == "F"
    df["settled_c"] = np.where(is_f, (df["settlement_value"] - 32.0) * 5.0 / 9.0, df["settlement_value"])
    df["z"] = df["settled_c"] - df["mu"]
    # Interval-censored bin edges: the settled INTEGER bin in native units, converted to Celsius.
    lower_native = df["settlement_value"] - 0.5
    upper_native = df["settlement_value"] + 0.5
    df["bin_lower_c"] = np.where(is_f, (lower_native - 32.0) * 5.0 / 9.0, lower_native)
    df["bin_upper_c"] = np.where(is_f, (upper_native - 32.0) * 5.0 / 9.0, upper_native)
    df["l"] = df["bin_lower_c"] - df["mu"]
    df["u"] = df["bin_upper_c"] - df["mu"]
    df["unit_family"] = df["unit"]
    df["event_key"] = df["city"].astype(str) + "|" + df["target_date"].astype(str)
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
    d = d[d["lead_issue_h"] >= 0.0].copy()
    n_dropped_negative_lead = n_before - len(d)
    d["taut"] = tau_bucket(d["lead_issue_h"])
    d["taut_decision"] = tau_bucket(d["lead_decision_h"])
    stats = dict(
        n_posteriors_read=len(post),
        n_settlements_read=len(sett),
        n_joined_raw=len(df),
        n_joined_dedup=n_before,
        n_dropped_negative_lead=n_dropped_negative_lead,
        n_final=len(d),
    )
    return d, stats


def _add_event_weights(sub: "pd.DataFrame") -> "pd.DataFrame":
    """event_weight = 1/(row count of this event WITHIN sub) -- each unique (city,target_date)
    event's rows sum to weight exactly 1 across the population passed in (a group, not re-derived
    per bucket -- an event spanning two buckets contributes a fraction of its unit weight to each,
    matching a single fixed design weight per event)."""
    sub = sub.copy()
    counts = sub.groupby("event_key")["event_key"].transform("count")
    sub["event_weight"] = 1.0 / counts
    return sub


def _weighted_mean(x: "np.ndarray", w: "np.ndarray") -> float:
    return float(np.sum(w * x) / np.sum(w))


def _weighted_var(x: "np.ndarray", w: "np.ndarray") -> float:
    """Reliability-weights unbiased variance (reduces to the standard ddof=1 formula for equal
    weights); NaN if the effective sample size is <=1."""
    mean = _weighted_mean(x, w)
    sw = float(np.sum(w))
    sw2 = float(np.sum(w ** 2))
    denom = sw - sw2 / sw if sw > 0 else 0.0
    if denom <= 0:
        return float("nan")
    return float(np.sum(w * (x - mean) ** 2) / denom)


def weighted_spread_skill_k(z: "np.ndarray", sig: "np.ndarray", w: "np.ndarray") -> float:
    """Event-weighted std(z)/rms(sig) -- the CROSS-CHECK estimator (was the primary before the
    2026-07-28 design-review correction; kept as k_normal_crosscheck)."""
    var_z = _weighted_var(z, w)
    mean_sig2 = _weighted_mean(sig ** 2, w)
    if not (math.isfinite(var_z) and var_z >= 0.0 and math.isfinite(mean_sig2) and mean_sig2 > 0.0):
        return float("nan")
    return float(math.sqrt(var_z / mean_sig2))


def _interval_censored_negloglik(scale: float, lo: "np.ndarray", hi: "np.ndarray", sigma_base: "np.ndarray", w: "np.ndarray") -> float:
    sigma = scale * sigma_base
    p = norm.cdf(hi / sigma) - norm.cdf(lo / sigma)
    p = np.clip(p, _LOG_EPS, 1.0)
    return float(-np.sum(w * np.log(p)))


def fit_interval_censored_scale(
    lo: "np.ndarray", hi: "np.ndarray", sigma_base: "np.ndarray", w: "np.ndarray", bounds: tuple[float, float]
) -> float:
    """argmax_scale of the (weighted) interval-censored bin likelihood -- the PRIMARY estimator.
    A 1-D bounded MLE (no closed form); cheap even at corpus scale (single scalar optimization)."""
    res = minimize_scalar(
        _interval_censored_negloglik, bounds=bounds, method="bounded", args=(lo, hi, sigma_base, w)
    )
    return float(res.x)


def _interval_censored_loglik_per_row(lo, hi, sigma):
    p = norm.cdf(hi / sigma) - norm.cdf(lo / sigma)
    return np.log(np.clip(p, _LOG_EPS, 1.0))


def fit_k_by_tau(sub: "pd.DataFrame") -> tuple[dict, float, float, int]:
    """PRIMARY k(tau) = interval-censored MLE scale per tau bucket (event-weighted); a bucket with
    n_events < MIN_BUCKET_N is UNFITTED and inherits the group-global pooled k. Also reports the
    Normal-density spread-skill cross-check k_normal_crosscheck at both group and bucket level."""
    w = sub["event_weight"].values
    global_k = fit_interval_censored_scale(sub["l"].values, sub["u"].values, sub["sig"].values, w, K_BOUNDS)
    global_k_normal = weighted_spread_skill_k(sub["z"].values, sub["sig"].values, w)
    n_events_group = sub["event_key"].nunique()
    buckets: dict = {}
    for label in TAU_LABELS:
        g = sub[sub["taut"] == label]
        n_rows = len(g)
        n_events = g["event_key"].nunique()
        if n_events >= MIN_BUCKET_N:
            k = fit_interval_censored_scale(g["l"].values, g["u"].values, g["sig"].values, g["event_weight"].values, K_BOUNDS)
            k_normal = weighted_spread_skill_k(g["z"].values, g["sig"].values, g["event_weight"].values)
            buckets[label] = {"k": k, "k_normal_crosscheck": k_normal, "n": int(n_rows), "n_events": int(n_events), "fitted": True}
        else:
            buckets[label] = {"k": global_k, "k_normal_crosscheck": global_k_normal, "n": int(n_rows), "n_events": int(n_events), "fitted": False}
    return buckets, global_k, global_k_normal, n_events_group


def fit_city_shrinkage(sub: "pd.DataFrame", k_by_bucket: dict) -> dict:
    """c_raw = interval-censored MLE scale on top of the (already-fitted) k(tau)*sig, per city
    (n_events >= MIN_CITY_N, pooled over tau), shrunk toward 1 via
    c_shrunk^2 = (n_e*c_raw^2 + N0) / (n_e + N0)."""
    k_row = sub["taut"].map(lambda lab: (k_by_bucket.get(lab) or {}).get("k")).astype(float)
    valid = k_row.notna() & (k_row > 0.0)
    tmp = sub[valid].copy()
    tmp["k_row"] = k_row[valid]
    tmp["sigma_base"] = tmp["k_row"] * tmp["sig"]
    cities: dict = {}
    for city, g in tmp.groupby("city"):
        n_events = g["event_key"].nunique()
        if n_events < MIN_CITY_N:
            continue
        w = g["event_weight"].values
        c_raw = fit_interval_censored_scale(g["l"].values, g["u"].values, g["sigma_base"].values, w, C_BOUNDS)
        c_raw_normal = weighted_spread_skill_k(g["z"].values, g["sigma_base"].values, w)
        c_raw2 = c_raw ** 2
        c_shrunk2 = (n_events * c_raw2 + CITY_SHRINKAGE_N0) / (n_events + CITY_SHRINKAGE_N0)
        cities[str(city)] = {
            "c_raw": round(c_raw, 6),
            "c_raw_normal_crosscheck": round(c_raw_normal, 6) if math.isfinite(c_raw_normal) else None,
            "c_shrunk": round(math.sqrt(c_shrunk2), 6),
            "n": int(len(g)),
            "n_events": int(n_events),
        }
    return cities


def fit_group(sub_raw: "pd.DataFrame") -> dict:
    sub = _add_event_weights(sub_raw)
    n_events_group = sub["event_key"].nunique()
    n_rows_group = len(sub)
    if n_events_group < MIN_GROUP_N:
        return {
            "fitted": False,
            "global_k": 1.0,
            "global_k_normal_crosscheck": None,
            "n": int(n_rows_group),
            "n_events": int(n_events_group),
            "refusal_reason": f"INSUFFICIENT_EVENTS:{n_events_group}<{MIN_GROUP_N}",
            "buckets": {},
            "cities": {},
        }
    buckets, global_k, global_k_normal, _n_ev = fit_k_by_tau(sub)
    cities = fit_city_shrinkage(sub, buckets)
    return {
        "fitted": True,
        "global_k": round(float(global_k), 6),
        "global_k_normal_crosscheck": round(float(global_k_normal), 6) if math.isfinite(global_k_normal) else None,
        "n": int(n_rows_group),
        "n_events": int(n_events_group),
        "buckets": {
            lab: {
                **b,
                "k": round(float(b["k"]), 6) if b["k"] is not None else None,
                "k_normal_crosscheck": round(float(b["k_normal_crosscheck"]), 6) if b["k_normal_crosscheck"] is not None and math.isfinite(b["k_normal_crosscheck"]) else None,
            }
            for lab, b in buckets.items()
        },
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


def validate(d: "pd.DataFrame", cutoff: str, *, tau_col: str, label: str) -> None:
    train_mask = d["target_date"] < cutoff
    val_mask = d["target_date"] >= cutoff
    print(f"[sigma-tau] validate cutoff={cutoff} clock={label} n_train_rows={int(train_mask.sum())}  n_val_rows={int(val_mask.sum())}")
    for unit, metric in GROUPS:
        train = d[train_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        val = d[val_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        # Overwrite (not rename-with-duplicate) "taut" with the requested clock's bucket labels --
        # both "taut" and "taut_decision" already exist on every row from prep(), so a rename would
        # create two columns named "taut" and break every downstream ["taut"] lookup.
        train["taut"] = train[tau_col]
        val["taut"] = val[tau_col]
        n_events_train = train["event_key"].nunique()
        n_events_val = val["event_key"].nunique()
        if n_events_train < MIN_GROUP_N or n_events_val < 10:
            print(f"  {unit}/{metric}: SKIP (n_events_train={n_events_train}, n_events_val={n_events_val})")
            continue
        group = fit_group(train)
        val = _add_event_weights(val)
        w = val["event_weight"].values
        k_eff = _k_eff_row(group, val["taut"], val["city"]).values

        ll_censored_base = _interval_censored_loglik_per_row(val["l"].values, val["u"].values, val["sig"].values)
        ll_censored_fit = _interval_censored_loglik_per_row(val["l"].values, val["u"].values, k_eff * val["sig"].values)
        ll_normal_base = normal_logpdf(val["z"].values, val["sig"].values)
        ll_normal_fit = normal_logpdf(val["z"].values, k_eff * val["sig"].values)

        def wmean(x):
            return float(np.sum(w * x) / np.sum(w))

        cov_base = float(np.sum(w * (val["z"].abs() <= val["sig"]).values) / np.sum(w))
        cov_fit = float(np.sum(w * (val["z"].abs() <= (k_eff * val["sig"])).values) / np.sum(w))
        print(
            f"  {unit}/{metric}: n_events_train={n_events_train} n_events_val={n_events_val} "
            f"global_k={group.get('global_k')} (normal_crosscheck={group.get('global_k_normal_crosscheck')})\n"
            f"    censored  oos_mean_loglik k=1:{wmean(ll_censored_base):.5f} fitted:{wmean(ll_censored_fit):.5f} delta:{(wmean(ll_censored_fit) - wmean(ll_censored_base)):+.5f}\n"
            f"    normal_xc oos_mean_loglik k=1:{wmean(ll_normal_base):.5f} fitted:{wmean(ll_normal_fit):.5f} delta:{(wmean(ll_normal_fit) - wmean(ll_normal_base)):+.5f}\n"
            f"    coverage@68.3 (event-weighted) k=1:{cov_base:.4f} fitted:{cov_fit:.4f}"
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
        validate(d, args.validate, tau_col="taut", label="PRIMARY(issue-clock)")
        validate(d, args.validate, tau_col="taut_decision", label="COMPARISON-ONLY(decision-clock)")
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
            "method": "tau_bucketed_interval_censored_mle_plus_city_variance_shrinkage_event_weighted",
            "tau_clock": "source_cycle_time (forecast issue; NOT computed_at/decision time -- 2026-07-28 correction)",
            "tau_buckets": {lab: [lo if math.isfinite(lo) else None, hi if math.isfinite(hi) else None]
                            for lab, lo, hi in zip(TAU_LABELS, TAU_EDGES, TAU_EDGES[1:])},
            "min_bucket_n_events": MIN_BUCKET_N,
            "min_group_n_events": MIN_GROUP_N,
            "min_city_n_events": MIN_CITY_N,
            "city_shrinkage_n0": CITY_SHRINKAGE_N0,
            "event_weighting": "1/n_e per (city,target_date) event, group-wide (not per-bucket-renormalized)",
            "components_fence_applied": False,
            "components_fence_reference_ts": COMPONENTS_FENCE_TS,
            "components_fence_note": (
                "This fitter reads only anchor_value_c/predictive_sigma_c (always populated, not a "
                "component field); current_evidence_shape's within/between/delta components are NOT "
                "read here, so no fence was needed for correctness. Recorded so a future editor who "
                "adds a component-field read is forced to confront the 2026-07-15T22:32:31Z rollout "
                "boundary rather than silently training across it."
            ),
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
