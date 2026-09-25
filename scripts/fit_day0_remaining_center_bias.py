#!/usr/bin/env python3
# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24;
#   docs/authority/replacement_final_form_2026_06_09.md "Day0 conditional
#   remaining-path operator"; served by src/calibration/day0_remaining_bias.py.
"""Fit ``state/day0_remaining_center_bias.json``: the settlement-graded center shift
of the Day0 remaining-day carrier, per (metric, 2-hour local band).

RECORDS. One per (city, target_date, metric, local hour on the target day): the last
live posterior of that hour whose served q is the shared remaining-day carrier
(``CARRIER_SHAPES``). Fast-residual posteriors are excluded from fitting and from
every evaluation: their served q is the carrier after a further fast-residual
transport, which the carrier likelihood below does not describe.
Each record keeps the carrier's UNSHIFTED remaining-hourly members, typed final-daily
centers, path sigma, observed boundary and report-survival weight exactly as
persisted, plus the settled integer from ``read_current_settlement_history`` (current
resolver, known-before-cutoff labels only).

LIKELIHOOD. log P(settled integer | b) under the shipped carrier builder
(``build_day0_remaining_probability_carrier`` with ``remaining_center_bias_native``):
the same observed-boundary atom, survival mixture, settlement rounding and typed
final centers the server integrates. ``b = 0`` is the live unshifted recipe, so every
comparison is against what already serves. ``b`` is evaluated on a fixed grid once
per record; every fit below is a sum over that matrix.

FIT. Per cell, the pooled MLE of ``b``; each city's own MLE is shrunk toward it by
empirical Bayes (normal-normal, method-of-moments between-city variance, per-city
variance from the log-likelihood curvature deflated by rows per city-day) and snapped
to the grid.

ACTIVATION (chosen inside the training data only). The last 30% of training dates is
an inner validation block; ``b`` is refit on the earlier dates (>= MIN_ROWS rows) and
scored there, clustered by city-day (hourly rows averaged within each city-day first).
A cell is active only with a mean gain >= MIN_GAIN_NATS and a one-sided 95% upper
bound of (new - old) log loss below zero. Active cells are then refit on all training
dates.

OUTER FOLDS. The last 30% of dates split into chronological blocks. Each block is
predicted by the whole rule (fit + activation) trained only on earlier dates whose
labels were known before the block's first decision; inactive cells score as
unchanged. The per-cell clustered result is printed and stored as ``oos_*``.

SERVED. A cell is served (``active``) only when the rule activates it on all training
dates (``rule_active``) AND the rule's own outer-fold record for that cell meets the
same bar. A cell the rule never activated out of sample has no out-of-sample win and
stays unshifted.

WALK-FORWARD. The artifact trains on labels known before ``fit_date`` 00:00 UTC and
serves decisions from that instant on; the loader refuses an artifact dated after
the decision. READ-ONLY on the database; writes only the JSON artifact.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.day0_remaining_bias import (  # noqa: E402
    MAX_ABS_SHIFT_C,
    SCHEMA_VERSION,
    cell_key,
)
from src.config import runtime_cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data.current_settlement_history import read_current_settlement_history  # noqa: E402
from src.data.day0_hourly_vectors import build_day0_remaining_probability_carrier  # noqa: E402
from src.signal.ensemble_signal import sigma_instrument_for_city  # noqa: E402

DEFAULT_FORECAST_DB = os.path.join(REPO, "state", "zeus-forecasts.db")
DEFAULT_OUT = os.path.join(REPO, "state", "day0_remaining_center_bias.json")

# Only posteriors whose served q IS the shared carrier. The materializer rewrites
# q_shape to "fused_day0_fast_residual_likelihood" whenever it transports the carrier
# q and draws through the fast-residual likelihood, so that shape served a different
# distribution than the carrier likelihood scored here and is excluded.
CARRIER_SHAPES = (
    "day0_remaining_shared_carrier_v1",
    "day0_remaining_shared_carrier_v2",
    "day0_remaining_shared_carrier_v3",
)
GRID_C = np.round(np.arange(-1.5, 1.5 + 1e-9, 0.1), 10)
ZERO = int(np.argmin(np.abs(GRID_C)))
MIN_ROWS = 200
MIN_GAIN_NATS = 0.02
Z_ONE_SIDED_95 = 1.6448536269514722
HOLDOUT_FRACTION = 0.3
OUTER_BLOCKS = 3
PROBABILITY_FLOOR = 1e-12
assert GRID_C[ZERO] == 0.0 and np.max(np.abs(GRID_C)) <= MAX_ABS_SHIFT_C

_FIELDS_SQL = """
SELECT runtime_layer,
       json_extract(provenance_json, '$.day0_remaining_carrier_future_extremes_c'),
       json_extract(provenance_json, '$.day0_remaining_carrier_final_extremes_c'),
       json_extract(provenance_json, '$.day0_remaining_carrier_path_error_sigma_c'),
       json_extract(provenance_json,
           '$.day0_preliminary_report_survival_likelihood.boundary_survival_probability'),
       json_extract(provenance_json, '$.day0_provisional_observation.observed_extreme_c'),
       json_extract(provenance_json,
           '$.day0_causal_evidence_bundle.observation_context.observed_extreme_c')
  FROM forecast_posteriors
 WHERE posterior_id = ?
"""


@dataclass(frozen=True)
class Record:
    city: str
    target_date: str
    metric: str
    cell: str
    decided_at: datetime
    label_known_at: datetime
    loglik: np.ndarray  # log P(settled integer | GRID_C[i])


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _instant(text: str) -> datetime:
    parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _loglik_row(
    *,
    city: object,
    metric: str,
    settled: float,
    future_c: list[float],
    final_c: list[float],
    path_sigma_c: float,
    survival: float,
    observed_c: float,
) -> np.ndarray:
    """log P(settled | b) for every grid shift, through the shipped carrier builder."""

    unit = str(city.settlement_unit).upper()
    scale, offset = (1.0, 0.0) if unit == "C" else (9.0 / 5.0, 32.0)
    k = float(settled)
    kwargs = dict(
        future_extremes_c=tuple(v * scale + offset for v in future_c),
        final_extreme_centers_c=tuple(v * scale + offset for v in final_c),
        boundary_scenarios=((observed_c * scale + offset, survival), (None, 1.0 - survival)),
        metric=metric,
        path_error_sigma_c=path_sigma_c * scale,
        instrument_sigma_c=float(sigma_instrument_for_city(city).to(unit).value),
        bin_bounds_c=((None, k - 1.0), (k, k), (k + 1.0, None)),
        n_point=1,
        n_samples=1,
        identity_inputs={"unit": unit},
        settlement_semantics=SettlementSemantics.for_city(city),
    )
    out = np.empty(GRID_C.size)
    for index, shift in enumerate(GRID_C):
        carrier = build_day0_remaining_probability_carrier(
            **kwargs, remaining_center_bias_native=float(shift) * scale
        )
        out[index] = math.log(max(float(carrier["q"][1]), PROBABILITY_FLOOR))
    return out


def build_records(forecast_db: str, *, fit_date: str) -> tuple[list[Record], dict]:
    """Settled carrier records whose labels were known before ``fit_date`` 00:00 UTC."""

    cities = runtime_cities_by_name()
    cutoff = datetime.combine(date.fromisoformat(fit_date), time(0), timezone.utc)
    conn = _ro(forecast_db)
    history = read_current_settlement_history(conn, cities_by_name=cities, as_of=cutoff)
    labels = {(row.city, row.target_date, row.metric): row for row in history.rows}
    last: dict[tuple, tuple[datetime, int]] = {}
    placeholders = ",".join("?" for _ in CARRIER_SHAPES)
    for posterior_id, city_name, target, metric, computed in conn.execute(
        # Index-only on q_shape plus leading row columns. runtime_layer sits after the
        # large provenance blob in the row; testing it here reads every blob's
        # overflow chain, so it is checked on the one chosen row per hour instead.
        "SELECT posterior_id, city, target_date, temperature_metric, computed_at "
        f"FROM forecast_posteriors WHERE q_shape IN ({placeholders})",
        CARRIER_SHAPES,
    ):
        city = cities.get(city_name)
        if city is None or (city_name, target, metric) not in labels:
            continue
        decided = _instant(computed)
        local = decided.astimezone(ZoneInfo(city.timezone))
        if local.date().isoformat() != target:
            continue
        key = (city_name, target, metric, local.hour)
        if key not in last or decided > last[key][0]:
            last[key] = (decided, int(posterior_id))
    records: list[Record] = []
    skipped: collections.Counter = collections.Counter()
    for (city_name, target, metric, hour), (decided, posterior_id) in sorted(last.items()):
        row = conn.execute(_FIELDS_SQL, (posterior_id,)).fetchone()
        layer, future, final, sigma, survival, observed, bundle_observed = row
        observed = observed if observed is not None else bundle_observed
        if layer != "live" or future is None:
            skipped["no_carrier"] += 1
            continue
        if sigma is None or survival is None or observed is None:
            skipped["carrier_fields_missing"] += 1
            continue
        city = cities[city_name]
        label = labels[(city_name, target, metric)]
        try:
            loglik = _loglik_row(
                city=city,
                metric=metric,
                settled=label.settlement_value,
                future_c=[float(v) for v in json.loads(future)],
                final_c=[float(v) for v in json.loads(final or "[]")],
                path_sigma_c=float(sigma),
                survival=float(survival),
                observed_c=float(observed),
            )
        except ValueError:
            skipped["operator_rejected"] += 1
            continue
        local = decided.astimezone(ZoneInfo(city.timezone))
        records.append(
            Record(
                city=city_name,
                target_date=target,
                metric=metric,
                cell=cell_key(metric, local.hour + local.minute / 60.0),
                decided_at=decided,
                label_known_at=label.label_known_at,
                loglik=loglik,
            )
        )
    conn.close()
    return records, {"hours": len(last), "records": len(records), "skipped": dict(skipped)}


def _grid_index(value: float) -> int:
    return int(np.argmin(np.abs(GRID_C - value)))


def fit_cell(records: list[Record]) -> tuple[int, dict[str, int]]:
    """Pooled grid MLE and EB-shrunk per-city grid indices for one cell."""

    total = np.sum([r.loglik for r in records], axis=0)
    pooled = int(np.argmax(total))
    by_city: dict[str, list[Record]] = collections.defaultdict(list)
    for record in records:
        by_city[record.city].append(record)
    raw: dict[str, tuple[float, float]] = {}
    step = float(GRID_C[1] - GRID_C[0])
    for city, rows in by_city.items():
        curve = np.sum([r.loglik for r in rows], axis=0)
        best = int(np.argmax(curve))
        if best in (0, GRID_C.size - 1):
            continue
        curvature = (curve[best + 1] - 2.0 * curve[best] + curve[best - 1]) / step**2
        if not curvature < 0.0:
            continue
        rows_per_day = len(rows) / len({r.target_date for r in rows})
        raw[city] = (float(GRID_C[best]), -rows_per_day / curvature)
    b_pool = float(GRID_C[pooled])
    tau2 = 0.0
    if len(raw) >= 2:
        tau2 = max(
            0.0,
            float(np.mean([(b - b_pool) ** 2 - v for b, v in raw.values()])),
        )
    stations = {
        city: _grid_index(b_pool + tau2 / (tau2 + v) * (b - b_pool))
        for city, (b, v) in raw.items()
    }
    return pooled, stations


def _chosen(record: Record, model: tuple[int, dict[str, int]] | None) -> int:
    if model is None:
        return ZERO
    pooled, stations = model
    return stations.get(record.city, pooled)


def clustered_gain(records: list[Record], model_for) -> dict:
    """Mean per-city-day log-loss reduction (nats) and the one-sided 95% UB of new-old.

    ``model_for(record)`` is the (pooled, stations) model serving that record, or None
    for an unchanged (b = 0) record. Hourly rows are averaged within each city-day first.
    """

    days: dict[tuple, list[float]] = collections.defaultdict(list)
    for record in records:
        index = _chosen(record, model_for(record))
        days[(record.city, record.target_date)].append(
            float(record.loglik[index] - record.loglik[ZERO])
        )
    gains = np.asarray([np.mean(v) for v in days.values()])
    if gains.size < 2:
        return {"n": len(records), "city_days": int(gains.size), "gain": None, "ub_new_minus_old": None}
    mean = float(gains.mean())
    se = float(gains.std(ddof=1) / math.sqrt(gains.size))
    return {
        "n": len(records),
        "city_days": int(gains.size),
        "gain": mean,
        "ub_new_minus_old": -mean + Z_ONE_SIDED_95 * se,
    }


def _passes(result: dict | None) -> bool:
    """The activation bar: mean gain >= MIN_GAIN_NATS and UB(new - old) < 0."""

    return (
        result is not None
        and result["gain"] is not None
        and result["gain"] >= MIN_GAIN_NATS
        and result["ub_new_minus_old"] < 0.0
    )


def _split(records: list[Record]) -> tuple[list[Record], list[Record]]:
    """Chronological inner (fit, validation) by target date: validation = the last
    HOLDOUT_FRACTION of dates. Label latency is enforced once, at the outer boundary
    (every record here was already known before the outer block or ``fit_date``);
    re-imposing it inside the training set would drop the whole pre-backfill history
    (current-resolver labels for 08-24..09-02 were recorded 09-13) without protecting
    anything the caller will be scored on."""

    dates = sorted({r.target_date for r in records})
    if len(dates) < 2:
        return records, []
    first = dates[max(1, int(len(dates) * (1.0 - HOLDOUT_FRACTION)))]
    return (
        [r for r in records if r.target_date < first],
        [r for r in records if r.target_date >= first],
    )


def fit_rule(records: list[Record]) -> tuple[dict, dict]:
    """(active models by cell, per-cell inner-validation verdicts) from ``records`` only."""

    inner_train, inner_valid = _split(records)
    by_cell = collections.defaultdict(list)
    for record in records:
        by_cell[record.cell].append(record)
    active: dict[str, tuple[int, dict[str, int]]] = {}
    verdicts: dict[str, dict] = {}
    for cell, rows in sorted(by_cell.items()):
        fit_rows = [r for r in inner_train if r.cell == cell]
        valid_rows = [r for r in inner_valid if r.cell == cell]
        verdict = {"n": len(rows), "active": False, "inner": None}
        # The activation evidence must itself come from a fit on >= MIN_ROWS rows.
        if len(fit_rows) >= MIN_ROWS and valid_rows:
            model = fit_cell(fit_rows)
            verdict["inner"] = clustered_gain(valid_rows, lambda _record: model)
            verdict["active"] = _passes(verdict["inner"])
        if verdict["active"]:
            active[cell] = fit_cell(rows)
        verdicts[cell] = verdict
    return active, verdicts


def outer_folds(records: list[Record]) -> dict:
    """Score the whole rule on chronological blocks of the last HOLDOUT_FRACTION."""

    dates = sorted({r.target_date for r in records})
    tail = dates[int(len(dates) * (1.0 - HOLDOUT_FRACTION)):]
    blocks = [list(b) for b in np.array_split(tail, min(OUTER_BLOCKS, len(tail))) if len(b)]
    scored: list[Record] = []
    models_by_record: dict[int, dict] = {}
    activations: collections.Counter = collections.Counter()
    for block in blocks:
        test = [r for r in records if block[0] <= r.target_date <= block[-1]]
        if not test:
            continue
        start = min(r.decided_at for r in test)
        train = [r for r in records if r.target_date < block[0] and r.label_known_at < start]
        active, _verdicts = fit_rule(train)
        activations.update(active.keys())
        for record in test:
            models_by_record[id(record)] = active
            scored.append(record)
    by_cell = collections.defaultdict(list)
    for record in scored:
        by_cell[record.cell].append(record)

    def gain(rows: list[Record]) -> dict:
        return clustered_gain(rows, lambda r: models_by_record[id(r)].get(r.cell))

    return {
        "blocks": [[str(b[0]), str(b[-1])] for b in blocks],
        "cells": {
            cell: {**gain(rows), "active_blocks": activations.get(cell, 0)}
            for cell, rows in sorted(by_cell.items())
        },
        "metrics": {
            metric: gain([r for r in scored if r.metric == metric])
            for metric in ("high", "low")
            if any(r.metric == metric for r in scored)
        },
    }


def build_artifact(records: list[Record], *, fit_date: str, record_counts: dict) -> dict:
    active, verdicts = fit_rule(records)
    folds = outer_folds(records)
    cells = {}
    for cell, verdict in verdicts.items():
        model = active.get(cell) or fit_cell([r for r in records if r.cell == cell])
        pooled, stations = model
        oos = folds["cells"].get(cell)
        cells[cell] = {
            "b_c": float(GRID_C[pooled]),
            "stations": {city: float(GRID_C[i]) for city, i in sorted(stations.items())},
            "n": verdict["n"],
            "rule_active": bool(verdict["active"]),
            "active": bool(verdict["active"]) and _passes(oos),
            "inner_gain": None if verdict["inner"] is None else verdict["inner"]["gain"],
            "inner_ub_new_minus_old": (
                None if verdict["inner"] is None else verdict["inner"]["ub_new_minus_old"]
            ),
            "oos_gain": (oos or {}).get("gain"),
            "oos_ub_new_minus_old": (oos or {}).get("ub_new_minus_old"),
            "oos_city_days": (oos or {}).get("city_days"),
            "oos_active_blocks": (oos or {}).get("active_blocks"),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "fit_date": fit_date,
        "fitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "grid_c": [float(GRID_C[0]), float(GRID_C[-1]), float(GRID_C[1] - GRID_C[0])],
        "rule": {
            "min_rows": MIN_ROWS,
            "min_gain_nats": MIN_GAIN_NATS,
            "holdout_fraction": HOLDOUT_FRACTION,
            "outer_blocks": OUTER_BLOCKS,
        },
        "record_counts": record_counts,
        "outer_folds": {"blocks": folds["blocks"], "metrics": folds["metrics"]},
        "cells": cells,
    }


def _write_artifact_atomic(artifact: dict, out_path: str) -> None:
    tmp_path = f"{out_path}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp_path, out_path)


def _format(value: object) -> str:
    return "      -" if value is None else f"{value:+.4f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-db", default=DEFAULT_FORECAST_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--fit-date",
        default=None,
        help="Train on labels known before this UTC date (default: today UTC). Also "
        "the first date the artifact may serve.",
    )
    args = parser.parse_args()
    fit_date = args.fit_date or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(fit_date)
    records, counts = build_records(args.forecast_db, fit_date=fit_date)
    artifact = build_artifact(records, fit_date=fit_date, record_counts=counts)
    _write_artifact_atomic(artifact, args.out)
    print(f"wrote {args.out} fit_date={fit_date} counts={counts}")
    print(f"outer blocks {artifact['outer_folds']['blocks']}")
    print(
        "cell        n  rule   served  b_c   inner_gain  inner_ub  "
        "oos_days  oos_gain  oos_ub(new-old)  blocks"
    )
    for cell, v in sorted(
        artifact["cells"].items(), key=lambda kv: (kv[0].split("|")[0], int(kv[0].split("|")[1]))
    ):
        print(
            f"{cell:9s} {v['n']:5d}  {str(v['rule_active']):5s}  {str(v['active']):5s} "
            f"{v['b_c']:+.2f}  {_format(v['inner_gain'])}  "
            f"{_format(v['inner_ub_new_minus_old'])}  {v['oos_city_days'] or 0:8d}  "
            f"{_format(v['oos_gain'])}  {_format(v['oos_ub_new_minus_old'])}  "
            f"{v['oos_active_blocks'] or 0}"
        )
    for metric, v in artifact["outer_folds"]["metrics"].items():
        print(
            f"{metric} pooled outer: n={v['n']} city_days={v['city_days']} "
            f"gain={_format(v['gain'])} ub(new-old)={_format(v['ub_new_minus_old'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
