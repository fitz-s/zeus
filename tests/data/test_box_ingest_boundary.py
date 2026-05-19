# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: F3 PR 4 / study at jobs/9ea6f95c/findings/f3_frozen_dataclass_study.md
"""Ingest boundary tests for CelsiusBox / FahrenheitBox wrapping.

Scope:
  - CelsiusBox / FahrenheitBox boundary pattern (construction, .value extraction,
    cross-unit guard at the wrap site).
  - Ogimet parse helpers (_parse_metar_temp_c, _parse_metar_csv_line): confirm
    Box-wrapped values flow through correctly and return float-compatible results.

HKO, Meteostat, and IEM ASOS boundary sites apply the same pattern and are
covered by tests/data/test_ingest_unit_types.py (F3 PR 2/3, on main since #171).
Per-client smoke tests here would require live HTTP or complex fixture setup.

Design note (F3 PR 4 brief): CelsiusBox/FahrenheitBox are created at the write
site and immediately unwrapped (.value) into the container float. The unit-witness
purpose is the explicit tagging; if someone accidentally writes
`CelsiusBox(x) + FahrenheitBox(y)` at a future ingest site, TypeError fires.
"""
from __future__ import annotations

import pytest

from src.data.ogimet_hourly_client import _parse_metar_temp_c, _parse_metar_csv_line
from src.types.temperature import CelsiusBox, FahrenheitBox


# ---------------------------------------------------------------------------
# CelsiusBox boundary pattern — unit witness + immediate extraction
# ---------------------------------------------------------------------------

def test_celsius_box_boundary_pattern_positive() -> None:
    """CelsiusBox(float(x)).value round-trips a positive Celsius value."""
    raw = "25.3"
    box = CelsiusBox(float(raw))
    assert box.value == pytest.approx(25.3)
    assert isinstance(box.value, float)


def test_celsius_box_boundary_pattern_negative() -> None:
    """CelsiusBox(float(x)).value round-trips a negative Celsius value."""
    raw = "-7.0"
    box = CelsiusBox(float(raw))
    assert box.value == pytest.approx(-7.0)


def test_celsius_box_boundary_cross_unit_guard() -> None:
    """Constructing CelsiusBox then attempting cross-unit add raises TypeError.

    This is the antibody: if unit confusion occurs at an ingest site
    (Celsius raw value accidentally combined with Fahrenheit raw value),
    the runtime guard fires.
    """
    c_box = CelsiusBox(20.0)
    f_box = FahrenheitBox(68.0)
    with pytest.raises(TypeError):
        _ = c_box + f_box


# ---------------------------------------------------------------------------
# FahrenheitBox boundary pattern — unit witness + immediate extraction
# ---------------------------------------------------------------------------

def test_fahrenheit_box_boundary_pattern() -> None:
    """FahrenheitBox(float(x)).value round-trips a Fahrenheit value."""
    raw = "72.5"
    box = FahrenheitBox(float(raw))
    assert box.value == pytest.approx(72.5)
    assert isinstance(box.value, float)


def test_fahrenheit_box_boundary_cross_unit_guard() -> None:
    """FahrenheitBox + CelsiusBox raises TypeError at boundary."""
    f_box = FahrenheitBox(68.0)
    c_box = CelsiusBox(20.0)
    with pytest.raises(TypeError):
        _ = f_box + c_box


# ---------------------------------------------------------------------------
# Ogimet parse helpers — verify the Box-wrapped values flow through correctly
# ---------------------------------------------------------------------------

def test_ogimet_parse_metar_temp_c_positive() -> None:
    """Ogimet METAR parser returns correct positive temperature (Box-wrapped + unwrapped)."""
    result = _parse_metar_temp_c("METAR LTFM 011150Z 35010KT 9999 FEW020 17/05 Q1013")
    assert result is not None
    assert isinstance(result, float)
    assert result == pytest.approx(17.0)


def test_ogimet_parse_metar_temp_c_negative() -> None:
    """Ogimet METAR parser returns correct negative temperature (M-prefix)."""
    result = _parse_metar_temp_c("LTFM 130000Z 360008KT M05/M10 Q1020")
    assert result is not None
    assert isinstance(result, float)
    assert result == pytest.approx(-5.0)


def test_ogimet_parse_metar_csv_line_tuple_shape() -> None:
    """Ogimet CSV line parser returns (datetime, float) with correct temperature."""
    line = "LTFM,2026,01,01,12,00,METAR LTFM 010000Z 360010KT 9999 17/05 Q1013"
    result = _parse_metar_csv_line(line)
    assert result is not None
    utc_dt, temp_c = result
    assert isinstance(temp_c, float)
    assert temp_c == pytest.approx(17.0)
