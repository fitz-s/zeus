#!/usr/bin/env python3
# Created: 2026-09-26
# Last audited: 2026-09-26
# Authority basis: design review REQ-20260925-223704 §3-§5 and §8 (family CDF
#   correction, dependence, release specification, historical rows); release
#   thresholds in docs/operations/current/plans/day0_probability_repair_2026-09-25.md.
"""Offline family-calibration fitter and release-test harness.

Builds a family corpus from ``tier0_candidate_set_provenance`` (trade DB) with
exact settlement topology and VERIFIED labels (forecasts DB); runs
chronological seven-settlement-date outer folds (expanding window, whole
city-days, label_available_at < fit cutoff) with inner-fold selection over the
nested candidates of ``src.calibration.market_anchored_family``; writes one
content-addressed out-of-sample prediction ledger; resamples whole settlement
dates in moving blocks, refitting the full procedure per replicate; and reports
simultaneous one-sided studentized-max bounds for the release endpoints.

READ-ONLY: every DB is opened as a ``file:...?mode=ro`` URI and outputs go only
to ``--out``. Historical rows are diagnostics, never licensing evidence (§8):
their artifact always records ``licensing_eligible: false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.calibration import market_anchored_family as maf  # noqa: E402

STATE = Path("/Users/leofitz/zeus/state")
PRICE_ONLY_DIAGNOSTIC = "price_only"
PROXY_Q_DIAGNOSTIC = "proxy_q"
PROXY_KIND = "LATEST_PRIOR_POSTERIOR_PROXY"
P_ASK = "NORMALIZED_YES_ASK"
P_MID = "NORMALIZED_YES_MID"
# A label is usable for training only after the local target day ends plus
# this lag; it bounds every un-rewritten recorded settlement (see manifest).
LABEL_LAG = timedelta(hours=72)
FOLD_DATES = 7
BLOCKS = (3, 7)
REPLICATES = 2000
MIN_TRAIN_CITY_DAYS = 30
MIN_INNER_TEST_CITY_DAYS = 10
FLOOR_CITY_DAYS = 100
FLOOR_DATES = 14
CONFIDENCE = 0.95
SERVED_HIGH = 0.95
BANDS = (("<0.15", 0.0, 0.15), ("0.15-0.50", 0.15, 0.50), (">0.50", 0.50, math.inf))
ALL = "ALL"
# Ask overround (sum of YES asks) at or below this marks a coherent market
# reference; above it the normalized ask vector is mostly spread.
COHERENT_OVERROUND = 1.3

FAMILY_SPECS = (
    (maf.MARKET_NULL, maf.LOG_LOSS),
    (maf.ARITHMETIC, maf.LOG_LOSS),
    (maf.ARITHMETIC, maf.RPS),
    (maf.PRICE_ONLY, maf.LOG_LOSS),
    (maf.PRICE_ONLY, maf.RPS),
    (maf.BLP, maf.LOG_LOSS),
    (maf.BLP, maf.RPS),
)
PRICE_SPECS = (
    (maf.MARKET_NULL, maf.LOG_LOSS),
    (maf.PRICE_ONLY, maf.LOG_LOSS),
    (maf.PRICE_ONLY, maf.RPS),
)
# Upper-bound gates (endpoint -> (strict, threshold)); a scope licenses only
# when every parent gate passes. Children are protected slices.
PARENT_GATES = {
    "family_ll_vs_market": (True, 0.0),
    "legs_ll_vs_p0": (True, 0.0),
    "family_brier_vs_market": (False, 0.002),
    "overstatement": (False, 0.03),
    "miss90": (False, 0.13),
}
CHILD_GATES = {
    "family_ll_vs_market": (False, 0.01),
    "family_brier_vs_market": (False, 0.002),
    "legs_ll_vs_market": (False, 0.01),
}
SERVED_HIGH_CHILD = f"served>={SERVED_HIGH}"
SERVED_HIGH_OVERSTATEMENT = 0.01
FAMILY_ENDPOINTS = ("family_ll_vs_market", "family_rps_vs_market", "family_brier_vs_market", "miss90")
LEG_ENDPOINTS = ("legs_ll_vs_p0", "legs_ll_vs_market", "overstatement")


def spec_name(spec) -> str:
    return f"{spec[0]}/{spec[1]}"


def band_of(x: np.ndarray) -> np.ndarray:
    out = np.empty(len(x), dtype=object)
    for name, lo, hi in BANDS:
        out[(x >= lo) & (x < hi)] = name
    return out


# ---------------------------------------------------------------------------
# Instants: one decision cut x family, with city-day weighting codes.
# ---------------------------------------------------------------------------


class Instants:
    """City-day weighting: cuts share their UTC hour, hours share their
    metric, metrics share the city-day, so every city-day totals one."""

    def __init__(self, city, target_date, metric, decision_at):
        self.city = np.asarray(city, dtype=object)
        self.target_date = np.asarray(target_date, dtype=object)
        self.metric = np.asarray(metric, dtype=object)
        self.decision_at = np.asarray(decision_at, dtype=float)
        n = len(self.city)
        self.dates = tuple(sorted(set(self.target_date.tolist())))
        date_ix = {d: i for i, d in enumerate(self.dates)}
        self.date = np.array([date_ix[d] for d in self.target_date], dtype=np.int64)
        hour = np.floor(self.decision_at / 3600.0).astype(np.int64)
        cd, cdm, cdmh = {}, {}, {}
        cdm_parent, cdmh_parent = [], []
        self.cd = np.empty(n, dtype=np.int64)
        self.cdm = np.empty(n, dtype=np.int64)
        self.cdmh = np.empty(n, dtype=np.int64)
        for i in range(n):
            k1 = (self.city[i], self.target_date[i])
            c1 = cd.setdefault(k1, len(cd))
            k2 = k1 + (self.metric[i],)
            if k2 not in cdm:
                cdm[k2] = len(cdm)
                cdm_parent.append(c1)
            k3 = k2 + (int(hour[i]),)
            if k3 not in cdmh:
                cdmh[k3] = len(cdmh)
                cdmh_parent.append(cdm[k2])
            self.cd[i], self.cdm[i], self.cdmh[i] = c1, cdm[k2], cdmh[k3]
        self.cdm_parent = np.asarray(cdm_parent, dtype=np.int64)
        self.cdmh_parent = np.asarray(cdmh_parent, dtype=np.int64)
        self.n_cd = len(cd)

    def __len__(self) -> int:
        return len(self.city)

    def weights(self, active=None, mult=None) -> np.ndarray:
        a = np.ones(len(self), bool) if active is None else np.asarray(active, bool)
        w = np.zeros(len(self))
        if not a.any():
            return w
        n_h = np.bincount(self.cdmh[a], minlength=len(self.cdmh_parent))
        hours = np.bincount(self.cdmh_parent[n_h > 0], minlength=len(self.cdm_parent))
        metrics = np.bincount(self.cdm_parent[hours > 0], minlength=self.n_cd)
        w[a] = 1.0 / (n_h[self.cdmh[a]] * hours[self.cdm[a]] * metrics[self.cd[a]])
        if mult is not None:
            w = w * np.asarray(mult, dtype=float)[self.date]
        return w

    def evidence(self, members) -> tuple[int, int]:
        m = np.asarray(members, bool)
        return len(set(self.cd[m].tolist())), len(set(self.date[m].tolist()))


@dataclass
class Corpus:
    """Fit population: family instants with complete P (and Q) plus legs."""

    inst: Instants
    label_at: np.ndarray
    y: np.ndarray
    P: np.ndarray
    Q: np.ndarray | None
    features: maf.FamilyFeatures
    leg_instant: np.ndarray
    leg_bin: np.ndarray
    leg_side: np.ndarray
    leg_p0: np.ndarray
    leg_y: np.ndarray
    ids: np.ndarray | None = None
    overround: np.ndarray | None = None

    def __post_init__(self):
        self.P = maf.as_family_masses(self.P)
        n, k = self.P.shape
        if self.Q is not None:
            self.Q = maf.as_family_masses(self.Q)
            if self.Q.shape != self.P.shape:
                raise ValueError("Q and P must share the family topology")
        self.y = np.asarray(self.y, dtype=np.int64)
        if len(self.y) != n or len(self.inst) != n or np.any((self.y < 0) | (self.y >= k)):
            raise ValueError("labels must index the family topology")
        self.label_at = np.asarray(self.label_at, dtype=float)
        self.leg_instant = np.asarray(self.leg_instant, dtype=np.int64)
        self.leg_bin = np.asarray(self.leg_bin, dtype=np.int64)
        self.leg_side = np.asarray(self.leg_side, dtype=object)
        self.leg_p0 = np.asarray(self.leg_p0, dtype=float)
        self.leg_y = np.asarray(self.leg_y, dtype=float)
        _, derived = maf.held_side(
            np.zeros(len(self.leg_bin)), self.y[self.leg_instant] == self.leg_bin, self.leg_side
        )
        if np.any(derived != self.leg_y):
            raise ValueError("a leg's held-side label disagrees with its family label")
        self.n_legs = np.bincount(self.leg_instant, minlength=n)
        self.cell = np.array(
            [f"{m}:{lane}" for m, lane in zip(self.features.metric, self.features.lane)], dtype=object
        )
        self.fam_band = self.leg_band = None
        if self.Q is not None:
            self.fam_band = band_of(np.max(np.abs(self.Q - self.P), axis=1))
            q_held, _ = maf.held_side(self.Q[self.leg_instant, self.leg_bin], self.leg_y, self.leg_side)
            self.leg_band = band_of(np.abs(q_held - self.leg_p0))

    def scopes(self) -> dict[str, np.ndarray]:
        out = {ALL: np.ones(len(self.inst), bool)}
        for cell in sorted(set(self.cell.tolist())):
            out[cell] = self.cell == cell
        return out

    def raw(self) -> np.ndarray:
        return self.Q if self.Q is not None else self.P


# ---------------------------------------------------------------------------
# Folds.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    train: np.ndarray
    test: np.ndarray
    cutoff: float
    test_dates: tuple[str, ...]


@dataclass(frozen=True)
class Fold:
    index: int
    outer: Split
    inner: Split | None


def chronological_splits(inst: Instants, label_at, eligible, fold_dates: int, first: int):
    """Consecutive ``fold_dates``-date test folds from date index ``first``.
    Training holds only earlier city-days whose labels were available before
    the fold's earliest decision instant."""

    eligible = np.asarray(eligible, bool)
    dates = sorted(set(inst.target_date[eligible].tolist()))
    splits = []
    for s in range(first, len(dates), fold_dates):
        test_dates = tuple(dates[s:s + fold_dates])
        test = eligible & np.isin(inst.target_date, test_dates)
        cutoff = float(inst.decision_at[test].min())
        train = eligible & (inst.target_date < test_dates[0]) & (label_at < cutoff)
        splits.append(Split(train, test, cutoff, test_dates))
    return splits


def build_folds(corpus: Corpus, fold_dates: int = FOLD_DATES) -> list[Fold]:
    inst = corpus.inst
    folds = []
    outer = chronological_splits(inst, corpus.label_at, np.ones(len(inst), bool), fold_dates, fold_dates)
    for i, split in enumerate(outer):
        n_dates = len(set(inst.target_date[split.train].tolist()))
        inner = None
        if n_dates >= 2:
            inner = chronological_splits(
                inst, corpus.label_at, split.train, fold_dates, max(1, n_dates - fold_dates)
            )[-1]
        folds.append(Fold(i, split, inner))
    return folds


# ---------------------------------------------------------------------------
# Procedure: inner selection, outer fits, out-of-sample predictions.
# ---------------------------------------------------------------------------


@dataclass
class Evaluation:
    served: np.ndarray
    tested: np.ndarray
    pred: dict[str, np.ndarray]
    selected: list[str]
    reasons: list[str]
    fits: dict = field(default_factory=dict)


def _cluster_se(d, w, cluster) -> float:
    total = float(w.sum())
    mean = float(w @ d) / total
    per = np.bincount(cluster, weights=w * (d - mean))
    return math.sqrt(float(per @ per)) / total


def _fit_specs(corpus: Corpus, active, mult, specs, init, key):
    w = corpus.inst.weights(active, mult)
    rows = np.flatnonzero(w > 0)
    fits = {spec: None for spec in specs}
    if len(set(corpus.inst.cd[rows].tolist())) < MIN_TRAIN_CITY_DAYS:
        return fits, "TRAIN_BELOW_MIN_CITY_DAYS"
    design = maf.DesignSpec.from_features(corpus.features.take(np.flatnonzero(active)))
    feats = corpus.features.take(rows)
    for spec in specs:
        if spec[0] == maf.MARKET_NULL:
            continue
        fits[spec] = _fit(spec, corpus, rows, w, feats, design, (init or {}).get(key + (spec_name(spec),)))
    return fits, "FIT"


def _fit(spec, corpus: Corpus, rows, w, feats, design, prior):
    """Point fits estimate station/era scales; a replicate warm-starts from
    its point fit and keeps those scales (one MAP pass)."""

    extra = {} if prior is None else {"tau": prior.tau, "eb_steps": 0, "init": prior}
    return maf.fit_family(spec[0], corpus.raw()[rows], corpus.P[rows], corpus.y[rows], w[rows],
                          feats, objective=spec[1], design=design, **extra)


def _predict(corpus: Corpus, fit, rows) -> np.ndarray:
    if fit is None:
        return corpus.P[rows]
    return fit.predict(corpus.raw()[rows], corpus.P[rows], corpus.features.take(rows))


def _select(corpus: Corpus, fold: Fold, mult, specs, init):
    null = specs[0]
    if fold.inner is None:
        return null, "NO_INNER_FOLD", {}
    fits, status = _fit_specs(corpus, fold.inner.train, mult, specs, init, (fold.index, "inner"))
    if status != "FIT":
        return null, f"INNER_{status}", fits
    w = corpus.inst.weights(fold.inner.test, mult)
    rows = np.flatnonzero(w > 0)
    if len(set(corpus.inst.cd[rows].tolist())) < MIN_INNER_TEST_CITY_DAYS:
        return null, "INNER_TEST_BELOW_MIN_CITY_DAYS", fits
    y = corpus.y[rows]
    loss = {s: maf.categorical_log_loss(_predict(corpus, fits[s], rows), y) for s in specs}
    mean = {s: float(w[rows] @ loss[s]) / float(w[rows].sum()) for s in specs}
    best = min(specs, key=lambda s: mean[s])
    for s in specs:  # simplest spec within one date-clustered SE of the best
        se = _cluster_se(loss[s] - loss[best], w[rows], corpus.inst.date[rows]) if s != best else 0.0
        if mean[s] - mean[best] <= se:
            return s, "ONE_SE_RULE", fits
    return best, "BEST", fits


def run_procedure(corpus: Corpus, specs, folds, mult=None, init=None) -> Evaluation:
    n, k = corpus.P.shape
    pred = {spec_name(s): np.full((n, k), np.nan) for s in specs}
    served = np.full((n, k), np.nan)
    tested = np.zeros(n, bool)
    selected, reasons, fits_out = [], [], {}
    for fold in folds:
        spec, reason, inner = _select(corpus, fold, mult, specs, init)
        outer, status = _fit_specs(corpus, fold.outer.train, mult, specs, init, (fold.index, "outer"))
        rows = np.flatnonzero(fold.outer.test)
        for s in specs:
            pred[spec_name(s)][rows] = _predict(corpus, outer[s], rows)
            if inner.get(s) is not None:
                fits_out[(fold.index, "inner", spec_name(s))] = inner[s]
            if outer.get(s) is not None:
                fits_out[(fold.index, "outer", spec_name(s))] = outer[s]
        if status == "FIT":  # an untrained fold evaluates nothing
            served[rows] = pred[spec_name(spec)][rows]
            tested[rows] = True
        selected.append(spec_name(spec))
        reasons.append(reason if status == "FIT" else f"{reason};OUTER_{status}")
    return Evaluation(served, tested, pred, selected, reasons, fits_out)


# ---------------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------------


def _wmean(x, w, m) -> float:
    total = float(w[m].sum())
    return float(w[m] @ x[m]) / total if total > 0.0 else float("nan")


def key(scope: str, endpoint: str, child: str = "") -> str:
    return f"{scope}|{endpoint}|{child}"


def endpoint_values(corpus: Corpus, served, tested, mult=None, evidence=False):
    """City-day-weighted endpoint means per scope and protected child slice.

    Family endpoints weight instants; leg endpoints split an instant's weight
    across its legs. Returns values (and, on request, contributing evidence).
    """

    rows = np.flatnonzero(tested)
    w = corpus.inst.weights(tested, mult)[rows]
    R, P, y = served[rows], corpus.P[rows], corpus.y[rows]
    fam = {
        "family_ll_vs_market": maf.categorical_log_loss(R, y) - maf.categorical_log_loss(P, y),
        "family_rps_vs_market": maf.ranked_probability_score(R, y) - maf.ranked_probability_score(P, y),
        "family_brier_vs_market": maf.full_simplex_brier(R, y) - maf.full_simplex_brier(P, y),
        "miss90": maf.prediction_set_miss(R, y),
    }
    pos = np.full(len(corpus.inst), -1)
    pos[rows] = np.arange(len(rows))
    legs = np.flatnonzero(tested[corpus.leg_instant])
    lr = pos[corpus.leg_instant[legs]]
    lb, side = corpus.leg_bin[legs], corpus.leg_side[legs]
    y_yes = (y[lr] == lb).astype(float)
    r_h, y_h = maf.held_side(R[lr, lb], y_yes, side)
    p_h, _ = maf.held_side(P[lr, lb], y_yes, side)
    lw = w[lr] / corpus.n_legs[corpus.leg_instant[legs]]
    leg = {
        "legs_ll_vs_p0": maf.binary_log_loss(r_h, y_h) - maf.binary_log_loss(corpus.leg_p0[legs], y_h),
        "legs_ll_vs_market": maf.binary_log_loss(r_h, y_h) - maf.binary_log_loss(p_h, y_h),
        "overstatement": r_h - y_h,
    }
    modal = np.argmax(R, axis=1)[lr]
    leg_children = {
        "side:YES": side == "YES",
        "side:NO": side == "NO",
        SERVED_HIGH_CHILD: r_h >= SERVED_HIGH,
        "modal_bin_NO": (side == "NO") & (lb == modal),
    }
    fam_children = {}
    if corpus.fam_band is not None:
        for name, _, _ in BANDS:
            fam_children[f"band:{name}"] = corpus.fam_band[rows] == name
            leg_children[f"legband:{name}"] = corpus.leg_band[legs] == name
    values, ev = {}, {}
    inst_rows_of_legs = corpus.leg_instant[legs]

    def record(k, x, weights, members, inst_members):
        values[k] = _wmean(x, weights, members)
        if evidence:
            ev[k] = corpus.inst.evidence(inst_members) if values[k] == values[k] else (0, 0)

    for scope, smask in corpus.scopes().items():
        s_rows, s_legs = smask[rows], smask[corpus.leg_instant[legs]]
        for e, x in fam.items():
            m = s_rows & (w > 0)
            record(key(scope, e), x, w, m, _inst_mask(corpus, rows[m]))
            for child, cm in fam_children.items():
                mc = m & cm
                record(key(scope, e, child), x, w, mc, _inst_mask(corpus, rows[mc]))
        for e, x in leg.items():
            m = s_legs & (lw > 0)
            record(key(scope, e), x, lw, m, _inst_mask(corpus, inst_rows_of_legs[m]))
            for child, cm in leg_children.items():
                mc = m & cm
                record(key(scope, e, child), x, lw, mc, _inst_mask(corpus, inst_rows_of_legs[mc]))
    return (values, ev) if evidence else values


def _inst_mask(corpus: Corpus, idx) -> np.ndarray:
    m = np.zeros(len(corpus.inst), bool)
    m[idx] = True
    return m


CANDIDATE_ENDPOINTS = ("family_ll_vs_market", "family_rps_vs_market", "legs_ll_vs_p0", "legs_ll_vs_market")


def candidate_gate(k: str):
    """Per-candidate comparison family: does this candidate beat the market
    (or p0) on the parent scope or a |q-p| band? Upper bound < 0 = PASS."""

    _, rest = k.split("#", 1)
    _, endpoint, child = rest.split("|")
    if endpoint in CANDIDATE_ENDPOINTS and (not child or "band:" in child):
        return (True, 0.0)
    return None


def gate_of(k: str):
    scope, endpoint, child = k.split("|")
    if not child:
        return PARENT_GATES.get(endpoint)
    if child == SERVED_HIGH_CHILD and endpoint == "overstatement":
        return (False, SERVED_HIGH_OVERSTATEMENT)
    return CHILD_GATES.get(endpoint)


def simultaneous_upper(point: dict, reps: np.ndarray, keys: list[str], evidence: dict,
                       gated: list[str], floor=(FLOOR_CITY_DAYS, FLOOR_DATES), gate=None,
                       min_finite: float = 1.0) -> dict:
    """Studentized-max simultaneous one-sided upper bounds over ``gated``.

    An endpoint enters the family only with the evidence floor, finite values
    in at least ``min_finite`` of the replicates and positive spread;
    otherwise it is INSUFFICIENT_EVIDENCE or DEGENERATE and never passes.
    The licensing family uses ``min_finite=1``: any empty resample denies.
    """

    col = {k: j for j, k in enumerate(keys)}
    out, family = {}, []
    for k in gated:
        theta = point[k]
        x = reps[:, col[k]]
        cds, dates = evidence.get(k, (0, 0))
        entry = {"theta": theta, "city_days": cds, "dates": dates}
        if cds < floor[0] or dates < floor[1] or not math.isfinite(theta):
            entry["status"] = "INSUFFICIENT_EVIDENCE"
        elif np.mean(np.isfinite(x)) < min_finite:
            entry["status"] = "INSUFFICIENT_EVIDENCE"
            entry["nonfinite_replicates"] = int(np.sum(~np.isfinite(x)))
        else:
            entry["nonfinite_replicates"] = int(np.sum(~np.isfinite(x)))
            se = float(np.nanstd(x, ddof=1))
            entry["se"] = se
            if se <= 0.0:
                entry["status"] = "DEGENERATE"
            else:
                family.append(k)
        out[k] = entry
    c = float("nan")
    if family:
        idx = [col[k] for k in family]
        th = np.array([point[k] for k in family])
        se = np.array([out[k]["se"] for k in family])
        z = np.nanmax((th[None, :] - reps[:, idx]) / se[None, :], axis=1)
        c = float(np.quantile(z[np.isfinite(z)], CONFIDENCE))
    for k in family:
        e = out[k]
        e["upper"] = e["theta"] + c * e["se"]
        strict, thr = (gate or gate_of)(k)
        ok = e["upper"] < thr if strict else e["upper"] <= thr
        e["status"] = "PASS" if ok else "FAIL"
        e["threshold"] = thr
    return {"critical_value": c, "family_size": len(family), "endpoints": out}


def decide(bounds: dict, scopes) -> dict:
    """Per-scope verdict: LICENSE needs every parent gate PASS; children that
    FAIL are blocked and never ride on the parent; unproved children are
    excluded from the license."""

    ends = bounds["endpoints"]
    out = {}
    for scope in scopes:
        parent = {e: ends.get(key(scope, e), {"status": "MISSING"})["status"] for e in PARENT_GATES}
        floor = ends.get(key(scope, "family_ll_vs_market"), {}).get("status")
        children = defaultdict(list)
        for k, e in ends.items():
            s, endpoint, child = k.split("|")
            if s == scope and child:
                children[child].append(e["status"])
        blocked = sorted(c for c, st in children.items() if "FAIL" in st)
        licensed = sorted(c for c, st in children.items() if all(x == "PASS" for x in st))
        unproved = sorted(set(children) - set(blocked) - set(licensed))
        if floor == "INSUFFICIENT_EVIDENCE":
            verdict = "INSUFFICIENT_EVIDENCE"
        elif all(st == "PASS" for st in parent.values()):
            verdict = "LICENSE"
        else:
            verdict = "DENY"
        out[scope] = {
            "verdict": verdict,
            "parent_gates": parent,
            "blocked_children": blocked,
            "licensed_children": licensed if verdict == "LICENSE" else [],
            "excluded_unproved_children": unproved,
        }
    return out


# ---------------------------------------------------------------------------
# Parameters per metric x lane cell.
# ---------------------------------------------------------------------------


def parameter_cells(corpus: Corpus, specs, mult=None, init=None) -> tuple[dict, dict]:
    """Full-sample fitted parameters per cell (city-day-weighted means) plus
    the unpooled constrained arithmetic weight and loss slope at w = 0."""

    w = corpus.inst.weights(None, mult)
    rows = np.flatnonzero(w > 0)
    design = maf.DesignSpec.from_features(corpus.features)
    feats = corpus.features.take(rows)
    out, fits = {}, {}
    cells = [ALL] + sorted(set(corpus.cell.tolist()))
    masks = {cell: (corpus.cell == cell) if cell != ALL else np.ones(len(w), bool) for cell in cells}
    if corpus.overround is not None:
        coherent = corpus.overround <= COHERENT_OVERROUND
        for cell in cells:
            masks[f"{cell}@coherent"] = masks[cell] & coherent
            masks[f"{cell}@incoherent"] = masks[cell] & ~coherent
    for spec in specs:
        if spec[0] == maf.MARKET_NULL or spec[1] != maf.LOG_LOSS:
            continue
        name = spec_name(spec)
        fit = _fit(spec, corpus, rows, w, feats, design, (init or {}).get(("full", name)))
        fits[("full", name)] = fit
        params = dict(zip(("w", "a", "b"), fit.parameters(corpus.features)))
        for p in maf.FREE_PARAMETERS[spec[0]]:
            for cell in cells:
                out[f"{name}|{p}|{cell}"] = _wmean(params[p], w, masks[cell])
    if corpus.Q is not None:
        slope = maf.loss_slope_at_market(corpus.Q, corpus.P, corpus.y)
        for cell, m in masks.items():
            r = np.flatnonzero(m & (w > 0))
            out[f"unpooled|w|{cell}"] = (
                maf.fit_arithmetic_weight(corpus.Q[r], corpus.P[r], corpus.y[r], w[r]) if len(r) else float("nan")
            )
            out[f"slope|w0|{cell}"] = _wmean(slope, w, m)
    return out, fits


# ---------------------------------------------------------------------------
# Resampling.
# ---------------------------------------------------------------------------


def calendar_dates(dates) -> list[str]:
    lo, hi = date.fromisoformat(min(dates)), date.fromisoformat(max(dates))
    return [(lo + timedelta(days=i)).isoformat() for i in range((hi - lo).days + 1)]


def block_multiplicities(n_dates: int, block: int, rng) -> np.ndarray:
    block = min(block, n_dates)
    starts = rng.integers(0, n_dates - block + 1, size=math.ceil(n_dates / block))
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n_dates]
    return np.bincount(idx, minlength=n_dates).astype(float)


@dataclass
class Context:
    corpus: Corpus
    specs: tuple
    folds: list
    init: dict
    keys: list
    param_keys: list
    report_keys: list
    seed: int


def _mult_for(ctx: Context, block: int, r: int) -> np.ndarray:
    cal = calendar_dates(ctx.corpus.inst.dates)
    rng = np.random.default_rng([ctx.seed, block, r])
    m = block_multiplicities(len(cal), block, rng)
    ix = {d: i for i, d in enumerate(cal)}
    return np.array([m[ix[d]] for d in ctx.corpus.inst.dates])


def replicate(ctx: Context, block: int, r: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mult = _mult_for(ctx, block, r)
    ev = run_procedure(ctx.corpus, ctx.specs, ctx.folds, mult, ctx.init)
    vals = endpoint_values(ctx.corpus, ev.served, ev.tested, mult)
    params, _ = parameter_cells(ctx.corpus, ctx.specs, mult, ctx.init)
    report = {}
    for s in ctx.specs:
        v = endpoint_values(ctx.corpus, ev.pred[spec_name(s)], ev.tested, mult)
        report.update({f"{spec_name(s)}#{k}": x for k, x in v.items()})
    return (
        np.array([vals.get(k, np.nan) for k in ctx.keys]),
        np.array([params.get(k, np.nan) for k in ctx.param_keys]),
        np.array([report.get(k, np.nan) for k in ctx.report_keys]),
    )


_WORKER_CTX: Context | None = None


def _init_worker(ctx: Context) -> None:
    global _WORKER_CTX
    _WORKER_CTX = ctx


def _run_chunk(args):
    block, indices = args
    return [replicate(_WORKER_CTX, block, r) for r in indices]


def run_replicates(ctx: Context, block: int, n: int, workers: int):
    chunks = [(block, list(range(i, min(n, i + 25)))) for i in range(0, n, 25)]
    if workers <= 1:
        _init_worker(ctx)
        results = [x for c in chunks for x in _run_chunk(c)]
    else:
        with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(ctx,)) as pool:
            results = [x for part in pool.map(_run_chunk, chunks) for x in part]
    return tuple(np.vstack([res[i] for res in results]) for i in range(3))


# ---------------------------------------------------------------------------
# Release evaluation (point + resampling + bounds + verdicts).
# ---------------------------------------------------------------------------


def evaluate_release(corpus: Corpus, specs, *, replicates=REPLICATES, blocks=BLOCKS,
                     workers=1, seed=20260926, fold_dates=FOLD_DATES) -> dict:
    folds = build_folds(corpus, fold_dates)
    point = run_procedure(corpus, specs, folds)
    values, evidence = endpoint_values(corpus, point.served, point.tested, evidence=True)
    params, full_fits = parameter_cells(corpus, specs)
    report = {}
    report_evidence = {}
    for s in specs:
        v, e = endpoint_values(corpus, point.pred[spec_name(s)], point.tested, evidence=True)
        report.update({f"{spec_name(s)}#{k}": x for k, x in v.items()})
        report_evidence.update({f"{spec_name(s)}#{k}": x for k, x in e.items()})
    keys = sorted(values)
    ctx = Context(corpus, tuple(specs), folds, {**point.fits, **full_fits}, keys,
                  sorted(params), sorted(report), seed)
    gated = [k for k in keys if gate_of(k) is not None]
    scopes = list(corpus.scopes())
    by_block = {}
    for block in blocks:
        reps, preps, rreps = run_replicates(ctx, block, replicates, workers)
        bounds = simultaneous_upper(values, reps, keys, evidence, gated)
        slope_keys = [k for k in ctx.param_keys if k.startswith("slope|")]
        slope = _slope_bounds(params, preps, ctx.param_keys, slope_keys)
        cand_keys = [k for k in ctx.report_keys if candidate_gate(k) is not None]
        by_block[str(block)] = {
            "bounds": bounds,
            "candidate_bounds": simultaneous_upper(report, rreps, ctx.report_keys, report_evidence,
                                                   cand_keys, floor=(1, 1), gate=candidate_gate,
                                                   min_finite=0.95),
            "verdicts": decide(bounds, scopes),
            "parameters": _percentiles(params, preps, ctx.param_keys),
            "slope_at_market": slope,
            "per_candidate": _percentiles(report, rreps, ctx.report_keys),
            "g_eff": _g_eff(corpus, point, reps, keys),
        }
    return {
        "folds": [_fold_record(corpus, f, point, i) for i, f in enumerate(folds)],
        "point": point,
        "values": values,
        "evidence": evidence,
        "report_evidence": report_evidence,
        "parameters": params,
        "by_block": by_block,
    }


def _slope_bounds(params, preps, keys, slope_keys) -> dict:
    """Simultaneous one-sided upper bounds on the log-loss slope at w = 0;
    an upper bound below zero means a positive weight lowers loss."""

    col = {k: j for j, k in enumerate(keys)}
    idx = [col[k] for k in slope_keys if np.all(np.isfinite(preps[:, col[k]]))]
    if not idx:
        return {}
    th = np.array([params[keys[j]] for j in idx])
    se = np.std(preps[:, idx], axis=0, ddof=1)
    c = float(np.quantile(np.max((th - preps[:, idx]) / se, axis=1), CONFIDENCE))
    return {keys[j]: {"theta": float(t), "se": float(s), "upper": float(t + c * s)}
            for j, t, s in zip(idx, th, se)} | {"critical_value": c}


def _percentiles(point: dict, reps: np.ndarray, keys: list[str]) -> dict:
    out = {}
    for j, k in enumerate(keys):
        x = reps[:, j]
        fin = x[np.isfinite(x)]
        entry = {"theta": point.get(k), "finite_replicates": int(len(fin))}
        if len(fin):
            entry.update({
                "p025": float(np.quantile(fin, 0.025)),
                "p05": float(np.quantile(fin, 0.05)),
                "p975": float(np.quantile(fin, 0.975)),
                "share_zero": float(np.mean(fin == 0.0)),
            })
        out[k] = entry
    return out


def _g_eff(corpus: Corpus, point: Evaluation, reps, keys) -> dict:
    """G x Var_iid / Var_date_block for the family log-loss difference."""

    rows = np.flatnonzero(point.tested)
    w = corpus.inst.weights(point.tested)[rows]
    d = (maf.categorical_log_loss(point.served[rows], corpus.y[rows])
         - maf.categorical_log_loss(corpus.P[rows], corpus.y[rows]))
    out = {}
    col = {k: j for j, k in enumerate(keys)}
    for scope, smask in corpus.scopes().items():
        m = smask[rows]
        cd = corpus.inst.cd[rows][m]
        if not m.any():
            continue
        wg = np.bincount(cd, weights=w[m])
        dg = np.bincount(cd, weights=w[m] * d[m])
        keep = wg > 0
        wg, dg = wg[keep], dg[keep] / wg[keep]
        g = len(wg)
        mean = float(wg @ dg) / float(wg.sum())
        var_iid = float((wg ** 2) @ ((dg - mean) ** 2)) / float(wg.sum()) ** 2
        x = reps[:, col[key(scope, "family_ll_vs_market")]]
        var_block = float(np.var(x[np.isfinite(x)], ddof=1)) if np.sum(np.isfinite(x)) > 1 else float("nan")
        out[scope] = {"city_days": g, "var_iid": var_iid, "var_date_block": var_block,
                      "g_eff": g * var_iid / var_block if var_block > 0 else None}
    return out


def _fold_record(corpus: Corpus, fold: Fold, point: Evaluation, i: int) -> dict:
    inst = corpus.inst

    def iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    rec = {
        "index": fold.index,
        "test_dates": list(fold.outer.test_dates),
        "fit_cutoff": iso(fold.outer.cutoff),
        "train_city_days": inst.evidence(fold.outer.train)[0],
        "train_dates": inst.evidence(fold.outer.train)[1],
        "test_city_days": inst.evidence(fold.outer.test)[0],
        "selected": point.selected[i],
        "selection_reason": point.reasons[i],
    }
    if fold.inner is not None:
        rec["inner"] = {
            "test_dates": list(fold.inner.test_dates),
            "fit_cutoff": iso(fold.inner.cutoff),
            "train_city_days": inst.evidence(fold.inner.train)[0],
            "test_city_days": inst.evidence(fold.inner.test)[0],
        }
    rec["outer_parameters"] = {}
    for (fi, stage, name), fit in point.fits.items():
        if fi == fold.index and stage == "outer":
            w = inst.weights(fold.outer.train)
            params = dict(zip(("w", "a", "b"), fit.parameters(corpus.features)))
            rec["outer_parameters"][name] = {
                p: {cell: _wmean(params[p], w, (corpus.cell == cell) & (w > 0))
                    for cell in sorted(set(corpus.cell.tolist()))}
                for p in maf.FREE_PARAMETERS[fit.candidate]
            }
    return rec


# ---------------------------------------------------------------------------
# Content-addressed outputs.
# ---------------------------------------------------------------------------


def _clean(x):
    """JSON-safe copy: numpy scalars/arrays unwrapped, non-finite floats -> null."""

    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


def canonical_bytes(obj) -> bytes:
    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_content_addressed(out_dir: Path, stem: str, suffix: str, data: bytes) -> tuple[Path, str]:
    """Write ``data`` once under its sha256; an existing file must match."""

    sha = hashlib.sha256(data).hexdigest()
    path = out_dir / f"{stem}_{sha[:16]}{suffix}"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise RuntimeError(f"content-addressed file collides with different bytes: {path}")
        return path, sha
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path, sha


def ledger_bytes(corpus: Corpus, result: dict) -> bytes:
    point, folds = result["point"], result["folds"]
    fold_of = np.full(len(corpus.inst), -1)
    dates_to_fold = {d: f["index"] for f in folds for d in f["test_dates"]}
    for i, d in enumerate(corpus.inst.target_date):
        fold_of[i] = dates_to_fold.get(d, -1)
    w = corpus.inst.weights(point.tested)
    legs_by = defaultdict(list)
    for j in range(len(corpus.leg_instant)):
        legs_by[int(corpus.leg_instant[j])].append(
            [int(corpus.leg_bin[j]), corpus.leg_side[j], float(corpus.leg_p0[j]), float(corpus.leg_y[j])]
        )
    lines = []
    for i in np.flatnonzero(point.tested):
        f = int(fold_of[i])
        lines.append(canonical_bytes({
            "id": None if corpus.ids is None else corpus.ids[i],
            "fold": f,
            "city": corpus.inst.city[i],
            "target_date": corpus.inst.target_date[i],
            "metric": corpus.inst.metric[i],
            "cell": corpus.cell[i],
            "decision_at": datetime.fromtimestamp(corpus.inst.decision_at[i], timezone.utc).isoformat(),
            "weight": float(w[i]),
            "y": int(corpus.y[i]),
            "P": corpus.P[i],
            "Q": None if corpus.Q is None else corpus.Q[i],
            "selected": point.selected[f],
            "served": point.served[i],
            "pred": {k: v[i] for k, v in point.pred.items()},
            "legs": legs_by[int(i)],
        }))
    return b"\n".join(lines) + b"\n"


# ---------------------------------------------------------------------------
# Live-DB corpus (read-only).
# ---------------------------------------------------------------------------


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=1")
    return conn


def _ts(text: str) -> float:
    s = str(text).replace("Z", "+00:00").replace(" ", "T")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


@dataclass
class LiveCorpus:
    """Every valid instant (for p0 reliability) and the fit corpus."""

    manifest: dict
    legs: dict
    corpus: Corpus | None
    proxy_records: list


def load_live_corpus(trade, forecast, cities, start: str, end: str, *, market: str = P_ASK,
                     proxy: bool = False, max_overround: float | None = None) -> LiveCorpus:
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.types.market import Bin

    man = Counter()
    rows = trade.execute(
        """
        SELECT selection_epoch_identity, decision_at_utc, city, target_date, family_key,
               side, action, p0, market_key, settled_y
          FROM tier0_candidate_set_provenance
         WHERE target_date BETWEEN ? AND ? AND p0 IS NOT NULL AND settled_y IS NOT NULL
        """,
        (start, end),
    ).fetchall()
    man["candidate_rows_p0_label"] = len(rows)
    legs, conflicts, row_keys = {}, set(), Counter()
    for epoch, at, city, td, fk, side, action, p0, mk, sy in rows:
        if action != "BUY":
            man["excluded_sell_rows"] += 1
            continue
        k = (epoch, fk, mk, side)
        row_keys[(epoch, fk)] += 1
        if k in legs:
            man["duplicate_leg_rows"] += 1
            if legs[k][3:] != (float(p0), int(sy)):
                conflicts.add(k)
            continue
        legs[k] = (at, city, td, float(p0), int(sy))
    for k in conflicts:
        legs.pop(k)
    man["duplicate_leg_conflicts"] = len(conflicts)
    man["unique_buy_legs"] = len(legs)

    events = {}
    for chunk in _chunks({k[2] for k in legs}):
        for cid, city, td, metric in forecast.execute(
            "SELECT condition_id, city, target_date, temperature_metric FROM market_events "
            "WHERE condition_id IN (SELECT value FROM json_each(?))", (json.dumps(chunk),)):
            events[cid] = (city, td, metric)
    families = sorted(set(events.values()))
    topo, labels = {}, {}
    for fam in families:
        bins = {}
        for cid, lo, hi, label in forecast.execute(
            "SELECT condition_id, range_low, range_high, range_label FROM market_events "
            "WHERE city=? AND target_date=? AND temperature_metric=?", fam):
            bins[cid] = (lo, hi, label)
        order = sorted(bins, key=lambda c: (-math.inf if bins[c][0] is None else bins[c][0],
                                            math.inf if bins[c][1] is None else bins[c][1]))
        spans = [bins[c][:2] for c in order]
        ok = (len(spans) >= 2 and spans[0][0] is None and spans[-1][1] is None
              and all(a[1] is not None and b[0] is not None and b[0] == a[1] + 1
                      for a, b in zip(spans, spans[1:])))
        if ok:
            topo[fam] = (tuple(order), tuple(bins[c][2] for c in order), tuple(spans))
    man["families"] = len(families)
    man["families_topology_invalid"] = len(families) - len(topo)

    recorded = {}
    for fam in topo:
        row = forecast.execute(
            "SELECT settlement_value, settlement_unit, recorded_at FROM settlement_outcomes "
            "WHERE city=? AND target_date=? AND temperature_metric=? AND authority='VERIFIED'", fam
        ).fetchone()
        city = cities.get(fam[0])
        if row is None or city is None:
            continue
        sem = SettlementSemantics.for_city(city)
        if str(row[1] or "").upper() != sem.measurement_unit:
            man["label_unit_mismatch"] += 1
            continue
        value = sem.assert_settlement_value(float(row[0]), context="fit_candidate_calibration")
        hits = [i for i, (lo, hi) in enumerate(topo[fam][2]) if Bin(lo, hi, sem.measurement_unit).contains(value)]
        if len(hits) != 1:
            man["label_bin_ambiguous"] += 1
            continue
        labels[fam] = hits[0]
        recorded[fam] = row[2]
    man["families_labelled"] = len(labels)

    by_instant = defaultdict(list)
    for (epoch, fk, mk, side), v in legs.items():
        by_instant[(epoch, fk)].append((mk, side) + v)
    inst_rows, leg_rows = [], []
    for (epoch, fk), items in sorted(by_instant.items()):
        fams = {events.get(mk) for mk, *_ in items}
        if len(fams) != 1 or None in fams:
            man["instants_family_ambiguous"] += 1
            continue
        fam = fams.pop()
        if fam not in topo or fam not in labels:
            man["instants_unlabelled_or_invalid_topology"] += 1
            continue
        city = cities[fam[0]]
        at = {_ts(it[2]) for it in items}
        if len(at) != 1 or any((it[3], it[4]) != fam[:2] for it in items):
            man["instants_identity_mismatch"] += 1
            continue
        decided = at.pop()
        tz = ZoneInfo(city.timezone)
        day_end = datetime.combine(date.fromisoformat(fam[1]) + timedelta(days=1), time(0), tz)
        hours = (day_end.timestamp() - decided) / 3600.0
        if hours <= 0:
            man["instants_after_local_day_end"] += 1
            continue
        local_date = datetime.fromtimestamp(decided, tz).date().isoformat()
        order = {c: i for i, c in enumerate(topo[fam][0])}
        y = labels[fam]
        kept = []
        for mk, side, _at, _c, _td, p0, sy in items:
            b = order[mk]
            held = int(b == y) if side == "YES" else int(b != y)
            if held != sy:
                man["legs_label_mismatch"] += 1
                continue
            kept.append((b, side, p0, sy))
        if not kept:
            continue
        idx = len(inst_rows)
        inst_rows.append({
            "id": f"{epoch}:{fk}", "fam": fam, "decided": decided, "hours": hours,
            "lane": maf.DAY0 if local_date == fam[1] else maf.FORECAST,
            "label_at": (day_end + LABEL_LAG).timestamp(), "y": y, "legs": kept,
            "rows": row_keys[(epoch, fk)],
        })
        leg_rows.extend((idx,) + leg for leg in kept)
    man["instants_valid"] = len(inst_rows)
    man["legs_valid"] = len(leg_rows)
    man["city_days_valid"] = len({(r["fam"][0], r["fam"][1]) for r in inst_rows})

    lag_hours = []
    for fam, rec in recorded.items():
        if fam[1] >= "2026-09-12" and rec:
            tz = ZoneInfo(cities[fam[0]].timezone)
            end_ = datetime.combine(date.fromisoformat(fam[1]) + timedelta(days=1), time(0), tz)
            lag_hours.append((_ts(rec) - end_.timestamp()) / 3600.0)
    label_lag = {
        "rule_hours": LABEL_LAG.total_seconds() / 3600.0,
        "note": "recorded_at for target dates <= 2026-09-11 was rewritten by the 2026-09-13 "
                "reconstruction; coverage is measured on later, original records",
        "original_records": len(lag_hours),
        "max_observed_hours": max(lag_hours) if lag_hours else None,
        "share_within_rule": (float(np.mean(np.array(lag_hours) <= LABEL_LAG.total_seconds() / 3600.0))
                              if lag_hours else None),
    }

    # Market reference per instant.
    for r in inst_rows:
        k = len(topo[r["fam"]][0])
        ask_yes, ask_no = np.full(k, np.nan), np.full(k, np.nan)
        for b, side, p0, _ in r["legs"]:
            (ask_yes if side == "YES" else ask_no)[b] = p0
        r["P_ask"] = ask_yes / ask_yes.sum() if np.all(ask_yes > 0) else None
        r["overround"] = float(ask_yes.sum()) if np.all(ask_yes > 0) else None
        bid = 1.0 - ask_no
        mid = (ask_yes + bid) / 2.0
        r["P_mid"] = (mid / mid.sum() if np.all(np.isfinite(mid)) and np.all(bid <= ask_yes)
                      and np.all(mid > 0) else None)
    man["instants_P_ask"] = sum(r["P_ask"] is not None for r in inst_rows)
    man["instants_P_mid"] = sum(r["P_mid"] is not None for r in inst_rows)

    proxy_records = []
    if proxy:
        proxy_records = _join_proxy(forecast, inst_rows, topo, man)

    legs_table = {
        "city": [inst_rows[i]["fam"][0] for i, *_ in leg_rows],
        "target_date": [inst_rows[i]["fam"][1] for i, *_ in leg_rows],
        "metric": [inst_rows[i]["fam"][2] for i, *_ in leg_rows],
        "lane": [inst_rows[i]["lane"] for i, *_ in leg_rows],
        "hours": [inst_rows[i]["hours"] for i, *_ in leg_rows],
        "overround": [inst_rows[i]["overround"] or math.nan for i, *_ in leg_rows],
        "instant": [i for i, *_ in leg_rows],
        "side": [s for _, _, s, _, _ in leg_rows],
        "p0": [p for _, _, _, p, _ in leg_rows],
        "y": [y for *_, y in leg_rows],
        "inst": Instants([r["fam"][0] for r in inst_rows], [r["fam"][1] for r in inst_rows],
                         [r["fam"][2] for r in inst_rows], [r["decided"] for r in inst_rows]),
        "inst_rows": inst_rows,
        "topology": topo,
    }
    key_P = "P_ask" if market == P_ASK else "P_mid"
    use = [i for i, r in enumerate(inst_rows) if r[key_P] is not None and (not proxy or r.get("Q") is not None)
           and (max_overround is None or (r["overround"] or math.inf) <= max_overround)]
    man["fit_instants"] = len(use)
    man["fit_candidate_rows"] = sum(inst_rows[i]["rows"] for i in use)
    corpus = _corpus(inst_rows, use, key_P, proxy, cities, topo) if use else None
    return LiveCorpus({"counts": dict(man), "label_lag": label_lag, "market_reference": market},
                      legs_table, corpus, proxy_records)


def _join_proxy(forecast, inst_rows, topo, man) -> list[dict]:
    """Latest live posterior computed before each decision instant for the
    same family; stored separately with provenance_kind=PROXY_KIND."""

    by_fam = defaultdict(list)
    for i, r in enumerate(inst_rows):
        by_fam[r["fam"]].append(i)
    chosen = {}
    for fam, idx in by_fam.items():
        posts = forecast.execute(
            "SELECT posterior_id, computed_at FROM forecast_posteriors "
            "WHERE runtime_layer='live' AND city=? AND target_date=? AND temperature_metric=? "
            "ORDER BY computed_at", fam).fetchall()
        times = np.array([_ts(c) for _, c in posts])
        for i in idx:
            j = int(np.searchsorted(times, inst_rows[i]["decided"], side="left")) - 1
            if j >= 0:
                chosen[i] = int(posts[j][0])
    meta = {}
    for chunk in _chunks(set(chosen.values())):
        for pid, computed, qj, shape, dv, ident, cycle in forecast.execute(
            "SELECT posterior_id, computed_at, q_json, q_shape, data_version, "
            "posterior_identity_hash, source_cycle_time FROM forecast_posteriors "
            "WHERE posterior_id IN (SELECT value FROM json_each(?))", (json.dumps(chunk),)):
            meta[pid] = (computed, json.loads(qj), shape, dv, ident, cycle)
    records = []
    for i, r in enumerate(inst_rows):
        pid = chosen.get(i)
        if pid is None:
            man["proxy_no_prior_posterior"] += 1
            continue
        computed, qmap, shape, dv, ident, cycle = meta[pid]
        vec = [qmap.get(label) for label in topo[r["fam"]][1]]
        if any(v is None for v in vec):
            man["proxy_label_missing"] += 1
            continue
        q = np.array(vec, dtype=float)
        if not np.all(np.isfinite(q)) or np.any(q < 0) or abs(q.sum() - 1.0) > 1e-3:
            man["proxy_invalid_vector"] += 1
            continue
        r["Q"] = q / q.sum()
        r["q_shape"] = shape or "UNKNOWN"
        records.append({
            "provenance_kind": PROXY_KIND,
            "instant_id": r["id"],
            "city": r["fam"][0], "target_date": r["fam"][1], "metric": r["fam"][2],
            "decision_at": datetime.fromtimestamp(r["decided"], timezone.utc).isoformat(),
            "posterior_id": pid, "posterior_identity_hash": ident,
            "computed_at": computed, "source_cycle_time": cycle,
            "gap_seconds": r["decided"] - _ts(computed),
            "q_shape": shape, "data_version": dv,
            "bin_labels": list(topo[r["fam"]][1]), "q": r["Q"].tolist(),
        })
    man["proxy_joined_instants"] = len(records)
    man["proxy_joined_candidate_rows"] = sum(inst_rows[i]["rows"] for i in range(len(inst_rows))
                                             if inst_rows[i].get("Q") is not None)
    return records


def _corpus(inst_rows, use, key_P, proxy, cities, topo) -> Corpus:
    from src.contracts.settlement_semantics import SettlementSemantics

    rows = [inst_rows[i] for i in use]
    ks = {len(r[key_P]) for r in rows}
    if len(ks) != 1:
        raise ValueError(f"fit corpus mixes family sizes {sorted(ks)}")
    res = []
    for r in rows:
        spans = topo[r["fam"]][2]
        widths = Counter(int(hi - lo + 1) for lo, hi in spans[1:-1])
        city = cities[r["fam"][0]]
        sem = SettlementSemantics.for_city(city)
        res.append(f"{sem.measurement_unit}{widths.most_common(1)[0][0]}:{sem.rounding_rule}")
    feats = maf.FamilyFeatures(
        metric=np.array([r["fam"][2] for r in rows], dtype=object),
        lane=np.array([r["lane"] for r in rows], dtype=object),
        hours_to_end=np.array([r["hours"] for r in rows]),
        res_class=np.array(res, dtype=object),
        station=np.array([r["fam"][0] for r in rows], dtype=object),
        era=np.array([r.get("q_shape", "market") if proxy else "market" for r in rows], dtype=object),
    )
    leg_i, leg_b, leg_s, leg_p, leg_y = [], [], [], [], []
    for j, r in enumerate(rows):
        for b, side, p0, y in r["legs"]:
            leg_i.append(j)
            leg_b.append(b)
            leg_s.append(side)
            leg_p.append(p0)
            leg_y.append(y)
    return Corpus(
        inst=Instants([r["fam"][0] for r in rows], [r["fam"][1] for r in rows],
                      [r["fam"][2] for r in rows], [r["decided"] for r in rows]),
        label_at=np.array([r["label_at"] for r in rows]),
        y=np.array([r["y"] for r in rows]),
        P=np.vstack([r[key_P] for r in rows]),
        Q=np.vstack([r["Q"] for r in rows]) if proxy else None,
        features=feats,
        leg_instant=np.array(leg_i), leg_bin=np.array(leg_b), leg_side=np.array(leg_s, dtype=object),
        leg_p0=np.array(leg_p), leg_y=np.array(leg_y),
        ids=np.array([r["id"] for r in rows], dtype=object),
        overround=np.array([r["overround"] for r in rows], dtype=float),
    )


# ---------------------------------------------------------------------------
# Diagnostic (a): market reliability.
# ---------------------------------------------------------------------------

P0_BANDS = (0.0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 0.98, 1.0 + 1e-9)
CELL_P0_BANDS = (0.0, 0.05, 0.2, 0.5, 0.8, 0.95, 1.0 + 1e-9)
LEAD_BANDS = ((0.0, 6.0), (6.0, 12.0), (12.0, 24.0), (24.0, 48.0), (48.0, math.inf))
MASS_BANDS = (0.0, 0.01, 0.05, 0.15, 0.3, 0.5, 1.0 + 1e-9)


def _band_label(edges, x):
    ix = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, len(edges) - 2)
    return np.array([f"[{edges[i]:.2f},{min(edges[i + 1], 1.0):.2f})" for i in ix], dtype=object)


def reliability(legs: dict, corpus: Corpus | None, replicates: int, block: int, seed: int) -> dict:
    """Held-side p0 reliability (all valid BUY legs) and normalized-P family
    reliability, city-day weighted, with simultaneous two-sided date-block
    bounds on E[y - p0] and E[1{Y=k} - P_k]."""

    inst = legs["inst"]
    li = np.asarray(legs["instant"])
    p0 = np.asarray(legs["p0"], dtype=float)
    y = np.asarray(legs["y"], dtype=float)
    side = np.asarray(legs["side"], dtype=object)
    metric = np.asarray(legs["metric"], dtype=object)
    lane = np.asarray(legs["lane"], dtype=object)
    hours = np.asarray(legs["hours"], dtype=float)
    over = np.asarray(legs["overround"], dtype=float)
    over_band = np.where(np.isnan(over), "incomplete", np.where(over <= COHERENT_OVERROUND, "coherent", "incoherent"))
    n_legs = np.bincount(li, minlength=len(inst))
    lead = np.empty(len(hours), dtype=object)
    for lo, hi in LEAD_BANDS:
        lead[(hours >= lo) & (hours < hi)] = f"h[{lo:g},{hi:g})"
    groups = {}
    band = _band_label(P0_BANDS, p0)
    cell_band = _band_label(CELL_P0_BANDS, p0)
    for s in ("ALL", "YES", "NO"):
        sm = np.ones(len(p0), bool) if s == "ALL" else side == s
        for b in sorted(set(band.tolist())):
            groups[f"p0band|{s}|{b}"] = sm & (band == b)
    for m in sorted(set(metric.tolist())):
        for ln in sorted(set(lane.tolist())):
            base = (metric == m) & (lane == ln)
            groups[f"cell|{m}:{ln}|ALL"] = base
            for b in sorted(set(cell_band.tolist())):
                groups[f"cellband|{m}:{ln}|{b}"] = base & (cell_band == b)
            for q in ("coherent", "incoherent", "incomplete"):
                for sd in ("YES", "NO"):
                    groups[f"cellref|{m}:{ln}|{q}|{sd}"] = base & (over_band == q) & (side == sd)
        for ld in sorted(set(lead.tolist())):
            groups[f"lead|{m}|{ld}"] = (metric == m) & (lead == ld)

    fam_groups, fam_x, fam_inst = {}, None, None
    if corpus is not None:
        n, k = corpus.P.shape
        fam_inst = np.repeat(np.arange(n), k)
        pk = corpus.P.ravel()
        hit = (corpus.y[:, None] == np.arange(k)[None, :]).astype(float).ravel()
        fam_x = hit - pk
        mb = _band_label(MASS_BANDS, pk)
        for b in sorted(set(mb.tolist())):
            fam_groups[f"Pmass|{b}"] = (mb == b, pk)

    def values(mult):
        w = inst.weights(None, mult)
        lw = w[li] / n_legs[li]
        out = {}
        for g, m in groups.items():
            out[g] = _wmean(y - p0, lw, m)
        if corpus is not None:
            wf = corpus.inst.weights(None, mult)[fam_inst] / corpus.P.shape[1]
            for g, (m, _) in fam_groups.items():
                out[g] = _wmean(fam_x, wf, m)
        return out

    point = values(None)
    names = sorted(point)
    cal = calendar_dates(inst.dates)
    ix = {d: i for i, d in enumerate(cal)}
    reps = np.empty((replicates, len(names)))
    for r in range(replicates):
        m = block_multiplicities(len(cal), block, np.random.default_rng([seed, block, r, 1]))
        mult = np.array([m[ix[d]] for d in inst.dates])
        v = values(mult)
        reps[r] = [v[g] for g in names]
    w0 = inst.weights()
    lw0 = w0[li] / n_legs[li]
    table = {}
    ok = [j for j, g in enumerate(names) if np.all(np.isfinite(reps[:, j])) and np.std(reps[:, j]) > 0]
    se = np.std(reps[:, ok], axis=0, ddof=1)
    th = np.array([point[names[j]] for j in ok])
    c = float(np.quantile(np.max(np.abs(reps[:, ok] - th) / se, axis=1), CONFIDENCE)) if ok else float("nan")
    bounds = {names[j]: (float(t - c * s), float(t + c * s)) for j, t, s in zip(ok, th, se)}
    for g in names:
        if g.startswith("Pmass"):
            m, pk = fam_groups[g]
            wf = corpus.inst.weights()[fam_inst] / corpus.P.shape[1]
            ev = corpus.inst.evidence(np.isin(np.arange(len(corpus.inst)), fam_inst[m]))
            table[g] = {"mean_P": _wmean(pk, wf, m), "freq": _wmean(pk + fam_x, wf, m),
                        "diff": point[g], "bins": int(m.sum()), "city_days": ev[0]}
        else:
            m = groups[g]
            ev = inst.evidence(np.isin(np.arange(len(inst)), li[m]))
            table[g] = {"mean_p0": _wmean(p0, lw0, m), "freq": _wmean(y, lw0, m), "diff": point[g],
                        "legs": int(m.sum()), "city_days": ev[0], "dates": ev[1]}
        if g in bounds:
            table[g]["simultaneous_bounds"] = bounds[g]
    return {"critical_value": c, "block_dates": block, "replicates": replicates, "groups": table}


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable_result(result: dict) -> dict:
    return {
        "folds": result["folds"],
        "point_values": result["values"],
        "evidence": {k: list(v) for k, v in result["evidence"].items()},
        "parameters_full_sample": result["parameters"],
        "by_block": result["by_block"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--diagnostic", required=True, choices=(PRICE_ONLY_DIAGNOSTIC, PROXY_Q_DIAGNOSTIC))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--start", default="2026-08-26")
    ap.add_argument("--end", default="2026-09-23")
    ap.add_argument("--market", default=P_ASK, choices=(P_ASK, P_MID))
    ap.add_argument("--replicates", type=int, default=REPLICATES)
    ap.add_argument("--blocks", default=",".join(map(str, BLOCKS)))
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--max-overround", type=float, default=None,
                    help=f"restrict the fit corpus to coherent references (declared: {COHERENT_OVERROUND})")
    args = ap.parse_args(argv)
    if (args.out.resolve() / "x").is_relative_to(args.state.resolve()):
        raise SystemExit("--out must not be under the state directory")
    from src.config import runtime_cities_by_name

    proxy = args.diagnostic == PROXY_Q_DIAGNOSTIC
    trade = open_ro(args.state / "zeus_trades.db")
    forecast = open_ro(args.state / "zeus-forecasts.db")
    live = load_live_corpus(trade, forecast, runtime_cities_by_name(), args.start, args.end,
                            market=args.market, proxy=proxy, max_overround=args.max_overround)
    label = args.diagnostic + ("" if args.max_overround is None else f"_overround_le_{args.max_overround:g}")
    blocks = tuple(int(b) for b in args.blocks.split(","))
    specs = FAMILY_SPECS if proxy else PRICE_SPECS
    artifact = {
        "artifact_kind": "family_calibration_release_diagnostic",
        "diagnostic": label,
        "licensing_eligible": False,
        "licensing_ineligible_reason": (
            "historical winner-producing-cut rows; the market reference is a normalized "
            "held-side ask vector"
            + ("; q is a LATEST_PRIOR_POSTERIOR_PROXY, not the decision's own witness" if proxy else "")
            + " (design review §8: diagnostics, never licensing evidence)"
        ),
        "code": {"script_sha256": _file_sha(Path(__file__)),
                 "module_sha256": _file_sha(Path(maf.__file__))},
        "inputs": {"state": str(args.state), "window": [args.start, args.end],
                   "trade_db": "zeus_trades.db?mode=ro", "forecast_db": "zeus-forecasts.db?mode=ro"},
        "config": {"fold_dates": FOLD_DATES, "blocks": list(blocks), "replicates": args.replicates,
                   "seed": args.seed, "label_lag_hours": LABEL_LAG.total_seconds() / 3600.0,
                   "max_overround": args.max_overround, "coherent_overround": COHERENT_OVERROUND,
                   "min_train_city_days": MIN_TRAIN_CITY_DAYS, "floor": [FLOOR_CITY_DAYS, FLOOR_DATES],
                   "confidence": CONFIDENCE, "specs": [spec_name(s) for s in specs],
                   "selection": "inner fold, simplest spec within one date-clustered SE of the best "
                                "categorical log loss"},
        "corpus": live.manifest,
        "population_notes": [
            "graded population = every archived BUY leg of winner-producing cuts; no-winner cuts "
            "were never persisted, so the all-cut law is not identified (design review §2)",
            "policy-selected population: UNAVAILABLE (no archived selector replay over corrected q)",
            "market reference P = per-instant YES asks normalized to one; midpoints need a NO ask "
            "on every bin, available for too few instants (see corpus counts)",
            "label_available_at = local target-day end + label_lag_hours (declared rule; see "
            "corpus.label_lag for observed coverage)",
            "replicates refit every fold and selection warm-started from the point fits, holding "
            "the point fits' station/era scales fixed",
        ],
    }
    if proxy:
        path, sha = write_content_addressed(
            args.out, "proxy_join", ".jsonl",
            b"\n".join(canonical_bytes(r) for r in live.proxy_records) + b"\n")
        artifact["proxy_join"] = {"path": str(path), "sha256": sha, "provenance_kind": PROXY_KIND,
                                  "records": len(live.proxy_records)}
    else:
        artifact["reliability"] = {
            str(b): reliability(live.legs, live.corpus, args.replicates, b, args.seed) for b in blocks
        }
    if live.corpus is not None:
        result = evaluate_release(live.corpus, specs, replicates=args.replicates, blocks=blocks,
                                  workers=args.workers, seed=args.seed)
        path, sha = write_content_addressed(args.out, f"ledger_{label}", ".jsonl",
                                            ledger_bytes(live.corpus, result))
        artifact["ledger"] = {"path": str(path), "sha256": sha,
                              "instants": int(result["point"].tested.sum())}
        artifact["release"] = _jsonable_result(result)
    body = canonical_bytes(artifact)
    artifact["sha256"] = hashlib.sha256(body).hexdigest()
    path, _ = write_content_addressed(args.out, f"release_{label}", ".json",
                                      canonical_bytes(artifact))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
