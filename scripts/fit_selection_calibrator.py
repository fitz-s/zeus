#!/usr/bin/env python3
# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-aware settlement q_lcb calibrator
#   (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22).
#   Walk-forward (no-leak) fitter — the ONLY writer of state/selection_calibrator.json. The runtime
#   serving rule src/decision/selection_calibrator.py READS it. Reuses the join/parse pattern of
#   scripts/fit_sigma_scale.py (forecast_posteriors ⋈ settlement_outcomes VERIFIED, freshest
#   posterior per (city,date,lead-bucket)), but grades the realized SETTLEMENT hit-rate of each SIDE
#   decision and aggregates by the live selection cell (side|lead_bucket|bin_class|raw_prob_bucket).
"""Fit the selection-aware settlement q_lcb calibrator artifact, WALK-FORWARD (no leak).

WHY (settlement-graded 2026-06-22): the admission gate ``q_lcb_side > price`` adversely-selects the
bins where the model most under-estimates the bin. On the real 104-bet buy_no slice the system's
YES-belief-in-bin = 0.126 but realized-in-bin = 0.327 (market 0.298) — a ~20pp over-claim on the
bought NO side that the center-uncertainty bootstrap q_lcb does not cover. This fitter learns, from
SETTLED rows ONLY, the realized hit-rate of each SIDE decision keyed by the RAW SIDE PROB bucket, and
the runtime serves its conservative lower bound as the admission lower bound (the over-claim is
LEARNED from settled data, never hard-coded).

MODEL (no parametric forecast model — it grades the SERVED posterior against settlement):
  For each (city, target_date, temperature_metric) with a VERIFIED settlement and the freshest
  posterior per lead bucket, integrate every bin's YES probability from the posterior's q_json. For
  each bin emit TWO side decisions:
    YES side: raw_side_prob = q_yes(bin);   side_won = 1 iff the bin IS the winning bin.
    NO  side: raw_side_prob = 1 - q_yes(bin); side_won = 1 iff the bin is NOT the winning bin.
  bin_class = "modal" for the posterior's modal (argmax q) bin, else "nonmodal". lead_days from the
  source_cycle_time -> target_date 00:00 UTC gap.

WALK-FORWARD NO-LEAK:
  Rows are accumulated in SETTLEMENT-TIME order. The persisted artifact is the END-OF-WINDOW fit
  (every settled row in the window), which the runtime then applies to FUTURE (un-settled) decisions
  — so the artifact never grades a decision with its own future. ``rows_strictly_before(rows, T)``
  is the primitive the forward-validation harness uses to reconstruct the as-of-T artifact for every
  historical decision (a row settled >= T is a leak and is excluded). The unit tests assert the
  strict-< boundary.

AGGREGATE -> CELL:
  Group rows by (side, lead_bucket, bin_class, raw_prob_bucket). hit_rate = wins / n. With
  --enforce-monotone (default), within each (side, lead_bucket, bin_class) group the per-prob-bucket
  hit-rates are projected onto the monotone NON-DECREASING cone in raw prob (a higher belief cannot
  map to a lower calibrated realized rate). Cells with n < min_n are still PERSISTED (so the runtime
  can see they are thin and fail-closed), but the runtime serves a bound ONLY when n >= min_n.

CONSERVATIVE BOUND:
  The artifact persists the realized (n, hit_rate); the runtime serves
  ``beta_lower_bound_95(round(hit_rate*n), n)`` — the one-sided Wilson 95% LOWER bound (the SAME bound
  the OOF reliability guard uses). Persisting (n, hit_rate) keeps the artifact a pure data record and
  the lower-bound math single-sourced in the serving module.

READ-ONLY over state/zeus-forecasts.db (forecast_posteriors + settlement_outcomes) — mode=ro. Writes
state/selection_calibrator.json via atomic replace. This script is the artifact's ONLY writer.
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
from dataclasses import dataclass

# Reuse fit_sigma_scale's settled-cell parse/join helpers (single source for q_json parsing + the
# 3-pass winning-bin match + lead-hour computation) so this fitter and the σ-scale fit grade the
# SAME settled cells the same way.
import scripts.fit_sigma_scale as fs
from src.decision import selection_calibrator as sc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "selection_calibrator.json")

MIN_N_DEFAULT = 30
AUTHORITY = "selection_calibrator_v1_walkforward"
# The served fused posterior method (forecast_posteriors.posterior_method) the calibrator is bound
# to. A served artifact whose posterior_version differs is STALE in the runtime serving rule.
POSTERIOR_VERSION = "openmeteo_ecmwf_ifs9_bayes_fusion"

# Same canonical join as the σ-scale fit, plus settled_at + posterior_method for walk-forward
# ordering and the version stamp. Freshest posterior per (city, target_date, lead-bucket) below.
_FIT_QUERY = (
    "SELECT fp.city, fp.target_date, fp.source_cycle_time, fp.computed_at, fp.q_json, "
    "       fp.posterior_method, "
    "       so.winning_bin, so.settlement_value, so.settlement_unit, so.settled_at "
    "FROM forecast_posteriors fp "
    "JOIN settlement_outcomes so "
    "  ON so.city=fp.city AND so.target_date=fp.target_date "
    " AND so.temperature_metric=fp.temperature_metric "
    "WHERE fp.temperature_metric='high' "
    "  AND so.authority='VERIFIED' AND so.winning_bin IS NOT NULL"
)


@dataclass(frozen=True)
class SettledDecisionRow:
    """One historical SIDE decision graded by settlement.

    settled_at: ISO timestamp of the settlement (the no-leak boundary key).
    side: "YES" | "NO".
    lead_days: source_cycle_time -> target_date gap in days.
    bin_class: "modal" | "nonmodal".
    raw_side_prob: the RAW point probability of this side (q_yes for YES, 1-q_yes for NO).
    side_won: 1 iff this side's claim was realized by settlement.
    """

    settled_at: str
    side: str
    lead_days: float
    bin_class: str
    raw_side_prob: float
    side_won: int


# ---------------------------------------------------------------------------
# Row construction (reuses fit_sigma_scale parse/match).
# ---------------------------------------------------------------------------

def build_rows(db_rows) -> list[SettledDecisionRow]:
    """Build per-side settled decision rows from the joined DB rows.

    Freshest posterior per (city, target_date, lead-bucket) — the SAME dedup the σ-scale fit uses —
    then for each bin emit a YES row and a NO row graded by the winning bin. Only rows whose
    posterior_method is the served fused version are kept (the calibrator is bound to that version).
    """
    # Freshest posterior per (city, target_date, bucket).
    best: dict = {}
    for (city, tdate, sct, comp, q_json_text, pmethod, winning_bin, sval, sunit, settled_at) in db_rows:
        if str(pmethod) != POSTERIOR_VERSION:
            continue
        bucket = fs._bucket_for_lead(fs._lead_hours(tdate, sct))
        if bucket is None:
            continue
        key = (city, tdate, bucket)
        prev = best.get(key)
        if prev is None or str(comp) > str(prev[3]):
            best[key] = (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, settled_at)

    rows: list[SettledDecisionRow] = []
    for (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, settled_at) in best.values():
        parsed = fs._parse_cell(q_json_text)
        if parsed is None:
            continue
        items, mode_index, step = parsed
        won_index = fs._winning_index(items, winning_bin, sval, step=step)
        if won_index is None:
            continue
        lead_h = fs._lead_hours(tdate, sct)
        lead_days = (lead_h / 24.0) if lead_h is not None else 0.0
        settled_iso = _normalize_iso(settled_at)
        for i, (_label, q_yes, _deg, _is_open) in enumerate(items):
            try:
                qy = float(min(max(float(q_yes), 0.0), 1.0))
            except (TypeError, ValueError):
                continue
            bin_class = "modal" if i == mode_index else "nonmodal"
            yes_won = 1 if i == won_index else 0
            rows.append(SettledDecisionRow(settled_iso, "YES", lead_days, bin_class, qy, yes_won))
            rows.append(SettledDecisionRow(settled_iso, "NO", lead_days, bin_class, 1.0 - qy, 1 - yes_won))
    # Settlement-time order (the walk-forward accumulation order).
    rows.sort(key=lambda r: r.settled_at)
    return rows


def _normalize_iso(settled_at) -> str:
    s = str(settled_at or "")
    if not s:
        return "0000-00-00T00:00:00+00:00"
    s = s.replace("Z", "+00:00")
    return s


# ---------------------------------------------------------------------------
# Walk-forward primitive.
# ---------------------------------------------------------------------------

def rows_strictly_before(rows, boundary: str) -> list[SettledDecisionRow]:
    """Return the rows settled STRICTLY BEFORE ``boundary`` (no leak: a row at-or-after T is excluded).

    This is the primitive the forward-validation harness uses to reconstruct the as-of-T artifact for
    every historical decision. The strict < boundary is the no-leak contract.
    """
    b = _normalize_iso(boundary)
    return [r for r in rows if r.settled_at < b]


# ---------------------------------------------------------------------------
# Aggregate -> cells (with optional isotonic monotone projection).
# ---------------------------------------------------------------------------

def fit_cells(
    rows,
    *,
    min_n: int = MIN_N_DEFAULT,
    posterior_version: str = POSTERIOR_VERSION,
    enforce_monotone: bool = True,
) -> dict:
    """Aggregate settled side-decision rows into the calibrator artifact.

    Cells keyed by the live ``selection_calibrator.cell_key`` schema. hit_rate = wins / n. With
    ``enforce_monotone``, within each (side, lead_bucket, bin_class) group the per-prob-bucket
    hit-rates are projected onto the monotone NON-DECREASING cone in raw prob. The artifact persists
    (n, hit_rate); the runtime serves the beta/Wilson 95% lower bound.
    """
    # First pass: raw counts per cell.
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # key -> [wins, n]
    bucket_mid: dict[str, float] = {}
    max_settled = ""
    for r in rows:
        key = sc.cell_key(side=r.side, lead_days=r.lead_days, bin_class=r.bin_class,
                          raw_side_prob=r.raw_side_prob)
        counts[key][0] += int(r.side_won)
        counts[key][1] += 1
        if key not in bucket_mid:
            bucket_mid[key] = sc.raw_prob_bucket(r.raw_side_prob)[1]
        if r.settled_at > max_settled:
            max_settled = r.settled_at

    cells: dict[str, dict] = {}
    for key, (wins, n) in counts.items():
        cells[key] = {"n": int(n), "hit_rate": float(wins) / float(n) if n else 0.0,
                      "wins": int(wins), "prob_mid": bucket_mid.get(key, 0.0)}

    if enforce_monotone:
        cells = _project_monotone(cells)

    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    qhash = hashlib.sha256(
        (_FIT_QUERY + f"|version={posterior_version}|n_rows={len(rows)}").encode("utf-8")
    ).hexdigest()[:16]
    return {
        "_meta": {
            "authority": AUTHORITY,
            "version": "sel_v1",
            "posterior_version": posterior_version,
            "armed_sides": ["NO"],
            "min_n": int(min_n),
            "max_settled_at": max_settled or None,
            "n_rows": len(rows),
            "created": fitted_at,
            "cell_key_schema": "side|lead_bucket|bin_class|raw_prob_bucket",
            "method": "walk_forward_settled_hit_rate_isotonic_in_raw_prob",
            "lower_bound": "beta_lower_bound_95 (Wilson one-sided 95% LB) served at runtime",
            "monotone": bool(enforce_monotone),
            "source": "forecast_posteriors ⋈ settlement_outcomes(authority=VERIFIED), high metric, freshest-per-lead",
            "source_query_hash": qhash,
        },
        "cells": cells,
    }


def _project_monotone(cells: dict[str, dict]) -> dict[str, dict]:
    """Project each (side, lead_bucket, bin_class) group's per-prob-bucket hit-rates onto the monotone
    NON-DECREASING cone in raw prob (isotonic). Keeps n; replaces hit_rate with the projected value.
    """
    # Group keys by everything except the prob-bucket suffix.
    groups: dict[tuple, list[str]] = defaultdict(list)
    for key in cells:
        parts = key.split("|")
        if len(parts) != 4:
            continue
        side, lead, klass, pb = parts
        groups[(side, lead, klass)].append(key)
    out = dict(cells)
    for _gkey, keys in groups.items():
        # Sort by prob-bucket index.
        keys_sorted = sorted(keys, key=lambda k: int(k.rsplit("pb", 1)[1]))
        xs = [cells[k]["prob_mid"] for k in keys_sorted]
        ys = [cells[k]["hit_rate"] for k in keys_sorted]
        # WEIGHTED PAVA ([MEDIUM] fix, consult REQ-...154643): weight each bucket by its N so a thin
        # cell cannot drag a deep neighbour. Keep wins_raw / n_raw; store the projected hit_rate_iso.
        ws = [float(cells[k]["n"]) for k in keys_sorted]
        fitted = sc.isotonic_nondecreasing_weighted(xs, ys, ws)
        for k, y in zip(keys_sorted, fitted):
            out[k] = dict(cells[k])
            out[k]["hit_rate_raw"] = float(cells[k]["hit_rate"])
            out[k]["wins_raw"] = int(cells[k].get("wins", round(cells[k]["hit_rate"] * cells[k]["n"])))
            out[k]["hit_rate"] = float(y)  # the projected (served) rate
    return out


# ---------------------------------------------------------------------------
# Hierarchical empirical-Bayes hybrid fit (consult REQ-20260622-154643 STEP-2).
#   corpus rows -> p0_b (the PRIOR, weighted-isotonic in raw prob).
#   selected rows (executed / would-admit, admit0=True) -> (w_s, n_s) the LIKELIHOOD.
#   beta-binomial shrinkage with a LEARNED tau -> persisted q_safe_lb (BetaInvCDF offline).
# STEP-1 forced the EXECUTED-EB fallback: no current-regime would-admit receipts exist, so the
# selected population is the executed trades; corpus alone never licenses (fail-closed on thin
# selected support).
# ---------------------------------------------------------------------------

def _corpus_prior_rates(corpus_rows, *, enforce_monotone: bool = True) -> dict[str, float]:
    """p0_b = weighted-isotonic full-corpus hit-rate per cell key (the EB PRIOR)."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    mids: dict[str, float] = {}
    for r in corpus_rows:
        key = sc.cell_key(side=r.side, lead_days=r.lead_days, bin_class=r.bin_class,
                          raw_side_prob=r.raw_side_prob)
        counts[key][0] += int(r.side_won)
        counts[key][1] += 1
        mids.setdefault(key, sc.raw_prob_bucket(r.raw_side_prob)[1])
    p0 = {k: (w / n if n else 0.0) for k, (w, n) in counts.items()}
    if not enforce_monotone:
        return p0
    # Weighted isotonic per (side, lead, class) group.
    groups: dict[tuple, list[str]] = defaultdict(list)
    for k in p0:
        side, lead, klass, _pb = k.split("|")
        groups[(side, lead, klass)].append(k)
    out = dict(p0)
    for _g, keys in groups.items():
        keys_sorted = sorted(keys, key=lambda k: int(k.rsplit("pb", 1)[1]))
        xs = [mids[k] for k in keys_sorted]
        ys = [p0[k] for k in keys_sorted]
        ws = [float(counts[k][1]) for k in keys_sorted]
        fitted = sc.isotonic_nondecreasing_weighted(xs, ys, ws)
        for k, y in zip(keys_sorted, fitted):
            out[k] = float(y)
    return out


def _selected_counts(selected_rows) -> dict[str, list[int]]:
    """(wins, n) per cell key over the selected/would-admit rows (the LIKELIHOOD)."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in selected_rows:
        key = sc.cell_key(side=r.side, lead_days=r.lead_days, bin_class=r.bin_class,
                          raw_side_prob=r.raw_side_prob)
        counts[key][0] += int(r.side_won)
        counts[key][1] += 1
    return counts


def learn_tau(*, corpus_rows, selected_rows, grid=None) -> float:
    """Learn the shrinkage strength tau by rolling-origin prequential negative-log-likelihood on the
    SELECTED rows (consult: tau must be LEARNED, never hard-coded).

    For each as-of settlement timestamp T, fit p0_b on the corpus and the selected counts on rows
    settled STRICTLY before T, then score the selected rows decided at T under the EB posterior MEAN
    rate. Choose the tau minimizing total prequential NLL. Falls back to a mid grid value when there
    is insufficient rolling support.
    """
    if grid is None:
        grid = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 50.0, 75.0, 100.0, 150.0, 250.0]
    p0 = _corpus_prior_rates(corpus_rows)
    sel_sorted = sorted(selected_rows, key=lambda r: r.settled_at)
    # Distinct settlement times that have prior rows -> rolling origins.
    times = sorted({r.settled_at for r in sel_sorted})
    if len(times) < 2:
        return float(grid[len(grid) // 2])  # not enough origins to learn -> conservative middle
    EPS = 1e-9
    best_tau, best_nll = grid[len(grid) // 2], float("inf")
    for tau in grid:
        nll = 0.0
        scored = 0
        for T in times:
            prior_rows = [r for r in sel_sorted if r.settled_at < T]
            score_rows = [r for r in sel_sorted if r.settled_at == T]
            if not prior_rows or not score_rows:
                continue
            sel_counts = _selected_counts(prior_rows)
            for r in score_rows:
                key = sc.cell_key(side=r.side, lead_days=r.lead_days, bin_class=r.bin_class,
                                  raw_side_prob=r.raw_side_prob)
                w_s, n_s = sel_counts.get(key, [0, 0])
                p0_b = p0.get(key, 0.5)
                a = tau * p0_b + w_s
                b = tau * (1.0 - p0_b) + (n_s - w_s)
                mean = a / (a + b) if (a + b) > 0 else p0_b
                mean = min(max(mean, EPS), 1.0 - EPS)
                nll -= (math.log(mean) if r.side_won else math.log(1.0 - mean))
                scored += 1
        if scored and nll < best_nll:
            best_nll, best_tau = nll, tau
    return float(best_tau)


def fit_eb_cells(
    *,
    corpus_rows,
    selected_rows,
    min_n: int = MIN_N_DEFAULT,
    posterior_version: str = POSTERIOR_VERSION,
    tau: float | None = None,
    enforce_monotone: bool = True,
) -> dict:
    """Fit the hierarchical EB hybrid artifact (v2). Persists per cell: n_selected, wins_selected,
    p0_corpus, n_corpus, tau, alpha_post, beta_post, q_safe_lb. Runtime serves q_safe_lb directly
    (no SciPy at runtime), gated on n_selected >= min_n (corpus alone never licenses).
    """
    p0 = _corpus_prior_rates(corpus_rows, enforce_monotone=enforce_monotone)
    corpus_n: dict[str, int] = defaultdict(int)
    for r in corpus_rows:
        corpus_n[sc.cell_key(side=r.side, lead_days=r.lead_days, bin_class=r.bin_class,
                             raw_side_prob=r.raw_side_prob)] += 1
    sel_counts = _selected_counts(selected_rows)

    if tau is None:
        tau = learn_tau(corpus_rows=corpus_rows, selected_rows=selected_rows)

    max_settled = ""
    for r in list(corpus_rows) + list(selected_rows):
        if r.settled_at > max_settled:
            max_settled = r.settled_at

    cells: dict[str, dict] = {}
    # Union of selected cells (the only ones that can license) — corpus-only cells are NOT persisted
    # as licensable (corpus is a prior, not a license). We persist selected cells with their EB bound.
    for key, (w_s, n_s) in sel_counts.items():
        p0_b = p0.get(key, 0.5)
        a = tau * p0_b + w_s
        b = tau * (1.0 - p0_b) + (n_s - w_s)
        q_safe_lb = sc.eb_lower_bound(p0=p0_b, tau=tau, wins=w_s, n=n_s, alpha_quantile=0.05)
        cells[key] = {
            "n": int(n_s), "n_selected": int(n_s), "wins_selected": int(w_s),
            "p0_corpus": round(float(p0_b), 6), "n_corpus": int(corpus_n.get(key, 0)),
            "tau": float(tau), "alpha_post": round(float(a), 6), "beta_post": round(float(b), 6),
            "q_safe_lb": round(float(q_safe_lb), 6),
        }

    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    qhash = hashlib.sha256(
        (_FIT_QUERY + f"|eb|version={posterior_version}|tau={tau}").encode("utf-8")
    ).hexdigest()[:16]
    return {
        "_meta": {
            "authority": AUTHORITY,
            "version": "sel_eb_v2",
            "schema": "eb_v2",
            "posterior_version": posterior_version,
            "armed_sides": ["NO"],
            "min_n": int(min_n),
            "tau": float(tau),
            "max_settled_at": max_settled or None,
            "n_corpus_rows": len(list(corpus_rows)),
            "n_selected_rows": len(list(selected_rows)),
            "created": fitted_at,
            "cell_key_schema": "side|lead_bucket|bin_class|raw_prob_bucket",
            "method": "hierarchical_eb_beta_binomial_corpus_prior_selected_likelihood_learned_tau",
            "lower_bound": "BetaInvCDF(0.05, tau*p0+w_s, tau*(1-p0)+n_s-w_s) persisted (no runtime scipy)",
            "selection_policy_version": "pre_selection_calibrator_q_lcb_price_gate_v1",
            "source_query_hash": qhash,
            "note": "STEP-1: no current-regime would-admit receipts -> selected=executed (fallback); shadow-log to accrue would-admit.",
        },
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def load_db_rows(fcst_path: str):
    con = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_FIT_QUERY)
        return cur.fetchall()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fit the selection-aware settlement q_lcb calibrator (walk-forward, no leak)."
    )
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlement_outcomes).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output selection_calibrator.json path.")
    ap.add_argument("--min-n", type=int, default=MIN_N_DEFAULT, help="min settled rows per cell to license a bound at runtime.")
    ap.add_argument("--no-monotone", action="store_true", help="disable isotonic monotone projection (diagnostic only).")
    args = ap.parse_args()

    db_rows = load_db_rows(args.fcst)
    rows = build_rows(db_rows)
    artifact = fit_cells(
        rows, min_n=args.min_n, posterior_version=POSTERIOR_VERSION,
        enforce_monotone=not args.no_monotone,
    )

    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    cells = artifact["cells"]
    deep = sum(1 for c in cells.values() if c["n"] >= args.min_n)
    print(f"[selection-calibrator] wrote {args.out}")
    print(f"    n_rows={len(rows)} cells={len(cells)} deep(>= {args.min_n})={deep} "
          f"max_settled_at={artifact['_meta']['max_settled_at']}")
    # Show the toxic NO region cells for eyeballing.
    for key in sorted(cells):
        if key.startswith("NO|L1|nonmodal|pb1"):
            c = cells[key]
            lb = sc.beta_lower_bound_95(int(round(c["hit_rate"] * c["n"])), c["n"])
            print(f"    {key}: n={c['n']} hit_rate={c['hit_rate']:.3f} served_LB={lb:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
