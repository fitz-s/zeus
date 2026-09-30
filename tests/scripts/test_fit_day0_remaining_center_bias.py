# Created: 2026-09-24
# Last reused or audited: 2026-09-30
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24;
#   2026-09-30 continuity repair: scripts/fit_day0_remaining_center_bias.py serves a
#   shrunk continuous node curve per metric (no activation gate, no step table).
"""Fitter contracts on synthetic log-likelihood curves (no database)."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np

from scripts import fit_day0_remaining_center_bias as fit
from src.calibration.day0_remaining_bias import NODE_HOURS, RemainingBiasTable


def _curve(true_b: float, sharp: float = 8.0) -> np.ndarray:
    return -sharp * (fit.GRID_C - true_b) ** 2 - 1.0


def _records(metric: str, band: int, true_b: float, *, days: int, cities: int, hours: int,
             noise: float, sharp: float = 8.0, seed: int = 7, start_day: int = 0):
    rng = np.random.default_rng(seed)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    out = []
    for day in range(start_day, start_day + days):
        decided = start + timedelta(days=day, hours=6)
        for city in range(cities):
            day_b = true_b + rng.normal(0.0, noise)
            for hour in range(hours):
                out.append(
                    fit.Record(
                        city=f"city{city}",
                        target_date=(start + timedelta(days=day)).date().isoformat(),
                        metric=metric,
                        band=band,
                        decided_at=decided + timedelta(minutes=hour),
                        label_known_at=decided + timedelta(hours=18),
                        loglik=_curve(day_b, sharp),
                    )
                )
    return out


def _node(artifact: dict, metric: str, band: int) -> float:
    return artifact["metrics"][metric]["bands"][str(band)]["node_c"]


def test_well_measured_band_serves_nearly_its_estimate() -> None:
    records = [
        *_records("high", 4, 0.5, days=30, cities=8, hours=2, noise=0.3),
        *_records("high", 12, -0.3, days=30, cities=8, hours=2, noise=0.3, seed=8),
        *_records("high", 20, 0.0, days=30, cities=8, hours=2, noise=0.3, seed=9),
    ]

    artifact = fit.build_artifact(records, fit_date="2026-09-01", record_counts={})

    band = artifact["metrics"]["high"]["bands"]["4"]
    assert abs(band["b_mle_c"] - 0.5) <= 0.1
    assert 0.8 * band["b_mle_c"] <= band["node_c"] <= band["b_mle_c"]
    assert _node(artifact, "high", 12) < 0.0


def test_unmeasured_band_serves_zero_and_the_curve_has_every_node() -> None:
    records = _records("high", 4, 0.5, days=30, cities=8, hours=2, noise=0.3)

    artifact = fit.build_artifact(records, fit_date="2026-09-01", record_counts={})

    nodes = artifact["metrics"]["high"]["nodes_c"]
    assert len(nodes) == len(NODE_HOURS)
    assert nodes[fit.BANDS.index(10)] == 0.0


def test_thin_band_is_shrunk_toward_zero_by_its_own_variance() -> None:
    """A band with a few noisy city-days and a large raw estimate may not serve its
    raw estimate: the posterior mean under the metric's prior shrinks it by
    tau2 / (tau2 + v), so its node is a small fraction of the MLE."""

    well = [
        *_records("high", 4, 0.3, days=30, cities=8, hours=2, noise=0.3),
        *_records("high", 12, -0.2, days=30, cities=8, hours=2, noise=0.3, seed=8),
        *_records("high", 20, 0.1, days=30, cities=8, hours=2, noise=0.3, seed=9),
    ]
    thin = _records("high", 22, 1.0, days=3, cities=1, hours=2, noise=1.0, sharp=0.6, seed=3)

    artifact = fit.build_artifact(well + thin, fit_date="2026-09-01", record_counts={})

    band = artifact["metrics"]["high"]["bands"]["22"]
    assert abs(band["b_mle_c"]) >= 0.5
    assert abs(band["node_c"]) <= 0.25 * abs(band["b_mle_c"])


def test_one_new_day_cannot_flip_a_band_on_or_off() -> None:
    """Refit-to-refit: adding one settled day moves every node by a small amount; the
    served curve never jumps between 0 and the full estimate as a gate would."""

    def build(days: int) -> dict:
        records = [
            *_records("high", 0, 0.8, days=days, cities=4, hours=2, noise=0.6, sharp=2.0),
            *_records("high", 12, -0.2, days=days, cities=4, hours=2, noise=0.6, seed=8),
            *_records("high", 20, 0.1, days=days, cities=4, hours=2, noise=0.6, seed=9),
        ]
        return fit.build_artifact(records, fit_date="2026-09-01", record_counts={})

    for days in range(6, 20):
        before, after = build(days), build(days + 1)
        for a, b in zip(before["metrics"]["high"]["nodes_c"], after["metrics"]["high"]["nodes_c"]):
            assert abs(b - a) < 0.25


def test_station_node_is_shrunk_toward_the_pooled_node() -> None:
    records = _records("high", 4, 0.3, days=30, cities=10, hours=2, noise=0.1)
    # city0 runs 0.6 warmer than the pool on every day.
    records = [
        r if r.city != "city0" else fit.Record(
            r.city, r.target_date, r.metric, r.band, r.decided_at, r.label_known_at,
            _curve(0.9),
        )
        for r in records
    ]

    curve = fit.build_artifact(records, fit_date="2026-09-01", record_counts={})["metrics"]["high"]
    table = RemainingBiasTable(
        {"schema_version": fit.SCHEMA_VERSION, "fit_date": "2026-09-01",
         "metrics": {"high": curve}},
        identity="t",
    )

    pooled = table.shift(city="unseen", metric="high", local_hour=5.0).shift_c
    own = table.shift(city="city0", metric="high", local_hour=5.0).shift_c
    assert pooled < own <= 0.9 + 1e-9


def test_estimate_variance_is_clustered_by_city_day() -> None:
    """Twenty identical hourly rows of one city-day are one piece of evidence."""

    one_day = _records("high", 4, 0.5, days=1, cities=1, hours=1, noise=0.0)
    twenty = _records("high", 4, 0.5, days=1, cities=1, hours=20, noise=0.0)

    v1 = fit.estimate(one_day, lambda r: (r.city, r.target_date)).v
    v20 = fit.estimate(twenty, lambda r: (r.city, r.target_date)).v

    assert math.isfinite(v1) and v20 >= v1 - 1e-12


def test_edge_maximum_is_unmeasured() -> None:
    rows = _records("high", 4, 3.0, days=5, cities=2, hours=1, noise=0.0)

    est = fit.estimate(rows, lambda r: (r.city, r.target_date))

    assert est.b == 0.0 and math.isinf(est.v)


def test_fast_residual_posteriors_are_excluded_from_records(tmp_path, monkeypatch) -> None:
    """A fast-residual posterior served the carrier AFTER a further likelihood
    transport, so the carrier likelihood does not describe it: it must never become
    a record, neither by winning its hour nor as the only posterior of an hour."""

    provenance = json.dumps(
        {
            "day0_remaining_carrier_future_extremes_c": [29.5, 30.1, 30.4],
            "day0_remaining_carrier_final_extremes_c": [30.0],
            "day0_remaining_carrier_path_error_sigma_c": 0.8,
            "day0_preliminary_report_survival_likelihood": {
                "boundary_survival_probability": 0.95
            },
            "day0_provisional_observation": {"observed_extreme_c": 28.3},
        }
    )
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, city TEXT, "
        "target_date TEXT, temperature_metric TEXT, computed_at TEXT, q_shape TEXT, "
        "runtime_layer TEXT, provenance_json TEXT)"
    )
    # Hong Kong is UTC+8: 18:10Z / 18:40Z on 09-19 are local 02:10 / 02:40 on 09-20,
    # 20:10Z is local 04:10.
    rows = (
        (1, "2026-09-19T18:10:00+00:00", "day0_remaining_shared_carrier_v3"),
        (2, "2026-09-19T18:40:00+00:00", "fused_day0_fast_residual_likelihood"),
        (3, "2026-09-19T20:10:00+00:00", "fused_day0_fast_residual_likelihood"),
    )
    conn.executemany(
        "INSERT INTO forecast_posteriors VALUES (?, 'Hong Kong', '2026-09-20', 'high', ?, ?, "
        "'live', ?)",
        [(pid, computed, shape, provenance) for pid, computed, shape in rows],
    )
    conn.commit()
    conn.close()
    label = SimpleNamespace(
        city="Hong Kong",
        target_date="2026-09-20",
        metric="high",
        settlement_value=30.0,
        label_known_at=datetime(2026, 9, 21, tzinfo=UTC),
    )
    monkeypatch.setattr(
        fit,
        "read_current_settlement_history",
        lambda *_args, **_kwargs: SimpleNamespace(rows=(label,)),
    )

    records, counts = fit.build_records(str(db), fit_date="2026-09-25")

    assert counts["hours"] == 1
    assert [r.decided_at for r in records] == [datetime(2026, 9, 19, 18, 10, tzinfo=UTC)]
    assert records[0].band == 2
