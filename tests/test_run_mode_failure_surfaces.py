# Created: 2026-05-19
# Last reused or audited: 2026-06-19
# Authority basis: codereview-may19-2.md relationship F
#                  + docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P1-1
# Lifecycle: created=2026-05-19; last_reviewed=2026-06-17; last_reused=2026-06-17
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

from src.control import live_health
from src.control.live_health import compute_composite_live_health, STATUS_FRESH_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def _healthy_execution_capability() -> dict:
    return {
        "entry": {
            "status": "allowed",
            "global_allow_submit": True,
            "components": [
                {"component": "heartbeat_supervisor", "allowed": True, "reason": "allowed"},
                {"component": "risk_allocator_global", "allowed": True, "reason": "ok"},
            ],
            "blocked_components": [],
        },
        "exit": {
            "status": "allowed",
            "global_allow_submit": True,
            "components": [
                {"component": "heartbeat_supervisor", "allowed": True, "reason": "allowed"},
                {"component": "risk_allocator_global", "allowed": True, "reason": "ok"},
            ],
            "blocked_components": [],
        },
    }


def _setup_healthy_state(sd: Path, offset_seconds: int = -30) -> None:
    """Write all composite surfaces in a healthy / fresh state."""
    cycle_time = _now_iso(offset_seconds)
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": cycle_time, "mode": "live"},
    )
    _write(
        sd / "venue-heartbeat-keeper.json",
        {
            "health": "HEALTHY",
            "resting_order_safe": True,
            "written_at": _now_iso(-5),
            "cadence_seconds": 5,
        },
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
                "candidates": 1,
                "entry_orders_submitted": 0,
                "trades": 0,
                "exits": 0,
                "no_trades": 1,
                "top_no_trade_reasons": {"EDGE_INSUFFICIENT": 1},
                "command_recovery": {"scanned": 0, "advanced": 0},
                "chain_sync": {"synced": 0},
            },
            "execution_capability": _healthy_execution_capability(),
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


def test_mode_specific_run_mode_failed_yields_degraded_in_legacy_cron(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_mode catches exceptions, so mode-specific failure is the authority."""
    monkeypatch.setattr(live_health, "_live_execution_mode", lambda: "legacy_cron")
    sd = tmp_path / "state"
    sd.mkdir()
    cycle_time = _now_iso(-30)
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": cycle_time, "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {
            "run_mode": {"status": "OK", "last_run_at": cycle_time, "last_success_at": cycle_time},
            "run_mode:opening_hunt": {
                "status": "FAILED",
                "last_run_at": cycle_time,
                "last_failure_reason": "exchange reconcile stuck",
            },
        },
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": cycle_time,
                "candidates": 0,
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["run_mode"]["ok"] is False
    assert result["surfaces"]["run_mode"]["issue"] == (
        "RUN_MODE_FAILED[run_mode:opening_hunt]: exchange reconcile stuck"
    )


def test_legacy_run_mode_failure_ignored_in_edli_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """edli_live does not register legacy cron run_mode jobs; stale rows are not live evidence."""
    monkeypatch.setattr(live_health, "_live_execution_mode", lambda: "edli_live")
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    scheduler = json.loads((sd / "scheduler_jobs_health.json").read_text())
    scheduler["run_mode:day0_capture"] = {
        "status": "FAILED",
        "last_run_at": "2026-06-14T00:47:10+00:00",
        "last_failure_reason": "legacy cron stale row",
    }
    _write(sd / "scheduler_jobs_health.json", scheduler)

    result = compute_composite_live_health(state_dir=sd)

    assert result["surfaces"]["run_mode"]["ok"] is True
    assert "run_mode" not in result["failing_surfaces"]


def test_bpf_capture_failed_yields_forecast_pipeline_degraded(tmp_path: Path) -> None:
    sd = tmp_path
    _setup_healthy_state(sd)
    health_path = sd / "scheduler_jobs_health.json"
    scheduler = json.loads(health_path.read_text())
    scheduler["bayes_precision_fusion_capture"] = {
        "status": "FAILED",
        "last_failure_reason": "global models unavailable",
        "last_run_at": _now_iso(-5),
    }
    _write(health_path, scheduler)

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False
    assert result["status"] == "DEGRADED"
    assert "forecast_pipeline" in result["failing_surfaces"]
    assert result["surfaces"]["forecast_pipeline"]["ok"] is False
    assert "bayes_precision_fusion_capture" in (
        result["surfaces"]["forecast_pipeline"]["issue"] or ""
    )


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
    for surface in (
        "heartbeat",
        "venue_heartbeat",
        "run_mode",
        "status_summary",
        "execution_capability",
    ):
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


def test_business_plane_zero_candidates_without_proof_yields_degraded(tmp_path: Path) -> None:
    """Zero candidates needs explicit no-market/source-freshness proof."""
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
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "ZERO_CANDIDATES_WITHOUT_SOURCE_OR_NO_MARKET_PROOF"
    )


def test_business_plane_candidates_without_final_intent_need_no_trade_reasons(tmp_path: Path) -> None:
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
                "candidates": 3,
                "final_intents_built": 0,
                "no_trades": 3,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS"
    )


def test_business_plane_candidates_blocked_by_entry_gate_have_explicit_proof(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    capability = _healthy_execution_capability()
    capability["entry"] = {
        "status": "blocked",
        "global_allow_submit": False,
        "components": [
            {
                "component": "risk_allocator_global",
                "allowed": False,
                "reason": "reduce_only_mode_active",
            }
        ],
        "blocked_components": ["risk_allocator_global"],
    }
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "control": {
                "entries_paused": True,
                "entries_pause_reason": "operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix",
            },
            "cycle": {
                "mode": "edli_event_reactor",
                "completed_at": _now_iso(-30),
                "candidates": 310,
                "final_intents_built": 0,
                "no_trades": 310,
                "top_no_trade_reasons": {},
            },
            "execution_capability": capability,
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    business = result["surfaces"]["business_plane"]
    assert business["ok"] is True
    assert business["progress"]["entry_blocked_proof"] is True
    assert business["progress"]["entry_blocked_reason"] == (
        "operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix"
    )
    assert result["surfaces"]["execution_capability"]["ok"] is False


def test_business_plane_final_intents_without_submit_attempts_yields_degraded(tmp_path: Path) -> None:
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
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "FINAL_INTENTS_WITHOUT_SUBMIT_ATTEMPTS"
    )


def test_business_plane_submit_without_ack_or_rejection_yields_degraded(tmp_path: Path) -> None:
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
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "SUBMIT_ATTEMPTS_WITHOUT_ACK_OR_DETERMINISTIC_REJECTION"
    )


def test_business_plane_submit_without_ack_allows_deterministic_rejection(tmp_path: Path) -> None:
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
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 0,
                "deterministic_rejections": {"invalid_amount_precision": 1},
            },
            "execution_capability": _healthy_execution_capability(),
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "HEALTHY"
    assert result["surfaces"]["business_plane"]["progress"]["deterministic_rejection_observed"] is True


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
            "execution_capability": _healthy_execution_capability(),
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


def test_execution_capability_blocked_yields_degraded(tmp_path: Path) -> None:
    """Fresh daemon/cycle signals cannot override the live order gate."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    cycle_time = _now_iso(-30)
    capability = _healthy_execution_capability()
    capability["entry"] = {
        "status": "blocked",
        "global_allow_submit": False,
        "components": [
            {
                "component": "heartbeat_supervisor",
                "allowed": False,
                "reason": "PolyApiException[status_code=None, error_message=Request exception!]",
            },
            {
                "component": "risk_allocator_global",
                "allowed": False,
                "reason": "heartbeat_lost",
            },
        ],
        "blocked_components": ["heartbeat_supervisor", "risk_allocator_global"],
    }
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": cycle_time,
                "candidates": 4,
                "final_intents_built": 0,
                "no_trades": 4,
                "top_no_trade_reasons": {"EDGE_INSUFFICIENT": 4},
            },
            "execution_capability": capability,
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "execution_capability" in result["failing_surfaces"]
    assert result["surfaces"]["business_plane"]["ok"] is True
    assert result["surfaces"]["execution_capability"]["ok"] is False
    assert "entry:heartbeat_supervisor,risk_allocator_global" in (
        result["surfaces"]["execution_capability"]["issue"] or ""
    )


def test_venue_heartbeat_lost_yields_degraded_even_when_daemon_heartbeat_is_fresh(
    tmp_path: Path,
) -> None:
    """Daemon liveness is not resting-order safety authority."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "venue-heartbeat-keeper.json",
        {
            "health": "LOST",
            "resting_order_safe": False,
            "written_at": _now_iso(-2),
            "cadence_seconds": 5,
            "last_error": "PolyApiException[status_code=None, error_message=Request exception!]",
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["heartbeat"]["ok"] is True
    assert "venue_heartbeat" in result["failing_surfaces"]
    assert result["surfaces"]["venue_heartbeat"]["issue"] == "VENUE_HEARTBEAT_LOST"


def test_command_recovery_mutation_summary_requires_allocator_refresh() -> None:
    """Command recovery mutations must refresh live submit gating in-process."""
    from src.main import _command_recovery_summary_mutated_allocator_inputs

    assert not _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 0, "partial_remainders": {"advanced": 0}}
    )
    assert _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 1, "partial_remainders": {"advanced": 0}}
    )
    assert _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 0, "recorded_maker_fill_economics": {"projected": 17}}
    )


def test_edli_command_recovery_cycle_refreshes_allocator_after_mutation(monkeypatch) -> None:
    """The scheduled recovery job must refresh allocator state after DB mutations."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConn()
    health_calls: list[tuple[str, bool, str | None]] = []
    refresh_calls: list[FakeConn] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: health_calls.append(
            ("reconcile_scope", False, str(kwargs.get("scope")))
        ) or {"scanned": 1, "advanced": 1},
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: fake_conn,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: refresh_calls.append(conn) or {"configured": True},
    )
    monkeypatch.setattr(
        main_module,
        "_write_scheduler_health",
        lambda job_name, failed=False, reason=None, **kwargs: health_calls.append(
            (job_name, failed, reason)
        ),
    )

    main_module._edli_command_recovery_cycle()

    assert refresh_calls == [fake_conn]
    assert fake_conn.closed is True
    assert ("reconcile_scope", False, "live_tick") in health_calls
    assert ("edli_command_recovery", False, None) in health_calls


def test_edli_command_recovery_runs_live_tick_during_active_redecision(monkeypatch) -> None:
    """Confirmed fill projection is part of the live management lane and must
    not starve behind continuous redecision activity."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module

    calls: list[str] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(main_module, "_edli_reactor_active", lambda: False)
    monkeypatch.setattr(
        main_module,
        "_edli_redecision_screen_lock",
        type("Locked", (), {"locked": lambda self: True})(),
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: calls.append(str(kwargs.get("scope"))) or {"scanned": 1, "advanced": 0},
    )

    main_module._edli_command_recovery_cycle()

    assert calls == ["live_tick"]


def test_edli_boot_command_recovery_runs_before_scheduler_tick(monkeypatch) -> None:
    """Boot must clear restart-relevant EDLI order state before first reactor tick."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConn()
    calls: list[str] = []
    refresh_calls: list[FakeConn] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: calls.append(str(kwargs.get("scope"))) or {"advanced": 1},
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: fake_conn,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: refresh_calls.append(conn) or {"configured": True},
    )

    main_module._edli_boot_command_recovery_once()

    assert calls == ["live_tick"]
    assert refresh_calls == [fake_conn]
    assert fake_conn.closed is True


def test_edli_command_recovery_emits_terminal_no_fill_continuation(monkeypatch) -> None:
    """A no-fill terminal order recovery must continue the redecision chain."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    trade_refresh_conn = FakeConn()
    trade_ro = FakeConn()
    forecasts_ro = FakeConn()
    summary = {
        "scanned": 1,
        "advanced": 1,
        "terminal_no_fill_continuations": [
            {"condition_id": "cond-1", "token_id": "tok-1", "command_id": "cmd-1"}
        ],
    }
    families = {("Singapore", "2026-06-27", "high")}
    emitted_calls: list[tuple[set[tuple[str, str, str]], str]] = []
    clear_calls: list[set[tuple[str, str, str]]] = []

    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: {"enabled": True, "event_writer_enabled": True},
    )
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: summary,
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: trade_refresh_conn,
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_read_only",
        lambda: trade_ro,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: forecasts_ro,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: {"configured": True},
    )
    monkeypatch.setattr(
        main_module,
        "_terminal_no_fill_continuation_families",
        lambda observed_summary, trade_conn, forecasts_conn: families,
    )
    monkeypatch.setattr(
        main_module,
        "_clear_redecision_acted_state_for_families",
        lambda observed_families: clear_calls.append(set(observed_families)) or 2,
    )
    monkeypatch.setattr(
        main_module,
        "_emit_terminal_no_fill_redecision_continuations",
        lambda observed_families, decision_time, received_at: (
            emitted_calls.append((set(observed_families), str(received_at))) or 1
        ),
    )

    main_module._edli_command_recovery_cycle()

    assert trade_refresh_conn.closed is True
    assert trade_ro.closed is True
    assert forecasts_ro.closed is True
    assert clear_calls == [families]
    assert emitted_calls and emitted_calls[0][0] == families
