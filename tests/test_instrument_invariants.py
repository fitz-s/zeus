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

import ast
import numpy as np
import pytest
from datetime import date
from pathlib import Path

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

def test_fahrenheit_center_bin_rejects_non_two_degree_width():
    with pytest.raises(ValueError, match="Fahrenheit non-shoulder bins must cover exactly 2"):
        Bin(low=50.0, high=52.0, label="50-52°F", unit="F")

def test_celsius_center_bin_rejects_non_point_width():
    with pytest.raises(ValueError, match="Celsius non-shoulder bins must cover exactly 1"):
        Bin(low=10.0, high=11.0, label="10-11°C", unit="C")

def test_bin_label_unit_mismatch_is_rejected():
    with pytest.raises(ValueError, match="label .* Fahrenheit but unit='C'"):
        Bin(low=50.0, high=51.0, label="50-51°F", unit="C")


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


def test_settlement_semantics_factory_covers_all_cities():
    """Every configured city must have a matching SettlementSemantics factory."""
    for name, city in cities_by_name.items():
        if city.settlement_unit == "C":
            # Must have a °C factory — not just default_wu_fahrenheit
            assert hasattr(SettlementSemantics, "for_city") or \
                   hasattr(SettlementSemantics, "from_city"), \
                f"No SettlementSemantics factory for {name} (°C city)"


# ---- p_raw Must Respect Bin Structure ----

def test_p_raw_density_normalization():
    """p_raw for a 5°F bin and a 1°C point bin should not be directly comparable.

    If 20 of 51 members land in a 5°F bin, p_raw ≈ 0.39.
    If 4 of 51 members land in a 1°C point bin, p_raw ≈ 0.08.
    These are NOT comparable probabilities — they're densities at different scales.
    Platt must either:
    (a) be trained separately for each bin-width type, or
    (b) receive density-normalized inputs
    """
    from src.calibration.platt import normalize_bin_probability_for_calibration

    range_bin = Bin(low=50.0, high=51.0, label="50-51°F", unit="F")
    point_bin = Bin(low=10.0, high=10.0, label="10°C", unit="C")

    assert normalize_bin_probability_for_calibration(0.40, bin_width=range_bin.width) == pytest.approx(0.20)
    assert normalize_bin_probability_for_calibration(0.08, bin_width=point_bin.width) == pytest.approx(0.08)


# ---- Settlement Rounding Must Match City Contract ----

def test_fahrenheit_settlement_rounds_to_integer():
    """WU °F settlement: 73.4°F → 73°F (integer)."""
    sem = SettlementSemantics.default_wu_fahrenheit("KLGA")
    assert sem.precision == 1.0
    assert sem.measurement_unit == "F"


def test_celsius_settlement_precision():
    """°C settlement precision must be determined from Polymarket contract.

    Key question: does Polymarket settle °C to integer (18°C) or to
    one decimal (18.3°C)? This determines whether point bins (4°C)
    cover exactly 1 value or a range of 0.1° increments.
    """
    celsius_cities = [c for c in cities_by_name.values() if c.settlement_unit == "C"]
    if not celsius_cities:
        pytest.skip("No °C cities configured")

    for city in celsius_cities:
        sem = SettlementSemantics.for_city(city)
        assert sem.measurement_unit == "C"
        assert sem.precision == 1.0


# ---- Day0: Observation → ENS Transition ----

def test_day0_observation_weight_increases_monotonically():
    """As hours_remaining decreases, observation_weight() must increase.

    Continuous function, not a binary switch at 80%.
    """
    import numpy as np
    from src.signal.day0_signal import Day0Signal
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    members = np.full(51, 20.0)
    hours_sequence = [12.0, 8.0, 6.0, 4.0, 2.0, 1.0, 0.5]
    weights = []
    for h in hours_sequence:
        sig = Day0Signal(
            observed_high_so_far=18.0,
            current_temp=17.0,
            hours_remaining=h,
            member_maxes_remaining=members,
            unit="C",
            diurnal_peak_confidence=0.0,
            temperature_metric=HIGH_LOCALDAY_MAX,
        )
        weights.append(sig.observation_weight())

    assert all(
        weights[i] <= weights[i + 1] for i in range(len(weights) - 1)
    ), f"observation_weight not monotonically increasing: {weights}"
    assert weights[-1] > weights[0], "Weight at 0.5h must be > weight at 12h"


def test_day0_post_peak_sigma_is_continuous():
    """Sigma should decrease continuously with peak_confidence, not binary-step at 0.7.

    No abrupt halving at diurnal_peak_confidence=0.7.
    """
    import numpy as np
    from datetime import datetime, timezone
    from src.signal.day0_signal import Day0Signal
    from src.signal.ensemble_signal import sigma_instrument
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    members = np.full(51, 20.0)
    base = sigma_instrument("C").value
    confidences = [0.0, 0.3, 0.5, 0.69, 0.70, 0.71, 1.0]
    sigmas = []

    # Provide fresh observation time to avoid staleness expansion
    now = datetime.now(timezone.utc)
    obs_time = now.isoformat()
    current_utc = now.isoformat()

    for pc in confidences:
        sig = Day0Signal(
            observed_high_so_far=18.0,
            current_temp=17.0,
            hours_remaining=6.0,
            member_maxes_remaining=members,
            unit="C",
            diurnal_peak_confidence=pc,
            observation_source="wu",  # trusted source
            observation_time=obs_time,
            current_utc_timestamp=current_utc,
            temperature_metric=HIGH_LOCALDAY_MAX,
        )
        sigmas.append(sig._sigma)

    # Must be monotonically decreasing (or at floor)
    assert all(
        sigmas[i] >= sigmas[i + 1] for i in range(len(sigmas) - 1)
    ), f"sigma not monotonically decreasing with peak_confidence: {list(zip(confidences, sigmas))}"

    # At peak=0: sigma = base
    assert abs(sigmas[0] - base) < 1e-9, "sigma at peak=0 must equal base_sigma"
    
    # At peak=1: sigma = max(floor, base * 0.5)
    # With floor=0.20 for C and raw=0.14, we get 0.20
    from src.signal.forecast_uncertainty import QUANTIZATION_NOISE_FLOOR_C
    expected_floor = max(QUANTIZATION_NOISE_FLOOR_C, base * 0.5)
    assert abs(sigmas[-1] - expected_floor) < 1e-9, f"sigma at peak=1 must equal floor or base*0.5, got {sigmas[-1]}"

    # No abrupt jump at 0.7 — the step from 0.69 to 0.71 must be small
    step = abs(sigmas[confidences.index(0.69)] - sigmas[confidences.index(0.71)])
    assert step < 0.02, f"Abrupt jump at 0.7 threshold: {step:.4f}"


# ---- Exit/Entry Epistemic Parity ----

def test_monitor_mc_count_matches_entry():
    """Monitor and entry must use the same MC count for p_raw computation.

    Currently: entry uses 5000, monitor uses 1000.
    This 5x asymmetry means monitor's p_posterior has ~2.2x more variance,
    causing false EDGE_REVERSAL near threshold boundaries.
    """
    from src.signal.ensemble_signal import DEFAULT_N_MC
    # Check what monitor_refresh actually uses
    import inspect
    from src.engine import monitor_refresh
    source = inspect.getsource(monitor_refresh)
    if "ensemble_n_mc()" not in source or "day0_n_mc()" not in source:
        pytest.fail(
            "monitor_refresh does not source MC counts from config helpers. "
            f"Entry uses {DEFAULT_N_MC}, so monitor must route through the same single source."
        )


def test_exit_uses_ci_not_raw_edge():
    """Exit should compare ci_lower of forward_edge against threshold,
    not the raw forward_edge point estimate.

    Entry uses bootstrap CI to quantify edge uncertainty.
    Exit should use the same epistemic rigor.
    """
    from src.execution.exit_triggers import evaluate_exit_triggers
    from src.contracts import EdgeContext, EntryMethod
    from src.state.portfolio import Position

    pos = Position(
        trade_id="ci-exit-1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entry_ci_width=0.10,
    )
    ctx = EdgeContext(
        p_raw=np.array([0.45]),
        p_cal=np.array([0.45]),
        p_market=np.array([0.40]),
        p_posterior=0.32,
        forward_edge=-0.08,
        alpha=0.55,
        confidence_band_upper=-0.03,
        confidence_band_lower=-0.13,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="snap-ci",
        n_edges_found=1,
        n_edges_after_fdr=1,
    )

    # Raw point estimate (-0.08) is not below the current buy-yes threshold (-0.10),
    # but the conservative lower bound (-0.13) is. CI-aware exits should therefore trigger
    # after the usual 2-cycle confirmation.
    assert evaluate_exit_triggers(pos, ctx) is None
    signal = evaluate_exit_triggers(pos, ctx)
    assert signal is not None
    assert signal.trigger == "EDGE_REVERSAL"


# ---- Data Confidence ----

def test_persistence_discount_requires_adequate_samples():
    """Persistence discount must not fire on < 30 samples, and must scale with n.

    Statistical rule: frequency estimate needs n >= 30 before applying discount.
    Discount scales linearly from 10% at n=30 to 30% at n>=100.
    """
    from src.engine.monitor_refresh import _check_persistence_anomaly
    from unittest.mock import MagicMock
    from datetime import date

    def make_conn(n_samples):
        conn = MagicMock()
        # yesterday settlement
        conn.execute.return_value.fetchone.side_effect = [
            {"settlement_value": 15.0},  # yesterday
            None,  # 2 days ago
            None,  # 3 days ago
            {"frequency": 0.02, "n_samples": n_samples},  # persistence lookup
        ]
        return conn

    target = date(2026, 4, 1)
    predicted = 25.0  # delta = +10 from 15.0 → ">10" bucket, rare

    # n=10: no discount (too few samples)
    conn_10 = make_conn(10)
    result_10 = _check_persistence_anomaly(conn_10, "London", target, predicted)
    assert result_10 == 1.0, f"n=10 should give no discount, got {result_10}"

    # n=29: still no discount
    conn_29 = make_conn(29)
    result_29 = _check_persistence_anomaly(conn_29, "London", target, predicted)
    assert result_29 == 1.0, f"n=29 should give no discount, got {result_29}"

    # n=30: minimum discount fires (10%)
    conn_30 = make_conn(30)
    result_30 = _check_persistence_anomaly(conn_30, "London", target, predicted)
    assert abs(result_30 - 0.90) < 0.01, f"n=30 should give 10% discount, got {1 - result_30:.2%}"

    # n=100: full 30% discount
    conn_100 = make_conn(100)
    result_100 = _check_persistence_anomaly(conn_100, "London", target, predicted)
    assert abs(result_100 - 0.70) < 0.01, f"n=100 should give 30% discount, got {1 - result_100:.2%}"

    # Discount must increase with n (more data → more confident → larger penalty)
    assert result_30 > result_100, "Larger n should yield larger discount"


# ---- Integer Rounding Must Be Unit-Aware ----

def test_no_hardcoded_integer_rounding_for_celsius():
    """Signal and bootstrap settlement paths must not hand-roll rounding.

    WMO settlement rounding lives in SettlementSemantics. K3 signal/strategy
    paths may call that law, but must not reintroduce NumPy banker's rounding.
    """
    import inspect
    from src.strategy import market_analysis
    from src.signal import day0_signal

    for module in [market_analysis, day0_signal]:
        source = inspect.getsource(module)
        for forbidden in ("astype(int)", "np.round("):
            if forbidden not in source:
                continue
            pytest.fail(
                f"{module.__name__} uses hardcoded settlement rounding via {forbidden}. "
                "Must use SettlementSemantics/WMO rounding law for unit-aware settlement."
            )


# ---- Settlement Precision Contract Enforcement ----

def test_settlement_semantics_round_single():
    """round_single() must use WMO asymmetric half-up, not banker's rounding."""
    sem_f = SettlementSemantics.default_wu_fahrenheit("KLGA")
    assert sem_f.rounding_rule == "wmo_half_up"
    assert sem_f.round_single(52.45) == 52.0
    assert sem_f.round_single(52.5) == 53.0
    assert sem_f.round_single(74.5) == 75.0

    sem_c = SettlementSemantics.default_wu_celsius("EGLL")
    assert sem_c.rounding_rule == "wmo_half_up"
    assert sem_c.round_single(4.4) == 4.0
    assert sem_c.round_single(4.6) == 5.0

    # Negative values round up the number line: floor(x + 0.5).
    assert sem_c.round_single(-0.5) == 0.0
    assert sem_c.round_single(-1.5) == -1.0
    assert sem_c.round_single(-2.5) == -2.0
    assert sem_c.round_single(-3.7) == -4.0
    assert sem_c.round_single(0.0) == 0.0


def test_signal_and_strategy_settle_paths_use_wmo_rounding():
    """Day0 and bootstrap settlement paths must match the K0 WMO contract."""
    from src.signal.day0_signal import Day0Signal
    from src.strategy.market_analysis import MarketAnalysis

    values = np.array([52.5, 74.5, -0.5, -1.5, -2.5])
    expected = np.array([53.0, 75.0, 0.0, -1.0, -2.0])

    day0 = Day0Signal.__new__(Day0Signal)
    day0._precision = 1.0
    np.testing.assert_array_equal(day0._settle(values), expected)

    analysis = MarketAnalysis.__new__(MarketAnalysis)
    analysis._precision = 1.0
    np.testing.assert_array_equal(analysis._settle(values), expected)


def test_no_banker_rounding_rule_in_active_settlement_surfaces():
    """Active machine-readable settlement surfaces must not name banker rounding."""
    root = Path(__file__).resolve().parents[1]
    surfaces = [
        "src/contracts/settlement_semantics.py",
        "src/signal/day0_signal.py",
        "src/strategy/market_analysis.py",
        "state/assumptions.json",
        "config/reality_contracts/data.yaml",
        "scripts/backfill_wu_daily_all.py",
        "scripts/onboard_cities.py",
    ]

    offenders = [
        path
        for path in surfaces
        if "round_half_to_even" in (root / path).read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_python_banker_rounding_of_settlement_values_in_active_code():
    """Active source/scripts must not derive settlement outcomes with Python round()."""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    def expression_mentions_settlement_value(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and "settlement_value" in node.id:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "settlement_value" in node.value
        if isinstance(node, ast.Attribute) and "settlement_value" in node.attr:
            return True
        return any(expression_mentions_settlement_value(child) for child in ast.iter_child_nodes(node))

    for base in ("src", "scripts"):
        for path in sorted((root / base).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            lines = source.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_python_round = isinstance(func, ast.Name) and func.id == "round"
                is_numpy_round = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "round"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in {"np", "numpy"}
                )
                if not (is_python_round or is_numpy_round):
                    continue
                if any(expression_mentions_settlement_value(arg) for arg in node.args):
                    line = lines[node.lineno - 1].strip() if node.lineno else ""
                    offenders.append(f"{rel}:{node.lineno}: {line}")

    assert offenders == []


def test_settlement_semantics_assert_rejects_nan():
    """assert_settlement_value() must raise on NaN/inf."""
    from src.contracts.exceptions import SettlementPrecisionError
    sem = SettlementSemantics.default_wu_fahrenheit("KLGA")

    with pytest.raises(SettlementPrecisionError):
        sem.assert_settlement_value(float("nan"), context="test")

    with pytest.raises(SettlementPrecisionError):
        sem.assert_settlement_value(float("inf"), context="test")


def test_settlement_semantics_assert_rounds_and_returns():
    """assert_settlement_value() must return the rounded value."""
    sem = SettlementSemantics.default_wu_fahrenheit("KLGA")
    assert sem.assert_settlement_value(72.4) == 72.0
    assert sem.assert_settlement_value(73.0) == 73.0


def test_no_raw_settlement_writes_bypass_contract():
    """Structural linter: all settlement DB write paths must round via SettlementSemantics.

    Scans ALL Python files for INSERT/UPDATE statements targeting the settlements table.
    Any file that writes settlement_value must import and use SettlementSemantics.
    Test files are exempt (they may use integer literals directly).
    """
    import re
    from pathlib import Path

    project_root = Path(__file__).parent.parent
    violations = []

    # Pattern: any SQL that inserts/updates the settlements table
    settlement_write_pattern = re.compile(
        r'(INSERT\s+.*INTO\s+settlements|UPDATE\s+settlements\s+SET)',
        re.IGNORECASE,
    )

    # Scan all non-test Python files
    for py_file in sorted(project_root.rglob("*.py")):
        rel = py_file.relative_to(project_root)
        # Skip test files, __pycache__, and .venv
        rel_str = str(rel)
        if (
            rel_str.startswith("tests/")
            or rel_str.startswith(".claude/")
            or rel_str.startswith(".omx/")
            or rel_str.startswith("docs/archives/")
            or "__pycache__" in rel_str
            or ".venv" in rel_str
        ):
            continue

        source = py_file.read_text(errors="ignore")
        if not settlement_write_pattern.search(source):
            continue

        # This file writes to settlements — must use SettlementSemantics
        if "SettlementSemantics" not in source:
            violations.append(
                f"{rel}: writes to settlements table but does not import SettlementSemantics"
            )

        if "assert_settlement_value" not in source and "round_single" not in source:
            violations.append(
                f"{rel}: writes to settlements table but never calls "
                "assert_settlement_value() or round_single()"
            )

    if violations:
        pytest.fail(
            "Settlement precision contract violations:\n"
            + "\n".join(f"  • {v}" for v in violations)
        )
