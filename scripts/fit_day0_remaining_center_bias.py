#!/usr/bin/env python3
# Created: 2026-09-24
# Last reused or audited: 2026-09-30
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24;
#   docs/authority/replacement_final_form_2026_06_09.md "Day0 conditional
#   remaining-path operator"; served by src/calibration/day0_remaining_bias.py.
#   2026-09-30: gate + step table replaced by a shrunk continuous node curve after a
#   band edge and a refit gate flip each moved Helsinki q(15C) 0.07 -> 0.45 on
#   unchanged evidence.
"""Fit ``state/day0_remaining_center_bias.json``: the settlement-graded center shift
of the Day0 remaining-day carrier, one continuous curve of local hour per metric.

RECORDS. One per (city, target_date, metric, local hour on the target day): the last
live posterior of that hour whose served q is the shared remaining-day carrier
(``CARRIER_SHAPES``). Fast-residual posteriors are excluded from fitting: their
served q is the carrier after a further fast-residual transport, which the carrier
likelihood below does not describe.
Each record keeps the carrier's UNSHIFTED remaining-hourly members, typed final-daily
centers, path sigma, observed boundary and report-survival weight exactly as
persisted, plus the settled integer from ``read_current_settlement_history`` (current
resolver, known-before-cutoff labels only).

LIKELIHOOD. log P(settled integer | b) under the shipped carrier builder
(``build_day0_remaining_probability_carrier`` with ``remaining_center_bias_native``):
the same observed-boundary atom, survival mixture, settlement rounding and typed
final centers the server integrates. ``b = 0`` is the unshifted recipe. ``b`` is
evaluated on a fixed grid once per record; every fit below is a sum over that matrix.

FIT. Evidence pools per (metric, 2-hour local band). Each band's MLE ``b`` comes with
a variance clustered by city-day. Its node is the posterior mean under a N(0, tau2)
prior, tau2 the Paule-Mandel spread of the metric's band MLEs:
``node = b * tau2 / (tau2 + v)``. A station's node adds its own deviation from the
band MLE, shrunk the same way toward 0. There is no activation gate: a thin band's
node is near 0 by its own variance, so a refit moves a node by the change in its
evidence, never by a binary flip. The loader interpolates linearly between nodes at
the band centres, so the served shift is continuous in local hour.

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
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.day0_remaining_bias import (  # noqa: E402
    BAND_HOURS,
    MAX_ABS_SHIFT_C,
    NODE_HOURS,
    SCHEMA_VERSION,
    band_of,
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
BANDS = tuple(range(0, 24, BAND_HOURS))
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
    band: int
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
                band=band_of(local.hour + local.minute / 60.0),
                decided_at=decided,
                label_known_at=label.label_known_at,
                loglik=loglik,
            )
        )
    conn.close()
    return records, {"hours": len(last), "records": len(records), "skipped": dict(skipped)}


@dataclass(frozen=True)
class Estimate:
    """One pooled likelihood estimate: MLE ``b`` and its clustered variance ``v``."""

    b: float
    v: float  # inf when the curve has no interior maximum
    n: int
    clusters: int


_UNMEASURED = Estimate(0.0, math.inf, 0, 0)


def estimate(records: list[Record], cluster) -> Estimate:
    """MLE of ``b`` from the summed log-likelihood curve and its clustered variance.

    ``b`` is the vertex of the parabola through the grid maximum and its neighbours.
    The variance is the larger of the cluster sandwich (sum of squared per-cluster
    scores over the squared curvature) and the model-based rows-per-cluster /
    curvature, so neither hourly autocorrelation nor a lucky score sum can make a
    thin cell look precise.
    """

    if not records:
        return _UNMEASURED
    step = float(GRID_C[1] - GRID_C[0])
    total = np.sum([r.loglik for r in records], axis=0)
    groups: dict[object, list[np.ndarray]] = collections.defaultdict(list)
    for record in records:
        groups[cluster(record)].append(record.loglik)
    # A maximum on the grid edge saturates at the edge with the edge curvature; it
    # never becomes "unmeasured", which would flip a node to 0 between refits.
    i = min(max(int(np.argmax(total)), 1), GRID_C.size - 2)
    y0, y1, y2 = total[i - 1], total[i], total[i + 1]
    second = (y0 - 2.0 * y1 + y2) / step**2
    if not second < 0.0:
        return Estimate(0.0, math.inf, len(records), len(groups))
    b = float(np.clip(GRID_C[i] + (y0 - y2) / (2.0 * second * step), GRID_C[0], GRID_C[-1]))
    scores = [
        float(np.interp(b, GRID_C, np.gradient(np.sum(curves, axis=0), step)))
        for curves in groups.values()
    ]
    sandwich = float(np.sum(np.square(scores))) / second**2
    model = (len(records) / len(groups)) / -second
    return Estimate(b, max(sandwich, model), len(records), len(groups))


def _prior_variance(pairs: list[tuple[float, float]]) -> float:
    """Spread tau2 of the true values behind noisy (estimate, variance) pairs.

    Paule-Mandel for a prior centred at 0: the tau2 at which
    sum b^2 / (v + tau2) equals the number of estimates. Each estimate's pull is
    bounded by its own precision, so one noisy estimate (b^2 < v) cannot drive tau2
    to 0 and switch every well-measured estimate off, as a plain mean of b^2 - v does.
    """

    finite = [(b * b, v) for b, v in pairs if math.isfinite(v) and v > 0.0]
    if len(finite) < 2:
        return 0.0

    def excess(tau2: float) -> float:
        return sum(b2 / (v + tau2) for b2, v in finite) - len(finite)

    if excess(0.0) <= 0.0:
        return 0.0
    low, high = 0.0, max(b2 for b2, _v in finite) + 1.0
    for _ in range(100):
        mid = 0.5 * (low + high)
        low, high = (mid, high) if excess(mid) > 0.0 else (low, mid)
    return 0.5 * (low + high)


def _shrink(value: float, variance: float, prior: float) -> float:
    """Posterior mean under N(0, prior): value x prior / (prior + variance)."""

    if not math.isfinite(variance) or prior <= 0.0:
        return 0.0
    return value * prior / (prior + variance)


def fit_metric(records: list[Record]) -> dict:
    """Node curve for one metric: per-band estimates shrunk toward 0, plus stations.

    Pooled node = band MLE shrunk by its clustered variance under a N(0, tau2) prior,
    tau2 the Paule-Mandel spread of the band MLEs. A station's node adds its own
    deviation from the band MLE, shrunk the same way toward 0 under the spread of
    all stations' deviations in that band; its variance is never below the band's
    median within-city per-city-day variance over its own city-days. No gate: an
    unmeasured band is 0 and a
    thin one is near 0, so a refit moves each node by the change in its evidence.
    """

    by_band: dict[int, list[Record]] = collections.defaultdict(list)
    for record in records:
        by_band[record.band].append(record)
    pooled = {
        band: estimate(by_band.get(band, []), lambda r: (r.city, r.target_date))
        for band in BANDS
    }
    tau2 = _prior_variance([(e.b, e.v) for e in pooled.values()])
    nodes = [_shrink(pooled[band].b, pooled[band].v, tau2) for band in BANDS]
    station_nodes: dict[str, list[float]] = {}
    for index, band in enumerate(BANDS):
        by_city: dict[str, list[Record]] = collections.defaultdict(list)
        for record in by_band.get(band, []):
            by_city[record.city].append(record)
        base = pooled[band].b
        # A few city-days cannot measure their own dispersion: a station's variance
        # is at least the band's typical within-city per-city-day variance over its
        # own city-days.
        own = {city: estimate(rows, lambda r: r.target_date) for city, rows in by_city.items()}
        per_day = [e.v * e.clusters for e in own.values() if math.isfinite(e.v)]
        floor = float(np.median(per_day)) if per_day else math.inf
        raw = {
            city: (e.b - base, max(e.v, floor / max(e.clusters, 1))) for city, e in own.items()
        }
        spread = _prior_variance(list(raw.values()))
        for city, (offset, variance) in raw.items():
            deviation = _shrink(offset, variance, spread)
            if deviation:
                station_nodes.setdefault(city, list(nodes))[index] = float(
                    np.clip(nodes[index] + deviation, -MAX_ABS_SHIFT_C, MAX_ABS_SHIFT_C)
                )
    return {
        "nodes_c": [float(v) for v in nodes],
        "stations": {city: [float(v) for v in curve] for city, curve in sorted(station_nodes.items())},
        "prior_variance_c2": tau2,
        "bands": {
            str(band): {
                "b_mle_c": pooled[band].b,
                "variance_c2": None if not math.isfinite(pooled[band].v) else pooled[band].v,
                "n": pooled[band].n,
                "city_days": pooled[band].clusters,
                "node_c": float(nodes[index]),
            }
            for index, band in enumerate(BANDS)
        },
    }


def build_artifact(records: list[Record], *, fit_date: str, record_counts: dict) -> dict:
    metrics = sorted({r.metric for r in records})
    return {
        "schema_version": SCHEMA_VERSION,
        "fit_date": fit_date,
        "fitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "grid_c": [float(GRID_C[0]), float(GRID_C[-1]), float(GRID_C[1] - GRID_C[0])],
        "node_hours_local": list(NODE_HOURS),
        "record_counts": record_counts,
        "metrics": {
            metric: fit_metric([r for r in records if r.metric == metric]) for metric in metrics
        },
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
    print("metric band     n  city_days   b_mle   sd     node")
    for metric, curve in artifact["metrics"].items():
        for band, v in curve["bands"].items():
            sd = None if v["variance_c2"] is None else math.sqrt(v["variance_c2"])
            print(
                f"{metric:6s} {int(band):4d} {v['n']:5d} {v['city_days']:10d} "
                f"{_format(v['b_mle_c'])} {_format(sd)} {_format(v['node_c'])}"
            )
        print(
            f"{metric} prior_variance={curve['prior_variance_c2']:.4f} "
            f"station_curves={len(curve['stations'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
