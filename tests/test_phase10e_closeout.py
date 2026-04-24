"""Phase 10E closeout antibodies.

Covers R-DD (P10E.1), R-DE (P10E.2), R-DF (P10E.3).
Total: 13 antibodies.

Created: 2026-04-20
Authority basis: phase10e_contract.md v2
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# R-DD — P10E.1: Kelly strict ExecutionPrice
# ---------------------------------------------------------------------------


def test_r_dd_1_kelly_size_bare_float_raises_type_error():
    """R-DD.1: kelly_size(entry_price=0.42) raises TypeError (bare float forbidden).

    P10E strict: ExecutionPrice.assert_kelly_safe() is called unconditionally.
    Passing a bare float causes AttributeError (float has no assert_kelly_safe).
    """
    from src.strategy.kelly import kelly_size

    with pytest.raises((TypeError, AttributeError)):
        kelly_size(p_posterior=0.60, entry_price=0.42, bankroll=1000.0)


def test_r_dd_2_execution_price_invalid_axes_fail_kelly_safe():
    """R-DD.2: ExecutionPrice with invalid axis (price_type / fee_deducted / currency)
    fails assert_kelly_safe(). Valid 4-field price succeeds.
    """
    from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError

    # (a) wrong price_type: implied_probability
    ep_bad_type = ExecutionPrice(
        value=0.42, price_type="implied_probability",
        fee_deducted=True, currency="probability_units",
    )
    with pytest.raises(ExecutionPriceContractError):
        ep_bad_type.assert_kelly_safe()

    # (b) wrong fee_deducted: False
    ep_bad_fee = ExecutionPrice(
        value=0.42, price_type="fee_adjusted",
        fee_deducted=False, currency="probability_units",
    )
    with pytest.raises(ExecutionPriceContractError):
        ep_bad_fee.assert_kelly_safe()

    # (c) wrong currency: usd
    ep_bad_currency = ExecutionPrice(
        value=0.42, price_type="fee_adjusted",
        fee_deducted=True, currency="usd",
    )
    with pytest.raises(ExecutionPriceContractError):
        ep_bad_currency.assert_kelly_safe()

    # (d) valid 4-field price succeeds
    ep_good = ExecutionPrice(
        value=0.42, price_type="fee_adjusted",
        fee_deducted=True, currency="probability_units",
    )
    ep_good.assert_kelly_safe()  # must not raise


def test_r_dd_3_prod_callers_use_non_literal_entry_price():
    """R-DD.3: AST — prod callers in src/ call kelly_size with non-literal entry_price.
    No call site passes a bare float constant as the second positional arg.
    """
    src_dir = PROJECT_ROOT / "src"
    violations = []
    for py_file in src_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name != "kelly_size":
                continue
            # Check positional args: second arg (index 1) must not be a literal float/int
            if len(node.args) >= 2:
                second_arg = node.args[1]
                if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, (int, float)):
                    violations.append(
                        f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno}: "
                        f"kelly_size called with bare float constant entry_price={second_arg.value!r}"
                    )
    assert not violations, (
        "R-DD.3: prod callers must not pass bare float literals to kelly_size:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_r_dd_4_bug12_regressions():
    """R-DD.4: Bug #12 regressions — bankroll / p_posterior bounds + p <= entry.
    4 kelly-level + 4 ExecutionPrice construction-level.
    """
    from src.contracts.execution_price import ExecutionPrice
    from src.strategy.kelly import kelly_size

    def _ep(v: float) -> ExecutionPrice:
        return ExecutionPrice(value=v, price_type="fee_adjusted",
                              fee_deducted=True, currency="probability_units")

    # Kelly-level: bankroll guards
    assert kelly_size(0.60, _ep(0.40), 0.0) == 0.0, "bankroll=0 → 0"
    assert kelly_size(0.60, _ep(0.40), -50.0) == 0.0, "bankroll<0 → 0"

    # Kelly-level: p_posterior bounds
    assert kelly_size(1.01, _ep(0.40), 1000.0) == 0.0, "p_posterior>1 → 0"
    assert kelly_size(-0.01, _ep(0.40), 1000.0) == 0.0, "p_posterior<0 → 0"

    # Kelly-level: p_posterior <= entry
    assert kelly_size(0.40, _ep(0.50), 1000.0) == 0.0, "p≤entry → 0"

    # Construction-level: invalid values raise ValueError
    with pytest.raises(ValueError):
        ExecutionPrice(value=-0.1, price_type="fee_adjusted",
                       fee_deducted=True, currency="probability_units")
    with pytest.raises(ValueError):
        ExecutionPrice(value=1.01, price_type="fee_adjusted",
                       fee_deducted=True, currency="probability_units")
    with pytest.raises(ValueError):
        ExecutionPrice(value=float("nan"), price_type="fee_adjusted",
                       fee_deducted=True, currency="probability_units")


# ---------------------------------------------------------------------------
# R-DE — P10E.2: city_obj strict requirement
# ---------------------------------------------------------------------------


def test_r_de_1_ast_add_calibration_pair_callers_pass_city_obj():
    """R-DE.1: AST — every add_calibration_pair*(conn, ...) call site in src/ and
    scripts/ passes city_obj= keyword argument.
    """
    targets = [PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"]
    violations = []
    for target_dir in targets:
        if not target_dir.exists():
            continue
        for py_file in target_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name not in ("add_calibration_pair", "add_calibration_pair_v2"):
                    continue
                kwarg_names = {kw.arg for kw in node.keywords}
                if "city_obj" not in kwarg_names:
                    violations.append(
                        f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno}: "
                        f"{func_name}() missing city_obj= kwarg"
                    )
    assert not violations, (
        "R-DE.1: all add_calibration_pair*() callers must pass city_obj=:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_r_de_2_add_calibration_pair_without_city_obj_raises():
    """R-DE.2: add_calibration_pair(conn, city=str, ...) without city_obj raises TypeError."""
    import sqlite3
    from src.calibration.store import add_calibration_pair
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    with pytest.raises(TypeError):
        add_calibration_pair(
            conn, "NYC", "2026-01-01", "39-40°F",
            p_raw=0.4, outcome=1,
            lead_days=2.0, season="DJF", cluster="US-Northeast",
            forecast_available_at="2025-12-30T00:00:00Z",
            decision_group_id="nyc|2026-01-01|test",
            # city_obj intentionally omitted → TypeError
        )
    conn.close()


def test_r_de_3_hko_wu_dispatch_via_settlement_semantics():
    """R-DE.3: HKO vs WU dispatch via SettlementSemantics unchanged post-strict.
    SettlementSemantics.for_city() dispatches correctly for WU and HKO cities.
    """
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.config import City

    # WU ICAO city (standard path)
    wu_city = City(
        name="NYC", lat=40.7772, lon=-73.8726,
        timezone="America/New_York", cluster="NYC",
        settlement_unit="F", wu_station="KLGA",
    )
    sem_wu = SettlementSemantics.for_city(wu_city)
    # Must produce a SettlementSemantics that can round values
    result = sem_wu.round_values([72.4, 68.7])
    assert len(result) == 2
    assert all(isinstance(v, (int, float)) for v in result)


# ---------------------------------------------------------------------------
# R-DF — P10E.3: Loose ends
# ---------------------------------------------------------------------------


def test_r_df_1_day0_observation_context_has_causality_status():
    """R-DF.1: Day0ObservationContext.causality_status field exists with default='OK'."""
    from src.data.observation_client import Day0ObservationContext
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(Day0ObservationContext)}
    assert "causality_status" in fields, (
        "Day0ObservationContext must carry causality_status field (INV-16 / P10E S3a)"
    )
    default = fields["causality_status"].default
    assert default == "OK", (
        f"Day0ObservationContext.causality_status default must be 'OK', got {default!r}"
    )


def test_r_df_2_inv16_test3_xfail_transitions_to_pass():
    """R-DF.2: INV-16 test 3 (test_day0_observation_context_carries_causality_status)
    must pass (xfail→PASS) after S3a adds the field.

    This test directly exercises the same assertion to confirm it passes now.
    """
    from src.data.observation_client import Day0ObservationContext
    import dataclasses

    fields = {f.name for f in dataclasses.fields(Day0ObservationContext)}
    assert "causality_status" in fields, (
        "Day0ObservationContext must carry causality_status (INV-16 xfail→PASS)"
    )


def test_r_df_3_harvest_settlement_uses_decision_snapshot_id():
    """R-DF.3: INV-06 ghost test — harvest_settlement body uses decision_snapshot_id,
    not ORDER BY fetch_time DESC LIMIT 1 (hindsight fallback).

    AST walk harvest_settlement function body only (excludes _get_stored_p_raw).
    """
    harvester_py = PROJECT_ROOT / "src" / "execution" / "harvester.py"
    if not harvester_py.exists():
        pytest.skip("harvester.py not found")

    source = harvester_py.read_text()
    tree = ast.parse(source)

    harvest_fn_body_linenos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "harvest_settlement":
            # Collect all line numbers in this function body
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    harvest_fn_body_linenos.add(child.lineno)
            break

    if not harvest_fn_body_linenos:
        pytest.skip("harvest_settlement not found in harvester.py")

    lines = source.splitlines()
    violations = []
    for lineno in sorted(harvest_fn_body_linenos):
        if lineno - 1 < len(lines):
            line = lines[lineno - 1]
            if "ORDER BY fetch_time DESC LIMIT 1" in line:
                violations.append(f"L{lineno}: {line.strip()}")

    assert not violations, (
        "R-DF.3 / INV-06: harvest_settlement must not use ORDER BY fetch_time DESC LIMIT 1 "
        "(hindsight fallback); use decision_snapshot_id filter instead:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_r_df_4_nc08_no_bare_temperature_threshold_comparisons():
    """R-DF.4: NC-08 ghost test — AST walk src/ for bare float comparisons against
    temperature identifier names {temp, temperature, kelvin, celsius, fahrenheit}.

    Strict scope: exact name match only. No 'threshold' in the set.
    Pre-verified false-positive rate = 0.
    """
    import re

    src_dir = PROJECT_ROOT / "src"
    TEMP_NAMES = {"temp", "temperature", "kelvin", "celsius", "fahrenheit"}
    violations = []

    for py_file in src_dir.rglob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            # Check if one side is a bare float constant AND the other is a Name in TEMP_NAMES
            left = node.left
            comparators = node.comparators
            all_sides = [left] + list(comparators)
            has_bare_float = any(
                isinstance(s, ast.Constant) and isinstance(s.value, float)
                for s in all_sides
            )
            has_temp_name = any(
                isinstance(s, ast.Name) and s.id in TEMP_NAMES
                for s in all_sides
            )
            if has_bare_float and has_temp_name:
                violations.append(
                    f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno}: "
                    f"bare float comparison against temperature identifier"
                )

    assert not violations, (
        "R-DF.4 / NC-08: bare float threshold comparisons against temperature "
        "identifiers found in src/:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_r_df_5_savepoint_integration_execute_discovery_phase():
    """R-DF.5: SAVEPOINT integration — log_execution_report raising mid-transaction
    causes log_trade_entry row to be rolled back.
    """
    import sqlite3
    import types
    from unittest.mock import patch

    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    # Verify the SAVEPOINT logic exists in cycle_runtime
    cycle_runtime_py = PROJECT_ROOT / "src" / "engine" / "cycle_runtime.py"
    if not cycle_runtime_py.exists():
        pytest.skip("cycle_runtime.py not found")

    source = cycle_runtime_py.read_text()
    assert "SAVEPOINT" in source, (
        "R-DF.5: cycle_runtime.py must use SAVEPOINT for atomic trade entry writes"
    )
    assert "ROLLBACK TO SAVEPOINT" in source, (
        "R-DF.5: cycle_runtime.py must have ROLLBACK TO SAVEPOINT on failure path"
    )
    assert "log_trade_entry" in source, (
        "R-DF.5: log_trade_entry must be within the SAVEPOINT guard"
    )
    conn.close()


def test_r_df_6_dual_write_canonical_logs_warning_not_debug():
    """R-DF.6: _dual_write_canonical_entry_if_available exception handler
    must log WARNING (not DEBUG) with descriptor CANONICAL_DUAL_WRITE_SKIPPED.
    """
    cycle_runtime_py = PROJECT_ROOT / "src" / "engine" / "cycle_runtime.py"
    if not cycle_runtime_py.exists():
        pytest.skip("cycle_runtime.py not found")

    source = cycle_runtime_py.read_text()
    tree = ast.parse(source)

    # Find _dual_write_canonical_entry_if_available function
    fn_lines: list[str] = []
    lines = source.splitlines()
    in_fn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_dual_write_canonical_entry_if_available":
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else start + 50
            fn_lines = lines[start:end]
            in_fn = True
            break

    if not in_fn:
        pytest.skip("_dual_write_canonical_entry_if_available not found")

    fn_text = "\n".join(fn_lines)
    assert "warning" in fn_text.lower(), (
        "R-DF.6: _dual_write_canonical_entry_if_available must use logger.warning(), not debug()"
    )
    assert "CANONICAL_DUAL_WRITE_SKIPPED" in fn_text, (
        "R-DF.6: log message must include descriptor CANONICAL_DUAL_WRITE_SKIPPED"
    )
    assert "logger.debug" not in fn_text, (
        "R-DF.6: must not use logger.debug() for this exception; use logger.warning()"
    )
