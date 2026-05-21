# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19-2.md relationship F
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Relationship-F antibody — assert that compute_composite_live_health()
#   surfaces DEGRADED when run_mode has failed or status_summary is stale, even
#   when the heartbeat is OK (closing the "scheduler alive but not trading" gap).
# Reuse: Run on every PR touching src/control/live_health.py, src/main.py
#   _write_heartbeat, or scheduler_jobs_health.

"""Relationship-F composite live-health antibody.

Background (codereview-may19-2.md relationship F):
> The scheduler can appear alive (heartbeat OK, process running) while
> run_mode is not successfully trading.  @_scheduler_job catches exceptions
> without re-raising (K2 fail-open design), and _run_mode() catches and writes
> a failed status_summary.  An operator watching only process PID or heartbeat
> sees "alive" while the system has degraded.

Invariant:
  live health = heartbeat OK AND latest run_mode OK
                AND status_summary fresh AND no entry blocker active

Probes:
  T1: heartbeat OK + run_mode FAILED → composite DEGRADED
  T2: all OK + status_summary stale (>5 min) → composite DEGRADED
  T3: all OK + fresh → composite HEALTHY
  T4: DEGRADED composite emits WARNING log with failing surface name
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.control.live_health import compute_composite_live_health, STATUS_FRESH_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def _setup_healthy_state(sd: Path, offset_seconds: int = -30) -> None:
    """Write all three surfaces in a healthy / fresh state."""
    cycle_time = _now_iso(offset_seconds)
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": cycle_time, "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {"_run_mode": {"status": "OK", "last_run_at": cycle_time, "last_success_at": cycle_time}},
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "started_at": cycle_time,
                "completed_at": cycle_time,
                "candidates": 0,
                "entry_orders_submitted": 0,
                "trades": 0,
                "exits": 0,
                "no_trades": 0,
                "command_recovery": {"scanned": 0, "advanced": 0},
                "chain_sync": {"synced": 0},
            },
        },
    )


# ---------------------------------------------------------------------------
# T1: heartbeat OK + run_mode FAILED → DEGRADED
# ---------------------------------------------------------------------------

def test_run_mode_failed_yields_degraded(tmp_path: Path) -> None:
    """T1: run_mode FAILED makes composite DEGRADED even with healthy heartbeat."""
    sd = tmp_path / "state"
    sd.mkdir()

    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-30), "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {
            "_run_mode": {
                "status": "FAILED",
                "last_run_at": _now_iso(-30),
                "last_failure_reason": "ValueError: no open markets",
            }
        },
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 0,
                "entry_orders_submitted": 0,
                "trades": 0,
                "exits": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False, f"run_mode FAILED must yield DEGRADED: {result}"
    assert result["status"] == "DEGRADED"
    assert "run_mode" in result["failing_surfaces"]
    assert result["surfaces"]["run_mode"]["ok"] is False
    assert "RUN_MODE_FAILED" in (result["surfaces"]["run_mode"]["issue"] or "")
    # heartbeat must still show OK
    assert result["surfaces"]["heartbeat"]["ok"] is True


# ---------------------------------------------------------------------------
# T2: status_summary stale → DEGRADED
# ---------------------------------------------------------------------------

def test_stale_status_summary_yields_degraded(tmp_path: Path) -> None:
    """T2: status_summary older than 5 min makes composite DEGRADED."""
    sd = tmp_path / "state"
    sd.mkdir()

    _setup_healthy_state(sd)
    # Overwrite status_summary with a stale timestamp (>5 min ago)
    stale_offset = -(STATUS_FRESH_BUDGET_SECONDS + 60)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(stale_offset),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(stale_offset),
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False, f"stale status_summary must yield DEGRADED: {result}"
    assert result["status"] == "DEGRADED"
    assert "status_summary" in result["failing_surfaces"]
    assert result["surfaces"]["status_summary"]["ok"] is False
    assert "STALE" in (result["surfaces"]["status_summary"]["issue"] or "")
    # heartbeat and run_mode should still show OK
    assert result["surfaces"]["heartbeat"]["ok"] is True
    assert result["surfaces"]["run_mode"]["ok"] is True


# ---------------------------------------------------------------------------
# T3: all healthy → HEALTHY
# ---------------------------------------------------------------------------

def test_all_healthy_surfaces_yield_healthy(tmp_path: Path) -> None:
    """T3: when all three surfaces are fresh and OK, composite is HEALTHY."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is True, f"all-OK surfaces must yield HEALTHY: {result}"
    assert result["status"] == "HEALTHY"
    assert result["failing_surfaces"] == []
    for surface in ("heartbeat", "run_mode", "status_summary"):
        assert result["surfaces"][surface]["ok"] is True, (
            f"surface {surface!r} should be OK: {result['surfaces'][surface]}"
        )


# ---------------------------------------------------------------------------
# T4: DEGRADED emits WARNING log with failing surface name
# ---------------------------------------------------------------------------

def test_degraded_emits_warning_log_with_surface_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T4: DEGRADED composite emits WARNING log naming the failing surface."""
    sd = tmp_path / "state"
    sd.mkdir()

    # Use a stale heartbeat to trigger DEGRADED
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-(STATUS_FRESH_BUDGET_SECONDS + 120)), "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {"_run_mode": {"status": "OK", "last_run_at": _now_iso(-30)}},
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 0,
            },
        },
    )

    with caplog.at_level(logging.WARNING, logger="src.control.live_health"):
        result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    # Must emit at least one WARNING mentioning "heartbeat"
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("heartbeat" in msg for msg in warning_texts), (
        f"No WARNING log mentioning 'heartbeat' found. Got: {warning_texts}"
    )
    # Must mention "DEGRADED" keyword
    assert any("DEGRADED" in msg for msg in warning_texts), (
        f"No WARNING log containing 'DEGRADED' found. Got: {warning_texts}"
    )


def test_business_plane_missing_candidate_counter_yields_degraded(tmp_path: Path) -> None:
    """F5: fresh process/status without cycle counters is not live progress proof."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "business_plane" in result["failing_surfaces"]
    assert result["surfaces"]["business_plane"]["ok"] is False
    assert result["surfaces"]["business_plane"]["issue"] == "CANDIDATE_COUNTER_MISSING"


def test_business_plane_skipped_cycle_yields_degraded(tmp_path: Path) -> None:
    """F6: scheduler OK plus skipped cycle is daemon liveness, not business progress."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "skipped": True,
                "skip_reason": "cycle_lock_held",
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == "CYCLE_SKIPPED: cycle_lock_held"


def test_business_plane_exposes_entry_and_reconcile_progress_counters(tmp_path: Path) -> None:
    """F7: composite output exposes candidate/intent/submit/ack/reconcile truth."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 4,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 1,
                "command_recovery": {"scanned": 3, "advanced": 1},
                "chain_sync": {"synced": 2},
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    progress = result["surfaces"]["business_plane"]["progress"]
    assert result["status"] == "HEALTHY"
    assert progress["candidate_evaluated"] is True
    assert progress["final_intent_built"] is True
    assert progress["submit_attempted"] is True
    assert progress["venue_ack_observed"] is True
    assert progress["reconcile_progress_observed"] is True


def test_business_plane_does_not_infer_venue_ack_from_submit_count(tmp_path: Path) -> None:
    """A submit attempt is not venue acknowledgement authority."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 4,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "command_recovery": {"scanned": 1},
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    progress = result["surfaces"]["business_plane"]["progress"]
    assert progress["submit_attempted"] is True
    assert progress["venue_acks"] == 0
    assert progress["venue_ack_observed"] is False
