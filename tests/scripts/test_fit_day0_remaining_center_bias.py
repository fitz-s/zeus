# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24;
#   scripts/fit_day0_remaining_center_bias.py activation rule (inner validation inside
#   each chronological outer fold, city-day clustered, >= 0.02 nats and UB < 0).
"""Fitter contracts on synthetic log-likelihood curves (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from scripts import fit_day0_remaining_center_bias as fit


def _curve(true_b: float, sharp: float = 8.0) -> np.ndarray:
    return -sharp * (fit.GRID_C - true_b) ** 2 - 1.0


def _records(cell: str, true_b: float, *, days: int, cities: int, hours: int, noise: float):
    rng = np.random.default_rng(7)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    out = []
    for day in range(days):
        decided = start + timedelta(days=day, hours=6)
        for city in range(cities):
            for hour in range(hours):
                out.append(
                    fit.Record(
                        city=f"city{city}",
                        target_date=(start + timedelta(days=day)).date().isoformat(),
                        metric=cell.split("|")[0],
                        cell=cell,
                        decided_at=decided + timedelta(minutes=hour),
                        label_known_at=decided + timedelta(hours=18),
                        loglik=_curve(true_b + rng.normal(0.0, noise)),
                    )
                )
    return out


def test_real_shift_is_activated_and_recovered() -> None:
    records = _records("high|4", 0.5, days=30, cities=6, hours=2, noise=0.3)

    active, verdicts = fit.fit_rule(records)

    assert verdicts["high|4"]["active"] is True
    pooled, _stations = active["high|4"]
    assert abs(fit.GRID_C[pooled] - 0.5) <= 0.1


def test_null_cell_is_not_activated() -> None:
    records = _records("low|0", 0.0, days=30, cities=6, hours=2, noise=0.3)

    active, verdicts = fit.fit_rule(records)

    assert "low|0" not in active
    assert verdicts["low|0"]["active"] is False


def test_low_n_cell_is_never_activated() -> None:
    records = _records("high|8", 0.8, days=20, cities=3, hours=1, noise=0.1)
    assert len(records) < fit.MIN_ROWS

    active, verdicts = fit.fit_rule(records)

    assert active == {}
    assert verdicts["high|8"]["inner"] is None


def test_inner_fit_below_min_rows_is_never_activated_even_when_the_cell_is_large() -> None:
    # 12 days x 20 rows = 240 >= MIN_ROWS in total, but the inner fit sees < MIN_ROWS.
    records = _records("high|8", 0.8, days=12, cities=10, hours=2, noise=0.1)
    inner_fit, _valid = fit._split(records)
    assert len(records) >= fit.MIN_ROWS > len(inner_fit)

    active, verdicts = fit.fit_rule(records)

    assert active == {} and verdicts["high|8"]["inner"] is None


def test_inner_split_is_chronological_by_target_date() -> None:
    records = _records("high|4", 0.5, days=10, cities=2, hours=1, noise=0.0)

    inner_fit, valid = fit._split(records)

    assert max(r.target_date for r in inner_fit) < min(r.target_date for r in valid)
    assert len(inner_fit) + len(valid) == len(records)


def test_outer_folds_train_only_on_labels_known_before_each_block() -> None:
    records = _records("high|4", 0.5, days=30, cities=6, hours=2, noise=0.3)
    seen: list[tuple[str, str]] = []
    original = fit.fit_rule

    def spy(train):
        seen.append((max(r.target_date for r in train), max(r.label_known_at for r in train).isoformat()))
        return original(train)

    fit.fit_rule = spy
    try:
        result = fit.outer_folds(records)
    finally:
        fit.fit_rule = original
    for (last_train_date, _), block in zip(seen, result["blocks"], strict=True):
        assert last_train_date < block[0]
    cell = result["cells"]["high|4"]
    assert cell["gain"] > fit.MIN_GAIN_NATS and cell["ub_new_minus_old"] < 0.0


def test_served_cell_needs_both_the_rule_and_its_outer_fold_record() -> None:
    real = _records("high|4", 0.5, days=30, cities=6, hours=2, noise=0.3)
    null = _records("low|0", 0.0, days=30, cities=6, hours=2, noise=0.3)

    artifact = fit.build_artifact(real + null, fit_date="2026-09-01", record_counts={})

    served, unshifted = artifact["cells"]["high|4"], artifact["cells"]["low|0"]
    assert served["rule_active"] and served["active"]
    assert served["oos_gain"] >= fit.MIN_GAIN_NATS and served["oos_ub_new_minus_old"] < 0.0
    assert not unshifted["active"]


def test_clustered_gain_averages_hours_within_a_city_day_first() -> None:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    good = fit.Record("a", "2026-08-01", "high", "high|4", base, base, _curve(0.5))
    many = [
        fit.Record("b", "2026-08-01", "high", "high|4", base, base, _curve(0.0))
        for _ in range(9)
    ]
    model = (int(np.argmin(np.abs(fit.GRID_C - 0.5))), {})

    result = fit.clustered_gain([good, *many], lambda _record: model)

    per_day = [
        float(_curve(0.5)[model[0]] - _curve(0.5)[fit.ZERO]),
        float(_curve(0.0)[model[0]] - _curve(0.0)[fit.ZERO]),
    ]
    assert result["city_days"] == 2
    assert np.isclose(result["gain"], np.mean(per_day))
