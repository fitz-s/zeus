"""Tests for Temperature and TemperatureDelta type system."""

import pytest
from src.types.temperature import (
    Temperature, TemperatureDelta, UnitMismatchError, cdf_probability,
)


# --- Temperature ---

def test_to_f():
    assert Temperature(0, "C").to("F").value == pytest.approx(32.0)


def test_to_c():
    assert Temperature(32, "F").to("C").value == pytest.approx(0.0)


def test_identity():
    t = Temperature(72, "F")
    assert t.to("F").value == 72.0


def test_roundtrip():
    assert Temperature(72, "F").to("C").to("F").value == pytest.approx(72.0, abs=1e-9)


def test_sub_gives_delta():
    d = Temperature(75, "F") - Temperature(70, "F")
    assert isinstance(d, TemperatureDelta)
    assert d.value == pytest.approx(5.0)


def test_add_delta():
    t = Temperature(70, "F") + TemperatureDelta(5, "F")
    assert isinstance(t, Temperature)
    assert t.value == pytest.approx(75.0)


def test_cross_unit_eq_raises():
    with pytest.raises(UnitMismatchError):
        Temperature(32, "F") == Temperature(0, "C")


def test_cross_unit_sub_raises():
    with pytest.raises(UnitMismatchError):
        Temperature(72, "F") - Temperature(20, "C")


def test_compare_float_returns_not_implemented():
    assert Temperature(72, "F").__lt__(5.0) is NotImplemented


def test_gt_lt():
    assert Temperature(75, "F") > Temperature(70, "F")
    assert Temperature(70, "F") < Temperature(75, "F")


def test_ge_le():
    assert Temperature(70, "F") >= Temperature(70, "F")
    assert Temperature(70, "F") <= Temperature(70, "F")


def test_str():
    assert "72.0°F" == str(Temperature(72, "F"))


# --- TemperatureDelta ---

def test_delta_vs_absolute_differ():
    """THE critical test: delta conversion has NO +32 offset."""
    assert Temperature(10, "C").to("F").value == pytest.approx(50.0)
    assert TemperatureDelta(10, "C").to("F").value == pytest.approx(18.0)
    assert abs(50.0 - 18.0) > 30  # they are VERY different


def test_delta_div_delta_gives_float():
    """Z-score: error / std → dimensionless."""
    z = TemperatureDelta(3, "F") / TemperatureDelta(1.5, "F")
    assert isinstance(z, float)
    assert z == pytest.approx(2.0)


def test_delta_div_scalar():
    d = TemperatureDelta(10, "F") / 2
    assert isinstance(d, TemperatureDelta)
    assert d.value == pytest.approx(5.0)


def test_delta_mul():
    d = TemperatureDelta(3, "C") * 2.0
    assert d.value == pytest.approx(6.0)
    d2 = 2.0 * TemperatureDelta(3, "C")
    assert d2.value == pytest.approx(6.0)


def test_delta_neg():
    assert (-TemperatureDelta(3, "F")).value == pytest.approx(-3.0)


def test_delta_abs():
    assert abs(TemperatureDelta(-3, "F")).value == pytest.approx(3.0)


def test_delta_cross_unit_raises():
    with pytest.raises(UnitMismatchError):
        TemperatureDelta(3, "F") + TemperatureDelta(1, "C")


def test_delta_div_zero():
    with pytest.raises(ZeroDivisionError):
        TemperatureDelta(3, "F") / 0


def test_delta_add():
    d = TemperatureDelta(3, "F") + TemperatureDelta(2, "F")
    assert d.value == pytest.approx(5.0)


def test_delta_sub():
    d = TemperatureDelta(5, "C") - TemperatureDelta(2, "C")
    assert d.value == pytest.approx(3.0)


# --- cdf_probability ---

def test_cdf_same_unit():
    p = cdf_probability(
        Temperature(75, "F"), Temperature(70, "F"), TemperatureDelta(3, "F")
    )
    assert 0 < p < 1


def test_cdf_mixed_unit_raises():
    with pytest.raises(UnitMismatchError):
        cdf_probability(
            Temperature(75, "F"), Temperature(20, "C"), TemperatureDelta(3, "F")
        )


def test_cdf_at_mean():
    """P(X <= mean) = 0.5 for normal distribution."""
    p = cdf_probability(
        Temperature(70, "F"), Temperature(70, "F"), TemperatureDelta(3, "F")
    )
    assert p == pytest.approx(0.5)
