"""Cross-module invariant tests.

These tests verify that modifications to one module's output are
synchronized with all downstream dependencies. Prevents the class of
bugs where a signal module changes but calibration doesn't follow.
"""

import sqlite3
import sys
from pathlib import Path

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

    bias_enabled = settings._data.get("bias_correction_enabled", False)
    if not bias_enabled:
        print("SKIP: bias_correction_enabled = false. No invariant to check.")
        return True

    from src.state.db import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)

    # Check if calibration_pairs has bias_corrected column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(calibration_pairs)").fetchall()]
    if "bias_corrected" not in cols:
        print("FAIL: bias_correction_enabled=true but calibration_pairs has no "
              "'bias_corrected' column. Pairs were computed without bias correction.")
        conn.close()
        return False

    # Check recent pairs
    pairs = conn.execute("""
        SELECT id, bias_corrected FROM calibration_pairs
        ORDER BY id DESC LIMIT 50
    """).fetchall()

    if not pairs:
        print("SKIP: No calibration pairs in database.")
        conn.close()
        return True

    uncorrected = [p for p in pairs if not p["bias_corrected"]]
    if uncorrected:
        print(f"FAIL: {len(uncorrected)}/{len(pairs)} recent calibration pairs "
              f"were computed WITHOUT bias correction, but live signal uses it.")
        print(f"  First uncorrected pair ID: {uncorrected[0]['id']}")
        print(f"  ACTION: Recompute all pairs with bias correction, then refit Platt.")
        conn.close()
        return False

    print(f"PASS: All {len(pairs)} recent pairs have bias_corrected=true.")
    conn.close()
    return True


def test_model_bias_table_not_empty_if_bias_enabled():
    """If bias correction is enabled, model_bias table must have data."""
    from src.config import settings

    bias_enabled = settings._data.get("bias_correction_enabled", False)
    if not bias_enabled:
        print("SKIP: bias_correction_enabled = false.")
        return True

    from src.state.db import get_connection

    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM model_bias WHERE source='ecmwf'").fetchone()[0]
    conn.close()

    if count == 0:
        print("FAIL: bias_correction_enabled=true but model_bias has 0 ECMWF rows.")
        return False

    print(f"PASS: model_bias has {count} ECMWF rows.")
    return True


def test_platt_models_consistent_with_bias_flag():
    """If bias correction is enabled, Platt models must have been trained
    on bias-corrected calibration pairs."""
    from src.config import settings

    bias_enabled = settings._data.get("bias_correction_enabled", False)
    if not bias_enabled:
        print("SKIP: bias_correction_enabled = false.")
        return True

    from src.state.db import get_connection

    conn = get_connection()
    
    # Check if platt_models has a bias_corrected flag
    cols = [r[1] for r in conn.execute("PRAGMA table_info(platt_models)").fetchall()]
    
    if "trained_with_bias_correction" not in cols:
        print("FAIL: bias_correction_enabled=true but platt_models has no "
              "'trained_with_bias_correction' column.")
        conn.close()
        return False

    uncorrected = conn.execute(
        "SELECT COUNT(*) FROM platt_models WHERE is_active=1 AND trained_with_bias_correction=0"
    ).fetchone()[0]
    
    if uncorrected > 0:
        print(f"FAIL: {uncorrected} active Platt models trained without bias correction.")
        conn.close()
        return False

    print("PASS: All active Platt models trained with bias correction.")
    conn.close()
    return True


if __name__ == "__main__":
    tests = [
        test_calibration_pairs_use_same_bias_correction_as_live,
        test_model_bias_table_not_empty_if_bias_enabled,
        test_platt_models_consistent_with_bias_flag,
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
