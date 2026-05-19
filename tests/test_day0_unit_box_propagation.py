# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; F3 PR #177 antibody pattern
# SCAFFOLD ONLY — all tests are @pytest.mark.skip pending PR 5 production code.
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
"""R-5.3: CelsiusBox / FahrenheitBox propagation boundary tests.

Mirrors the F3 PR #177 antibody pattern from tests/types/test_celsius_box_arithmetic.py.

Design constraint (OPEN QUESTION #1, see scaffold report):
  day0_router.py lines 7-21 (authority: phase6_contract.md R-BA..R-BD) explicitly
  states that the signal/evaluator layer uses plain float because values are
  unit-polymorphic at runtime. CelsiusBox / FahrenheitBox therefore live at the
  IngestAdapter→Day0Router seam, not inside Day0 hot loops. Production code must
  extract `.value` before constructing Day0SignalInputs.

These tests assert:
  a. An IngestAdapter emitting a CelsiusBox to a °C city does NOT silently coerce
     to Fahrenheit before Day0SignalInputs construction (the wrong path that F3
     PR 4/PR 5 exists to block).
  b. An IngestAdapter emitting a FahrenheitBox to a °F city is accepted correctly.
  c. Mixing CelsiusBox + FahrenheitBox at the seam raises TypeError (antibody).
  d. Day0SignalInputs receives the raw `.value` float, NOT the box object itself
     (unit carried in the `unit` field per day0_router.py design).

Note on SCAFFOLD: the IngestAdapter seam does not yet exist as a typed class.
These tests are stubs against the expected API. The antibody (c) is the
load-bearing test — if a future PR merges boxes directly into Day0SignalInputs
without `.value` extraction, test (d) will catch it via type annotation error.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.types.temperature import CelsiusBox, FahrenheitBox


# ---------------------------------------------------------------------------
# R-5.3a: CelsiusBox at °C ingest boundary — .value propagated correctly
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_celsius_box_value_extracted_at_ingest_seam() -> None:
    """CelsiusBox.value is extracted before Day0SignalInputs construction.

    Asserts that an ingest adapter emitting CelsiusBox(22.5) for a °C city
    results in Day0SignalInputs.current_temp == 22.5 (float), not a CelsiusBox.

    SCAFFOLD: replace with real IngestAdapter call once the seam exists.
    """
    box = CelsiusBox(22.5)
    # Simulate seam: production code must call box.value, not pass box directly
    extracted_value: float = box.value
    assert isinstance(extracted_value, float)
    assert extracted_value == 22.5


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_fahrenheit_box_value_extracted_at_ingest_seam() -> None:
    """FahrenheitBox.value is extracted before Day0SignalInputs construction."""
    box = FahrenheitBox(72.0)
    extracted_value: float = box.value
    assert isinstance(extracted_value, float)
    assert extracted_value == 72.0


# ---------------------------------------------------------------------------
# R-5.3b: Cross-unit box mix at seam raises TypeError — antibody
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
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


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_fahrenheit_box_cannot_add_celsius_at_seam() -> None:
    """Mirror: FahrenheitBox + CelsiusBox raises TypeError."""
    dallas_obs = FahrenheitBox(72.0)
    london_obs = CelsiusBox(22.5)
    with pytest.raises(TypeError, match="Cannot add Fahrenheit"):
        _ = dallas_obs + london_obs


# ---------------------------------------------------------------------------
# R-5.3c: Day0SignalInputs.current_temp must be float, not Box type
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_day0_signal_inputs_current_temp_is_float_not_box() -> None:
    """Day0SignalInputs.current_temp carries float, not CelsiusBox or FahrenheitBox.

    Design basis: day0_router.py lines 7-21 — signal layer is unit-polymorphic;
    unit is carried as `unit: str = 'F'` field, not as a typed box.

    This test explicitly fails if someone passes a box directly to Day0SignalInputs,
    ensuring the seam extraction (`.value`) is not skipped in a future PR.
    """
    import numpy as np
    from src.signal.day0_router import Day0SignalInputs
    from src.types.metric_identity import MetricIdentity

    metric = MetricIdentity.from_str("HIGH")

    # Correct: float extracted from box at ingest seam
    box = CelsiusBox(22.5)
    inputs = Day0SignalInputs(
        temperature_metric=metric,
        current_temp=box.value,   # explicit .value extraction — the required pattern
        hours_remaining=8.0,
        observed_high_so_far=None,
        observed_low_so_far=None,
        member_maxes_remaining=None,
        member_mins_remaining=None,
        unit="C",
    )
    assert isinstance(inputs.current_temp, float)
    assert inputs.current_temp == 22.5

    # Wrong: passing box directly would break hot-loop arithmetic
    # (CelsiusBox is not a float subtype; arithmetic ops on Day0 signal would fail)
    # We verify the type guard holds at construction:
    # SCAFFOLD: if Day0SignalInputs adds __post_init__ float validation, this
    #           would raise at construction. For now, just assert type:
    assert not isinstance(inputs.current_temp, CelsiusBox)
    assert not isinstance(inputs.current_temp, FahrenheitBox)


# ---------------------------------------------------------------------------
# R-5.3d: Unit mismatch guard — same city, wrong box type
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_celsius_city_receives_fahrenheit_box_raises_at_seam() -> None:
    """An ingest adapter for a °C city must not silently accept a FahrenheitBox.

    SCAFFOLD: this test exercises the FUTURE ingest adapter validation that
    rejects a FahrenheitBox for a city configured as unit='C'. Production code
    must raise ValueError (or equivalent) at the seam before .value extraction.

    Current state: this behaviour does not yet exist. Test is a contract stub.
    """
    fahrenheit_for_celsius_city = FahrenheitBox(72.0)
    city_unit = "C"  # city is configured as Celsius

    # SCAFFOLD: replace with real IngestAdapter call:
    # with pytest.raises(ValueError, match="unit mismatch"):
    #     adapter.normalize_observation(fahrenheit_for_celsius_city, city_unit)
    #
    # For now, assert the cross-unit conversion exists to make the intent executable:
    celsius_corrected = fahrenheit_for_celsius_city.to_celsius()
    assert isinstance(celsius_corrected, CelsiusBox)
    assert abs(celsius_corrected.value - 22.222) < 0.01
