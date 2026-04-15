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

if __name__ == "__main__":
    tests = [
    test_calibration_pairs_use_same_bias_correction_as_live,
        test_model_bias_table_not_empty_if_bias_enabled,
        test_platt_models_consistent_with_bias_flag,
        test_structural_linter_gate,
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
