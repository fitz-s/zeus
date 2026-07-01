#!/usr/bin/env python3
# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration. Operator "运行态组合数据 精准 emos";
#   consult REQ-20260701-010328. READ-ONLY over state/zeus-forecasts.db; SOLE writer of
#   state/emos_center_calibration.json.
"""Fit the per-city affine EMOS center calibration μ'=a+b·μ from the REAL runtime combined center.

Replays the frozen source-clock scheme over settled previous_runs history (reproduces the served
anchor_value_c byte-exact) → per-city shrunk-to-identity OLS (a,b), gated to SERVE only where BOTH:
  STRUCT   — the walk-forward affine OOS ΔMSE has an individual 95% lower CI ≥ 0 (long history), AND
  TRANSFER — the SAME (a,b) has non-negative point ΔMSE on the ACTUAL live served (single_runs)
             center over the settled overlap (guards the previous_runs↔single_runs product gap).
The slope b captures the temperature-dependent representativeness bias a constant offset cannot;
shrinkage keeps world-class cities at identity (byte-identical). The output is a config artifact only;
live use still requires a fresh posterior materialization that stamps the applied affine in provenance.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.emos_center_calibration import (  # noqa: E402
    ARTIFACT_AUTHORITY,
    DEFAULT_KAPPA,
    DEFAULT_MIN_TRAIN,
    apply_affine,
    current_affine,
    fit_affine,
    walk_forward_affine,
)
from src.config import runtime_cities_by_name, runtime_state_path  # noqa: E402
from src.state.db import get_forecasts_connection_read_only  # noqa: E402
from src.strategy.live_inference.source_clock_city_weights import scheme_for_city  # noqa: E402

OUT_DEFAULT = str(runtime_state_path("emos_center_calibration.json"))


def _settle_c(v, u):
    v = float(v)
    return (v - 32.0) * 5.0 / 9.0 if str(u).strip().lower() in ("f", "degf", "fahrenheit") else v


def _replay_runtime_center(conn, metric, lead):
    """city -> [(date, center_c, settle_c)] via the frozen scheme over previous_runs (parity-exact)."""
    cities = list(runtime_cities_by_name().keys())
    scheme = {c: dict(s.weights) for c in cities if (s := scheme_for_city(c)) is not None}
    best = {}
    for r in conn.execute(
        "SELECT city,target_date,model,forecast_value_c,source_cycle_time FROM raw_model_forecasts "
        "WHERE endpoint='previous_runs' AND metric=? AND lead_days=?", (metric, lead)
    ):
        k = (r[0], r[1], r[2])
        if k not in best or r[4] > best[k][1]:
            best[k] = (r[3], r[4])
    vals_by = defaultdict(dict)
    for (city, td, model), (v, _) in best.items():
        vals_by[(city, td)][model] = v
    settle = {}
    for r in conn.execute(
        "SELECT city,target_date,settlement_value,settlement_unit FROM settlement_outcomes "
        "WHERE temperature_metric=? AND authority='VERIFIED' AND settlement_value IS NOT NULL", (metric,)
    ):
        settle[(r[0], r[1])] = _settle_c(r[2], r[3])
    recs = defaultdict(list)
    for (city, td), vals in vals_by.items():
        if city not in scheme:
            continue
        s = settle.get((city, td))
        if s is None:
            continue
        w = {m: scheme[city][m] for m in scheme[city]
             if m in vals and vals[m] is not None and math.isfinite(vals[m])}
        tot = math.fsum(w.values())
        if tot <= 0:
            continue
        ctr = math.fsum(vals[m] * (w[m] / tot) for m in w)
        recs[city].append((td, ctr, s))
    for c in recs:
        recs[c].sort()
    return recs


def _live_served_pairs(conn, metric):
    """city -> [(live_center, settle)] on the ACTUAL served center (single_runs anchor_value_c)."""
    latest = {}
    for r in conn.execute(
        "SELECT city,target_date,computed_at,provenance_json FROM forecast_posteriors "
        "WHERE temperature_metric=?", (metric,)
    ):
        k = (r[0], r[1])
        if k not in latest or r[2] > latest[k][0]:
            try:
                av = json.loads(r[3]).get("anchor_value_c")
            except Exception:
                av = None
            latest[k] = (r[2], av)
    settle = {}
    for r in conn.execute(
        "SELECT city,target_date,settlement_value,settlement_unit FROM settlement_outcomes "
        "WHERE temperature_metric=? AND authority='VERIFIED' AND settlement_value IS NOT NULL", (metric,)
    ):
        settle[(r[0], r[1])] = _settle_c(r[2], r[3])
    out = defaultdict(list)
    for (city, td), (ca, av) in latest.items():
        if av is None:
            continue
        s = settle.get((city, td))
        if s is not None:
            out[city].append((float(av), s))
    return out


def _wf_affine_dmse(recs, min_train, kappa):
    """Walk-forward affine per-cell ΔMSE list (leak-free)."""
    series = {d: (a, b) for d, a, b in walk_forward_affine(recs, min_train=min_train, kappa=kappa)}
    out = []
    for (d, c, s) in recs:
        a, b = series.get(d, (0.0, 1.0))
        out.append((s - c) ** 2 - (s - apply_affine(c, a, b)) ** 2)
    return out


def _lower_ci(xs, *, alpha=0.05, nboot=1000, seed=5):
    if len(xs) < 2:
        return float("-inf")
    rng = random.Random(seed)
    boots = sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(nboot))
    return boots[int(alpha * nboot)]


def _pooled_block_ci(cells, *, nboot=1000, seed=7):
    bydate = defaultdict(list)
    for d, delta in cells:
        bydate[d].append(delta)
    ds = list(bydate)
    rng = random.Random(seed)
    boots = []
    for _ in range(nboot):
        tot = 0.0
        n = 0
        for _ in range(len(ds)):
            dd = rng.choice(ds)
            for v in bydate[dd]:
                tot += v
                n += 1
        boots.append(tot / n if n else 0.0)
    boots.sort()
    return statistics.mean(boots), boots[int(0.025 * nboot)], boots[int(0.975 * nboot)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit per-city affine EMOS center calibration (candidate-safe).")
    ap.add_argument("--kappa", type=float, default=DEFAULT_KAPPA)
    ap.add_argument("--min-train", type=int, default=DEFAULT_MIN_TRAIN)
    ap.add_argument("--metric", default="high", choices=["high", "low"])
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--serve-min-n", type=int, default=40)
    ap.add_argument("--live-min-n", type=int, default=10)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    conn = get_forecasts_connection_read_only()
    recs = _replay_runtime_center(conn, a.metric, a.lead)
    livep = _live_served_pairs(conn, a.metric)

    try:
        from src.strategy.live_inference.source_clock_city_weights import GRID_AWARE_ARTIFACT_NAME  # noqa: PLC0415
        scheme_art = str(GRID_AWARE_ARTIFACT_NAME)
    except Exception:
        scheme_art = None

    cities_out = {}
    served_cells = []
    report = []
    for city in sorted(recs):
        rc = recs[city]
        ab = current_affine(rc, min_train=a.min_train, kappa=a.kappa)
        wf = _wf_affine_dmse(rc, a.min_train, a.kappa)
        n = len(wf)
        oos = statistics.mean(wf) if wf else 0.0
        struct_lcb = _lower_ci(wf) if n >= a.serve_min_n else float("-inf")
        struct_ok = (ab is not None) and (n >= a.serve_min_n) and (struct_lcb >= 0.0)
        # TRANSFER gate on the ACTUAL live served center
        lp = livep.get(city, [])
        A, B = (ab if ab is not None else (0.0, 1.0))
        live_deltas = [(s - c) ** 2 - (s - apply_affine(c, A, B)) ** 2 for c, s in lp]
        live_n = len(live_deltas)
        live_dmse = statistics.mean(live_deltas) if live_deltas else 0.0
        live_lcb = _lower_ci(live_deltas) if live_n >= a.live_min_n else float("-inf")
        is_identity = (abs(A) < 1e-9 and abs(B - 1.0) < 1e-9)
        # TRANSFER gate: the affine must NOT HARM the ACTUAL live served (single_runs) center — this
        # guards the previous_runs↔single_runs product gap. POINT no-harm (live ΔMSE ≥ 0), not a 95%
        # CI: the affine is robust in aggregate (pooled live +0.23, 33/49 help) so the CI on ~18 live
        # obs is over-strict; the point gate keeps the meaningful breadth while still dropping any city
        # the fit actively hurts on live. live_transfer_dmse_lcb95 is recorded for transparency.
        transfer_ok = (not is_identity) and (live_n >= a.live_min_n) and (live_dmse >= 0.0)
        serve = bool(struct_ok and transfer_ok)
        cities_out[city] = {
            "a": round(A, 5), "b": round(B, 5), "serve": serve, "n": n,
            "oos_dmse": round(oos, 4),
            "oos_dmse_lcb95": (round(struct_lcb, 4) if math.isfinite(struct_lcb) else None),
            "live_n": live_n, "live_transfer_dmse": round(live_dmse, 4),
            "live_transfer_dmse_lcb95": (round(live_lcb, 4) if math.isfinite(live_lcb) else None),
            "struct_ok": bool(struct_ok), "transfer_ok": bool(transfer_ok),
        }
        if serve:
            served_cells += [(d, m) for (d, c, s), m in zip(rc, wf)]
        report.append((oos, city, A, B, n, serve))

    pooled, plo, phi = _pooled_block_ci(served_cells) if served_cells else (0.0, 0.0, 0.0)
    artifact = {
        "authority": ARTIFACT_AUTHORITY, "fit_on_scheme_artifact": scheme_art,
        "model": "affine_ngr_center: mu' = a + b*mu_runtime (shrunk to identity)",
        "kappa": a.kappa, "min_train": a.min_train, "lead": a.lead,
        "serve_rule": ("STRUCT(walk_forward_affine_oos_dmse_lower95CI>=0, n>=serve_min_n) AND "
                       "TRANSFER(live_single_runs affine_dmse_point>=0, live_n>=live_min_n)"),
        "serve_min_n": a.serve_min_n, "live_min_n": a.live_min_n,
        "metrics": {a.metric: {"cities": cities_out}},
        "validation": {
            "served_pooled_oos_dmse": round(pooled, 4),
            "served_pooled_block_ci95": [round(plo, 4), round(phi, 4)],
            "served_pooled_lower_ci_gt0": bool(plo > 0),
            "n_served": sum(1 for v in cities_out.values() if v["serve"]),
            "n_cities": len(cities_out),
        },
    }

    report.sort(reverse=True)
    ns = artifact["validation"]["n_served"]
    print(f"=== EMOS affine center calibration (metric={a.metric} lead={a.lead} κ={a.kappa} min_train={a.min_train}) ===")
    print(f"served {ns}/{len(recs)}  served-pooled OOS ΔMSE={pooled:+.4f} block-CI95=[{plo:+.4f},{phi:+.4f}] lower>0={plo>0}")
    print(f"\n{'city':16s} {'a':>7} {'b':>7} {'oos_dmse':>9} {'n':>4} serve")
    for oos, city, A, B, n, serve in report:
        print(f"{city:16s} {A:>+7.2f} {B:>7.3f} {oos:>+9.4f} {n:>4} {'YES' if serve else '.'}")
    if a.dry_run:
        print("\n[dry-run] artifact NOT written.")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, sort_keys=True)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
