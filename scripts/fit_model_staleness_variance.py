#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (b): mixed-cycle fusion's correct unit is ERROR VARIANCE, never age
#   haircuts — per-model v_m(cycle-lag) from strictly-prior settlements is ADDED to the
#   model's residual second moment in the precision weights; stale instruments are
#   DOWNWEIGHTED, never excluded (E4: exclusion measured +0.152C worse). Sanity anchor:
#   measured staleness cost +0.099..+0.168 C per 6h cycle (~ +0.096C/cycle linear).
"""Fit the per-(model, metric, lead-bucket) staleness VARIANCE artifact.

WHAT IS MEASURED (operator law: data-time relationships are MEASURED, never guessed):
the ``previous_runs`` archive keeps, for each (model, city, metric, target_date) cell,
one archived run per ``lead_days`` bucket (typically leads {0, 1, 2, 3, 5, 7}). A larger
lead is the SAME model's forecast for the SAME target issued from an OLDER cycle, so the
walk-forward residual second moment per (model, metric, lead_days) — pooled across
cities — measures exactly how much error variance one extra day of cycle-lag adds.

DERIVED v TABLE: per (model, metric), the freshest bucket is the smallest lead with
n >= MIN_CELL_N settled residuals. ``v(lead) = cummax(max(0, m2(lead) − m2(freshest)))``
over increasing lead — non-negative, monotone non-decreasing in lag (a Wiener-like error
growth cannot shrink with age; sampling noise that says otherwise is clipped, never
extrapolated). v is in degC² (residuals are computed in degC; F settlements are converted
first) and is consumed by ``src/forecast/staleness_variance.py::v_for`` which ADDS it to
the model's raw residual second moment in the serving precision weights.

SERVING-SIDE LAG MAPPING (documented here because the fit defines the unit): the serving
loader maps a served row's cycle-lag behind the decision's selected cycle to a bucket via
``freshest_lead + floor(lag_hours / 24)`` — 24h per lead-day, the same day-granularity
this archive measures. Lags beyond the largest fitted bucket CLAMP to the largest fitted
v (measured values only, never extrapolated).

DATA (STRICTLY WALK-FORWARD): raw_model_forecasts (endpoint='previous_runs', every
archived lead) JOIN settlement_outcomes (authority='VERIFIED') on (city, target_date,
metric), restricted to target_date < as_of. Settlements in degF are converted to degC
before the residual (same discipline as scripts/fit_source_clock_city_weights.py).

DETERMINISM: same DB state + as_of => byte-identical content (json.dumps(sort_keys=True),
fixed rounding). ``generated_at`` is run metadata; pass ``--generated-at`` to pin it
(the determinism test does).

SANITY TABLE: the script prints, per metric and lead, the pooled RMSE increment per 6h
cycle implied by the fitted m2 curve, next to the measured anchor +0.099..+0.168 C/6h.

READ-ONLY over state/zeus-forecasts.db (file:...?mode=ro). Writes ONLY the versioned
artifact + ACTIVE.json pointer under state/staleness_variance/.
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
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
OUT_DIR_DEFAULT = ROOT / "state" / "staleness_variance"
METRICS = ("high", "low")
# A (model, metric, lead) cell needs this many settled residuals before its m2 is trusted
# in the v derivation — the same thin-evidence threshold as src.forecast.center.MIN_SETTLED_N
# (kept as a literal here so the fit does not import serving-path modules it does not use).
MIN_CELL_N = 30
# Measured sanity anchor: staleness cost per 6h issuance cycle (degC of MAE/RMSE scale).
ANCHOR_PER_CYCLE_C = (0.099, 0.168)

_FIT_QUERY = """
    SELECT r.city AS city, r.metric AS metric, r.model AS model,
           r.target_date AS target_date, r.lead_days AS lead_days,
           r.forecast_value_c AS forecast_value_c,
           s.settlement_value AS settlement_value, s.settlement_unit AS settlement_unit
    FROM raw_model_forecasts AS r
    JOIN settlement_outcomes AS s
      ON s.city = r.city AND s.target_date = r.target_date AND s.temperature_metric = r.metric
    WHERE r.endpoint = 'previous_runs'
      AND r.lead_days IS NOT NULL
      AND s.authority = 'VERIFIED'
      AND s.settlement_value IS NOT NULL
      AND r.target_date < ?
    ORDER BY r.city, r.metric, r.model, r.target_date, r.lead_days
"""


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def load_cell_stats(
    conn: sqlite3.Connection, *, as_of: str
) -> dict[tuple[str, str, int], tuple[float, int]]:
    """{(model, metric, lead_days): (m2_degC2, n)} pooled across cities, walk-forward.

    One residual per (model, city, metric, target_date, lead_days) cell — the archive
    keeps one run per cell-lead; a duplicate row (re-capture) is dropped first-wins in
    the deterministic ORDER of ``_FIT_QUERY``.
    """
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    sq_sums: dict[tuple[str, str, int], float] = {}
    counts: dict[tuple[str, str, int], int] = {}
    seen: set[tuple[str, str, str, str, int]] = set()
    for row in cur.execute(_FIT_QUERY, (as_of,)):
        try:
            model, city, metric = str(row["model"]), str(row["city"]), str(row["metric"])
            target_date = str(row["target_date"])
            lead = int(row["lead_days"])
            fc = float(row["forecast_value_c"])
            settle_c = _settlement_to_celsius(row["settlement_value"], row["settlement_unit"])
        except (TypeError, ValueError):
            continue
        cell = (model, city, metric, target_date, lead)
        if cell in seen:
            continue
        seen.add(cell)
        r = fc - settle_c
        key = (model, metric, lead)
        sq_sums[key] = sq_sums.get(key, 0.0) + r * r
        counts[key] = counts.get(key, 0) + 1
    return {k: (sq_sums[k] / counts[k], counts[k]) for k in sorted(counts)}


def derive_v_tables(
    cell_stats: Mapping[tuple[str, str, int], tuple[float, int]],
    *,
    min_cell_n: int = MIN_CELL_N,
) -> dict[str, dict[str, dict[str, object]]]:
    """{model: {metric: {freshest_lead, m2_by_lead, n_by_lead, v_by_lead}}}.

    v(lead) = cummax(max(0, m2(lead) − m2(freshest))) over the leads whose cell reaches
    ``min_cell_n``; leads below the floor are recorded (m2/n, for transparency) but get
    no v entry, so the serving side fails open to the nearest smaller fitted bucket.
    """
    by_model_metric: dict[tuple[str, str], dict[int, tuple[float, int]]] = {}
    for (model, metric, lead), (m2, n) in cell_stats.items():
        by_model_metric.setdefault((model, metric), {})[lead] = (m2, n)

    out: dict[str, dict[str, dict[str, object]]] = {}
    for (model, metric), cells in sorted(by_model_metric.items()):
        fitted_leads = sorted(lead for lead, (_m2, n) in cells.items() if n >= min_cell_n)
        if not fitted_leads:
            continue
        freshest = fitted_leads[0]
        base_m2 = cells[freshest][0]
        v_by_lead: dict[str, float] = {}
        running = 0.0
        for lead in fitted_leads:
            running = max(running, max(0.0, cells[lead][0] - base_m2))
            v_by_lead[str(lead)] = round(running, 6)
        out.setdefault(model, {})[metric] = {
            "freshest_lead": freshest,
            "m2_by_lead": {str(lead): round(m2, 6) for lead, (m2, _n) in sorted(cells.items())},
            "n_by_lead": {str(lead): n for lead, (_m2, n) in sorted(cells.items())},
            "v_by_lead": v_by_lead,
        }
    return out


def sanity_table(models: Mapping[str, Mapping[str, Mapping[str, object]]]) -> str:
    """Pooled per-lead RMSE increment per 6h cycle vs the measured anchor.

    For every model with a fitted bucket at ``lead > freshest``, the implied per-6h-cycle
    RMSE increment is (sqrt(m2_lead) − sqrt(m2_freshest)) / (4 * (lead − freshest))
    (4 six-hour cycles per lead-day). Averaged over models per (metric, lead).
    """
    lines = [
        "sanity: fitted per-6h-cycle RMSE increment vs measured anchor "
        f"+{ANCHOR_PER_CYCLE_C[0]:.3f}..+{ANCHOR_PER_CYCLE_C[1]:.3f} C/cycle",
        f"{'metric':<7}{'lead':>5}{'models':>8}{'mean dRMSE_C':>14}{'per_6h_C':>10}",
    ]
    for metric in METRICS:
        pooled: dict[int, list[tuple[float, float]]] = {}
        for model, per_metric in sorted(models.items()):
            entry = per_metric.get(metric)
            if not entry:
                continue
            freshest = int(entry["freshest_lead"])
            m2 = {int(k): float(v) for k, v in entry["m2_by_lead"].items()}
            fitted = {int(k) for k in entry["v_by_lead"]}
            base_rmse = math.sqrt(m2[freshest])
            for lead in sorted(fitted):
                if lead <= freshest:
                    continue
                d_rmse = math.sqrt(m2[lead]) - base_rmse
                pooled.setdefault(lead, []).append((d_rmse, d_rmse / (4.0 * (lead - freshest))))
        for lead, rows in sorted(pooled.items()):
            mean_d = sum(d for d, _p in rows) / len(rows)
            mean_p = sum(p for _d, p in rows) / len(rows)
            lines.append(f"{metric:<7}{lead:>5}{len(rows):>8}{mean_d:>14.4f}{mean_p:>10.4f}")
    return "\n".join(lines)


def build_artifact(
    conn: sqlite3.Connection, *, as_of: str, generated_at: str, git_sha: str
) -> dict[str, object]:
    cell_stats = load_cell_stats(conn, as_of=as_of)
    models = derive_v_tables(cell_stats)
    return {
        "schema_version": 1,
        "as_of": as_of,
        "generated_at": generated_at,
        "git_sha": git_sha,
        "unit": "degC2",
        "lag_mapping": "bucket = freshest_lead + floor(cycle_lag_hours / 24); clamp to max fitted bucket",
        "min_cell_n": MIN_CELL_N,
        "settled_cells_used": sum(n for _m2, n in cell_stats.values()),
        "models": models,
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
    fname = f"staleness_variance_{args.as_of.replace('-', '')}.json"
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    (args.out_dir / fname).write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    pointer = {"artifact": fname, "sha256": sha, "as_of": args.as_of}
    (args.out_dir / "ACTIVE.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {args.out_dir / fname} (sha256={sha}); "
        f"settled_cells_used={artifact['settled_cells_used']}; "
        f"models_fitted={len(artifact['models'])}"
    )
    print(sanity_table(artifact["models"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
