#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — the Clopper-Pearson finite-evidence bound treats ~51 DEPENDENT
#   ECMWF-ENS members as independent binomial trials and is therefore overconfident;
#   the honest form uses an effective n from MEASURED member dependence.
#   Empirical basis for the COVERAGE-CALIBRATED (not ICC) operational rho:
#   cp_coverage measurement 2026-07-17 over 3171 settled targets.
#   Lifecycle: config_writer — read-only over zeus-forecasts.db (file:...?mode=ro);
#   writes state/ens_member_dependence/ens_member_dependence_<as_of>.json + ACTIVE.json
#   pointer only. Strictly walk-forward (target_date < as_of); no market backtests.
"""Fit the ECMWF-ENS member-dependence artifact (coverage-calibrated rho).

WHAT THE OPERATIONAL rho IS — AND IS NOT
========================================
The serving-side ``_finite_evidence_binomial_ucb`` applies the Kish design
effect

    n_eff = n / (1 + (n - 1) * rho),  k_eff = k * n_eff / n
    UCB   = betaincinv(k_eff + 1, n_eff - k_eff, 1 - alpha)

so the bound needs the SMALLEST rho at which the UCB actually covers the
realized outcome rate — NOT the member intraclass correlation. The two are
different quantities: the 2026-07-17 measurement (cp_coverage over 3171
settled targets) found ICC rho_icc = 0.294 (high) / 0.329 (low), yet the
empirical exceedance r(k) = P(settled bin | k member hits) violates the
rho=0 bound ONLY at k in {0, 1} (r(0) = 0.066 vs UCB 0.057), and the
smallest rho achieving nominal coverage is 0.0051 (high) / 0.0580 (low,
thin: 247 targets, CI [0, 0.07]) / 0.0040 (pooled), with a walk-forward
split (train < 2026-06-14 -> rho 0.0066) showing ZERO test coverage
failures. Activating the ICC value would push the zero-hit UCB from 0.057
to ~0.60 and destroy the evidence value of 51 members with no empirical
justification. The artifact therefore stores the COVERAGE-CALIBRATED rho as
the operational ``metrics.{metric}.rho`` (read unchanged by
src/forecast/ens_member_dependence.member_dependence_rho) and keeps the ICC
as the provenance field ``rho_icc``.

CALIBRATION (this script's operational estimator)
=================================================
1. Events = freshest VERIFIED ecmwf_ens snapshot per settled
   (city, target_date, metric) triple, exactly the serving-side snapshot
   filter (authority/causality/boundary/window-attribution/extrema), joined
   to settlement_outcomes (authority='VERIFIED', settlement_value NOT NULL)
   — the settlement VALUE is consumed (the hit-vs-outcome label).
   Strict walk-forward: target_date < as_of. Null members
   (boundary-quarantined) skipped; < MIN_MEMBERS survivors dropped; per
   metric only events at the MODAL member count n are kept.
2. Members and the settled value are converted to the snapshot's SETTLEMENT
   unit and labeled by its settlement rounding policy (wmo_half_up:
   floor(x+0.5); oracle_truncate/floor: floor(x); ceil: ceil(x)) — the same
   preimage convention as settlement_semantics.settlement_preimage_offsets.
3. Coverage cells: one per (target, integer bin) over the grid
   [min member label - PAD, max member label + PAD] plus the settled bin
   (PAD = PAD_C degrees C, converted to 5 grid steps for F cities);
   k = member hits in the bin, outcome = 1 iff the settled label is the bin.
4. Per k with >= MIN_CELLS_PER_K cells and k <= k99 (99th percentile of k
   occurrence), compute the target-clustered block-bootstrap 97.5% upper
   bound of r(k) (N_BOOT resamples of whole targets, SEEDED rng — fixed
   constant seed, fully deterministic).
5. Calibrated rho = smallest rho >= 0 (binary search to RHO_TOL) such that
   boot_upper(k) <= UCB(k, n_modal, rho) for every qualifying k. Computed
   per metric AND pooled over metrics. A metric with fewer than
   MIN_TARGETS_FOR_METRIC_RHO settled targets stores
   max(metric_rho, pooled_rho) as its operational rho — its own thin-sample
   calibration may understate the needed widening, and taking the max is
   the conservative direction (larger rho => smaller n_eff => wider UCB).

ICC PROVENANCE (kept, not operational)
======================================
``rho_icc`` per metric uses the audited ANOVA moment ICC imported from
scripts/measure_member_correlation.py::_anova_icc
(statistical_calibration_authority_2026-06-12.txt Task 3.1) over
(settlement_unit, integer label) member-frequency cells, weighted by p(1-p)
with the imported P_LO/P_HI degenerate-cell filter. It measures member
CORRELATION — retained so the gap between correlation and coverage stays
visible in the artifact.

Deterministic: seeded bootstrap (fixed constant BOOT_SEED, sorted target
order), sorted iteration, overridable generated_at (--generated-at) for
byte-identity tests.

OUTPUT
======
state/ens_member_dependence/ens_member_dependence_<as_of>.json:
    {"_meta": {...},
     "pooled": {"rho_calibrated": r, "violated_k_at_zero": [...], ...},
     "metrics": {"high": {"rho": <operational calibrated>,
                          "rho_calibrated": r, "rho_icc": r|null,
                          "rho_pooled_calibrated": r,
                          "pooled_fallback_applied": bool,
                          "violated_k_at_zero": [...], "n_cells": ...,
                          "k_constraint_range": [kmin, kmax], ...}, ...}}
plus ACTIVE.json {"artifact": <fname>, "sha256": <hex>, "as_of": <as_of>}.
Consumer: src/forecast/ens_member_dependence.member_dependence_rho reads
``metrics.{metric}.rho`` unchanged (fail-open: missing/invalid artifact =>
rho 0.0 => byte-identical serving).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from scipy.special import betaincinv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Imported, not copied: the audited ANOVA moment ICC estimator + degenerate-cell
# filter bounds (statistical_calibration_authority_2026-06-12.txt Task 3.1;
# tests/calibration/test_member_correlation.py proves recovery within 0.05).
from measure_member_correlation import P_HI, P_LO, _anova_icc  # noqa: E402

FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
OUT_DIR_DEFAULT = ROOT / "state" / "ens_member_dependence"

METHOD = "coverage_calibrated_cp_rho_with_anova_icc_provenance"
ESTIMATOR_PROVENANCE = (
    "coverage calibration ported from cp_coverage measurement 2026-07-17; "
    "rho_icc via scripts/measure_member_correlation.py::_anova_icc (imported)"
)

ALPHA = 0.05  # serving _FINITE_EVIDENCE_BAND_ALPHA (95% one-sided UCB)
# Serving floor mirror: _current_evidence_shape_from_values rejects < 20 members.
MIN_MEMBERS = 20
# Same minimum-events bar as measure_member_correlation.MIN_EVENTS_AIFS (ICC lane).
MIN_EVENTS_PER_CELL = 20
# Coverage calibration constants (cp_coverage measurement parity).
PAD_C = 3             # grid padding, degrees C (5 grid steps for F cities)
MIN_CELLS_PER_K = 30  # a k needs this many cells to enter a coverage constraint
N_BOOT = 1000
BOOT_SEED = 42        # fixed constant — determinism is part of the contract
RHO_TOL = 1e-4        # binary-search tolerance for the calibrated rho
K99_PCTILE = 99.0     # constraint set capped at this percentile of k occurrence
# A metric with fewer settled targets than this stores max(own, pooled) rho
# (thin-sample calibration may understate the needed widening; max is the
# conservative direction).
MIN_TARGETS_FOR_METRIC_RHO = 500

METRICS = ("high", "low")


def load_settled_member_events(
    conn: sqlite3.Connection, *, as_of: str
) -> list[dict]:
    """Freshest settled ENS snapshot per (city, target_date, metric), walk-forward.

    Mirrors the serving-side snapshot filter (replacement_forecast_materializer
    ensemble_snapshots SELECT) and its freshness ORDER BY. The settlement join
    carries the settled VALUE (the coverage-calibration outcome label).
    Strict boundary: target_date < as_of.
    """
    rows = conn.execute(
        """
        SELECT es.city, es.target_date, es.temperature_metric,
               es.members_json, es.members_unit,
               es.settlement_unit, es.settlement_rounding_policy,
               so.settlement_value, so.settlement_unit
        FROM ensemble_snapshots es
        JOIN settlement_outcomes so
          ON lower(so.city) = lower(es.city)
         AND so.target_date = es.target_date
         AND so.temperature_metric = es.temperature_metric
         AND so.authority = 'VERIFIED'
         AND so.settlement_value IS NOT NULL
        WHERE es.source_id = 'ecmwf_open_data'
          AND es.model_version = 'ecmwf_ens'
          AND es.authority = 'VERIFIED'
          AND es.causality_status = 'OK'
          AND es.boundary_ambiguous = 0
          AND es.forecast_window_attribution_status = 'FULLY_INSIDE_TARGET_LOCAL_DAY'
          AND es.contributes_to_target_extrema = 1
          AND es.target_date < ?
        ORDER BY es.city, es.target_date, es.temperature_metric,
                 COALESCE(es.source_cycle_time, es.issue_time) DESC,
                 COALESCE(es.source_available_at, es.available_at) DESC,
                 es.snapshot_id DESC
        """,
        (as_of,),
    ).fetchall()

    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for (
        city,
        tdate,
        metric,
        members_json,
        members_unit,
        s_unit,
        s_policy,
        settle_value,
        settle_unit,
    ) in rows:
        key = (str(city).lower(), str(tdate), str(metric))
        if key in seen:
            continue  # ORDER BY puts the freshest snapshot first per triple
        seen.add(key)
        try:
            values = [float(v) for v in json.loads(members_json) if v is not None]
        except (TypeError, ValueError):
            continue
        if len(values) < MIN_MEMBERS:
            continue
        events.append(
            {
                "city": str(city),
                "target_date": str(tdate),
                "metric": str(metric).strip().lower(),
                "members": values,
                "members_unit": str(members_unit or "").strip().lower(),
                "settlement_unit": str(s_unit or "C").strip().upper(),
                "rounding_policy": str(s_policy or "wmo_half_up").strip().lower(),
                "settlement_value": float(settle_value),
                "settlement_value_unit": str(settle_unit or "C").strip().lower(),
            }
        )
    return events


def _to_settlement_unit(
    values: Sequence[float], members_unit: str, settlement_unit: str
) -> list[float] | None:
    mu = "F" if members_unit in {"degf", "f", "°f"} else (
        "C" if members_unit in {"degc", "c", "°c"} else None
    )
    su = settlement_unit if settlement_unit in {"C", "F"} else None
    if mu is None or su is None:
        return None
    if mu == su:
        return list(values)
    if mu == "C":  # -> F
        return [v * 9.0 / 5.0 + 32.0 for v in values]
    return [(v - 32.0) * 5.0 / 9.0 for v in values]  # F -> C


def _label(value: float, rounding_policy: str) -> int | None:
    """Integer settlement label under the snapshot's declared rounding policy.

    Mirrors src/contracts/settlement_semantics.settlement_preimage_offsets:
    wmo_half_up preimage of t is [t-0.5, t+0.5) <=> t = floor(x + 0.5);
    oracle_truncate/floor preimage is [t, t+1) <=> t = floor(x);
    ceil preimage is (t-1, t] <=> t = ceil(x).
    """
    if rounding_policy == "wmo_half_up":
        return int(math.floor(value + 0.5))
    if rounding_policy in {"oracle_truncate", "floor"}:
        return int(math.floor(value))
    if rounding_policy == "ceil":
        return int(math.ceil(value))
    return None


def _prep_metric_events(events: Sequence[Mapping[str, object]]) -> dict[str, list[dict]]:
    """Label members + settled value per event, grouped by metric.

    Returns {metric: [{n, unit, counts, lo, hi, out, tkey}, ...]} for events
    whose member and settlement labels are all constructible.
    """
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        metric = str(event["metric"])
        if metric not in METRICS:
            continue
        policy = str(event["rounding_policy"])
        unit = str(event["settlement_unit"])
        converted = _to_settlement_unit(
            event["members"],  # type: ignore[arg-type]
            str(event["members_unit"]),
            unit,
        )
        if converted is None:
            continue
        labels = [_label(v, policy) for v in converted]
        if any(lbl is None for lbl in labels):
            continue
        out_conv = _to_settlement_unit(
            [float(event["settlement_value"])],  # type: ignore[list-item]
            str(event["settlement_value_unit"]),
            unit,
        )
        if out_conv is None:
            continue
        out_lbl = _label(out_conv[0], policy)
        if out_lbl is None:
            continue
        by_metric[metric].append(
            {
                "n": len(labels),
                "unit": unit,
                "counts": Counter(labels),
                "lo": min(labels),  # type: ignore[type-var]
                "hi": max(labels),  # type: ignore[type-var]
                "out": out_lbl,
                "tkey": (
                    str(event["city"]).lower(),
                    str(event["target_date"]),
                    metric,
                ),
            }
        )
    return by_metric


# ---------------------------------------------------------------------------
# ICC provenance lane (NOT operational — see module docstring)
# ---------------------------------------------------------------------------

def estimate_rho_icc(used: Sequence[Mapping[str, object]], n_modal: int) -> float | None:
    """ANOVA moment ICC of member bin-indicator frequencies over label cells.

    ``used``: modal-n events of ONE metric (from _prep_metric_events). Cells are
    (settlement_unit, integer label) with explicit zeros one step beyond each
    event's own support; per cell with >= MIN_EVENTS_PER_CELL events and mean p
    in [P_LO, P_HI] (imported filter), ICC = _anova_icc(p_arr, n_modal); pooled
    by p(1-p) weight, clamped to [0, 1]. None when no usable cell.
    """
    cell_obs: dict[tuple[str, int], list[float]] = defaultdict(list)
    for e in used:
        for lbl in range(int(e["lo"]) - 1, int(e["hi"]) + 2):
            cell_obs[(str(e["unit"]), lbl)].append(
                e["counts"].get(lbl, 0) / n_modal  # type: ignore[union-attr]
            )

    icc_values: list[float] = []
    weights: list[float] = []
    for cell_key in sorted(cell_obs):
        p_arr = np.asarray(cell_obs[cell_key], dtype=float)
        if len(p_arr) < MIN_EVENTS_PER_CELL:
            continue
        p_mean = float(p_arr.mean())
        if p_mean < P_LO or p_mean > P_HI:
            continue
        icc = _anova_icc(p_arr, n_modal)
        if not np.isfinite(icc):
            continue
        icc_values.append(float(icc))
        weights.append(p_mean * (1.0 - p_mean))

    if not icc_values:
        return None
    rho = float(np.average(np.asarray(icc_values), weights=np.asarray(weights)))
    return min(max(rho, 0.0), 1.0)


# ---------------------------------------------------------------------------
# Coverage-calibration lane (OPERATIONAL)
# ---------------------------------------------------------------------------

def build_coverage_cells(
    used: Sequence[Mapping[str, object]], *, pad_c: int = PAD_C
) -> list[tuple[int, int, tuple[str, str, str]]]:
    """(k, outcome, target_key) per (target, bin) over the padded member grid.

    Grid = [min member label - pad, max member label + pad] plus the settled
    bin wherever it falls; pad is PAD_C degrees C (rounded to grid steps for
    F cities). k = member hits in the bin; outcome = 1 iff the settled label
    is the bin.
    """
    cells: list[tuple[int, int, tuple[str, str, str]]] = []
    for e in used:
        pad = int(round(pad_c * 9.0 / 5.0)) if str(e["unit"]) == "F" else int(pad_c)
        out = int(e["out"])  # type: ignore[arg-type]
        grid = set(range(int(e["lo"]) - pad, int(e["hi"]) + pad + 1))
        grid.add(out)
        for b in sorted(grid):
            cells.append(
                (
                    int(e["counts"].get(b, 0)),  # type: ignore[union-attr]
                    1 if b == out else 0,
                    e["tkey"],  # type: ignore[arg-type]
                )
            )
    return cells


def _ucb(k: float, n: float, rho: float, *, alpha: float = ALPHA) -> float:
    """The SAME dependence-corrected CP UCB the serving bound applies."""
    if k >= n:
        return 1.0
    if rho <= 0.0:
        return float(betaincinv(k + 1, n - k, 1.0 - alpha))
    n_eff = n / (1.0 + (n - 1) * rho)
    k_eff = k * n_eff / n
    return float(betaincinv(k_eff + 1.0, n_eff - k_eff, 1.0 - alpha))


def cluster_boot_upper(
    cells: Sequence[tuple[int, int, tuple[str, str, str]]],
    ks: Sequence[int],
    *,
    reps: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> dict[int, float]:
    """Target-clustered block-bootstrap 97.5% upper bound of r(k) per k.

    Whole targets are resampled with replacement (SEEDED rng, sorted target
    order — deterministic). k with no observations in a rep contributes
    nothing to that rep.
    """
    by_t: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)
    for k, o, tkey in cells:
        by_t[tkey].append((k, o))
    tkeys = sorted(by_t)
    rng = np.random.default_rng(seed)
    per_k: dict[int, list[float]] = {k: [] for k in ks}
    for _ in range(reps):
        pick = rng.choice(len(tkeys), size=len(tkeys), replace=True)
        num: dict[int, int] = defaultdict(int)
        den: dict[int, int] = defaultdict(int)
        for i in pick:
            for k, o in by_t[tkeys[i]]:
                num[k] += o
                den[k] += 1
        for k in ks:
            if den[k] > 0:
                per_k[k].append(num[k] / den[k])
    return {
        k: (float(np.percentile(per_k[k], 97.5)) if per_k[k] else float("nan"))
        for k in ks
    }


def calibrate_rho(
    cells: Sequence[tuple[int, int, tuple[str, str, str]]],
    n_modal: int,
    *,
    reps: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> dict[str, object]:
    """Smallest rho with nominal coverage over the qualifying-k constraint set.

    Qualifying k: >= MIN_CELLS_PER_K cells AND k <= the K99_PCTILE percentile
    of k occurrence. Calibrated rho = smallest rho >= 0 (binary search to
    RHO_TOL) with boot_upper(k) <= UCB(k, n_modal, rho) for every qualifying k
    (1.0 if even rho=1 cannot cover). Empty constraint set => rho 0.0 with
    k_constraint_range None (no evidence of violation — the pooled fallback in
    estimate_metrics still applies for thin metrics).
    """
    if not cells:
        return {
            "rho": 0.0,
            "violated_k_at_zero": [],
            "k_constraint_range": None,
            "n_cells": 0,
            "n_targets": 0,
        }
    counts: Counter[int] = Counter(k for k, _o, _t in cells)
    k99 = int(np.percentile([k for k, _o, _t in cells], K99_PCTILE))
    qualifying = [
        k for k in sorted(counts) if counts[k] >= MIN_CELLS_PER_K and k <= k99
    ]
    n_targets = len({t for _k, _o, t in cells})
    if not qualifying:
        return {
            "rho": 0.0,
            "violated_k_at_zero": [],
            "k_constraint_range": None,
            "n_cells": len(cells),
            "n_targets": n_targets,
        }
    upper = cluster_boot_upper(cells, qualifying, reps=reps, seed=seed)
    violated0 = [
        k
        for k in qualifying
        if math.isfinite(upper[k]) and upper[k] > _ucb(k, n_modal, 0.0)
    ]
    need = 0.0
    for k in qualifying:
        r = upper[k]
        if not math.isfinite(r):
            continue
        if _ucb(k, n_modal, 0.0) >= r:
            continue
        if _ucb(k, n_modal, 1.0) < r:
            need = 1.0
            break
        lo, hi = 0.0, 1.0
        while hi - lo > RHO_TOL:
            mid = (lo + hi) / 2.0
            if _ucb(k, n_modal, mid) >= r:
                hi = mid
            else:
                lo = mid
        need = max(need, hi)
    return {
        "rho": min(max(need, 0.0), 1.0),
        "violated_k_at_zero": violated0,
        "k_constraint_range": [qualifying[0], qualifying[-1]],
        "n_cells": len(cells),
        "n_targets": n_targets,
    }


def estimate_metrics(
    events: Sequence[Mapping[str, object]],
    *,
    reps: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> tuple[dict[str, dict], dict[str, object]]:
    """(metrics, pooled) for the artifact. See module docstring steps 1-5.

    metrics.{m}.rho is the OPERATIONAL coverage-calibrated rho (with the
    max(own, pooled) fallback for metrics under MIN_TARGETS_FOR_METRIC_RHO
    targets); rho_icc is provenance only.
    """
    by_metric = _prep_metric_events(events)

    # Modal member count per metric (ties toward the larger n); pooled cells
    # are restricted to the GLOBAL modal n so one UCB(n_modal) applies.
    used_by_metric: dict[str, list[dict]] = {}
    n_modal_by_metric: dict[str, int] = {}
    for metric, pool in by_metric.items():
        n_modal = max(
            Counter(e["n"] for e in pool).items(), key=lambda kv: (kv[1], kv[0])
        )[0]
        n_modal_by_metric[metric] = int(n_modal)
        used_by_metric[metric] = [e for e in pool if e["n"] == n_modal]
    if not used_by_metric:
        return {}, {}
    global_n_modal = max(
        Counter(
            e["n"] for used in used_by_metric.values() for e in used
        ).items(),
        key=lambda kv: (kv[1], kv[0]),
    )[0]

    pooled_cells = [
        cell
        for metric in sorted(used_by_metric)
        for cell in build_coverage_cells(
            [e for e in used_by_metric[metric] if e["n"] == global_n_modal]
        )
    ]
    pooled_cal = calibrate_rho(pooled_cells, int(global_n_modal), reps=reps, seed=seed)
    rho_pooled = float(pooled_cal["rho"])  # type: ignore[arg-type]
    pooled_out = {
        "rho_calibrated": round(rho_pooled, 6),
        "n_members": int(global_n_modal),
        "n_targets": pooled_cal["n_targets"],
        "n_cells": pooled_cal["n_cells"],
        "violated_k_at_zero": pooled_cal["violated_k_at_zero"],
        "k_constraint_range": pooled_cal["k_constraint_range"],
    }

    metrics_out: dict[str, dict] = {}
    for metric in sorted(used_by_metric):
        used = used_by_metric[metric]
        n_modal = n_modal_by_metric[metric]
        cal = calibrate_rho(build_coverage_cells(used), n_modal, reps=reps, seed=seed)
        rho_cal = float(cal["rho"])  # type: ignore[arg-type]
        n_targets = int(cal["n_targets"])  # type: ignore[arg-type]
        pooled_fallback = n_targets < MIN_TARGETS_FOR_METRIC_RHO
        rho_op = max(rho_cal, rho_pooled) if pooled_fallback else rho_cal
        rho_icc = estimate_rho_icc(used, n_modal)
        n_eff = n_modal / (1.0 + (n_modal - 1) * rho_op)
        metrics_out[metric] = {
            # OPERATIONAL — read by member_dependence_rho, applied in the CP bound.
            "rho": round(rho_op, 6),
            "rho_calibrated": round(rho_cal, 6),
            "rho_pooled_calibrated": round(rho_pooled, 6),
            "pooled_fallback_applied": pooled_fallback,
            # PROVENANCE — member correlation, NOT the coverage quantity.
            "rho_icc": None if rho_icc is None else round(rho_icc, 6),
            "n_targets": n_targets,
            "n_cells": cal["n_cells"],
            "n_members": int(n_modal),
            "n_eff": round(n_eff, 4),
            "violated_k_at_zero": cal["violated_k_at_zero"],
            "k_constraint_range": cal["k_constraint_range"],
        }
    return metrics_out, pooled_out


def build_artifact(
    conn: sqlite3.Connection, *, as_of: str, generated_at: str, git_sha: str
) -> dict[str, object]:
    events = load_settled_member_events(conn, as_of=as_of)
    metrics, pooled = estimate_metrics(events)
    dates = sorted({str(e["target_date"]) for e in events})
    return {
        "schema_version": 2,
        "_meta": {
            "method": METHOD,
            "estimator": ESTIMATOR_PROVENANCE,
            "as_of": as_of,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "data_window": (
                f"settled-{dates[0]}..{dates[-1]}" if dates else "empty"
            ),
            "n_settled_triples": len(events),
            "alpha": ALPHA,
            "min_members": MIN_MEMBERS,
            "pad_c": PAD_C,
            "min_cells_per_k": MIN_CELLS_PER_K,
            "boot_seed": BOOT_SEED,
            "boot_reps": N_BOOT,
            "rho_tol": RHO_TOL,
            "k99_pctile": K99_PCTILE,
            "min_targets_for_metric_rho": MIN_TARGETS_FOR_METRIC_RHO,
            "icc_min_events_per_cell": MIN_EVENTS_PER_CELL,
            "icc_p_filter_lo": P_LO,
            "icc_p_filter_hi": P_HI,
            "source": (
                "ensemble_snapshots(ecmwf_open_data/ecmwf_ens, VERIFIED, causality OK, "
                "boundary_ambiguous=0, FULLY_INSIDE_TARGET_LOCAL_DAY, "
                "contributes_to_target_extrema=1) freshest per settled triple, "
                "JOIN settlement_outcomes(VERIFIED, settlement_value NOT NULL), "
                "target_date < as_of"
            ),
        },
        "pooled": pooled,
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
    try:
        artifact = build_artifact(
            conn, as_of=args.as_of, generated_at=generated_at, git_sha=_git_sha()
        )
    finally:
        conn.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"ens_member_dependence_{args.as_of.replace('-', '')}.json"
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    (args.out_dir / fname).write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    pointer = {"artifact": fname, "sha256": sha, "as_of": args.as_of}
    (args.out_dir / "ACTIVE.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    metrics = artifact["metrics"]
    print(
        f"Wrote {args.out_dir / fname} (sha256={sha}); "
        + "; ".join(
            f"{m}: rho={c['rho']} (cal={c['rho_calibrated']} icc={c['rho_icc']} "
            f"pooled_fb={c['pooled_fallback_applied']}) n_eff={c['n_eff']} "
            f"n_targets={c['n_targets']}"
            for m, c in sorted(metrics.items())  # type: ignore[union-attr]
        )
        if metrics
        else f"Wrote {args.out_dir / fname} (sha256={sha}); NO usable cells"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
