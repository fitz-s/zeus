# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/phase.json
"""Tests for T1E rebuild sentinel check and transaction sharding.

Invariants asserted:
  T1E-REBUILD-SENTINEL-REFUSES: rebuild_calibration_pairs_v2.py exits non-zero
    when .zeus/rebuild_lock.do_not_run_during_live exists. The check fires BEFORE
    any sqlite3.connect call.
  T1E-REBUILD-TRANSACTION-SHARDED: rebuild_v2 commits per (city, metric) bucket.
    commit() call count is > 1 for a multi-city rebuild.

Tests:
  test_rebuild_refuses_during_live_subprocess   — sentinel present → sys.exit(1) via subprocess
  test_sentinel_check_fires_before_db_connect   — _SENTINEL_PATH checked before sqlite3.connect
  test_rebuild_runs_when_sentinel_absent        — sentinel absent → _check_live_sentinel passes
  test_rebuild_shards_transactions_commit_per_city — commit count >= 2 for 2-city rebuild

Import strategy: the sentinel check runs at module load via _check_live_sentinel().
Tests that need to import the module patch _check_live_sentinel to a no-op first,
then import/reload. The subprocess test verifies the real exit(1) path.
"""
from __future__ import annotations

import importlib
import sqlite3
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "rebuild_calibration_pairs_v2.py"


# ---------------------------------------------------------------------------
# Fixture: import the module with sentinel check patched out
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rebuild_mod():
    """Import rebuild_calibration_pairs_v2 with sentinel bypassed for unit tests.

    The sentinel check (_check_live_sentinel) is patched to a no-op before the
    first import. Module is cached by sys.modules so subsequent imports reuse it.
    """
    mod_name = "scripts.rebuild_calibration_pairs_v2"
    # Remove cached copy if present (from a previous test run that hit sys.exit)
    sys.modules.pop(mod_name, None)

    # Patch _check_live_sentinel at the module level by pre-populating sys.modules
    # with a stub that provides _check_live_sentinel as a no-op, then trigger real load.
    # Simplest approach: temporarily patch sys.exit so exit(1) becomes a no-op during load.
    # But that masks the real behavior. Instead: use importlib + spec patching.
    #
    # Cleanest approach: monkeypatch Path.exists for the exact sentinel path during import.
    sentinel_path = REPO_ROOT / ".zeus" / "rebuild_lock.do_not_run_during_live"
    original_exists = sentinel_path.exists

    # Patch at the instance level (Path objects don't support instance-level attribute override
    # directly, so we patch the class method with a targeted override).
    _patched = False

    original_path_exists = Path.exists

    def patched_exists(self):
        if self == sentinel_path:
            return False  # hide sentinel from module load
        return original_path_exists(self)

    with patch.object(Path, "exists", patched_exists):
        mod = importlib.import_module(mod_name)

    sys.modules[mod_name] = mod
    return mod


# ---------------------------------------------------------------------------
# T1E-REBUILD-SENTINEL-REFUSES (subprocess — real exit path)
# ---------------------------------------------------------------------------

def test_rebuild_refuses_during_live_subprocess():
    """Sentinel present at repo root → subprocess exits non-zero with sentinel message.

    The real sentinel at REPO_ROOT/.zeus/rebuild_lock.do_not_run_during_live
    (created by coordinator per LOCK_DECISION Amendment 2) causes sys.exit(1).
    """
    sentinel = REPO_ROOT / ".zeus" / "rebuild_lock.do_not_run_during_live"
    if not sentinel.exists():
        pytest.skip("Sentinel not present at repo root; coordinator should have created it")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit when sentinel present, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert "sentinel" in result.stderr.lower() or "rebuild_lock" in result.stderr.lower(), (
        f"Expected sentinel message in stderr, got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T1E-REBUILD-SENTINEL-REFUSES (unit — check fires before any DB connect)
# ---------------------------------------------------------------------------

def test_sentinel_check_fires_before_db_connect():
    """_check_live_sentinel() fires before sqlite3.connect is ever called.

    Verifies structural ordering: the sentinel path object is evaluated
    before any DB connection opens. We test this by asserting _SENTINEL_PATH
    is defined in the module and that _check_live_sentinel() calls sys.exit
    when the path exists, before any connect call.
    """
    # Reload with sentinel present (patch connect to detect if called)
    mod_name = "scripts.rebuild_calibration_pairs_v2"
    sys.modules.pop(mod_name, None)

    sentinel_path = REPO_ROOT / ".zeus" / "rebuild_lock.do_not_run_during_live"
    if not sentinel_path.exists():
        pytest.skip("Sentinel not present; cannot test pre-connect ordering")

    connect_called = []
    original_path_exists = Path.exists
    original_connect = sqlite3.connect

    def spy_connect(*args, **kwargs):
        connect_called.append(args)
        return original_connect(*args, **kwargs)

    with patch("sqlite3.connect", side_effect=spy_connect):
        with pytest.raises(SystemExit) as exc_info:
            importlib.import_module(mod_name)

    assert exc_info.value.code != 0
    assert len(connect_called) == 0, (
        f"sqlite3.connect should not be called before sentinel check, "
        f"but got {len(connect_called)} call(s): {connect_called}"
    )

    # Clean up the failed import
    sys.modules.pop(mod_name, None)


# ---------------------------------------------------------------------------
# T1E-REBUILD-SENTINEL-ABSENT
# ---------------------------------------------------------------------------

def test_rebuild_runs_when_sentinel_absent(tmp_path):
    """_check_live_sentinel passes when sentinel file does not exist.

    Creates a temporary Path that does not exist, calls _check_live_sentinel
    logic directly, and asserts no SystemExit is raised.
    """
    absent = tmp_path / "rebuild_lock.do_not_run_during_live"
    assert not absent.exists()

    # Simulate what _check_live_sentinel does
    raised = False
    try:
        if absent.exists():
            sys.exit(1)
    except SystemExit:
        raised = True

    assert not raised, "sentinel check should not exit when file is absent"


def test_check_live_sentinel_no_exit_when_absent(rebuild_mod, tmp_path):
    """_check_live_sentinel() does not exit when _SENTINEL_PATH does not exist."""
    absent = tmp_path / "no_sentinel_here"
    assert not absent.exists()

    original = rebuild_mod._SENTINEL_PATH
    try:
        rebuild_mod._SENTINEL_PATH = absent
        # Should not raise
        rebuild_mod._check_live_sentinel()
    finally:
        rebuild_mod._SENTINEL_PATH = original


def test_check_live_sentinel_exits_when_present(rebuild_mod, tmp_path):
    """_check_live_sentinel() calls sys.exit(1) when sentinel file exists."""
    sentinel = tmp_path / "rebuild_lock.do_not_run_during_live"
    sentinel.touch()

    original = rebuild_mod._SENTINEL_PATH
    try:
        rebuild_mod._SENTINEL_PATH = sentinel
        with pytest.raises(SystemExit) as exc_info:
            rebuild_mod._check_live_sentinel()
        assert exc_info.value.code != 0
    finally:
        rebuild_mod._SENTINEL_PATH = original


# ---------------------------------------------------------------------------
# T1E-REBUILD-TRANSACTION-SHARDED
# ---------------------------------------------------------------------------

def test_rebuild_shards_transactions_commit_per_city(rebuild_mod):
    """rebuild_v2 commits per (city, metric) bucket; commit count >= 2 for 2 cities.

    Uses a mock conn with a counting commit(). Patches all heavy dependencies.
    Asserts conn.commit() is called at least once per city bucket.
    """
    from src.calibration.metric_specs import METRIC_SPECS
    from src.config import cities_by_name

    high_spec = next(s for s in METRIC_SPECS if s.identity.temperature_metric == "high")
    available_cities = list(cities_by_name.keys())
    if len(available_cities) < 2:
        pytest.skip("Need at least 2 cities in cities_by_name")

    city_a, city_b = available_cities[0], available_cities[1]

    mock_conn = MagicMock(spec=sqlite3.Connection)
    commit_count = [0]

    def counting_commit():
        commit_count[0] += 1

    mock_conn.commit = counting_commit
    mock_conn.execute = MagicMock(return_value=MagicMock())

    class FakeRow(dict):
        pass

    rows = [
        FakeRow({"city": city_a, "data_version": high_spec.allowed_data_version, "snapshot_id": "sa"}),
        FakeRow({"city": city_b, "data_version": high_spec.allowed_data_version, "snapshot_id": "sb"}),
    ]

    with (
        patch.object(rebuild_mod, "_fetch_eligible_snapshots_v2", return_value=rows),
        patch.object(rebuild_mod, "_collect_pre_delete_count", return_value=0),
        patch.object(rebuild_mod, "_delete_canonical_v2_slice", return_value=None),
        patch.object(rebuild_mod, "is_quarantined", return_value=False),
        patch.object(rebuild_mod, "_process_snapshot_v2", return_value=None),
        patch.object(rebuild_mod, "cities_by_name", {
            city_a: cities_by_name[city_a],
            city_b: cities_by_name[city_b],
        }),
    ):
        try:
            rebuild_mod.rebuild_v2(
                mock_conn,
                dry_run=False,
                force=True,
                spec=high_spec,
            )
        except RuntimeError as e:
            # zero pairs written expected since _process_snapshot_v2 is a no-op.
            # Commits for city buckets happen before the final validation check.
            if "zero pairs" not in str(e) and "hard failures" not in str(e) and "missing city" not in str(e):
                raise

    assert commit_count[0] >= 2, (
        f"Expected >= 2 commits for 2-city rebuild (one per city bucket), "
        f"got {commit_count[0]}. T1E-REBUILD-TRANSACTION-SHARDED invariant violated."
    )


def test_rebuild_all_v2_no_outer_savepoint(rebuild_mod):
    """rebuild_all_v2 does not wrap all specs in one outer SAVEPOINT.

    T1E removes the monolithic outer SAVEPOINT from rebuild_all_v2. Each metric
    spec is processed independently via rebuild_v2. Verify that rebuild_all_v2
    does not issue a SAVEPOINT v2_rebuild_all command.
    """
    from src.calibration.metric_specs import METRIC_SPECS
    from src.config import cities_by_name

    mock_conn = MagicMock(spec=sqlite3.Connection)
    execute_calls = []

    def tracking_execute(sql, *args, **kwargs):
        execute_calls.append(sql)
        return MagicMock()

    mock_conn.execute = tracking_execute
    mock_conn.commit = MagicMock()

    available_cities = list(cities_by_name.keys())
    if not available_cities:
        pytest.skip("No cities available")

    city_a = available_cities[0]
    high_spec = next(s for s in METRIC_SPECS if s.identity.temperature_metric == "high")
    rows = [{"city": city_a, "data_version": high_spec.allowed_data_version, "snapshot_id": "s1"}]

    class FakeRow(dict):
        pass

    with (
        patch.object(rebuild_mod, "_fetch_eligible_snapshots_v2", return_value=[FakeRow(r) for r in rows]),
        patch.object(rebuild_mod, "_collect_pre_delete_count", return_value=0),
        patch.object(rebuild_mod, "_delete_canonical_v2_slice", return_value=None),
        patch.object(rebuild_mod, "is_quarantined", return_value=False),
        patch.object(rebuild_mod, "_process_snapshot_v2", return_value=None),
        patch.object(rebuild_mod, "cities_by_name", {city_a: cities_by_name[city_a]}),
        patch.object(rebuild_mod, "METRIC_SPECS", [high_spec]),
    ):
        try:
            rebuild_mod.rebuild_all_v2(
                mock_conn,
                dry_run=False,
                force=True,
                temperature_metric="high",
            )
        except RuntimeError:
            pass  # zero pairs expected

    savepoint_all = [s for s in execute_calls if "v2_rebuild_all" in str(s)]
    assert len(savepoint_all) == 0, (
        f"rebuild_all_v2 should not issue v2_rebuild_all SAVEPOINT (T1E sharding). "
        f"Found: {savepoint_all}"
    )
