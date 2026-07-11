# Created: 2026-03-30
# Last reused/audited: 2026-05-05
# Authority basis: midstream verdict v2 2026-04-23; object-meaning invariance Wave10 cycle_runner ChainState enum/string boundary.
"""Cross-module invariant tests.

These tests verify that modifications to one module's output are
synchronized with all downstream dependencies. Prevents the class of
bugs where a signal module changes but calibration doesn't follow.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_sklearn_stub_for_cycle_runner_import() -> None:
    try:
        from sklearn.linear_model import LogisticRegression as _LogisticRegression  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    import types

    sklearn = types.ModuleType("sklearn")
    linear_model = types.ModuleType("sklearn.linear_model")

    class LogisticRegression:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def fit(self, X, y):
            return self

        def predict_proba(self, X):
            return [[0.5, 0.5] for _ in range(len(X))]

    linear_model.LogisticRegression = LogisticRegression
    sklearn.linear_model = linear_model
    sys.modules.setdefault("sklearn", sklearn)
    sys.modules.setdefault("sklearn.linear_model", linear_model)


def test_cycle_runner_discovery_gate_all_clear_without_quarantine_kwarg():
    """Quarantine excision T2 (docs/rebuild/quarantine_excision_2026-07-11.md):
    _discovery_gates_allow_entries no longer takes ``has_quarantine`` — the
    portfolio-wide gate and its backing ``_has_quarantined_positions`` are
    deleted. This test keeps the cross-module all-clear/DATA_DEGRADED check
    (the gate's replacement signal) without the retired kwarg.
    """
    _ensure_sklearn_stub_for_cycle_runner_import()

    from src.engine.cycle_runner import _discovery_gates_allow_entries
    from src.riskguard.risk_level import RiskLevel

    green_gate = {"entry": {"allow_submit": True}}
    all_clear = {
        "risk_level": RiskLevel.GREEN,
        "heartbeat_status": green_gate,
        "ws_gap_status": green_gate,
        "cutover_summary": green_gate,
        "governor_status": green_gate,
        "current_posture": "NORMAL",
        "chain_ready": True,
        "force_exit": False,
        "freshness_allows_entries": True,
        "entry_bankroll": 1000.0,
        "exposure_gate_hit": False,
        "entries_paused": False,
    }
    assert _discovery_gates_allow_entries(**all_clear) is True
    # DATA_DEGRADED now carries the replacement signal (unbounded
    # EntryExposureObligation / unmapped ChainOnlyFact family) — any
    # non-GREEN risk_level still blocks, same as before the excision.
    assert _discovery_gates_allow_entries(**{**all_clear, "risk_level": RiskLevel.DATA_DEGRADED}) is False


def test_evaluator_quarantined_position_bridging_preserves_chain_state_enum_meaning():
    """Cross-module invariant (T2 excision replacement): the bridging shim
    that feeds phase='quarantined' positions into the family-scoped block
    (src.engine.evaluator._quarantined_position_bridging_family_keys) must
    treat the ChainState enum and its raw wire-string form identically — the
    same enum/string-boundary invariant the retired
    ``_has_quarantined_positions`` used to cover for the (now-deleted)
    portfolio-wide gate.
    """
    _ensure_sklearn_stub_for_cycle_runner_import()

    from src.contracts.semantic_types import ChainState
    from src.engine.evaluator import _quarantined_position_bridging_family_keys
    from src.state.portfolio import PortfolioState, Position
    from src.strategy.family_exclusive_dedup import WeatherFamilyKey

    def _position(trade_id: str, chain_state: object) -> Position:
        return Position(
            trade_id=trade_id,
            market_id="m1",
            city="NYC",
            cluster="NYC",
            target_date="2026-04-01",
            bin_label="39-40°F",
            direction="buy_yes",
            temperature_metric="high",
            state="quarantined",
            chain_state=chain_state,
        )

    quarantined_enum = _position("chain-state-enum", ChainState.ENTRY_AUTHORITY_QUARANTINED)
    quarantined_str = _position("chain-state-str", "entry_authority_quarantined")
    synced = _position("chain-state-synced", ChainState.SYNCED)

    expected = {WeatherFamilyKey("NYC", "2026-04-01", "high", "")}
    assert _quarantined_position_bridging_family_keys(PortfolioState(positions=[quarantined_enum])) == expected
    assert _quarantined_position_bridging_family_keys(PortfolioState(positions=[quarantined_str])) == expected
    assert _quarantined_position_bridging_family_keys(PortfolioState(positions=[synced])) == set()


def test_structural_linter_gate():
    """Run the structural linter to ensure all cross-module semantic invariants hold.
    Also tests an intentional violation to explicitly prove the gate works.
    """
    import tempfile
    import os
    import ast
    from pathlib import Path
    from scripts.semantic_linter import SemanticAnalyzer, run_linter
    
    # 1. Test intentional violation
    with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w') as test_file:
        test_file.write("""
def bad_function(obj):
    return obj.p_raw[0]
""")
        test_file_path = test_file.name

    try:
        py_file = Path(test_file_path)
        tree = ast.parse(py_file.read_text())
        analyzer = SemanticAnalyzer(py_file)
        analyzer.visit(tree)
        assert analyzer.violations, "Linter gate did NOT catch the intentional p_raw violation."
        assert any(
            'p_raw' in e and ('bias' in e.lower() or 'cal' in e.lower() or 'platt' in e.lower() or 'sigma' in e.lower())
            for e in analyzer.violations
        ), "Linter gate caught errors, but not the p_raw rule as expected."
    finally:
        os.remove(test_file_path)

    # 2. Test entire repo passes
    repo_errors = run_linter(Path('src'))
    assert repo_errors == 0, "Linter gate flagged existing code in src/."

def test_inv03_harvester_prefers_decision_snapshot_over_latest():
    """INV-06 / NC-05: harvest_settlement must use decision_snapshot_id filter,
    NOT ORDER BY fetch_time DESC LIMIT 1 (hindsight fallback).

    AST walks the harvest_settlement function body ONLY — excludes _get_stored_p_raw
    which legitimately uses ORDER BY fetch_time DESC LIMIT 1 as a separate fallback.
    """
    import ast
    harvester_py = PROJECT_ROOT / "src" / "execution" / "harvester.py"
    if not harvester_py.exists():
        pytest.skip("harvester.py not found")

    source = harvester_py.read_text()
    tree = ast.parse(source)

    harvest_fn_body_linenos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "harvest_settlement":
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
        "INV-06 / NC-05: harvest_settlement must not use ORDER BY fetch_time DESC LIMIT 1 "
        "(hindsight fallback). Use decision_snapshot_id filter:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_inv04_no_bare_temperature_threshold_comparisons_in_src():
    """NC-08: No bare float threshold comparisons against temperature identifier names
    in src/. Strict set: {temp, temperature, kelvin, celsius, fahrenheit}. No 'threshold'.

    Pre-verified false-positive rate = 0 (P10E contract M3 correction).
    """
    import ast

    TEMP_NAMES = {"temp", "temperature", "kelvin", "celsius", "fahrenheit"}
    violations = []

    for py_file in (PROJECT_ROOT / "src").rglob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
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
        "NC-08: bare float threshold comparisons against temperature identifiers in src/:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


if __name__ == "__main__":
    tests = [
        test_structural_linter_gate,
        test_inv03_harvester_prefers_decision_snapshot_over_latest,
        test_inv04_no_bare_temperature_threshold_comparisons_in_src,
    ]

    results = {}
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            results[test.__name__] = test()
        except Exception as e:
            print(f"ERROR: {e}")
            results[test.__name__] = False

    print("\n" + "=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"Results: {passed}/{total} passed")

    if not all(results.values()):
        print("\nFAILED TESTS:")
        for name, result in results.items():
            if not result:
                print(f"  ✗ {name}")
        sys.exit(1)
    else:
        print("All cross-module invariants satisfied.")
