# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration (consult REQ-20260701-010328).
# Tests the pure affine estimator (shrink-to-identity + slope clamp) and the fail-soft lookup.
import json

import pytest

from src.calibration.emos_center_calibration import (
    DEFAULT_KAPPA,
    SLOPE_MAX,
    SLOPE_MIN,
    apply_affine,
    current_affine,
    fit_affine,
    lookup_affine,
    walk_forward_affine,
)


# ---- affine estimator -----------------------------------------------------------------------
def test_identity_data_gives_identity():
    a, b = fit_affine([(float(c), float(c)) for c in range(10, 40)])
    assert abs(a) < 1e-6 and abs(b - 1.0) < 1e-6  # settle==center -> no correction


def test_constant_bias_recovers_shrunk_intercept():
    # settle = center + 2: OLS (a=2,b=1); shrink-to-identity pulls a below 2 but keeps b≈1.
    a, b = fit_affine([(float(c), float(c) + 2.0) for c in range(0, 120)])
    assert 1.2 < a < 2.0
    assert abs(b - 1.0) < 0.05


def test_slope_bias_shrinks_toward_identity_before_clamp():
    # settle = 1.30*center: enough evidence to move the slope, but not enough to serve raw OLS.
    a, b = fit_affine([(float(c), 1.30 * c) for c in range(10, 40)])
    assert b <= SLOPE_MAX + 1e-9
    assert b >= 1.0
    n = 30
    expected_weight = n / (n + DEFAULT_KAPPA)
    assert b == pytest.approx(1.0 + expected_weight * 0.30)
    mx = sum(range(10, 40)) / 30
    assert (a + b * mx) < (1.30 * mx)  # shrinkage deliberately pulls back from raw OLS.


def test_slope_clamp_never_exceeds_band():
    for target_b in (0.4, 0.6, 1.5, 2.0):
        _, b = fit_affine([(float(c), target_b * c + 5.0) for c in range(15, 45)])
        assert SLOPE_MIN - 1e-9 <= b <= SLOPE_MAX + 1e-9


def test_thin_data_is_identity():
    assert fit_affine([(1.0, 3.0), (2.0, 4.0)]) == (0.0, 1.0)


def test_apply_affine():
    assert apply_affine(20.0, 0.0, 1.0) == 20.0            # identity
    assert apply_affine(20.0, 1.0, 1.05) == pytest.approx(22.0)


def test_walk_forward_warmup_is_identity():
    series = walk_forward_affine([(f"d{i:03d}", float(i), float(i) + 2.0) for i in range(40)], min_train=25)
    assert all((a, b) == (0.0, 1.0) for _, a, b in series[:25])   # warmup -> identity
    assert any(abs(a) > 0.1 for _, a, b in series[25:])           # then it corrects


def test_current_affine_below_min_train_is_none():
    assert current_affine([(f"d{i}", float(i), float(i) + 2.0) for i in range(10)], min_train=25) is None


# ---- artifact lookup (fail-soft) ------------------------------------------------------------
def _write(tmp_path, monkeypatch, cities):
    art = {"authority": "emos_center_calibration_v1", "metrics": {"high": {"cities": cities}}}
    (tmp_path / "emos_center_calibration.json").write_text(json.dumps(art), encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)


def test_lookup_identity_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high") == (0.0, 1.0)


def test_lookup_serves_gated_city(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "Seoul": {"a": 0.54, "b": 1.062, "serve": True},
        "Madrid": {"a": 0.2, "b": 1.01, "serve": False},
    })
    assert lookup_affine("Seoul", "high") == (pytest.approx(0.54), pytest.approx(1.062))
    assert lookup_affine("Madrid", "high") == (0.0, 1.0)   # serve=False -> identity
    assert lookup_affine("Tokyo", "high") == (0.0, 1.0)    # absent -> identity
    assert lookup_affine("Seoul", "low") == (0.0, 1.0)     # wrong metric -> identity


def test_lookup_does_not_serve_canary_tier(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "Shanghai": {"a": 0.8, "b": 1.04, "serve": False, "tier": "canary"},
    })
    assert lookup_affine("Shanghai", "high") == (0.0, 1.0)


def test_lookup_failsoft_on_malformed(tmp_path, monkeypatch):
    (tmp_path / "emos_center_calibration.json").write_text("{ bad json", encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high") == (0.0, 1.0)   # never raises
