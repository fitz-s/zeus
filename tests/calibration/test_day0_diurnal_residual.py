# Created: 2026-09-04
# Last reused or audited: 2026-09-30
# Authority basis: operator directive 2026-09-30 prohibits historical fitted
#   mixtures in live probability; preserve the offline estimator/operator math
#   against hand-computed values, not its own implementation's output.
"""Offline-only diurnal residual estimator, artifact lookup and pure operator."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from src.calibration import day0_diurnal_residual as mod
from src.calibration.day0_diurnal_residual import (
    APPLIED,
    ARTIFACT_UNAVAILABLE,
    INACTIVE_CELL,
    J_MAX,
    MIN_WEIGHT_ROWS,
    PRIOR_WEIGHT,
    SCHEMA_VERSION,
    Day0DiurnalMixture,
    DiurnalResidualNowcast,
    day0_diurnal_mixture,
    k_bucket,
)

FIT_DATE = "2026-08-04"
NOW = datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)


def _half_up(value: float) -> float:
    return float(math.floor(value + 0.5))


def _truncate(value: float) -> float:
    return float(math.floor(value))


def _counts(**by_j: int) -> list[int]:
    counts = [0] * (J_MAX + 1)
    for key, value in by_j.items():
        counts[int(key[1:])] = value
    return counts


def _artifact(**overrides: object) -> dict:
    base = {
        "schema_version": SCHEMA_VERSION,
        "fit_date": FIT_DATE,
        "j_max": J_MAX,
        # Peak 12 => at local hour 10, k = 2.
        "peak_hours": {"Testville": 12.0},
        "trough_hours": {"Testville": 4.0},
        "unit": {"Testville": "C"},
        "pooled": {"high|C|2": _counts(j0=60, j1=30, j2=10)},
        "city": {},
        "weights": {"high|2": {"w": 0.5, "n": MIN_WEIGHT_ROWS}},
    }
    base.update(overrides)
    return base


def _pooled_pmf() -> list[float]:
    denominator = 100 + 0.5 * (J_MAX + 1)
    raw = [60, 30, 10] + [0] * (J_MAX - 2)
    return [(count + 0.5) / denominator for count in raw]


def test_pooled_cell_is_keyed_by_settlement_unit() -> None:
    nowcast = DiurnalResidualNowcast(_artifact())

    pmf = nowcast.pmf(city="Testville", metric="high", unit="C", local_hour=10.0)
    assert pmf is not None
    for actual, want in zip(pmf, _pooled_pmf()):
        assert actual == pytest.approx(want, abs=1e-12)
    # The F histogram is a different physical grid; a C cell never serves it.
    assert nowcast.pmf(city="Testville", metric="high", unit="F", local_hour=10.0) is None


def test_city_cell_shrinks_toward_the_pooled_pmf_with_prior_25() -> None:
    city_counts = _counts(j0=5, j1=5)
    nowcast = DiurnalResidualNowcast(_artifact(city={"high|Testville|2": city_counts}))

    pmf = nowcast.pmf(city="Testville", metric="high", unit="C", local_hour=10.0)

    base = _pooled_pmf()
    expected = [(city_counts[j] + PRIOR_WEIGHT * base[j]) / (10 + PRIOR_WEIGHT) for j in range(J_MAX + 1)]
    total = sum(expected)
    for actual, want in zip(pmf, expected):
        assert actual == pytest.approx(want / total, abs=1e-12)


def test_mixture_keeps_dead_bins_and_mixes_live_mass_by_hand() -> None:
    nowcast = DiurnalResidualNowcast(_artifact())
    # Running 30.4 -> A = 30. Bins: <=28 (dead), 29 (dead), 30, 31, >=32.
    bounds = [(None, 28.0), (29.0, 29.0), (30.0, 30.0), (31.0, 31.0), (32.0, None)]
    mixture = nowcast.mixture(
        city="Testville", metric="high", unit="C", local_hour=10.0,
        running_extreme=30.4, bin_bounds=bounds, round_to_grid=_half_up,
    )
    assert mixture is not None
    assert mixture.dead == (True, True, False, False, False)
    assert mixture.weight == 0.5
    pmf = _pooled_pmf()
    assert mixture.pi[2] == pytest.approx(pmf[0], abs=1e-12)
    assert mixture.pi[3] == pytest.approx(pmf[1], abs=1e-12)
    assert mixture.pi[4] == pytest.approx(sum(pmf[2:]), abs=1e-12)

    row = [0.1, 0.1, 0.7, 0.05, 0.05]
    mixed = mixture.apply(row)
    # Dead bins untouched; live bins: (1-w) r + w (1-m) pi with m = 0.2.
    assert mixed[:2] == [0.1, 0.1]
    for index in (2, 3, 4):
        assert mixed[index] == pytest.approx(0.5 * row[index] + 0.5 * 0.8 * mixture.pi[index], abs=1e-12)
    assert sum(mixed) == pytest.approx(1.0, abs=1e-12)


def test_low_metric_reverses_direction_and_dead_side() -> None:
    artifact = _artifact(pooled={"low|C|2": _counts(j0=60, j1=30, j2=10)})
    nowcast = DiurnalResidualNowcast(artifact)
    # trough 4 => local hour 2 gives k = 2. LOW cannot settle ABOVE its running min.
    bounds = [(None, 8.0), (9.0, 9.0), (10.0, 10.0), (11.0, None)]
    mixture = nowcast.mixture(
        city="Testville", metric="low", unit="C", local_hour=2.0,
        running_extreme=10.0, bin_bounds=bounds, round_to_grid=_half_up,
    )
    assert mixture is not None
    assert mixture.dead == (False, False, False, True)
    pmf = _pooled_pmf()
    assert mixture.pi[2] == pytest.approx(pmf[0], abs=1e-12)
    assert mixture.pi[1] == pytest.approx(pmf[1], abs=1e-12)
    assert mixture.pi[0] == pytest.approx(sum(pmf[2:]), abs=1e-12)


def test_city_grid_places_the_anchor_hong_kong_truncates() -> None:
    nowcast = DiurnalResidualNowcast(_artifact())
    bounds = [(None, 29.0), (30.0, 30.0), (31.0, None)]
    kwargs = dict(city="Testville", metric="high", unit="C", local_hour=10.0,
                  running_extreme=29.6, bin_bounds=bounds)
    half_up = nowcast.mixture(round_to_grid=_half_up, **kwargs)
    truncate = nowcast.mixture(round_to_grid=_truncate, **kwargs)
    # Half-up: A = 30, bin <=29 is dead. Truncate: A = 29, nothing is dead yet.
    assert half_up.anchor == 30.0 and half_up.dead == (True, False, False)
    assert truncate.anchor == 29.0 and truncate.dead == (False, False, False)


def test_zero_weight_is_the_identity_and_unfitted_cell_serves_zero() -> None:
    nowcast = DiurnalResidualNowcast(_artifact(weights={}))
    mixture = nowcast.mixture(
        city="Testville", metric="high", unit="C", local_hour=10.0, running_extreme=30.0,
        bin_bounds=[(None, 29.0), (30.0, 30.0), (31.0, None)], round_to_grid=_half_up,
    )
    assert mixture.weight == 0.0 and mixture.status == INACTIVE_CELL
    row = [0.2, 0.5, 0.3]
    assert mixture.apply(row) == row


def test_mixture_round_trips_through_its_payload() -> None:
    nowcast = DiurnalResidualNowcast(_artifact())
    mixture = nowcast.mixture(
        city="Testville", metric="high", unit="C", local_hour=10.0, running_extreme=30.0,
        bin_bounds=[(None, 29.0), (30.0, 30.0), (31.0, None)], round_to_grid=_half_up,
    )
    restored = Day0DiurnalMixture.from_payload(json.loads(json.dumps(mixture.to_payload())))
    assert restored == mixture
    assert restored.identity() == mixture.identity()


def test_apply_rejects_a_row_of_the_wrong_shape() -> None:
    mixture = Day0DiurnalMixture(0.5, (0.5, 0.5), (False, False), 2, 30.0, FIT_DATE, "x")
    with pytest.raises(ValueError, match="SHAPE"):
        mixture.apply([1.0])


def test_k_bucket_cells() -> None:
    assert [k_bucket(k) for k in (-9, -3, 0, 3, 4, 7, 8, 15)] == [-3, -3, 0, 3, 4, 4, 8, 8]


def test_artifact_weight_cell_below_minimum_rows_is_refused() -> None:
    with pytest.raises(ValueError):
        DiurnalResidualNowcast(_artifact(weights={"high|2": {"w": 0.4, "n": MIN_WEIGHT_ROWS - 1}}))


# ----------------------------- served lookup ------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    mod.reset_cache()
    yield
    mod.reset_cache()


def _install(tmp_path, monkeypatch, artifact: object) -> None:
    path = tmp_path / mod.ARTIFACT_FILENAME
    path.write_text(artifact if isinstance(artifact, str) else json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(mod, "artifact_path", lambda: path)


def _served(decision_time: datetime = NOW):
    # Testville is served at UTC local time; 10:00 local => k = 2.
    return day0_diurnal_mixture(
        city="Testville", metric="high", unit="C",
        decision_time=decision_time.replace(hour=10),
        timezone_name="UTC", running_extreme=30.0,
        bin_bounds=[(None, 29.0), (30.0, 30.0), (31.0, None)], round_to_grid=_half_up,
    )


def test_served_lookup_applies_a_fresh_artifact(tmp_path, monkeypatch) -> None:
    _install(tmp_path, monkeypatch, _artifact())
    mixture, provenance = _served()
    assert mixture is not None and provenance["day0_diurnal_mixture_status"] == APPLIED
    assert provenance["day0_diurnal_mixture_artifact"].startswith(FIT_DATE + ":")


@pytest.mark.parametrize(
    "setup",
    ["missing", "malformed", "schema", "stale", "future"],
)
def test_served_lookup_falls_back_to_the_carrier_q(tmp_path, monkeypatch, setup) -> None:
    when = NOW
    if setup == "missing":
        monkeypatch.setattr(mod, "artifact_path", lambda: tmp_path / "absent.json")
    elif setup == "malformed":
        _install(tmp_path, monkeypatch, "{not json")
    elif setup == "schema":
        _install(tmp_path, monkeypatch, _artifact(schema_version=SCHEMA_VERSION - 1))
    else:
        _install(tmp_path, monkeypatch, _artifact())
        when = NOW + timedelta(days=mod.MAX_ARTIFACT_AGE_DAYS + 1) if setup == "stale" else NOW - timedelta(days=3)
    mixture, provenance = _served(when)
    assert mixture is None
    assert provenance == {"day0_diurnal_mixture_status": ARTIFACT_UNAVAILABLE}


def _bad_counts(value) -> list:
    counts = _counts(j0=60, j1=30, j2=10)
    counts[3] = value
    return counts


@pytest.mark.parametrize(
    "corruption",
    [
        {"pooled": {"high|C|2": _bad_counts(-5)}},
        {"pooled": {"high|C|2": _bad_counts(float("nan"))}},
        {"pooled": {"high|C|2": _bad_counts(float("inf"))}},
        {"pooled": {"high|C|2": _bad_counts(2.5)}},
        {"city": {"high|Testville|2": _bad_counts(-1)}},
        {"weights": {"high|2": {"w": float("nan"), "n": MIN_WEIGHT_ROWS}}},
        {"weights": {"high|2": {"w": 1.5, "n": MIN_WEIGHT_ROWS}}},
        {"weights": {"high|2": {"w": -0.1, "n": MIN_WEIGHT_ROWS}}},
        {"peak_hours": {"Testville": float("nan")}},
        {"peak_hours": {"Testville": -3.0}},
        {"peak_hours": {"Testville": 30.0}},
    ],
)
def test_corrupt_artifact_values_fall_back_to_the_unmixed_q(
    tmp_path, monkeypatch, corruption
) -> None:
    """A negative, non-finite or non-integer count, a weight outside [0, 1], or an
    anchor hour off the clock makes the artifact malformed: the served lookup returns
    the unmixed-q fallback and never raises into the cut."""

    path = tmp_path / mod.ARTIFACT_FILENAME
    path.write_text(json.dumps(_artifact(**corruption)), encoding="utf-8")
    monkeypatch.setattr(mod, "artifact_path", lambda: path)

    mixture, provenance = _served()

    assert mixture is None
    assert provenance == {"day0_diurnal_mixture_status": ARTIFACT_UNAVAILABLE}
    with pytest.raises(ValueError):
        DiurnalResidualNowcast(_artifact(**corruption))
