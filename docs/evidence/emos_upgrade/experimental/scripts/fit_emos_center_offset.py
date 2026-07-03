#!/usr/bin/env python3
# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS-on-runtime-output center-bias correction. Frontier math consult
#   REQ-20260701-010328-c6d43a + universality validation (docs/evidence/emos_upgrade/2026-07-01).
#   READ-ONLY over state/zeus-forecasts.db; SOLE writer of state/emos_center_offset.json.
"""Fit the per-city EMOS center-bias offset from the REAL runtime combined center.

Replays the live serving center — the FROZEN source-clock fixed-weight combination
(``scheme_for_city``) applied to the RAW fixed-lead ``previous_runs`` model values — over the
full settled history. This reproduces the served ``forecast_posteriors.anchor_value_c`` byte-exact
(parity max|err|=0), so the correction is fit on the ACTUAL served center, not a reconstruction.

For each city it computes the walk-forward EWMA(H) + EB-shrunk offset (src.calibration.
emos_center_offset) and its walk-forward out-of-sample ΔMSE with a block-bootstrap CI, and SERVES a
city only when that ΔMSE has an individual 95% lower CI ≥ 0 (per-unit no-material-harm). Writes
state/emos_center_offset.json (the materializer READS it fail-soft via lookup_center_offset).

Deploy is decided by the math (per-city OOS gain lower-CI ≥ 0 + pooled lower-CI > 0), not by a hand
allow-list — an unfitted / thin / drift-unstable city simply is not served.
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

from src.calibration.emos_center_offset import (  # noqa: E402
    ARTIFACT_AUTHORITY,
    DEFAULT_HALF_LIFE,
    DEFAULT_MIN_TRAIN,
    current_offset,
    walk_forward_offset_series,
)
from src.config import runtime_cities_by_name, runtime_state_path  # noqa: E402
from src.state.db import ZEUS_FORECASTS_DB_PATH  # noqa: E402
from src.strategy.live_inference.source_clock_city_weights import scheme_for_city  # noqa: E402

OUT_DEFAULT = str(runtime_state_path("emos_center_offset.json"))


def _settle_c(v, u):
    v = float(v)
    return (v - 32.0) * 5.0 / 9.0 if str(u).strip().lower() in ("f", "degf", "fahrenheit") else v


def _replay_runtime_center(conn, metric: str, lead: int):
    """city -> [(date, center_c, settle_c, residual_c)] via the frozen scheme over previous_runs."""
    cities = list(runtime_cities_by_name().keys())
    scheme = {c: dict(s.weights) for c in cities if (s := scheme_for_city(c)) is not None}
    best: dict[tuple, tuple] = {}
    for r in conn.execute(
        "SELECT city,target_date,model,forecast_value_c,source_cycle_time FROM raw_model_forecasts "
        "WHERE endpoint='previous_runs' AND metric=? AND lead_days=?", (metric, lead)
    ):
        k = (r[0], r[1], r[2])
        if k not in best or r[4] > best[k][1]:
            best[k] = (r[3], r[4])
    vals_by: dict[tuple, dict] = defaultdict(dict)
    for (city, td, model), (v, _) in best.items():
        vals_by[(city, td)][model] = v
    settle: dict[tuple, float] = {}
    for r in conn.execute(
        "SELECT city,target_date,settlement_value,settlement_unit FROM settlement_outcomes "
        "WHERE temperature_metric=? AND authority='VERIFIED' AND settlement_value IS NOT NULL", (metric,)
    ):
        settle[(r[0], r[1])] = _settle_c(r[2], r[3])
    recs: dict[str, list] = defaultdict(list)
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
        recs[city].append((td, ctr, s, s - ctr))
    for c in recs:
        recs[c].sort()
    return recs


def _city_oos(recs, half_life, min_train):
    """Walk-forward OOS per-cell ΔMSE list for a city (leak-free served-offset series)."""
    off = dict(walk_forward_offset_series([(d, r) for (d, c, s, r) in recs],
                                          half_life=half_life, min_train=min_train))
    return [(s - c) ** 2 - (s - (c + off.get(d, 0.0))) ** 2 for (d, c, s, r) in recs]


def _lower_ci(xs, *, alpha=0.05, nboot=1000, seed=5):
    """One-sided (1-alpha) lower bound on the mean via iid bootstrap."""
    if len(xs) < 2:
        return float("-inf")
    rng = random.Random(seed)
    boots = sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(nboot))
    return boots[int(alpha * nboot)]


def _pooled_block_ci(cells, *, nboot=1000, seed=7):
    """Date-block bootstrap CI on pooled per-cell ΔMSE (respects cross-city common shocks)."""
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
    ap = argparse.ArgumentParser(description="Fit per-city EMOS center-bias offset (candidate-safe).")
    ap.add_argument("--half-life", type=float, default=DEFAULT_HALF_LIFE)
    ap.add_argument("--min-train", type=int, default=DEFAULT_MIN_TRAIN)
    ap.add_argument("--metric", default="high", choices=["high", "low"])
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--serve-min-n", type=int, default=40)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true", help="print the report; do NOT write the artifact.")
    a = ap.parse_args()

    import sqlite3  # noqa: PLC0415
    conn = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
    recs = _replay_runtime_center(conn, a.metric, a.lead)

    cities_out: dict[str, dict] = {}
    served_cells: list[tuple[str, float]] = []
    report: list[tuple] = []
    for city in sorted(recs):
        rc = recs[city]
        dated = [(d, r) for (d, c, s, r) in rc]
        off = current_offset(dated, half_life=a.half_life, min_train=a.min_train)
        cell_mse = _city_oos(rc, a.half_life, a.min_train)
        n = len(cell_mse)
        oos = statistics.mean(cell_mse) if cell_mse else 0.0
        lcb = _lower_ci(cell_mse) if n >= a.serve_min_n else float("-inf")
        # SERVE gate: enough history AND the correction is individually validated non-harmful
        # (its own walk-forward OOS ΔMSE lower 95% CI ≥ 0). Offset must exist (warmup/cold-start pass).
        serve = (off is not None) and (n >= a.serve_min_n) and (lcb >= 0.0)
        cities_out[city] = {
            "offset_c": round(off, 4) if off is not None else 0.0,
            "serve": bool(serve),
            "n": n,
            "full_bias_c": round(statistics.mean(r for (d, c, s, r) in rc), 4),
            "oos_dmse": round(oos, 4),
            "oos_dmse_lcb95": (round(lcb, 4) if math.isfinite(lcb) else None),
        }
        if serve:
            served_cells += [(d, m) for (d, c, s, r), m in zip(rc, cell_mse)]
        report.append((oos, city, off if off is not None else 0.0, n, serve, lcb))

    pooled, plo, phi = _pooled_block_ci(served_cells) if served_cells else (0.0, 0.0, 0.0)
    artifact = {
        "authority": ARTIFACT_AUTHORITY,
        "half_life": a.half_life,
        "min_train": a.min_train,
        "lead": a.lead,
        "serve_rule": "n>=serve_min_n AND walk_forward_oos_dmse_lower95CI>=0",
        "serve_min_n": a.serve_min_n,
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
    n_served = artifact["validation"]["n_served"]
    print(f"=== EMOS center offset (metric={a.metric} lead={a.lead} H={a.half_life} min_train={a.min_train}) ===")
    print(f"served {n_served}/{len(recs)}  pooled OOS ΔMSE={pooled:+.4f} block-CI95=[{plo:+.4f},{phi:+.4f}] lower>0={plo>0}")
    print(f"\n{'city':16s} {'offset':>8} {'full_bias':>10} {'oos_dmse':>9} {'lcb95':>8} {'n':>4} serve")
    for oos, city, off, n, serve, lcb in report:
        lcbs = f"{lcb:+.3f}" if math.isfinite(lcb) else "  n/a"
        print(f"{city:16s} {off:>+8.3f} {cities_out[city]['full_bias_c']:>+10.3f} {oos:>+9.4f} {lcbs:>8} {n:>4} {'YES' if serve else '.'}")

    if a.dry_run:
        print("\n[dry-run] artifact NOT written.")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, sort_keys=True)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
