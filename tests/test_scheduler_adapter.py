# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Executor-class assignment (no DB writer on file-only executor; UMA->backfill_db).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6);
#   operator spec §7 (Scheduler adapter / executor classes).
"""PR6: registry -> scheduler executor-class assignment (pure planner, daemon wiring deferred)."""
from __future__ import annotations


def test_registry_scheduler_disabled_by_default() -> None:
    """The registry-built scheduler must be OFF unless the operator opts in — so PR6 changes
    no runtime behavior on its own."""
    from src.data.scheduler_adapter import scheduler_registry_enabled

    assert scheduler_registry_enabled() is False


def test_no_db_writer_on_file_only_executor() -> None:
    """STRUCTURAL ANTIBODY: every writes_db job is assigned a *_db executor class, never
    io/heartbeat. This is the lock-starvation fix the whole 'fast' split exists for."""
    from src.data.scheduler_adapter import build_job_specs, validate_executor_assignment

    specs = build_job_specs()
    assert validate_executor_assignment(specs) == []
    for s in specs:
        if s.is_db_writer:
            assert s.executor_class.endswith("_db")
            assert s.executor_class not in ("io", "heartbeat")


def test_uma_listener_assigned_backfill_db_not_fast() -> None:
    """The audited fault — UMA writes DB on the file-only 'fast' executor — is structurally
    fixed by the adapter: UMA (historical settlement) is assigned backfill_db."""
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    uma = by_id["ingest_uma_resolution_listener"]
    assert uma.executor_class == "backfill_db"   # NOT heartbeat/io (its current 'fast')
    assert uma.is_db_writer


def test_executor_class_assignments_by_role() -> None:
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert by_id["ingest_harvester_truth_writer"].executor_class == "live_db"   # live settlement
    assert by_id["ingest_market_scan"].executor_class == "live_db"              # live
    assert by_id["ingest_tigge_archive_backfill"].executor_class == "backfill_db"
    assert by_id["ingest_calibration_auto_promote"].executor_class == "derived_db"
    assert by_id["ingest_heartbeat"].executor_class == "heartbeat"             # file-only
    assert by_id["ingest_source_health_probe"].executor_class == "heartbeat"   # file-only


def test_all_jobs_single_instance_coalesce_preserved() -> None:
    """F10: every job (incl. heartbeat/health/status) is single-instance + coalesce, matching
    the current scheduler. The prior 3/coalesce=False for non-DB jobs would have made
    heartbeats/health overlap on activation — not behavior-preserving."""
    from src.data.scheduler_adapter import build_job_specs

    for s in build_job_specs():
        assert s.max_instances == 1, f"{s.job_id} max_instances must be 1"
        assert s.coalesce is True, f"{s.job_id} must coalesce"


def test_build_job_specs_owner_filter() -> None:
    """F9: build_job_specs(owner) must return ONLY that daemon's jobs — otherwise activation
    would cross-schedule both daemons and bypass the OpenData singleton."""
    from src.data.scheduler_adapter import build_job_specs

    ingest = build_job_specs("ingest_main")
    assert ingest and all(s.owner_daemon == "ingest_main" for s in ingest)
    assert not any(s.job_id.startswith("forecast_live_") for s in ingest)

    fl = build_job_specs("forecast_live_daemon")
    assert fl and all(s.owner_daemon == "forecast_live_daemon" for s in fl)
    assert not any(s.job_id.startswith("ingest_") for s in fl)

    assert len(build_job_specs()) == len(ingest) + len(fl)   # None = full inventory
