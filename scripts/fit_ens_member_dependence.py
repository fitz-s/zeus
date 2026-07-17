#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — the Clopper-Pearson finite-evidence bound treats ~51 DEPENDENT
#   ECMWF-ENS members as independent binomial trials and is therefore overconfident;
#   the honest form uses an effective n from MEASURED member dependence.
#   Lifecycle: config_writer — read-only over zeus-forecasts.db (file:...?mode=ro);
#   writes state/ens_member_dependence/ens_member_dependence_<as_of>.json + ACTIVE.json
#   pointer only. Strictly walk-forward (target_date < as_of); no market backtests.
"""Fit the ECMWF-ENS member-dependence (intraclass correlation) artifact.

WHAT IS MEASURED
================
The serving-side ``_finite_evidence_binomial_ucb`` treats the n≈51 member
settlement-preimage hit indicators of one ensemble snapshot as independent
Bernoulli trials. Members share the model's synoptic state, so the indicators
are positively correlated and the implied evidence count is inflated. This
script measures the exchangeable member correlation rho per temperature metric
from the settled ENS archive and writes it as a versioned artifact; serving
applies the design-effect correction

    n_eff = n / (1 + (n - 1) * rho)        (Kish design effect)
    k_eff = k * n_eff / n
    UCB   = betaincinv(k_eff + 1, n_eff - k_eff, 1 - alpha)

which is conservative-only (UCB monotone non-decreasing in rho; rho = 0 is the
exact integer Clopper-Pearson identity).

ESTIMATOR (imported from scripts/measure_member_correlation.py — NOT copied)
============================================================================
``_anova_icc`` — the one-way ANOVA moment ICC for binary indicators
(statistical_calibration_authority_2026-06-12.txt Task 3.1):

    p_hat_i = (members in cell) / n            for event i
    MS_B    = n * var(p_hat, ddof=1)
    MS_W    = mean(p_hat * (1 - p_hat)) * n / (n - 1)
    ICC     = (MS_B - MS_W) / (MS_B + (n - 1) * MS_W)

Cell construction (this script):
  1. Events = freshest VERIFIED ecmwf_ens snapshot per settled
     (city, target_date, metric) triple, exactly the serving-side snapshot
     filter (authority/causality/boundary/window-attribution/extrema), joined
     to settlement_outcomes (authority='VERIFIED', settlement_value NOT NULL)
     purely as a settled-archive license — the settlement VALUE is not used.
     Strict walk-forward: target_date < as_of.
  2. Null members (boundary-quarantined) are skipped; events with fewer than
     MIN_MEMBERS survivors are dropped; per metric only events at the MODAL
     member count are kept (the ANOVA ICC requires a common group size n).
  3. Each member is converted to the snapshot's SETTLEMENT unit and labeled by
     the snapshot's settlement rounding policy (wmo_half_up: floor(x+0.5);
     oracle_truncate/floor: floor(x); ceil: ceil(x); NULL policy defaults to
     wmo_half_up) — the same preimage convention as
     src/contracts/settlement_semantics.settlement_preimage_offsets.
  4. A cell is (metric, settlement_unit, integer label). Each event emits
     p_hat for every label in [min_label - 1, max_label + 1] (explicit zeros
     inside and one step beyond its own support, so zero cells are observed
     without dragging in climatologically unrelated events).
  5. Per cell with >= MIN_EVENTS_PER_CELL events and mean p in [P_LO, P_HI]
     (degenerate-cell filter imported from measure_member_correlation),
     ICC = _anova_icc(p_arr, n); the per-metric rho is the p_bar*(1-p_bar)-
     weighted average over cells, clamped to [0, 1].

Pooling by ABSOLUTE label across cities/seasons can only inflate the
between-event variance and hence rho — inflation is conservative for the
serving correction (larger rho => smaller n_eff => wider UCB), which is the
licensed direction (conservative-only widening).

Deterministic: no RNG anywhere; the artifact JSON is sort_keys and the
generated_at stamp is overridable (--generated-at) for byte-identity tests.

OUTPUT
======
state/ens_member_dependence/ens_member_dependence_<as_of>.json:
    {"_meta": {...}, "metrics": {"high": {"rho": r, "n_targets": ...,
     "n_cells_used": ..., "n_members": n, "n_pairs": ..., "n_eff": ...}, ...}}
plus ACTIVE.json {"artifact": <fname>, "sha256": <hex>, "as_of": <as_of>}.
Consumer: src/forecast/ens_member_dependence.member_dependence_rho (fail-open:
missing/invalid artifact => rho 0.0 => byte-identical serving).
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Imported, not copied: the audited ANOVA moment ICC estimator + degenerate-cell
# filter bounds (statistical_calibration_authority_2026-06-12.txt Task 3.1;
# tests/calibration/test_member_correlation.py proves recovery within 0.05).
from measure_member_correlation import P_HI, P_LO, _anova_icc  # noqa: E402

FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
OUT_DIR_DEFAULT = ROOT / "state" / "ens_member_dependence"

METHOD = "anova_moment_icc_over_settlement_grid_member_indicators"
ESTIMATOR_PROVENANCE = "scripts/measure_member_correlation.py::_anova_icc (imported)"

# Serving floor mirror: _current_evidence_shape_from_values rejects < 20 members.
MIN_MEMBERS = 20
# Same minimum-events bar as measure_member_correlation.MIN_EVENTS_AIFS.
MIN_EVENTS_PER_CELL = 20

METRICS = ("high", "low")


def load_settled_member_events(
    conn: sqlite3.Connection, *, as_of: str
) -> list[dict]:
    """Freshest settled ENS snapshot per (city, target_date, metric), walk-forward.

    Mirrors the serving-side snapshot filter (replacement_forecast_materializer
    ensemble_snapshots SELECT) and its freshness ORDER BY. The settlement join is
    an EXISTS license only (no settlement value is consumed). Strict boundary:
    target_date < as_of.
    """
    rows = conn.execute(
        """
        SELECT es.city, es.target_date, es.temperature_metric,
               es.members_json, es.members_unit,
               es.settlement_unit, es.settlement_rounding_policy
        FROM ensemble_snapshots es
        WHERE es.source_id = 'ecmwf_open_data'
          AND es.model_version = 'ecmwf_ens'
          AND es.authority = 'VERIFIED'
          AND es.causality_status = 'OK'
          AND es.boundary_ambiguous = 0
          AND es.forecast_window_attribution_status = 'FULLY_INSIDE_TARGET_LOCAL_DAY'
          AND es.contributes_to_target_extrema = 1
          AND es.target_date < ?
          AND EXISTS (
              SELECT 1 FROM settlement_outcomes so
              WHERE lower(so.city) = lower(es.city)
                AND so.target_date = es.target_date
                AND so.temperature_metric = es.temperature_metric
                AND so.authority = 'VERIFIED'
                AND so.settlement_value IS NOT NULL
          )
        ORDER BY es.city, es.target_date, es.temperature_metric,
                 COALESCE(es.source_cycle_time, es.issue_time) DESC,
                 COALESCE(es.source_available_at, es.available_at) DESC,
                 es.snapshot_id DESC
        """,
        (as_of,),
    ).fetchall()

    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for city, tdate, metric, members_json, members_unit, s_unit, s_policy in rows:
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
            }
        )
    return events


def _to_settlement_unit(values: Sequence[float], members_unit: str, settlement_unit: str) -> list[float] | None:
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


def estimate_rho_by_metric(events: Sequence[Mapping[str, object]]) -> dict[str, dict]:
    """Per-metric pooled ICC over settlement-grid member-indicator cells.

    See module docstring steps 2-5 for the exact construction. Returns
    {metric: {rho, n_targets, n_cells_used, n_members, n_pairs, n_eff}} for
    every metric with at least one usable cell; metrics without usable cells
    are omitted (the loader then serves its conservative fallback).
    """
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        metric = str(event["metric"])
        if metric not in METRICS:
            continue
        converted = _to_settlement_unit(
            event["members"],  # type: ignore[arg-type]
            str(event["members_unit"]),
            str(event["settlement_unit"]),
        )
        if converted is None:
            continue
        labels = [_label(v, str(event["rounding_policy"])) for v in converted]
        if any(lbl is None for lbl in labels):
            continue
        by_metric[metric].append(
            {
                "n": len(labels),
                "unit": str(event["settlement_unit"]),
                "counts": Counter(labels),
                "lo": min(labels),  # type: ignore[type-var]
                "hi": max(labels),  # type: ignore[type-var]
            }
        )

    out: dict[str, dict] = {}
    for metric in sorted(by_metric):
        pool = by_metric[metric]
        # Common group size for the ANOVA ICC: keep only the modal member count
        # (ties broken toward the larger n — more evidence per event).
        n_modal = max(
            Counter(e["n"] for e in pool).items(), key=lambda kv: (kv[1], kv[0])
        )[0]
        used = [e for e in pool if e["n"] == n_modal]

        # cell -> list of p_hat observations (explicit zeros one step beyond
        # each event's own support; see module docstring step 4).
        cell_obs: dict[tuple[str, int], list[float]] = defaultdict(list)
        for e in used:
            for lbl in range(int(e["lo"]) - 1, int(e["hi"]) + 2):
                cell_obs[(e["unit"], lbl)].append(
                    e["counts"].get(lbl, 0) / n_modal
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
            continue
        rho = float(np.average(np.asarray(icc_values), weights=np.asarray(weights)))
        rho = min(max(rho, 0.0), 1.0)
        n_eff = n_modal / (1.0 + (n_modal - 1) * rho)
        out[metric] = {
            "rho": round(rho, 6),
            "n_targets": len(used),
            "n_cells_used": len(icc_values),
            "n_members": int(n_modal),
            "n_pairs": len(used) * n_modal * (n_modal - 1) // 2,
            "n_eff": round(n_eff, 4),
        }
    return out


def build_artifact(
    conn: sqlite3.Connection, *, as_of: str, generated_at: str, git_sha: str
) -> dict[str, object]:
    events = load_settled_member_events(conn, as_of=as_of)
    metrics = estimate_rho_by_metric(events)
    dates = sorted({str(e["target_date"]) for e in events})
    return {
        "schema_version": 1,
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
            "min_members": MIN_MEMBERS,
            "min_events_per_cell": MIN_EVENTS_PER_CELL,
            "p_filter_lo": P_LO,
            "p_filter_hi": P_HI,
            "source": (
                "ensemble_snapshots(ecmwf_open_data/ecmwf_ens, VERIFIED, causality OK, "
                "boundary_ambiguous=0, FULLY_INSIDE_TARGET_LOCAL_DAY, "
                "contributes_to_target_extrema=1) freshest per settled triple, "
                "EXISTS settlement_outcomes(VERIFIED, settlement_value NOT NULL), "
                "target_date < as_of"
            ),
        },
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
            f"{m}: rho={c['rho']} n_eff={c['n_eff']} n_targets={c['n_targets']}"
            for m, c in sorted(metrics.items())  # type: ignore[union-attr]
        )
        if metrics
        else f"Wrote {args.out_dir / fname} (sha256={sha}); NO usable cells"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
