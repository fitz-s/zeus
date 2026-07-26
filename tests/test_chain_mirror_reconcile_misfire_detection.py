# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: chain_mirror_reconcile's own docstring (a skipped 10-minute
#   trigger is not retried and can age chain_seen_at past its 30-minute
#   fail-closed bound) plus observed post-fix silent gaps of 36, 115, and 290
#   minutes with no error, defer, or restart logged (cause unknown, suspected
#   APScheduler misfire).
"""Silent-misfire detection for the chain_mirror_reconcile scheduler job.

This is DETECTION ONLY -- it does not fix or retry a missed trigger, it makes
the gap observable via a WARNING log line naming the gap duration and the
number of missed cadence intervals.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

import src.main as main_mod
from src.observability import scheduler_health as health_mod
from src.observability.scheduler_health import (
    _write_scheduler_health,
    read_scheduler_job_health,
)

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_health_path(tmp_path, monkeypatch):
    path = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(health_mod, "_SCHEDULER_HEALTH_PATH", path)
    return path


def test_read_scheduler_job_health_round_trips_written_entry():
    _write_scheduler_health("chain_mirror_reconcile", failed=False)

    entry = read_scheduler_job_health("chain_mirror_reconcile")

    assert entry.get("status") == "OK"
    assert entry.get("last_success_at")


def test_read_scheduler_job_health_returns_empty_for_missing_job():
    assert read_scheduler_job_health("chain_mirror_reconcile") == {}


def test_read_scheduler_job_health_returns_empty_for_corrupt_file():
    health_mod._SCHEDULER_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    health_mod._SCHEDULER_HEALTH_PATH.write_text("{not valid json")

    assert read_scheduler_job_health("chain_mirror_reconcile") == {}


def test_no_warning_on_normal_cadence(caplog):
    last_success = (NOW - timedelta(minutes=9)).isoformat()
    _seed_last_success(last_success)

    with caplog.at_level(logging.WARNING, logger="zeus"):
        main_mod._chain_mirror_reconcile_warn_on_silent_misfire_gap(NOW)

    assert not _misfire_warnings(caplog)


def test_no_warning_when_no_prior_success_recorded(caplog):
    with caplog.at_level(logging.WARNING, logger="zeus"):
        main_mod._chain_mirror_reconcile_warn_on_silent_misfire_gap(NOW)

    assert not _misfire_warnings(caplog)


@pytest.mark.parametrize(
    ("gap_minutes", "expected_missed_cycles"),
    (
        (36, 3),
        (115, 11),
        (290, 29),
    ),
)
def test_warning_fires_with_correct_gap_and_missed_cycle_count(
    caplog, gap_minutes, expected_missed_cycles
):
    last_success = NOW - timedelta(minutes=gap_minutes)
    _seed_last_success(last_success.isoformat())

    with caplog.at_level(logging.WARNING, logger="zeus"):
        main_mod._chain_mirror_reconcile_warn_on_silent_misfire_gap(NOW)

    warnings = _misfire_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0]
    assert f"{gap_minutes * 60:.0f}s" in message or f"{gap_minutes * 60}s" in message
    assert f"~{expected_missed_cycles} missed" in message


def test_no_warning_exactly_at_threshold_boundary(caplog):
    """3x cadence (30 min) is the threshold; exactly 30 min must not alarm --
    only a gap STRICTLY GREATER than the bound does."""
    last_success = NOW - timedelta(
        seconds=main_mod._CHAIN_MIRROR_RECONCILE_GAP_WARNING_SECONDS
    )
    _seed_last_success(last_success.isoformat())

    with caplog.at_level(logging.WARNING, logger="zeus"):
        main_mod._chain_mirror_reconcile_warn_on_silent_misfire_gap(NOW)

    assert not _misfire_warnings(caplog)


def _seed_last_success(iso_timestamp: str) -> None:
    path = health_mod._SCHEDULER_HEALTH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "chain_mirror_reconcile": {
                    "status": "OK",
                    "last_run_at": iso_timestamp,
                    "last_success_at": iso_timestamp,
                }
            }
        )
    )


def _misfire_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "chain_mirror_reconcile: silent scheduling gap" in record.getMessage()
    ]
