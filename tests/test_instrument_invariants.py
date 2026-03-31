"""Instrument invariant tests: Zeus must model the product it trades.

A temperature bin is not just a probability — it's a probability density × bin width.
The bin's unit, width, and settlement semantics must propagate through every module
that touches bin probabilities: p_raw computation, Platt calibration, edge calculation,
exit evaluation, and settlement.

These tests encode the cross-module relationships that the spec describes.
Tests marked xfail are not yet implemented — they define the target.

Structural Decision: "The instrument must carry its own settlement contract."
Same pattern as HeldSideProbability preventing probability space flips.
"""

import numpy as np
import pytest
from datetime import date

from src.types import Bin
from src.contracts.settlement_semantics import SettlementSemantics
from src.config import cities_by_name


# ---- Bin Type System ----

def test_bin_carries_unit():
    """Bin must know its unit. A Bin(50, 51) without unit is ambiguous."""
    b = Bin(low=50.0, high=51.0, label="50-51°F", unit="F")
    assert hasattr(b, "unit"), "Bin must carry unit (°F or °C)"
    assert b.unit == "F"


def test_bin_knows_width():
    """Bin must know its width for probability density normalization."""
    b = Bin(low=50.0, high=51.0, label="50-51°F", unit="F")
    assert hasattr(b, "width"), "Bin must have a width property"
    assert b.width == 2  # {50, 51}


def test_point_bin_width_is_one():
    """°C point bin (10°C) has width=1: it covers exactly one integer degree."""
    b = Bin(low=10.0, high=10.0, label="10°C", unit="C")
    assert b.width == 1
    assert b.settlement_values == [10]


def test_range_bin_width_is_two():
    """°F range bin (50-51°F) covers 2 integer values: {50, 51}.

    This is the ACTUAL Polymarket bin structure — verified from market data.
    Every °F bin is exactly 2°F wide: 40-41, 42-43, 44-45, ...
    """
    b = Bin(low=50.0, high=51.0, label="50-51°F", unit="F")
    assert b.width == 2
    assert b.settlement_values == [50, 51]


# ---- Settlement Semantics Per City ----

def test_celsius_cities_get_celsius_semantics():
    """°C cities must get °C SettlementSemantics via for_city()."""
    celsius_cities = [c for c in cities_by_name.values() if c.settlement_unit == "C"]
    if not celsius_cities:
        pytest.skip("No °C cities configured")

    for city in celsius_cities:
        sem = SettlementSemantics.for_city(city)
        assert sem.measurement_unit == "C", (
            f"{city.name} is °C but got {sem.measurement_unit} settlement semantics"
        )


@pytest.mark.xfail(reason="SettlementSemantics factory for °C cities not implemented")
def test_settlement_semantics_factory_covers_all_cities():
    """Every configured city must have a matching SettlementSemantics factory."""
    for name, city in cities_by_name.items():
        if city.settlement_unit == "C":
            # Must have a °C factory — not just default_wu_fahrenheit
            assert hasattr(SettlementSemantics, "for_city") or \
                   hasattr(SettlementSemantics, "from_city"), \
                f"No SettlementSemantics factory for {name} (°C city)"


# ---- p_raw Must Respect Bin Structure ----

@pytest.mark.xfail(reason="p_raw normalization by bin width not implemented")
def test_p_raw_density_normalization():
    """p_raw for a 5°F bin and a 1°C point bin should not be directly comparable.

    If 20 of 51 members land in a 5°F bin, p_raw ≈ 0.39.
    If 4 of 51 members land in a 1°C point bin, p_raw ≈ 0.08.
    These are NOT comparable probabilities — they're densities at different scales.
    Platt must either:
    (a) be trained separately for each bin-width type, or
    (b) receive density-normalized inputs
    """
    # This test documents the requirement, not the implementation
    assert False, "Platt calibration must account for bin width differences"


# ---- Settlement Rounding Must Match City Contract ----

def test_fahrenheit_settlement_rounds_to_integer():
    """WU °F settlement: 73.4°F → 73°F (integer)."""
    sem = SettlementSemantics.default_wu_fahrenheit("KLGA")
    assert sem.precision == 1.0
    assert sem.measurement_unit == "F"


@pytest.mark.xfail(reason="°C SettlementSemantics not yet defined")
def test_celsius_settlement_precision():
    """°C settlement precision must be determined from Polymarket contract.

    Key question: does Polymarket settle °C to integer (18°C) or to
    one decimal (18.3°C)? This determines whether point bins (4°C)
    cover exactly 1 value or a range of 0.1° increments.
    """
    # When implemented, something like:
    # sem = SettlementSemantics.for_city("London")
    # assert sem.measurement_unit == "C"
    # assert sem.precision in (1.0, 0.1)
    assert False


# ---- Day0: Observation → ENS Transition ----

@pytest.mark.xfail(reason="Day0 continuous transition function not implemented")
def test_day0_observation_weight_increases_monotonically():
    """As hours_to_settlement decreases, observation weight must increase.

    Not a binary switch (obs_dominates at 80%). A continuous function:
    weight_obs = f(hours_since_sunrise, diurnal_peak_confidence, obs_count)
    """
    # When implemented: for a sequence of decreasing hours_to_settlement,
    # the observation weight should be monotonically non-decreasing
    hours_sequence = [12.0, 8.0, 6.0, 4.0, 2.0, 1.0, 0.5]
    weights = []
    for h in hours_sequence:
        # w = day0_observation_weight(hours_to_settlement=h, ...)
        # weights.append(w)
        pass
    # assert all(weights[i] <= weights[i+1] for i in range(len(weights)-1))
    assert False, "Day0 must have continuous observation weight function"


@pytest.mark.xfail(reason="Day0 post-peak sigma reduction not continuous")
def test_day0_post_peak_sigma_is_continuous():
    """After diurnal peak, instrument noise sigma should decrease continuously.

    Currently: binary — if diurnal_peak_confidence > 0.7, sigma halved.
    Should be: sigma = base_sigma * (1 - peak_confidence * decay_factor)
    """
    assert False


# ---- Exit/Entry Epistemic Parity ----

def test_monitor_mc_count_matches_entry():
    """Monitor and entry must use the same MC count for p_raw computation.

    Currently: entry uses 5000, monitor uses 1000.
    This 5x asymmetry means monitor's p_posterior has ~2.2x more variance,
    causing false EDGE_REVERSAL near threshold boundaries.
    """
    from src.signal.ensemble_signal import DEFAULT_N_MC
    # Check what monitor_refresh actually uses
    import ast, inspect
    from src.engine import monitor_refresh
    source = inspect.getsource(monitor_refresh)
    # Look for n_mc= in the source
    if "n_mc=1000" in source or "n_mc = 1000" in source:
        pytest.fail(
            f"monitor_refresh uses n_mc=1000 but entry uses {DEFAULT_N_MC}. "
            f"This 5x asymmetry causes false EDGE_REVERSAL."
        )


@pytest.mark.xfail(reason="CI-aware exit threshold not implemented")
def test_exit_uses_ci_not_raw_edge():
    """Exit should compare ci_lower of forward_edge against threshold,
    not the raw forward_edge point estimate.

    Entry uses bootstrap CI to quantify edge uncertainty.
    Exit should use the same epistemic rigor.
    """
    assert False


# ---- Data Confidence ----

@pytest.mark.xfail(reason="Persistence anomaly sample size guard too low")
def test_persistence_discount_requires_adequate_samples():
    """A 30% alpha discount should not fire on 10 samples.

    Statistical rule of thumb: for a frequency estimate to be reliable,
    n should be large enough that expected count in category > 5.
    For frequency=0.05, n=100 gives expected count=5. n=10 gives 0.5.
    """
    # When fixed: minimum sample count for persistence discount
    # should be at least 30, not 10
    assert False


# ---- Integer Rounding Must Be Unit-Aware ----

def test_no_hardcoded_integer_rounding_for_celsius():
    """np.round().astype(int) in bootstrap/day0 is wrong for °C if
    settlement precision is 0.1°C.

    Search for hardcoded integer rounding in signal computation paths.
    """
    import inspect
    from src.strategy import market_analysis
    from src.signal import day0_signal

    for module in [market_analysis, day0_signal]:
        source = inspect.getsource(module)
        if "astype(int)" in source:
            # This is a known issue — integer rounding is hardcoded
            # Should use SettlementSemantics.precision to determine rounding
            pytest.fail(
                f"{module.__name__} uses hardcoded astype(int) rounding. "
                f"Must use SettlementSemantics.precision for unit-aware rounding."
            )
