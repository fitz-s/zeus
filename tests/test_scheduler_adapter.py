# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Executor-class assignment (no DB writer on file-only executor; UMA->backfill_db).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6);
#   operator spec §7 (Scheduler adapter / executor classes).
"""PR6: registry -> scheduler executor-class assignment (pure planner, daemon wiring deferred)."""
from __future__ import annotations


def test_legacy_scheduler_mode_flags_deleted() -> None:
    """R3 (2026-07-08): the legacy hand-coded add_job() scheduler mode and its mode-selection
    flags were deleted (zero-caller-verified — no deploy/launchd plist ever set them). The
    registry-built scheduler is unconditional now, not merely the default."""
    from src.data import scheduler_adapter as sa

    for removed in (
        "DATA_COLLECTION_MODE_FLAG", "LEGACY_DATA_COLLECTION_FLAG", "SCHEDULER_REGISTRY_FLAG",
        "REGISTRY_MODE", "LEGACY_MODE", "data_collection_mode", "registry_scheduler_active",
        "assert_single_collection_mode",
    ):
        assert not hasattr(sa, removed), f"{removed} should have been deleted with legacy mode"


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


def test_validator_catches_writes_db_on_file_only_lane() -> None:
    """ANTIBODY (PR #329 review P2): the validator must compare the REGISTRY writes_db truth
    against the assigned executor class. The prior check used ``is_db_writer`` (==
    executor_class.endswith('_db')), making ``is_db_writer and class in (io,heartbeat)``
    unreachable — a tautology that could never fire. Plant a writes_db job on the heartbeat lane
    and require a violation, so a future executor_class_for() regression is caught."""
    from src.data.scheduler_adapter import JobBuildSpec, validate_executor_assignment

    # ingest_market_scan is writes_db=True in the registry; route it to a file-only lane:
    planted = [JobBuildSpec("ingest_market_scan", "ingest_main", "heartbeat", 1, True, 60)]
    violations = validate_executor_assignment(planted)
    assert violations and "ingest_market_scan" in violations[0], (
        "validator failed to flag a writes_db job on a file-only executor (tautology regression)"
    )


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


def test_replacement_availability_poll_uses_fast_source_clock_cadence(monkeypatch) -> None:
    """The source-clock download poll must not sit behind the old 5-minute interval."""
    import src.ingest_main as ingest_main

    def _poll_kwargs() -> dict:
        for _fn, trigger, kwargs in ingest_main._ingest_main_job_specs():
            if kwargs.get("id") == "ingest_replacement_availability_poll":
                assert trigger == "interval"
                return kwargs
        raise AssertionError("ingest_replacement_availability_poll spec missing")

    monkeypatch.delenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, raising=False)
    kwargs = _poll_kwargs()
    assert kwargs["seconds"] == 15
    assert "minutes" not in kwargs
    assert kwargs["misfire_grace_time"] == 120
    assert kwargs["next_run_time"] is not None

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "20")
    assert _poll_kwargs()["seconds"] == 20

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "5")
    assert _poll_kwargs()["seconds"] == 15


def test_replacement_current_target_maintenance_stays_minute_bounded(
    monkeypatch,
) -> None:
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.delenv(
        ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV,
        raising=False,
    )

    assert ingest_main._replacement_maintenance_due(now_monotonic=100.0)
    assert not ingest_main._replacement_maintenance_due(now_monotonic=159.999)
    assert ingest_main._replacement_maintenance_due(now_monotonic=160.0)


def test_replacement_availability_fast_poll_skips_heavy_path_when_source_clock_current(monkeypatch) -> None:
    """The source-clock poll must stay lightweight when no public run changed."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _NoChange:
        updated_sources = ()

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
                "updated_sources": [],
                "affected_cities": [],
                "error": None,
            }

    def _scoped_path(*_args, **_kwargs):
        raise AssertionError("scoped source-clock download path should not run without a source-clock change")

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    call_order: list[str] = []
    probe_kwargs: list[dict[str, object]] = []

    def _probe(**kwargs):
        call_order.append("probe")
        probe_kwargs.append(kwargs)
        return _NoChange()

    monkeypatch.setattr(source_clock_probe, "probe_openmeteo_source_clock_updates", _probe)
    monkeypatch.setattr(source_clock_probe, "advance_source_clock_cursor", lambda report: ())
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", _scoped_path)
    current_target_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda cfg, **_kwargs: call_order.append("current_targets")
        or current_target_calls.append(dict(cfg))
        or {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "coverage": {
                "status": "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE",
                "target_count": 2,
                "covered_count": 1,
                "missing_coverage_count": 1,
                "can_seed_count": 0,
                "missing_openmeteo_manifest_count": 0,
                "day0_observed_extreme_required_count": 0,
            },
        },
    )
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg: None)
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0, "advances_detected": 0},
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["status"] == "SOURCE_CLOCK_POLL_CURRENT"
    assert result["source_clock_status"] == "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE"
    assert result["source_clock_updated_sources"] == []
    assert result["current_target_download"]["status"] == "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"
    assert result["current_target_download"]["coverage"]["missing_coverage_count"] == 1
    assert current_target_calls == [{"download_current_targets_enabled": True}]
    assert probe_kwargs == [{"advance_cursor": False}]
    assert call_order == ["probe", "current_targets"]


def test_replacement_materializer_default_limit_matches_seed_burst(monkeypatch) -> None:
    """Default materialization capacity must not under-drain the default seed burst."""
    import src.data.replacement_forecast_production as prod

    source = prod.settings._data if hasattr(prod.settings, "_data") else prod.settings
    monkeypatch.setitem(source, "replacement_forecast_live", {})

    cfg = prod._replacement_forecast_live_materialization_queue_config()

    assert cfg["seed_discovery_limit"] == 80
    assert cfg["seed_limit"] == 80
    assert cfg["limit"] == 80
    assert cfg["limit"] >= cfg["seed_limit"]


def test_replacement_availability_fast_poll_passes_changed_source_clock_report(monkeypatch) -> None:
    """A detected public run change must drive the heavy path with the same probe report."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Munich"],
                "error": None,
            }

    changed_report = _Changed()
    call_order: list[str] = []

    def _scoped_path(
        cfg,
        *,
        source_clock_report=None,
        max_wall_clock_seconds=None,
        on_source_commit=None,
    ):
        call_order.append("scoped_download")
        assert cfg["download_current_targets_enabled"] is True
        assert source_clock_report is changed_report
        assert max_wall_clock_seconds == 45.0
        assert on_source_commit is not None
        on_source_commit("icon_global", {"written_row_count": 1})
        call_order.append("scoped_download_complete")
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_clock_status": "SOURCE_CLOCK_UPDATES_CHANGED",
            "source_clock_updated_sources": ["icon_global"],
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    probe_kwargs: list[dict[str, object]] = []

    def _probe(**kwargs):
        call_order.append("probe")
        probe_kwargs.append(kwargs)
        return changed_report

    monkeypatch.setattr(source_clock_probe, "probe_openmeteo_source_clock_updates", _probe)
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda report, *, sources=None: call_order.append("cursor")
        or tuple(sources or ()),
    )
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", _scoped_path)
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda cfg, **_kwargs: call_order.append("current_targets")
        or {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "available_cycle": "2026-07-02T12:00:00+00:00",
            "coverage": {
                "status": "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE",
                "target_count": 2,
                "covered_count": 2,
                "missing_coverage_count": 0,
                "can_seed_count": 0,
                "missing_openmeteo_manifest_count": 0,
                "day0_observed_extreme_required_count": 0,
            },
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda cfg: call_order.append("fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: call_order.append("cycle_reseed")
        or {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 2,
            "advances_detected": 2,
            "held_advances_detected": 1,
            "freshest_materializable_cycle": "2026-07-02T12:00:00+00:00",
        },
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["status"] == "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert result["source_clock_updated_sources"] == ["icon_global"]
    assert "current_target_download" not in result
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert result["cycle_advance_detail"]["held_advances_detected"] == 1
    assert result["source_clock_cursor_advanced_sources"] == ("icon_global",)
    assert result["source_clock_cursor_deferred_sources"] == ()
    assert probe_kwargs == [{"advance_cursor": False}]
    assert call_order == [
        "probe",
        "scoped_download",
        "fusion_reseed",
        "cycle_reseed",
        "scoped_download_complete",
        "fusion_reseed",
        "cycle_reseed",
        "cursor",
    ]


def test_replacement_availability_cooldown_suppresses_repeated_reseed_scans(
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Munich"],
                "error": None,
            }

    calls: list[str] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: calls.append("probe") or _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report: (),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        lambda *_args, **_kwargs: calls.append("scoped_download")
        or {
            "status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED",
            "cooldown_seconds": 241,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: calls.append("fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: calls.append("cycle_reseed")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )

    first = ingest_main._replacement_availability_poll_tick.__wrapped__()
    second = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert first["fusion_upgrade_status"] == "FUSION_UPGRADE_TRIGGER"
    assert first["cycle_advance_status"] == "CYCLE_ADVANCE_TRIGGER"
    assert second["reseed_maintenance_status"] == (
        "RESEED_MAINTENANCE_NOT_DUE"
    )
    assert "fusion_upgrade_status" not in second
    assert calls == [
        "probe",
        "scoped_download",
        "fusion_reseed",
        "cycle_reseed",
        "probe",
        "scoped_download",
    ]
    assert ingest_main._REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC == 341.0


def test_replacement_availability_poll_throttles_timeboxed_maintenance(monkeypatch) -> None:
    """A timeboxed maintenance pass must not repeat on every metadata tick."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _NoChange:
        updated_sources = ()

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
                "updated_sources": [],
                "affected_cities": [],
                "error": None,
            }

    monkeypatch.setenv(ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV, "1")
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    calls: list[float | None] = []

    def _timeboxed(_cfg, *, max_wall_clock_seconds=None):
        calls.append(max_wall_clock_seconds)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "unattempted_target_count": 2,
            "max_wall_clock_seconds": max_wall_clock_seconds,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _timeboxed,
    )
    monkeypatch.setattr(source_clock_probe, "probe_openmeteo_source_clock_updates", lambda **kwargs: _NoChange())
    monkeypatch.setattr(source_clock_probe, "advance_source_clock_cursor", lambda report: ())
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg: None)
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 3, "advances_detected": 0},
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["status"] == "SOURCE_CLOCK_POLL_CURRENT"
    assert result["current_target_download"]["status"] == "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
    assert result["current_target_download"]["timeboxed_incomplete"] is True
    assert result["current_target_download"]["unattempted_target_count"] == 2
    assert result["cycle_advance_seeds_enqueued"] == 3

    second = ingest_main._replacement_availability_poll_tick.__wrapped__()
    assert second["current_target_download"]["status"] == "CURRENT_TARGET_MAINTENANCE_NOT_DUE"
    assert calls == [1.0]


def test_replacement_availability_fast_poll_caps_scoped_download_under_cadence(monkeypatch) -> None:
    """The scoped download keeps a useful budget across faster metadata polls."""
    import src.ingest_main as ingest_main

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "20")
    monkeypatch.delenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, raising=False)
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 45.0

    monkeypatch.setenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, "999")
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 60.0

    monkeypatch.setenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, "0")
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 1.0


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


class _FakeScheduler:
    """Captures add_job calls so build_registry_scheduler can be tested without APScheduler."""
    def __init__(self):
        self.jobs = []
    def add_job(self, fn, trigger, *, id, executor, max_instances, coalesce, misfire_grace_time, **kw):
        self.jobs.append({"id": id, "executor": executor, "trigger": trigger,
                          "max_instances": max_instances, "coalesce": coalesce,
                          "misfire_grace_time": misfire_grace_time, "kw": kw})


def _ingest_main_job_defs():
    """Daemon-supplied (callable, trigger, trigger_kwargs) for EXACTLY the registry's ingest_main
    expected set (OpenData owned by ingest_main)."""
    from src.data.scheduler_adapter import expected_registry_job_ids
    expected = expected_registry_job_ids("ingest_main", "ingest_main")
    return {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in expected}


def test_build_registry_scheduler_builds_exact_set_and_routes_executors() -> None:
    """PR #329 review A acceptance: in registry mode the daemon builds its jobs FROM the registry —
    every expected job is added with the registry's executor class (lane), not a hand-coded one,
    and the manual add_job set is fully replaced."""
    from src.data.scheduler_adapter import build_registry_scheduler, executor_class_for
    from src.data.source_job_registry import JOB_REGISTRY

    sched = _FakeScheduler()
    job_defs = _ingest_main_job_defs()
    built = build_registry_scheduler(sched, "ingest_main", job_defs, forecast_live_owner_env="ingest_main")

    assert set(built) == set(job_defs)                       # built exactly the registry set
    assert {j["id"] for j in sched.jobs} == set(job_defs)
    # each job routed to its REGISTRY executor class (lane), and all are valid lanes:
    for j in sched.jobs:
        assert j["executor"] == executor_class_for(JOB_REGISTRY[j["id"]])
        assert j["executor"] in (
            "source_clock_db",
            "live_db",
            "backfill_db",
            "derived_db",
            "io",
            "heartbeat",
        )
        assert j["max_instances"] == 1 and j["coalesce"] is True   # anti-overlap preserved


def test_ingest_main_registry_scheduler_replaces_manual_add_job_when_enabled() -> None:
    """PR #329 review A acceptance (named, integration): the REAL ingest_main spec list drives the
    registry build to EXACTLY the registry's expected set — no live job dropped, none invented —
    and every job lands on its registry executor lane (the manual 2-pool add_job is fully replaced).
    """
    import os

    import src.ingest_main as im
    from src.data.scheduler_adapter import (
        build_registry_scheduler, executor_class_for, expected_registry_job_ids, job_defs_from_specs,
    )
    from src.data.source_job_registry import JOB_REGISTRY

    os.environ.pop("ZEUS_FORECAST_LIVE_OWNER", None)   # ingest_main owns OpenData (default)
    specs = im._ingest_main_job_specs()
    job_defs = job_defs_from_specs(specs)
    expected = expected_registry_job_ids("ingest_main", im._forecast_live_owner())
    assert set(job_defs) == expected, f"spec/registry drift: {set(job_defs) ^ expected}"

    sched = _FakeScheduler()
    built = build_registry_scheduler(sched, "ingest_main", job_defs,
                                     forecast_live_owner_env=im._forecast_live_owner())
    assert set(built) == expected
    # every built job routed to its registry lane (manual executor='fast'/'default' replaced):
    for j in sched.jobs:
        assert j["executor"] == executor_class_for(JOB_REGISTRY[j["id"]])
    by_id = {j["id"]: j for j in sched.jobs}
    assert by_id["ingest_day0_metar_source_clock"]["executor"] == "source_clock_db"
    assert by_id["ingest_day0_oracle_anomaly"]["executor"] == "live_db"
    assert by_id["ingest_uma_resolution_listener"]["executor"] == "backfill_db"   # PR8 fix landed
    assert by_id["ingest_heartbeat"]["executor"] == "heartbeat"                   # file-only lane


def test_ingest_main_non_owner_excludes_opendata_from_registry_build() -> None:
    """The OpenData singleton holds through the spec list: when ingest_main does NOT own OpenData,
    its spec list (and thus the registry build) drops the 3 OpenData jobs — matching the registry's
    expected set, so the boot assert passes and OpenData is never double-scheduled."""
    import os

    import src.ingest_main as im
    from src.data.scheduler_adapter import expected_registry_job_ids, job_defs_from_specs

    os.environ["ZEUS_FORECAST_LIVE_OWNER"] = "forecast_live"
    try:
        job_defs = job_defs_from_specs(im._ingest_main_job_specs())
        assert "ingest_opendata_daily_mx2t6" not in job_defs   # OpenData not owned -> not built
        assert job_defs.keys() == expected_registry_job_ids("ingest_main", "forecast_live")
    finally:
        os.environ.pop("ZEUS_FORECAST_LIVE_OWNER", None)


def test_build_registry_scheduler_boot_assert_catches_drift() -> None:
    """The fail-fast boot assert: a daemon whose job_defs miss a registry job (or add an unknown
    one) must REFUSE to boot rather than run a schedule that diverges from the registry."""
    import pytest

    from src.data.scheduler_adapter import build_registry_scheduler, expected_registry_job_ids

    expected = expected_registry_job_ids("ingest_main", "ingest_main")
    # drop one expected job -> mismatch -> raise
    short = {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in list(expected)[1:]}
    with pytest.raises(RuntimeError, match="job-set mismatch"):
        build_registry_scheduler(_FakeScheduler(), "ingest_main", short, forecast_live_owner_env="ingest_main")
    # add an unknown job -> mismatch -> raise
    extra = {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in expected}
    extra["not_a_real_job"] = ((lambda: None), "interval", {"minutes": 5})
    with pytest.raises(RuntimeError, match="job-set mismatch"):
        build_registry_scheduler(_FakeScheduler(), "ingest_main", extra, forecast_live_owner_env="ingest_main")


def test_forecast_live_legacy_and_registry_triggers_are_equivalent(monkeypatch) -> None:
    """BRIDGE EQUIVALENCE (advisor #1): the registry path and the legacy path are TWO CONSUMERS of
    ONE spec list, so per job the (id, trigger_type, trigger_params) must be identical. The
    boot-assert guards the id SET; this guards the trigger PARAMS — catching a future edit where
    the two paths silently diverge on cadence. Executor/concurrency intentionally differ (lanes)."""
    import src.ingest.forecast_live_daemon as fld
    from datetime import datetime, timezone
    from src.config import settings

    specs = fld.forecast_live_job_specs(startup_run_date=datetime(2026, 5, 24, tzinfo=timezone.utc))

    # legacy view: id -> (trigger, sorted trigger-only kwargs)
    owned = fld._REGISTRY_OWNED_KWARGS
    legacy = {
        str(kw["id"]): (trig, sorted((k, str(v)) for k, v in kw.items() if k not in owned))
        for _fn, trig, kw in specs
    }
    # registry view from the SAME derivation used at boot:
    registry = {
        jid: (trig, sorted((k, str(v)) for k, v in tkw.items()))
        for jid, (_fn, trig, tkw) in fld._job_defs_from_specs(specs).items()
    }
    assert legacy == registry, "forecast_live legacy vs registry trigger divergence (cadence drift risk)"


def test_forecast_live_boot_assert_holds_in_both_owner_envs(monkeypatch) -> None:
    """PR #329 review #2+#3: forecast_live_daemon only runs as the OpenData owner, so its expected
    registry set is its full 8 jobs REGARDLESS of ZEUS_FORECAST_LIVE_OWNER — the boot assert must
    not crash the forecast daemon (total OpenData-collection outage) if the env var is unset. This
    is the coverage gap that let the fragility hide while 46 tests passed."""
    import src.ingest.forecast_live_daemon as fld
    from datetime import datetime, timezone
    from src.data.scheduler_adapter import (
        build_registry_scheduler, expected_registry_job_ids, job_defs_from_specs,
    )
    from src.config import settings

    specs = fld.forecast_live_job_specs(startup_run_date=datetime(2026, 5, 24, tzinfo=timezone.utc))
    job_defs = job_defs_from_specs(specs)
    assert len(job_defs) == 8

    for env in ("", "forecast_live", "ingest_main"):
        expected = expected_registry_job_ids("forecast_live_daemon", env)
        assert set(job_defs) == expected, (
            f"forecast_live boot assert would FAIL with ZEUS_FORECAST_LIVE_OWNER={env!r}: "
            f"built 8 vs expected {len(expected)} (daemon refuses to boot -> OpenData outage)"
        )
        # and the build actually succeeds (no RuntimeError) in each env:
        built = build_registry_scheduler(_FakeScheduler(), "forecast_live_daemon", job_defs,
                                         forecast_live_owner_env=env)
        assert len(built) == 8


def test_ingest_main_opendata_still_env_gated() -> None:
    """The #2 fix must NOT break the ingest_main side of the singleton: ingest_main (which runs
    regardless of ownership) still drops OpenData when it is not the active owner."""
    from src.data.scheduler_adapter import expected_registry_job_ids

    owns = expected_registry_job_ids("ingest_main", "ingest_main")
    not_owns = expected_registry_job_ids("ingest_main", "forecast_live")
    assert "ingest_opendata_daily_mx2t6" in owns
    assert "ingest_opendata_daily_mx2t6" not in not_owns   # singleton preserved
    assert len(owns) - len(not_owns) == 3                  # the 3 OpenData jobs (2 daily + startup)
