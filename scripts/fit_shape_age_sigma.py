#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult P2-B: stale-shape reuse is licensed only as anomaly transport; the REMAINING
#   risk of an aged shape is priced as sigma_t^2 = max(sigma_min^2, a_g + b_g*S_e^2 +
#   gamma_g*age/6), where gamma_g "needs its own fit on 'fresh center + then-available
#   shape' pairs using ACTUAL availability timestamps (never retrospective newest-pairing)".
#   Operator order 2026-07-17: execute the fit from EXISTING archives (math/stats, no new
#   accumulation); strictly walk-forward; no market backtests.
"""Fit the shape-age sigma term gamma_g for the transported ENS evidence shape.

PAIR CONSTRUCTION (availability-replay, actual timestamps): for each settled
(city, target_date, metric) and each ARCHIVED ENS cycle ``e`` passing the serving snapshot
filter, decision references are the 6h-aligned UTC grid points strictly after
``e.source_available_at`` (exogenous schedule, consult P2-C) up to
min(avail_e + 48h, local_day_start_utc + 30h). At each reference the then-freshest fitted
provider center is rebuilt from the source-clock weights basket over then-available
``previous_runs`` values (newest cycle per model with source_available_at <= ref; present
weights renormalized with the serving PRESENT_WEIGHT_FLOOR=0.25). Members of ``e`` are
translated onto that center (a pure shift — within-spread preserved), and

    sigma0^2 = within^2 + between^2

with ``between`` the weighted spread of the then-available basket values around the center
(the serving between proxy; when fewer than 2 basket models are present the pair falls
back to within-only sigma0 and is counted in ``n_between_missing``).

COVARIATE: the fitted x is ``shape_lag = (carrier_cycle - e.source_cycle_time)/6h`` where
``carrier_cycle`` is the newest cycle among the basket models used for the center — this
is EXACTLY the ``shape_lag_hours`` covariate the serving term multiplies
(src/data/replacement_forecast_materializer._current_evidence_shape_from_values), so fit
and serving share one unit. The task-sheet's decision-referenced age
(decision_ref - e.source_cycle_time) is recorded per pair (``age_h``) and reported per
bucket for transparency; it upper-bounds the carrier lag by the carrier's own
publication age and is NOT the serving covariate. Pairs where the shape is newer than the
carrier (lag < 0) are dropped — serving enforces ens_cycle <= carrier_cycle.

ESTIMATOR (binned method-of-moments + WLS, and why): per pair
``y = (settle - center)^2 - sigma0^2`` is an unbiased-in-mean but chi-square-heavy-tailed
excess-variance observation; the bucket mean over a 6h lag bucket is the moment estimator
of the excess variance at that lag (a median would be biased for a variance), and an
n-weighted WLS line ``y = a + gamma * lag/6`` over bucket means keeps the estimator linear
in the measured moments. The intercept absorbs lag-independent sigma0 miscalibration (the
consult's a_g/b_g degrees of freedom, which serving does NOT apply) so the slope is the
age-MARGINAL excess; serving consumes only ``gamma = max(0, slope)``. SE/p-value come from
a target-date cluster bootstrap (pairs sharing a target date share settlements and
synoptic regimes; i.i.d. SEs would be fake).

WALK-FORWARD: only target_date < as_of enters at all; the fit uses target_date <
holdout_start and the CRPS/PIT/coverage replay (gamma=0 vs fitted) uses the held-out
target_date >= holdout_start. Centers use only values with source_available_at <= the
decision reference — never retrospective newest-pairing.

DETERMINISM: same DB state + as_of/holdout_start + generated_at => byte-identical artifact
(json sort_keys, fixed rounding, seeded bootstrap). READ-ONLY over the forecasts DB;
writes ONLY the versioned artifact + ACTIVE.json under --out-dir (orchestrator installs
the live artifact post-merge; this script must not be pointed at state/ during review).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import random
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
OUT_DIR_DEFAULT = ROOT / "state" / "shape_age_sigma"
WEIGHTS_DIR_DEFAULT = ROOT / "state" / "source_clock_weights"

METRICS = ("high", "low")
# Serving floor mirror: _current_evidence_shape_from_values rejects < 20 members.
MIN_MEMBERS = 20
# Serving mirror: source_clock_city_weights.PRESENT_WEIGHT_FLOOR.
PRESENT_WEIGHT_FLOOR = 0.25
# Decision references per ENS cycle: 6h grid from availability out to this window.
MAX_DECISION_WINDOW_H = 48.0
# Lag buckets are 6h wide; lags beyond the last bucket edge are clamped into it
# (measured support only — serving clamps nothing, the artifact just never sees
# a fitted point beyond what the archive supports).
MAX_LAG_BUCKET = 8
# A lag bucket needs this many pairs before its moment enters the WLS line.
MIN_BUCKET_N = 30
N_BOOT = 500
BOOT_SEED = 42

# Central-interval z half-widths for the coverage replay.
_Z80 = 1.2815515655446004
_Z50 = 0.6744897501960817


def _parse_utc(text: object) -> _dt.datetime | None:
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.UTC)
    return parsed.astimezone(_dt.UTC)


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    if str(unit or "").strip().upper() == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def _members_to_celsius(values: Sequence[float], members_unit: str) -> list[float]:
    unit = str(members_unit or "").strip().lower()
    if unit in {"degf", "f", "°f"}:
        return [(float(v) - 32.0) * 5.0 / 9.0 for v in values]
    return [float(v) for v in values]


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _gaussian_crps(residual: float, sigma: float) -> float:
    z = residual / sigma
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return sigma * (z * (2.0 * _norm_cdf(z) - 1.0) + 2.0 * pdf - 1.0 / math.sqrt(math.pi))


def load_basket_weights(weights_dir: Path) -> dict[tuple[str, str], dict[str, float]]:
    """{(city_lower, metric): {model: weight}} from the source-clock ACTIVE artifact.

    sha256-checked like the serving loader; a bad pointer raises (the fit cannot run
    without the basket definition — there is no legacy-CSV fallback here because the fit
    is per-metric and the CSV is metric-agnostic).
    """
    pointer = json.loads((weights_dir / "ACTIVE.json").read_text(encoding="utf-8"))
    raw = (weights_dir / str(pointer["artifact"])).read_bytes()
    if hashlib.sha256(raw).hexdigest() != str(pointer.get("sha256", "")):
        raise ValueError(f"source-clock weights artifact sha mismatch under {weights_dir}")
    artifact = json.loads(raw.decode("utf-8"))
    out: dict[tuple[str, str], dict[str, float]] = {}
    for city, per_metric in (artifact.get("cities") or {}).items():
        for metric, cell in (per_metric or {}).items():
            if metric not in METRICS:
                continue
            models = {
                str(m): float(w)
                for m, w in (cell.get("models") or {}).items()
                if math.isfinite(float(w)) and float(w) > 0.0
            }
            if models:
                out[(str(city).lower(), str(metric))] = models
    return out


def load_settlements(conn: sqlite3.Connection, *, as_of: str) -> dict[tuple[str, str, str], float]:
    """{(city_lower, target_date, metric): settlement_c}, VERIFIED, walk-forward."""
    out: dict[tuple[str, str, str], float] = {}
    rows = conn.execute(
        """
        SELECT city, target_date, temperature_metric, settlement_value, settlement_unit
        FROM settlement_outcomes
        WHERE authority = 'VERIFIED' AND settlement_value IS NOT NULL AND target_date < ?
        ORDER BY city, target_date, temperature_metric
        """,
        (as_of,),
    ).fetchall()
    for city, tdate, metric, value, unit in rows:
        try:
            out[(str(city).lower(), str(tdate), str(metric))] = _settlement_to_celsius(
                float(value), unit
            )
        except (TypeError, ValueError):
            continue
    return out


def load_ens_cycles(
    conn: sqlite3.Connection, *, as_of: str
) -> dict[tuple[str, str, str], list[dict]]:
    """ALL archived ENS cycles per settled-eligible triple, serving filter mirrored.

    Unlike scripts/fit_ens_member_dependence.load_settled_member_events (freshest-only),
    this keeps EVERY distinct cycle — the aged cycles ARE the sample. Per (triple, cycle)
    the freshest-captured snapshot wins (same ORDER BY as serving).
    """
    rows = conn.execute(
        """
        SELECT city, target_date, temperature_metric, members_json, members_unit,
               COALESCE(source_cycle_time, issue_time) AS cycle_time,
               COALESCE(source_available_at, available_at) AS available_at,
               local_day_start_utc
        FROM ensemble_snapshots
        WHERE source_id = 'ecmwf_open_data'
          AND model_version = 'ecmwf_ens'
          AND authority = 'VERIFIED'
          AND causality_status = 'OK'
          AND boundary_ambiguous = 0
          AND forecast_window_attribution_status = 'FULLY_INSIDE_TARGET_LOCAL_DAY'
          AND contributes_to_target_extrema = 1
          AND target_date < ?
        ORDER BY city, target_date, temperature_metric,
                 COALESCE(source_cycle_time, issue_time) DESC,
                 COALESCE(source_available_at, available_at) DESC,
                 snapshot_id DESC
        """,
        (as_of,),
    ).fetchall()
    out: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for city, tdate, metric, members_json, members_unit, cycle, avail, day_start in rows:
        key = (str(city).lower(), str(tdate), str(metric))
        cycle_key = (*key, str(cycle))
        if cycle_key in seen:
            continue  # ORDER BY puts the freshest capture first per (triple, cycle)
        seen.add(cycle_key)
        cycle_dt, avail_dt = _parse_utc(cycle), _parse_utc(avail)
        if cycle_dt is None or avail_dt is None:
            continue
        try:
            values = [v for v in json.loads(members_json) if v is not None]
        except (TypeError, ValueError):
            continue
        if len(values) < MIN_MEMBERS:
            continue
        members = _members_to_celsius(values, str(members_unit or ""))
        if any(not math.isfinite(v) for v in members):
            continue
        out[key].append(
            {
                "cycle": cycle_dt,
                "avail": avail_dt,
                "members": members,
                "day_start": _parse_utc(day_start),
            }
        )
    return out


def load_previous_runs(
    conn: sqlite3.Connection, *, as_of: str, models: set[str]
) -> dict[tuple[str, str, str, str], list[tuple[_dt.datetime, _dt.datetime, float]]]:
    """{(model, city_lower, metric, target_date): [(avail, cycle, value_c)] sorted by avail}."""
    out: dict[tuple[str, str, str, str], list[tuple[_dt.datetime, _dt.datetime, float]]] = (
        defaultdict(list)
    )
    rows = conn.execute(
        """
        SELECT model, city, metric, target_date, source_cycle_time, source_available_at,
               forecast_value_c
        FROM raw_model_forecasts
        WHERE endpoint = 'previous_runs' AND target_date < ?
        ORDER BY model, city, metric, target_date, source_available_at, source_cycle_time
        """,
        (as_of,),
    ).fetchall()
    for model, city, metric, tdate, cycle, avail, value in rows:
        if str(model) not in models:
            continue
        cycle_dt, avail_dt = _parse_utc(cycle), _parse_utc(avail)
        if cycle_dt is None or avail_dt is None:
            continue
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value_f):
            continue
        out[(str(model), str(city).lower(), str(metric), str(tdate))].append(
            (avail_dt, cycle_dt, value_f)
        )
    for runs in out.values():
        runs.sort(key=lambda r: (r[0], r[1]))
    return out


def _then_fresh_center(
    basket: Mapping[str, float],
    runs_by_model: Mapping[str, Sequence[tuple[_dt.datetime, _dt.datetime, float]]],
    ref: _dt.datetime,
) -> tuple[float, float | None, _dt.datetime] | None:
    """(center_c, between_c | None, carrier_cycle) from then-available basket values.

    Per model the newest cycle with source_available_at <= ref serves (availability law —
    never retrospective newest-pairing); present weights renormalize; fewer than 2 present
    models => between=None (within-only sigma0 upstream); present weight < the serving
    floor => no center (pair skipped, mirroring fixed_weight_center_from_values).
    """
    used: list[tuple[str, float, float, _dt.datetime]] = []
    for model, weight in sorted(basket.items()):
        best: tuple[_dt.datetime, _dt.datetime, float] | None = None
        for avail, cycle, value in runs_by_model.get(model, ()):
            if avail > ref:
                break  # sorted by avail
            if best is None or (cycle, avail) > (best[1], best[0]):
                best = (avail, cycle, value)
        if best is not None:
            used.append((model, best[2], float(weight), best[1]))
    if not used:
        return None
    present_weight = sum(w for _m, _v, w, _c in used)
    if present_weight < PRESENT_WEIGHT_FLOOR:
        return None
    center = sum(v * (w / present_weight) for _m, v, w, _c in used)
    between: float | None = None
    if len(used) >= 2:
        between = math.sqrt(
            sum((w / present_weight) * (v - center) ** 2 for _m, v, w, _c in used)
        )
    carrier_cycle = max(c for _m, _v, _w, c in used)
    return center, between, carrier_cycle


def _decision_refs(
    avail: _dt.datetime, day_start: _dt.datetime | None, target_date: str
) -> list[_dt.datetime]:
    """6h-aligned UTC grid strictly after ``avail``, bounded by window and day end."""
    if day_start is not None:
        end = day_start + _dt.timedelta(hours=30)
    else:
        end = _dt.datetime.fromisoformat(target_date).replace(tzinfo=_dt.UTC) + _dt.timedelta(
            hours=36
        )
    end = min(end, avail + _dt.timedelta(hours=MAX_DECISION_WINDOW_H))
    grid = avail.replace(hour=(avail.hour // 6) * 6, minute=0, second=0, microsecond=0)
    refs: list[_dt.datetime] = []
    while grid <= avail:
        grid += _dt.timedelta(hours=6)
    while grid <= end:
        refs.append(grid)
        grid += _dt.timedelta(hours=6)
    return refs


def build_pairs(
    *,
    ens_cycles: Mapping[tuple[str, str, str], Sequence[dict]],
    settlements: Mapping[tuple[str, str, str], float],
    runs: Mapping[tuple[str, str, str, str], Sequence[tuple[_dt.datetime, _dt.datetime, float]]],
    baskets: Mapping[tuple[str, str], Mapping[str, float]],
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Availability-replay pairs per metric + construction counters."""
    pairs: dict[str, list[dict]] = {metric: [] for metric in METRICS}
    counters = {
        "triples_with_basket": 0,
        "triples_without_basket": 0,
        "n_center_unavailable": 0,
        "n_between_missing": 0,
        "n_negative_lag_dropped": 0,
    }
    for key in sorted(ens_cycles):
        city, tdate, metric = key
        if metric not in METRICS or key not in settlements:
            continue
        basket = baskets.get((city, metric))
        if not basket:
            counters["triples_without_basket"] += 1
            continue
        counters["triples_with_basket"] += 1
        settle = settlements[key]
        runs_by_model = {
            model: runs.get((model, city, metric, tdate), ()) for model in basket
        }
        center_cache: dict[_dt.datetime, tuple[float, float | None, _dt.datetime] | None] = {}
        for ens in sorted(ens_cycles[key], key=lambda e: (e["cycle"], e["avail"])):
            members = ens["members"]
            member_mean = sum(members) / len(members)
            within_sq = sum((v - member_mean) ** 2 for v in members) / len(members)
            for ref in _decision_refs(ens["avail"], ens["day_start"], tdate):
                if ref not in center_cache:
                    center_cache[ref] = _then_fresh_center(basket, runs_by_model, ref)
                resolved = center_cache[ref]
                if resolved is None:
                    counters["n_center_unavailable"] += 1
                    continue
                center, between, carrier_cycle = resolved
                lag_h = (carrier_cycle - ens["cycle"]).total_seconds() / 3600.0
                if lag_h < 0.0:
                    counters["n_negative_lag_dropped"] += 1
                    continue
                if between is None:
                    counters["n_between_missing"] += 1
                sigma0_sq = within_sq + (between or 0.0) ** 2
                residual = settle - center
                pairs[metric].append(
                    {
                        "city": city,
                        "target_date": tdate,
                        "lag_h": lag_h,
                        "age_h": (ref - ens["cycle"]).total_seconds() / 3600.0,
                        "residual": residual,
                        "sigma0_sq": sigma0_sq,
                        "y": residual * residual - sigma0_sq,
                        "bucket": min(int(lag_h // 6.0), MAX_LAG_BUCKET),
                    }
                )
    return pairs, counters


def _bucket_means(
    pairs: Sequence[dict], *, min_bucket_n: int
) -> dict[int, tuple[int, float, float]]:
    """{bucket: (n, mean_lag_over_6, mean_y)} over buckets meeting the n floor."""
    grouped: dict[int, list[dict]] = defaultdict(list)
    for pair in pairs:
        grouped[pair["bucket"]].append(pair)
    out: dict[int, tuple[int, float, float]] = {}
    for bucket in sorted(grouped):
        rows = grouped[bucket]
        if len(rows) < min_bucket_n:
            continue
        out[bucket] = (
            len(rows),
            sum(p["lag_h"] for p in rows) / len(rows) / 6.0,
            sum(p["y"] for p in rows) / len(rows),
        )
    return out


def _wls_slope(means: Mapping[int, tuple[int, float, float]]) -> tuple[float, float] | None:
    """n-weighted least squares (slope, intercept) over bucket means; None if < 2 buckets
    or degenerate x support."""
    if len(means) < 2:
        return None
    sw = sum(n for n, _x, _y in means.values())
    sx = sum(n * x for n, x, _y in means.values())
    sy = sum(n * y for n, _x, y in means.values())
    sxx = sum(n * x * x for n, x, _y in means.values())
    sxy = sum(n * x * y for n, x, y in means.values())
    denom = sw * sxx - sx * sx
    if denom <= 0.0:
        return None
    slope = (sw * sxy - sx * sy) / denom
    return slope, (sy - slope * sx) / sw


def fit_gamma(
    pairs: Sequence[dict],
    *,
    min_bucket_n: int = MIN_BUCKET_N,
    n_boot: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> dict[str, object]:
    """Bucketed method-of-moments + WLS slope, target-date cluster bootstrap SE/p."""
    means = _bucket_means(pairs, min_bucket_n=min_bucket_n)
    fit = _wls_slope(means)
    clusters = sorted({p["target_date"] for p in pairs})
    result: dict[str, object] = {
        "n_pairs": len(pairs),
        "n_clusters": len(clusters),
        "buckets": {
            str(b): {"n": n, "mean_lag_h": round(x * 6.0, 3), "mean_excess_c2": round(y, 4)}
            for b, (n, x, y) in sorted(means.items())
        },
    }
    if fit is None:
        result.update(
            {"gamma_per_6h": 0.0, "slope": None, "intercept": None, "se": None,
             "p_value": None, "status": "INSUFFICIENT_BUCKETS"}
        )
        return result
    slope, intercept = fit
    by_cluster: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        by_cluster[pair["target_date"]].append(pair)
    rng = random.Random(seed)
    boot_slopes: list[float] = []
    for _ in range(n_boot):
        sample: list[dict] = []
        for _ in clusters:
            sample.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        boot_fit = _wls_slope(_bucket_means(sample, min_bucket_n=min_bucket_n))
        if boot_fit is not None:
            boot_slopes.append(boot_fit[0])
    se: float | None = None
    p_value: float | None = None
    if len(boot_slopes) >= 2:
        mean_b = sum(boot_slopes) / len(boot_slopes)
        se = math.sqrt(
            sum((s - mean_b) ** 2 for s in boot_slopes) / (len(boot_slopes) - 1)
        )
        if se > 0.0:
            p_value = 2.0 * (1.0 - _norm_cdf(abs(slope) / se))
    result.update(
        {
            "gamma_per_6h": round(max(0.0, slope), 6),
            "slope": round(slope, 6),
            "intercept": round(intercept, 6),
            "se": None if se is None else round(se, 6),
            "p_value": None if p_value is None else round(p_value, 6),
            "boot_reps_used": len(boot_slopes),
            "status": "OK",
        }
    )
    return result


def replay_holdout(pairs: Sequence[dict], gamma: float) -> list[dict[str, object]]:
    """Per lag bucket: CRPS / mean PIT / central 80%+50% coverage, gamma=0 vs fitted."""
    grouped: dict[int, list[dict]] = defaultdict(list)
    for pair in pairs:
        grouped[pair["bucket"]].append(pair)
    table: list[dict[str, object]] = []
    for bucket in sorted(grouped):
        rows = grouped[bucket]
        stats = {"gamma0": defaultdict(float), "fitted": defaultdict(float)}
        for pair in rows:
            sigma0 = math.sqrt(pair["sigma0_sq"])
            if sigma0 <= 0.0:
                continue
            for name, sigma_sq in (
                ("gamma0", pair["sigma0_sq"]),
                ("fitted", pair["sigma0_sq"] + gamma * pair["lag_h"] / 6.0),
            ):
                sigma = math.sqrt(sigma_sq)
                z = pair["residual"] / sigma
                acc = stats[name]
                acc["crps"] += _gaussian_crps(pair["residual"], sigma)
                acc["pit"] += _norm_cdf(z)
                acc["cov80"] += 1.0 if abs(z) <= _Z80 else 0.0
                acc["cov50"] += 1.0 if abs(z) <= _Z50 else 0.0
                acc["n"] += 1.0
        n = stats["gamma0"]["n"]
        if n <= 0:
            continue
        row: dict[str, object] = {
            "bucket": bucket,
            "n": int(n),
            "mean_lag_h": round(sum(p["lag_h"] for p in rows) / len(rows), 2),
            "mean_age_h": round(sum(p["age_h"] for p in rows) / len(rows), 2),
        }
        for name in ("gamma0", "fitted"):
            acc = stats[name]
            row[name] = {
                "crps": round(acc["crps"] / n, 4),
                "pit_mean": round(acc["pit"] / n, 4),
                "cov80": round(acc["cov80"] / n, 4),
                "cov50": round(acc["cov50"] / n, 4),
            }
        table.append(row)
    return table


def build_artifact(
    conn: sqlite3.Connection,
    *,
    baskets: Mapping[tuple[str, str], Mapping[str, float]],
    as_of: str,
    holdout_start: str,
    generated_at: str,
    git_sha: str,
    min_bucket_n: int = MIN_BUCKET_N,
    n_boot: int = N_BOOT,
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    """(artifact, {metric: holdout replay table}). Fit strictly on target_date < holdout_start."""
    settlements = load_settlements(conn, as_of=as_of)
    ens_cycles = load_ens_cycles(conn, as_of=as_of)
    models = {model for basket in baskets.values() for model in basket}
    runs = load_previous_runs(conn, as_of=as_of, models=models)
    pairs, counters = build_pairs(
        ens_cycles=ens_cycles, settlements=settlements, runs=runs, baskets=baskets
    )
    metrics_out: dict[str, object] = {}
    replay_out: dict[str, list[dict[str, object]]] = {}
    for metric in METRICS:
        fit_pairs = [p for p in pairs[metric] if p["target_date"] < holdout_start]
        holdout_pairs = [p for p in pairs[metric] if p["target_date"] >= holdout_start]
        fitted = fit_gamma(fit_pairs, min_bucket_n=min_bucket_n, n_boot=n_boot)
        fitted["n_holdout_pairs"] = len(holdout_pairs)
        metrics_out[metric] = fitted
        replay_out[metric] = replay_holdout(holdout_pairs, float(fitted["gamma_per_6h"]))
    artifact = {
        "schema_version": 1,
        "as_of": as_of,
        "holdout_start": holdout_start,
        "generated_at": generated_at,
        "git_sha": git_sha,
        "unit": "degC2 per 6h of shape lag",
        "covariate": "carrier_cycle - ens_source_cycle_time (hours)/6 — serving shape_lag_hours",
        "method": (
            "6h-bucket method-of-moments excess-variance means, n-weighted WLS with "
            "intercept, slope clamped >= 0; target-date cluster bootstrap SE"
        ),
        "min_bucket_n": min_bucket_n,
        "construction_counters": counters,
        "metrics": metrics_out,
    }
    return artifact, replay_out


def render_report(
    artifact: Mapping[str, object], replay: Mapping[str, Sequence[Mapping[str, object]]]
) -> str:
    lines = [
        "# Shape-age sigma term (gamma_g) — fit + availability-replay validation",
        "",
        f"- as_of: {artifact['as_of']}  holdout_start: {artifact['holdout_start']}  "
        f"generated_at: {artifact['generated_at']}  git: {artifact['git_sha'][:12]}",
        f"- covariate: {artifact['covariate']}",
        f"- method: {artifact['method']}",
        f"- construction: {json.dumps(artifact['construction_counters'], sort_keys=True)}",
        "",
    ]
    for metric in METRICS:
        fit = (artifact.get("metrics") or {}).get(metric) or {}
        lines += [
            f"## {metric}",
            "",
            f"- gamma_per_6h = **{fit.get('gamma_per_6h')}** degC2/6h "
            f"(raw slope {fit.get('slope')}, intercept {fit.get('intercept')}, "
            f"SE {fit.get('se')}, p {fit.get('p_value')}, status {fit.get('status')})",
            f"- n_pairs(fit) = {fit.get('n_pairs')}, clusters = {fit.get('n_clusters')}, "
            f"n_pairs(holdout) = {fit.get('n_holdout_pairs')}",
            "",
            "Fit buckets (lag bucket -> n, mean lag h, mean excess degC2):",
            "",
            "| bucket | n | mean_lag_h | mean_excess_c2 |",
            "| --- | --- | --- | --- |",
        ]
        for bucket, row in sorted((fit.get("buckets") or {}).items(), key=lambda kv: int(kv[0])):
            lines.append(
                f"| {bucket} | {row['n']} | {row['mean_lag_h']} | {row['mean_excess_c2']} |"
            )
        lines += [
            "",
            "Holdout availability-replay (gamma=0 vs fitted):",
            "",
            "| bucket | n | lag_h | CRPS g0 | CRPS fit | PIT g0 | PIT fit | "
            "cov80 g0 | cov80 fit | cov50 g0 | cov50 fit |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in replay.get(metric, ()):  # already bucket-sorted
            g0, ft = row["gamma0"], row["fitted"]
            lines.append(
                f"| {row['bucket']} | {row['n']} | {row['mean_lag_h']} | {g0['crps']} | "
                f"{ft['crps']} | {g0['pit_mean']} | {ft['pit_mean']} | {g0['cov80']} | "
                f"{ft['cov80']} | {g0['cov50']} | {ft['cov50']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


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
    p.add_argument(
        "--holdout-start",
        default="2026-07-01",
        help="target_date split: fit strictly before, CRPS/PIT/coverage replay on/after",
    )
    p.add_argument("--weights-dir", type=Path, default=WEIGHTS_DIR_DEFAULT)
    p.add_argument("--generated-at", default=None, help="Override for determinism tests")
    p.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    p.add_argument("--report", type=Path, default=None, help="Optional markdown report path")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = args.generated_at or _dt.datetime.now(_dt.UTC).isoformat()
    baskets = load_basket_weights(args.weights_dir)
    conn = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    try:
        artifact, replay = build_artifact(
            conn,
            baskets=baskets,
            as_of=args.as_of,
            holdout_start=args.holdout_start,
            generated_at=generated_at,
            git_sha=_git_sha(),
        )
    finally:
        conn.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"shape_age_sigma_{args.as_of.replace('-', '')}.json"
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    (args.out_dir / fname).write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    pointer = {"artifact": fname, "sha256": sha, "as_of": args.as_of}
    (args.out_dir / "ACTIVE.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = render_report(artifact, replay)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    print(f"Wrote {args.out_dir / fname} (sha256={sha})")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
