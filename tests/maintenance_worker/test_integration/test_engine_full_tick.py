# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Tick Lifecycle"
"""
Integration tests — MaintenanceEngine full tick (end-to-end state machine).

Exercises run_tick(config) through all 7 phases and asserts on TickResult
fields. The engine stubs in P5.1–P5.3 mean _enumerate_candidates returns [],
_emit_dry_run_proposal returns empty ProposalManifest, and _emit_summary only
logs (does NOT write SUMMARY.md). Assertions are scoped accordingly.

Key invariants tested:
  (a) state_machine_breadcrumbs records all 7 phases in order on happy path
  (b) run_id is a non-empty UUID4 string
  (c) TickResult.skipped=False on a clean guard-passing tick
  (d) TickResult.apply_results is empty (stub _enumerate_candidates returns [])
  (e) CONFIG_INVALID (missing required path) → refuse_fatal exits non-zero
  (f) run_tick is importable as a module-level function
  (g) Multiple consecutive ticks produce distinct run_ids
  (h) Force dry-run via MANUAL_CLI invocation mode produces dry_run_only results
  (i) TickResult.guard_report is not None after CHECK_GUARDS
  (j) started_at is a timezone-aware datetime
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.core.engine import MaintenanceEngine, TickResult, run_tick
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> EngineConfig:
    """Minimal valid EngineConfig for test use. All required paths exist."""
    repo_root = tmp_path / "repo"
    state_dir = tmp_path / "state"
    evidence_dir = tmp_path / "evidence"
    task_catalog = tmp_path / "TASK_CATALOG.yaml"
    safety_contract = tmp_path / "SAFETY_CONTRACT.md"

    for d in (repo_root, state_dir, evidence_dir):
        d.mkdir(parents=True)
    task_catalog.write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")
    safety_contract.write_text("# Safety Contract\n", encoding="utf-8")

    return EngineConfig(
        repo_root=repo_root,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=task_catalog,
        safety_contract_path=safety_contract,
        live_default=False,
        scheduler="cron",
        notification_channel="none",
    )


# ---------------------------------------------------------------------------
# (a) Happy-path breadcrumbs — all 7 phases recorded in order
# ---------------------------------------------------------------------------


def test_full_tick_breadcrumbs_happy_path(tmp_path: Path) -> None:
    """
    On a clean tick (all guards pass, CRON invocation), the state machine
    records all 7 breadcrumb phases in order with ok=True.
    """
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    phase_names = [name for name, _ in result.state_machine_breadcrumbs]
    expected_phases = [
        "START",
        "LOAD_CONFIG",
        "CHECK_GUARDS",
        "ENUMERATE_CANDIDATES",
        "DRY_RUN_PROPOSAL",
        "APPLY_DECISIONS",
        "SUMMARY_REPORT",
        "END",
    ]
    assert phase_names == expected_phases, (
        f"Expected phases {expected_phases!r}, got {phase_names!r}"
    )
    for name, ok in result.state_machine_breadcrumbs:
        assert ok is True, f"Phase '{name}' recorded ok=False on happy path"


# ---------------------------------------------------------------------------
# (b) run_id is a non-empty UUID4 string
# ---------------------------------------------------------------------------


def test_full_tick_run_id_is_uuid4(tmp_path: Path) -> None:
    """run_id on TickResult must be parseable as a UUID4."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    assert result.run_id, "run_id must not be empty"
    parsed = uuid.UUID(result.run_id)
    assert parsed.version == 4, f"run_id must be UUID4; got version {parsed.version}"


# ---------------------------------------------------------------------------
# (c) skipped=False on clean tick
# ---------------------------------------------------------------------------


def test_full_tick_not_skipped_on_clean_run(tmp_path: Path) -> None:
    """A successful tick must not set skipped=True."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    assert result.skipped is False
    assert result.skip_reason == ""


# ---------------------------------------------------------------------------
# (d) apply_results empty (P5.1 stub _enumerate_candidates returns [])
# ---------------------------------------------------------------------------


def test_full_tick_apply_results_empty_stub(tmp_path: Path) -> None:
    """With P5.1 stubs, _enumerate_candidates returns [] → apply_results is []."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    assert result.apply_results == [], (
        f"Expected empty apply_results from P5.1 stub; got {result.apply_results!r}"
    )


# ---------------------------------------------------------------------------
# (e) CONFIG_INVALID → refuse_fatal exits non-zero
# ---------------------------------------------------------------------------


def test_full_tick_invalid_config_exits_nonzero(tmp_path: Path) -> None:
    """
    An EngineConfig with a non-Path repo_root fails _validate_config →
    refuse_fatal calls sys.exit with a non-zero code.
    """
    # Build a valid config then corrupt repo_root to an empty string path
    config = _make_config(tmp_path)
    # Construct a bad config by replacing repo_root with a path string object
    # that is an empty string (fails isinstance check in _validate_config)
    bad_config = EngineConfig(
        repo_root=Path(""),  # empty string path — _validate_config returns False
        state_dir=config.state_dir,
        evidence_dir=config.evidence_dir,
        task_catalog_path=config.task_catalog_path,
        safety_contract_path=config.safety_contract_path,
        live_default=False,
        scheduler="cron",
        notification_channel="none",
    )
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         pytest.raises(SystemExit) as exc_info:
        engine.run_tick(bad_config)

    assert exc_info.value.code != 0, "CONFIG_INVALID must exit non-zero"


# ---------------------------------------------------------------------------
# (f) Module-level run_tick importable and functional
# ---------------------------------------------------------------------------


def test_module_level_run_tick_importable(tmp_path: Path) -> None:
    """run_tick as module-level function must behave identically to engine.run_tick."""
    config = _make_config(tmp_path)

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = run_tick(config)

    assert isinstance(result, TickResult)
    assert result.run_id


# ---------------------------------------------------------------------------
# (g) Consecutive ticks produce distinct run_ids
# ---------------------------------------------------------------------------


def test_consecutive_ticks_distinct_run_ids(tmp_path: Path) -> None:
    """Two consecutive run_tick calls must produce different run_ids."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    def _run():
        with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
             patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
            from maintenance_worker.core.guards import GuardReport, CheckResult
            mock_eval.return_value = GuardReport(
                results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
            )
            return engine.run_tick(config)

    r1 = _run()
    r2 = _run()
    assert r1.run_id != r2.run_id, "Consecutive ticks must produce distinct run_ids"


# ---------------------------------------------------------------------------
# (h) MANUAL_CLI invocation → all apply results are dry_run_only
# ---------------------------------------------------------------------------


def test_full_tick_manual_cli_forces_dry_run(tmp_path: Path) -> None:
    """
    MANUAL_CLI invocation mode forces force_dry_run=True in _apply_decisions.
    With stub candidates=[], apply_results is empty — but the phase still
    records ok=True in breadcrumbs.
    """
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="MANUAL_CLI"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    # Stub returns [] candidates → apply_results is empty; phase still records ok
    apply_phase = next(
        (ok for name, ok in result.state_machine_breadcrumbs if name == "APPLY_DECISIONS"),
        None,
    )
    assert apply_phase is True, "APPLY_DECISIONS must record ok=True even with MANUAL_CLI"
    # All apply results (empty in stub) must have dry_run_only=True
    for r in result.apply_results:
        assert r.dry_run_only is True, f"Task {r.task_id!r} apply_result not dry_run_only"


# ---------------------------------------------------------------------------
# (i) guard_report is populated after CHECK_GUARDS
# ---------------------------------------------------------------------------


def test_full_tick_guard_report_populated(tmp_path: Path) -> None:
    """TickResult.guard_report must be set (not None) after run_tick."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    assert result.guard_report is not None, "guard_report must be set after CHECK_GUARDS"


# ---------------------------------------------------------------------------
# (j) started_at is timezone-aware
# ---------------------------------------------------------------------------


def test_full_tick_started_at_timezone_aware(tmp_path: Path) -> None:
    """TickResult.started_at must be a UTC-aware datetime."""
    config = _make_config(tmp_path)
    engine = MaintenanceEngine()

    with patch("maintenance_worker.core.engine.check_scheduler_invocation", return_value="CRON"), \
         patch("maintenance_worker.core.engine.evaluate_all") as mock_eval:
        from maintenance_worker.core.guards import GuardReport, CheckResult
        mock_eval.return_value = GuardReport(
            results=[("all_guards", CheckResult(ok=True, reason="", details={}))],
        )
        result = engine.run_tick(config)

    assert isinstance(result.started_at, datetime), "started_at must be a datetime"
    assert result.started_at.tzinfo is not None, "started_at must be timezone-aware"
