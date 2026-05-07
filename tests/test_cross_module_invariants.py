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


def test_cycle_runner_quarantine_gate_preserves_chain_state_enum_meaning():
    """Cycle entry blocking must treat ChainState enum and wire strings identically."""
    _ensure_sklearn_stub_for_cycle_runner_import()

    from src.contracts.semantic_types import ChainState
    from src.engine.cycle_runner import _discovery_gates_allow_entries, _has_quarantined_positions
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import PortfolioState, Position

    def _position(trade_id: str, chain_state: object) -> Position:
        return Position(
            trade_id=trade_id,
            market_id="m1",
            city="NYC",
            cluster="NYC",
            target_date="2026-04-01",
            bin_label="39-40°F",
            direction="buy_yes",
            chain_state=chain_state,
        )

    quarantined = _position("chain-state-enum", "quarantined")
    expired = _position("chain-state-expired", ChainState.QUARANTINE_EXPIRED)
    synced = _position("chain-state-synced", ChainState.SYNCED)
    fill_quarantined = _position("fill-quarantine", ChainState.SYNCED)
    fill_quarantined.state = "quarantined"

    assert quarantined.chain_state is ChainState.QUARANTINED
    assert _has_quarantined_positions(PortfolioState(positions=[quarantined])) is True
    assert _has_quarantined_positions(PortfolioState(positions=[expired])) is True
    assert _has_quarantined_positions(PortfolioState(positions=[fill_quarantined])) is True
    assert _has_quarantined_positions(PortfolioState(positions=[synced])) is False

    green_gate = {"entry": {"allow_submit": True}}
    assert _discovery_gates_allow_entries(
        RiskLevel.GREEN,
        green_gate,
        green_gate,
        has_quarantine=False,
    ) is True
    assert _discovery_gates_allow_entries(
        RiskLevel.GREEN,
        green_gate,
        green_gate,
        has_quarantine=True,
    ) is False


def test_calibration_pairs_use_same_bias_correction_as_live():
    """If bias correction is enabled in ensemble_signal,
    ALL calibration pairs must also be computed with bias correction.

    RATIONALE: Platt learns sigmoid params mapping P_raw → outcome.
    If P_raw changes (via bias correction) but Platt was trained on
    uncorrected P_raw, the sigmoid is in the wrong domain.
    """
    from src.config import settings

    bias_enabled = settings.bias_correction_enabled
    if not bias_enabled:
        pytest.skip("bias_correction_enabled = false. No invariant to check.")

    from src.state.db import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)

    # Check if calibration_pairs has bias_corrected column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(calibration_pairs)").fetchall()]
    if "bias_corrected" not in cols:
        conn.close()
        pytest.fail(
            "bias_correction_enabled=true but calibration_pairs has no "
            "'bias_corrected' column. Pairs were computed without bias correction."
        )

    # Check recent pairs
    pairs = conn.execute("""
        SELECT id, bias_corrected FROM calibration_pairs
        ORDER BY id DESC LIMIT 50
    """).fetchall()

    if not pairs:
        conn.close()
        pytest.skip("No calibration pairs in database.")

    uncorrected = [p for p in pairs if not p["bias_corrected"]]
    if uncorrected:
        conn.close()
        pytest.fail(
            f"{len(uncorrected)}/{len(pairs)} recent calibration pairs were computed "
            f"WITHOUT bias correction, but live signal uses it. "
            f"First uncorrected pair ID: {uncorrected[0]['id']}. "
            "Action: recompute all pairs with bias correction, then refit Platt."
        )

    conn.close()


def test_model_bias_table_not_empty_if_bias_enabled():
    """If bias correction is enabled, model_bias table must have data."""
    from src.config import settings

    bias_enabled = settings.bias_correction_enabled
    if not bias_enabled:
        pytest.skip("bias_correction_enabled = false.")

    from src.state.db import get_connection

    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM model_bias WHERE source='ecmwf'").fetchone()[0]
    conn.close()

    if count == 0:
        pytest.fail("bias_correction_enabled=true but model_bias has 0 ECMWF rows.")


def test_platt_models_consistent_with_bias_flag():
    """If bias correction is enabled, Platt models must have been trained
    on bias-corrected calibration pairs."""
    from src.config import settings

    bias_enabled = settings.bias_correction_enabled
    if not bias_enabled:
        pytest.skip("bias_correction_enabled = false.")

    from src.state.db import get_connection

    conn = get_connection()
    
    # Check if platt_models has a bias_corrected flag
    cols = [r[1] for r in conn.execute("PRAGMA table_info(platt_models)").fetchall()]
    
    if "trained_with_bias_correction" not in cols:
        conn.close()
        pytest.fail(
            "bias_correction_enabled=true but platt_models has no "
            "'trained_with_bias_correction' column."
        )

    uncorrected = conn.execute(
        "SELECT COUNT(*) FROM platt_models WHERE is_active=1 AND trained_with_bias_correction=0"
    ).fetchone()[0]
    
    if uncorrected > 0:
        conn.close()
        pytest.fail(f"{uncorrected} active Platt models trained without bias correction.")

    conn.close()


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
    test_calibration_pairs_use_same_bias_correction_as_live,
        test_model_bias_table_not_empty_if_bias_enabled,
        test_platt_models_consistent_with_bias_flag,
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
