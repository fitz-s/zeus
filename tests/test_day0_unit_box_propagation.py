# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; F3 PR #177 antibody pattern
# Production code implemented in PR 5 (2026-05-19). All tests live.
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
"""R-5.3: IngestAdapter + CelsiusBox / FahrenheitBox propagation boundary tests.

Exercises the IngestAdapter seam (src/contracts/day0_observation_context.py).
Mirrors the F3 PR #177 antibody pattern from tests/types/test_celsius_box_arithmetic.py.

Design constraint (day0_router.py lines 7-21, authority: phase6_contract.md R-BA..R-BD):
  The signal/evaluator layer uses plain float because values are unit-polymorphic at
  runtime (Dallas=°F, London=°C share the same code paths). CelsiusBox / FahrenheitBox
  live at the IngestAdapter→Day0Router seam, not inside Day0 hot loops. The adapter
  extracts `.value` before Day0SignalInputs construction.

These tests assert:
  a. IngestAdapter for a °C city accepts CelsiusBox and returns the float value.
  b. IngestAdapter for a °F city accepts FahrenheitBox and returns the float value.
  c. Mixing CelsiusBox + FahrenheitBox at the seam raises TypeError (antibody).
  d. Day0SignalInputs receives the raw `.value` float, NOT the box object itself
     (unit carried in the `unit` field per day0_router.py design).
  e. IngestAdapter raises ValueError on unit mismatch (FahrenheitBox for °C city).
  f. IngestAdapter raises TypeError on bare float (box required at boundary).
"""
from __future__ import annotations

import pytest

from src.contracts.day0_observation_context import IngestAdapter
from src.types.temperature import CelsiusBox, FahrenheitBox


# ---------------------------------------------------------------------------
# R-5.3a: CelsiusBox at °C ingest boundary — .value propagated correctly
# ---------------------------------------------------------------------------


def test_celsius_box_value_extracted_at_ingest_seam() -> None:
    """IngestAdapter for a °C city accepts CelsiusBox and returns float value.

    Asserts that the adapter correctly extracts CelsiusBox.value (22.5 float)
    and does NOT silently coerce to Fahrenheit before Day0SignalInputs construction.
    """
    adapter = IngestAdapter(city_unit="C")
    box = CelsiusBox(22.5)
    result = adapter.normalize_observation(box)
    assert isinstance(result, float)
    assert result == 22.5


def test_fahrenheit_box_value_extracted_at_ingest_seam() -> None:
    """IngestAdapter for a °F city accepts FahrenheitBox and returns float value."""
    adapter = IngestAdapter(city_unit="F")
    box = FahrenheitBox(72.0)
    result = adapter.normalize_observation(box)
    assert isinstance(result, float)
    assert result == 72.0


# ---------------------------------------------------------------------------
# R-5.3b: Cross-unit box mix at seam raises TypeError — antibody
# ---------------------------------------------------------------------------


def test_celsius_box_cannot_add_fahrenheit_at_seam() -> None:
    """Mirror of F3 PR #177 antibody: CelsiusBox + FahrenheitBox raises TypeError.

    Antibody: if __add__ cross-unit guard is removed from CelsiusBox, this test
    fails. Mirrors test_celsius_add_fahrenheit_raises in test_celsius_box_arithmetic.py.
    Included here to anchor the same property in the Day0 seam context.
    """
    london_obs = CelsiusBox(22.5)      # London = °C city
    dallas_obs = FahrenheitBox(72.0)   # Dallas = °F city
    with pytest.raises(TypeError, match="Cannot add Celsius"):
        _ = london_obs + dallas_obs


def test_fahrenheit_box_cannot_add_celsius_at_seam() -> None:
    """Mirror: FahrenheitBox + CelsiusBox raises TypeError."""
    dallas_obs = FahrenheitBox(72.0)
    london_obs = CelsiusBox(22.5)
    with pytest.raises(TypeError, match="Cannot add Fahrenheit"):
        _ = dallas_obs + london_obs


# ---------------------------------------------------------------------------
# R-5.3c: Day0SignalInputs.current_temp must be float, not Box type
# ---------------------------------------------------------------------------


def test_day0_signal_inputs_current_temp_is_float_not_box() -> None:
    """Day0SignalInputs.current_temp carries float, not CelsiusBox or FahrenheitBox.

    Design basis: day0_router.py lines 7-21 — signal layer is unit-polymorphic;
    unit is carried as `unit: str = 'F'` field, not as a typed box.

    Exercises the full seam: IngestAdapter.normalize_observation → float →
    Day0SignalInputs.current_temp.
    """
    from src.signal.day0_router import Day0SignalInputs
    from src.types.metric_identity import MetricIdentity

    metric = MetricIdentity.from_raw("high")
    adapter = IngestAdapter(city_unit="C")
    box = CelsiusBox(22.5)

    # IngestAdapter extracts .value; signal inputs receive float
    current_temp = adapter.normalize_observation(box)
    inputs = Day0SignalInputs(
        temperature_metric=metric,
        current_temp=current_temp,  # float from adapter — the required pattern
        hours_remaining=8.0,
        observed_high_so_far=None,
        observed_low_so_far=None,
        member_maxes_remaining=None,
        member_mins_remaining=None,
        unit="C",
    )
    assert isinstance(inputs.current_temp, float)
    assert inputs.current_temp == 22.5
    assert not isinstance(inputs.current_temp, CelsiusBox)
    assert not isinstance(inputs.current_temp, FahrenheitBox)


# ---------------------------------------------------------------------------
# R-5.3d: Unit mismatch guard — FahrenheitBox for °C city raises ValueError
# ---------------------------------------------------------------------------


def test_celsius_city_receives_fahrenheit_box_raises_at_seam() -> None:
    """IngestAdapter for a °C city must not silently accept a FahrenheitBox.

    Exercises the unit-mismatch guard in IngestAdapter.normalize_observation.
    A FahrenheitBox arriving at a °C ingest boundary indicates a data-routing
    error and must raise ValueError before .value extraction.
    """
    adapter = IngestAdapter(city_unit="C")
    fahrenheit_for_celsius_city = FahrenheitBox(72.0)

    with pytest.raises(ValueError, match="[Uu]nit mismatch"):
        adapter.normalize_observation(fahrenheit_for_celsius_city)


# ---------------------------------------------------------------------------
# R-5.3e: Bare float at seam raises TypeError — boxes required at boundary
# ---------------------------------------------------------------------------


def test_bare_float_at_ingest_seam_raises_type_error() -> None:
    """IngestAdapter raises TypeError when a bare float is passed.

    Bare float does not carry unit information. The ingest boundary requires
    an explicit CelsiusBox or FahrenheitBox so the unit is always present.
    """
    adapter = IngestAdapter(city_unit="F")
    with pytest.raises(TypeError, match="CelsiusBox or FahrenheitBox"):
        adapter.normalize_observation(72.0)  # type: ignore[arg-type]
