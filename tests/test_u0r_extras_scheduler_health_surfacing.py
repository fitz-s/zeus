# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: continuity audit 2026-06-09 — silent-death lane fix;
#   defect: U0R extras sub-task failure was invisible at scheduler_jobs_health.json
#   (parent job showed OK while model degradation silently accumulated).
"""Antibody: U0R extras failure surfaces to scheduler_jobs_health.json.

Before this fix, _download_u0r_extra_raw_inputs_if_needed() returning
{"status": "U0R_EXTRA_CAPTURE_FAILSOFT_SKIPPED"} was logged at WARNING level
only — the parent replacement_forecast_download job continued showing status=OK
in scheduler_jobs_health.json.  An operator would not detect sustained OpenMeteo
outages until fusion degraded visibly in production.

Fix: _replacement_forecast_download_cycle() now calls _write_scheduler_health(
"u0r_multimodel_capture", failed=True) when the extras report is FAILSOFT_SKIPPED
or reports global_models_unavailable — and failed=False on a clean run.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers to import the production module under test
# ---------------------------------------------------------------------------

def _get_download_cycle_fn():
    """Return the UNWRAPPED _replacement_forecast_download_cycle function."""
    import importlib
    mod = importlib.import_module("src.data.replacement_forecast_production")
    fn = mod._replacement_forecast_download_cycle
    # The function is wrapped by @_scheduler_job — unwrap to test logic directly.
    return getattr(fn, "__wrapped__", fn)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_FLAGS = {"openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled": True}
_BASE_CFG = {
    "forecast_db": None,
    "seed_dir": None,
    "seed_processed_dir": None,
    "seed_failed_dir": None,
    "seed_discovery_limit": 1,
    "seed_limit": 1,
    "limit": 1,
    "request_dir": None,
    "processed_dir": None,
    "failed_dir": None,
}


def _patch_env(extras_report: dict | None, download_report=None):
    """Context manager that patches runtime flags, cfg, and sub-calls."""
    return patch.multiple(
        "src.data.replacement_forecast_production",
        _replacement_forecast_runtime_flags_from_settings=MagicMock(return_value=_BASE_FLAGS),
        _replacement_forecast_shadow_materialization_queue_config=MagicMock(return_value=_BASE_CFG),
        _download_replacement_forecast_current_targets_if_needed=MagicMock(return_value=download_report),
        _download_u0r_extra_raw_inputs_if_needed=MagicMock(return_value=extras_report),
    )


# ---------------------------------------------------------------------------
# Test: FAILSOFT_SKIPPED → health entry written with failed=True
# ---------------------------------------------------------------------------


def test_extras_failsoft_skipped_writes_health_failed():
    """U0R extras FAILSOFT_SKIPPED → _write_scheduler_health("u0r_multimodel_capture", failed=True)."""
    failsoft_report = {"status": "U0R_EXTRA_CAPTURE_FAILSOFT_SKIPPED", "error": "connection timeout"}

    health_calls = []

    def _fake_write_health(job_name, *, failed, reason=None, **_kw):
        health_calls.append({"job_name": job_name, "failed": failed, "reason": reason})

    with _patch_env(failsoft_report):
        with patch(
            "src.observability.scheduler_health._write_scheduler_health",
            side_effect=_fake_write_health,
        ):
            fn = _get_download_cycle_fn()
            fn()

    u0r_calls = [c for c in health_calls if c["job_name"] == "u0r_multimodel_capture"]
    assert u0r_calls, "Expected _write_scheduler_health('u0r_multimodel_capture', ...) to be called"
    assert u0r_calls[-1]["failed"] is True, (
        f"Expected failed=True for FAILSOFT_SKIPPED; got {u0r_calls[-1]}"
    )
    assert "timeout" in (u0r_calls[-1]["reason"] or ""), (
        f"Expected reason to include original error; got {u0r_calls[-1]['reason']!r}"
    )


# ---------------------------------------------------------------------------
# Test: global_models_unavailable → health entry written with failed=True
# ---------------------------------------------------------------------------


def test_extras_global_models_unavailable_writes_health_failed():
    """U0R extras global_models_unavailable → health failed=True."""
    unavailable_report = {
        "status": "OK",
        "global_models_unavailable": ["gfs_global", "icon_global"],
    }

    health_calls = []

    def _fake_write_health(job_name, *, failed, reason=None, **_kw):
        health_calls.append({"job_name": job_name, "failed": failed})

    with _patch_env(unavailable_report):
        with patch(
            "src.observability.scheduler_health._write_scheduler_health",
            side_effect=_fake_write_health,
        ):
            fn = _get_download_cycle_fn()
            fn()

    u0r_calls = [c for c in health_calls if c["job_name"] == "u0r_multimodel_capture"]
    assert u0r_calls, "Expected u0r_multimodel_capture health entry"
    assert u0r_calls[-1]["failed"] is True


# ---------------------------------------------------------------------------
# Test: clean success report → health entry written with failed=False
# ---------------------------------------------------------------------------


def test_extras_clean_success_writes_health_ok():
    """U0R extras clean success → health failed=False for u0r_multimodel_capture."""
    success_report = {
        "status": "U0R_EXTRA_CAPTURED",
        "captured_count": 40,
        "global_models_unavailable": [],
    }

    health_calls = []

    def _fake_write_health(job_name, *, failed, reason=None, **_kw):
        health_calls.append({"job_name": job_name, "failed": failed})

    with _patch_env(success_report):
        with patch(
            "src.observability.scheduler_health._write_scheduler_health",
            side_effect=_fake_write_health,
        ):
            fn = _get_download_cycle_fn()
            fn()

    u0r_calls = [c for c in health_calls if c["job_name"] == "u0r_multimodel_capture"]
    assert u0r_calls, "Expected u0r_multimodel_capture health entry on success"
    assert u0r_calls[-1]["failed"] is False


# ---------------------------------------------------------------------------
# Test: NO_TARGETS → no health entry written (not a failure, no data)
# ---------------------------------------------------------------------------


def test_extras_no_targets_does_not_write_health():
    """U0R extras U0R_EXTRA_NO_TARGETS → no health entry written (not a failure)."""
    no_targets_report = {"status": "U0R_EXTRA_NO_TARGETS"}

    health_calls = []

    def _fake_write_health(job_name, *, failed, reason=None, **_kw):
        health_calls.append({"job_name": job_name, "failed": failed})

    with _patch_env(no_targets_report):
        with patch(
            "src.observability.scheduler_health._write_scheduler_health",
            side_effect=_fake_write_health,
        ):
            fn = _get_download_cycle_fn()
            fn()

    u0r_calls = [c for c in health_calls if c["job_name"] == "u0r_multimodel_capture"]
    assert not u0r_calls, (
        "U0R_EXTRA_NO_TARGETS must NOT write a health entry (normal no-op when plan is empty)"
    )


# ---------------------------------------------------------------------------
# Test: extras returns None (flag off) → no health entry written
# ---------------------------------------------------------------------------


def test_extras_flag_off_no_health_entry():
    """Flag off (extras returns None) → no u0r_multimodel_capture health entry."""
    health_calls = []

    def _fake_write_health(job_name, *, failed, reason=None, **_kw):
        health_calls.append({"job_name": job_name, "failed": failed})

    with _patch_env(None):  # None = flag off
        with patch(
            "src.observability.scheduler_health._write_scheduler_health",
            side_effect=_fake_write_health,
        ):
            fn = _get_download_cycle_fn()
            fn()

    u0r_calls = [c for c in health_calls if c["job_name"] == "u0r_multimodel_capture"]
    assert not u0r_calls, "Flag-off (None return) must NOT write u0r_multimodel_capture health entry"
