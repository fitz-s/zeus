#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §4a (staleness
#   degrade ladder). AMBER-band sigma inflation MUST be settlement-fitted, never guessed
#   (operator go 2026-07-17: 使用数学和统计就能证明一切).
"""Fit the per-(metric, age-band) POSTERIOR-AGE variance inflation artifact.

WHAT IS MEASURED (operator law: data-time relationships are MEASURED, never guessed):
for each settled target (city, target_date, metric) the live fusion product produced one
posterior per source cycle. The whole-posterior center error is the predictive-mean
(q-weighted bin center) minus the settled value, in degC. Holding the TARGET fixed and
varying the cycle isolates the causal staleness penalty (a paired estimator, immune to
the survivorship deflation of a served-window model): for a cycle at cycle-lag L behind
the target's freshest cycle,

    v_incr(L) = mean( err(lag L)^2 − err(freshest)^2 )   over paired targets   [degC²]

is exactly the extra center-error VARIANCE one carries by serving an L-hours-older cycle.

AGE MAPPING (the fit defines the unit the loader keys on): a served posterior of age A
(= decision_time − source_cycle_time) is, physically, lag L = A − FRESH_SERVING_FLOOR
behind where a freshly-materialized cycle would sit — MEASURED fresh serving age
(computed_at − source_cycle_time) is p50 7.19h. So each paired lag-L sample is assigned
to the AGE band containing (FRESH_SERVING_FLOOR + L), and

    v(age_band) = cummax( max(0, v_incr) )   over increasing age band

is non-negative and monotone non-decreasing in age (a Wiener-like error growth cannot
shrink with age; sampling noise that says otherwise is clipped, never extrapolated). The
loader ``src/forecast/posterior_age_inflation.py::v_for`` adds v(age_band) to the served
predictive VARIANCE at admission — but only in the AMBER band; GREEN is unchanged and RED
blocks entry, so only the (18h, 24h] band's v is ever consumed.

DATA (STRICTLY WALK-FORWARD): forecast_posteriors (runtime_layer='live',
training_allowed=0, the live fusion product) JOIN settlement_outcomes (authority=
'VERIFIED') on (city, target_date, metric), restricted to target_date < as_of.
Settlements in degF are converted to degC before the residual.

DETERMINISM: same DB state + as_of => byte-identical content (json.dumps(sort_keys=True),
fixed rounding). ``generated_at`` is run metadata; pass ``--generated-at`` to pin it.

READ-ONLY over state/zeus-forecasts.db (file:...?mode=ro). Writes ONLY the versioned
artifact + ACTIVE.json pointer under state/posterior_age_inflation/.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
OUT_DIR_DEFAULT = ROOT / "state" / "posterior_age_inflation"
LIVE_FUSION_PRODUCT_ID = "openmeteo_ecmwf_ifs9_bayes_fusion_v1"
METRICS = ("high", "low")
BAND_HOURS = 6
# MEASURED fresh serving age p50 (computed_at − source_cycle_time), from healthy live
# operation — the offset that maps a paired cycle-lag to the posterior age the admission
# gate observes. Documented in the evidence file; a run re-derives it below and pins the
# ACTUAL measured value into the artifact so the mapping is never a stale literal.
FRESH_SERVING_FLOOR_HOURS_DEFAULT = 7.2
# A (metric, band) cell needs this many paired residuals before its v is trusted.
MIN_CELL_N = 100
# The only band the ladder actually consumes (AMBER). Fitting all bands is transparency;
# the loader gates consumption to AMBER, so this is recorded, not enforced.
AMBER_BAND_HOURS = (18, 24)


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def _q_mean_c(q_json: str, bin_topology: list) -> float | None:
    """Predictive mean (°C) = Σ q(bin)·center(bin)."""
    try:
        q = json.loads(q_json)
        centers = {b["bin_id"]: b.get("center_c") for b in bin_topology}
    except Exception:
        return None
    num = 0.0
    wsum = 0.0
    for k, p in q.items():
        c = centers.get(k)
        if c is None:
            continue
        num += float(p) * float(c)
        wsum += float(p)
    return num / wsum if wsum > 0.0 else None


def load_target_cycle_errors(
    conn: sqlite3.Connection, *, as_of: str
) -> tuple[dict[tuple[str, str, str], dict], list[float]]:
    """{(city,td,metric): {cycle_iso: (err_c, computed_iso)}} + fresh serving ages.

    First-materialized (freshest computed_at) row wins per (target, cycle).
    """
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    by_target: dict[tuple[str, str, str], dict] = defaultdict(dict)
    fresh_ages: list[float] = []
    for row in cur.execute(
        """
        SELECT p.city AS city, p.target_date AS target_date,
               p.temperature_metric AS metric, p.source_cycle_time AS source_cycle_time,
               p.computed_at AS computed_at, p.q_json AS q_json,
               p.provenance_json AS provenance_json,
               s.settlement_value AS settlement_value, s.settlement_unit AS settlement_unit
        FROM forecast_posteriors AS p
        JOIN settlement_outcomes AS s
          ON s.city = p.city AND s.target_date = p.target_date
         AND s.temperature_metric = p.temperature_metric
        WHERE p.runtime_layer = 'live' AND p.training_allowed = 0
          AND p.product_id = ? AND s.authority = 'VERIFIED'
          AND s.settlement_value IS NOT NULL AND p.target_date < ?
        ORDER BY p.city, p.target_date, p.temperature_metric,
                 p.source_cycle_time, p.computed_at, p.posterior_id
        """,
        (LIVE_FUSION_PRODUCT_ID, as_of),
    ):
        try:
            provenance = json.loads(str(row["provenance_json"]))
            topo = provenance.get("bin_topology")
            if not topo:
                continue
            mu = _q_mean_c(str(row["q_json"]), topo)
            if mu is None:
                continue
            settle_c = _settlement_to_celsius(row["settlement_value"], row["settlement_unit"])
            cyc = _dt.datetime.fromisoformat(str(row["source_cycle_time"]))
            comp = _dt.datetime.fromisoformat(str(row["computed_at"]))
        except (TypeError, ValueError, KeyError):
            continue
        key = (str(row["city"]), str(row["target_date"]), str(row["metric"]))
        cyc_iso = cyc.isoformat()
        cell = by_target[key]
        # First-materialized wins (deterministic ORDER above yields earliest computed_at first).
        if cyc_iso not in cell:
            cell[cyc_iso] = (abs(mu - settle_c), comp.isoformat())
            a = (comp - cyc).total_seconds() / 3600.0
            if 0.0 <= a < 40.0:
                fresh_ages.append(a)
    return by_target, fresh_ages


def derive_v_tables(
    by_target: dict[tuple[str, str, str], dict],
    *,
    fresh_floor_hours: float,
    band_hours: int = BAND_HOURS,
    min_cell_n: int = MIN_CELL_N,
) -> dict[str, dict[str, object]]:
    """{metric: {v_by_age_band, n_by_age_band, m2_incr_by_age_band}}.

    Paired cycle-lag squared-error increments, assigned to age band
    ``floor((fresh_floor + lag)/band)*band``; v = cummax(max(0, mean increment)).
    """
    # metric -> band -> list[(err_lag^2 − err_fresh^2)]
    incr: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (_city, _td, metric), cells in by_target.items():
        if len(cells) < 1:
            continue
        # Freshest cycle = latest source_cycle_time.
        freshest_iso = max(cells, key=lambda c: _dt.datetime.fromisoformat(c))
        freshest_dt = _dt.datetime.fromisoformat(freshest_iso)
        fresh_err = cells[freshest_iso][0]
        for cyc_iso, (err, _comp) in cells.items():
            lag_h = (freshest_dt - _dt.datetime.fromisoformat(cyc_iso)).total_seconds() / 3600.0
            if lag_h < 0.0:
                continue
            age = fresh_floor_hours + lag_h
            band = int(age // band_hours) * band_hours
            incr[metric][band].append(err * err - fresh_err * fresh_err)

    out: dict[str, dict[str, object]] = {}
    for metric in METRICS:
        bands = incr.get(metric)
        if not bands:
            continue
        fitted = sorted(b for b, xs in bands.items() if len(xs) >= min_cell_n)
        if not fitted:
            continue
        v_by_band: dict[str, float] = {}
        n_by_band: dict[str, int] = {}
        m2_incr_by_band: dict[str, float] = {}
        running = 0.0
        for band in fitted:
            xs = bands[band]
            mean_incr = sum(xs) / len(xs)
            running = max(running, max(0.0, mean_incr))
            v_by_band[str(band)] = round(running, 6)
            n_by_band[str(band)] = len(xs)
            m2_incr_by_band[str(band)] = round(mean_incr, 6)
        out[metric] = {
            "v_by_age_band": v_by_band,
            "n_by_age_band": n_by_band,
            "m2_incr_by_age_band": m2_incr_by_band,
        }
    return out


def _measured_fresh_floor(fresh_ages: list[float]) -> float:
    if not fresh_ages:
        return FRESH_SERVING_FLOOR_HOURS_DEFAULT
    ordered = sorted(fresh_ages)
    return round(ordered[len(ordered) // 2], 4)  # p50


def build_artifact(
    conn: sqlite3.Connection, *, as_of: str, generated_at: str, git_sha: str
) -> dict[str, object]:
    by_target, fresh_ages = load_target_cycle_errors(conn, as_of=as_of)
    fresh_floor = _measured_fresh_floor(fresh_ages)
    metrics = derive_v_tables(by_target, fresh_floor_hours=fresh_floor)
    paired = sum(len(cells) for cells in by_target.values())
    return {
        "schema_version": 1,
        "as_of": as_of,
        "generated_at": generated_at,
        "git_sha": git_sha,
        "unit": "degC2",
        "band_hours": BAND_HOURS,
        "fresh_serving_floor_hours": fresh_floor,
        "amber_band_hours": list(AMBER_BAND_HOURS),
        "min_cell_n": MIN_CELL_N,
        "age_mapping": "age_band = floor((fresh_serving_floor + cycle_lag_hours)/band_hours)*band_hours",
        "targets_used": len(by_target),
        "paired_cells_used": paired,
        "metrics": metrics,
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        return "unknown"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fcst", type=Path, default=FCST_DEFAULT)
    p.add_argument("--as-of", default=_dt.date.today().isoformat())
    p.add_argument("--generated-at", default=None, help="Override for determinism tests")
    p.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = args.generated_at or _dt.datetime.now(_dt.UTC).isoformat()
    conn = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        artifact = build_artifact(
            conn, as_of=args.as_of, generated_at=generated_at, git_sha=_git_sha()
        )
    finally:
        conn.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"posterior_age_inflation_{args.as_of.replace('-', '')}.json"
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    (args.out_dir / fname).write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    pointer = {"artifact": fname, "sha256": sha, "as_of": args.as_of}
    (args.out_dir / "ACTIVE.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {args.out_dir / fname} (sha256={sha}); "
        f"targets_used={artifact['targets_used']}; "
        f"fresh_floor_h={artifact['fresh_serving_floor_hours']}"
    )
    for metric, entry in sorted(artifact["metrics"].items()):
        amber_lo = AMBER_BAND_HOURS[0]
        amber_band = int(amber_lo // BAND_HOURS) * BAND_HOURS
        v = entry["v_by_age_band"].get(str(amber_band))
        print(f"  {metric}: AMBER band {amber_band}h v={v} degC² (n={entry['n_by_age_band'].get(str(amber_band))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
