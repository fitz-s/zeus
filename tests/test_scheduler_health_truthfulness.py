# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant D
#   (daemon health is truthful: a job whose result reports a structural
#   failure status must produce status=FAILED in scheduler_jobs_health.json,
#   not status=OK).
"""Antibody for Invariant D — truthful per-job health.

The legacy ``_scheduler_job`` decorator in ``src/ingest_main.py`` wrote
``status=OK`` whenever the wrapped function did not raise — even if the
function returned a dict like ``{"status": "download_failed"}`` indicating a
silent zero-row run. This antibody locks down ``_classify_result`` so:

  - None / non-dict results → not failed (most ticks).
  - dict with ``status`` in ``_TRUTHFUL_FAIL_STATUSES`` → failed.
  - dict with ``status`` in ``{paused_by_control_plane, noop_no_dates}`` →
    not failed (legitimate noops).
  - dict whose ``stages`` list contains an ``ok=False`` entry → failed.
"""
from __future__ import annotations

from src.ingest_main import _classify_result, _TRUTHFUL_FAIL_STATUSES


def test_none_result_is_not_failed():
    failed, reason = _classify_result(None)
    assert failed is False
    assert reason is None


def test_ok_status_is_not_failed():
    failed, reason = _classify_result({"status": "ok", "snapshots_inserted": 10})
    assert failed is False
    assert reason is None


def test_download_failed_status_is_failed():
    failed, reason = _classify_result({
        "status": "download_failed",
        "track": "mx2t6_high",
        "error": "MARS auth missing",
    })
    assert failed is True
    assert "download_failed" in reason
    assert "MARS auth" in reason


def test_extract_failed_status_is_failed():
    failed, reason = _classify_result({"status": "extract_failed"})
    assert failed is True
    assert "extract_failed" in reason


def test_paused_mars_credentials_is_failed():
    failed, _reason = _classify_result({"status": "paused_mars_credentials"})
    assert failed is True


def test_paused_by_control_plane_is_not_failed():
    """Operator-paused source is a legitimate noop — not a daemon health failure."""
    failed, _reason = _classify_result({"status": "paused_by_control_plane"})
    assert failed is False


def test_noop_no_dates_is_not_failed():
    failed, _reason = _classify_result({"status": "noop_no_dates", "dates": []})
    assert failed is False


def test_failed_stage_marks_job_failed():
    """A successful top-level status with a failed stage is still a failure."""
    failed, reason = _classify_result({
        "status": "ok",
        "stages": [
            {"label": "download_mx2t6", "ok": True},
            {"label": "extract_mx2t6", "ok": False, "error": "GRIB parse error"},
        ],
    })
    assert failed is True
    assert "stage_failed" in reason
    assert "extract_mx2t6" in reason


def test_truthful_fail_statuses_set_locked_down():
    """The exact set of structural-failure statuses is intentional. Pinning
    it here means a future agent who reduces the set must update this test
    consciously rather than silently reintroduce the legacy bug."""
    assert "download_failed" in _TRUTHFUL_FAIL_STATUSES
    assert "extract_failed" in _TRUTHFUL_FAIL_STATUSES
    assert "paused_mars_credentials" in _TRUTHFUL_FAIL_STATUSES
    assert "bad_target_date" in _TRUTHFUL_FAIL_STATUSES
    # paused_by_control_plane explicitly NOT in the failed set.
    assert "paused_by_control_plane" not in _TRUTHFUL_FAIL_STATUSES
    assert "ok" not in _TRUTHFUL_FAIL_STATUSES
