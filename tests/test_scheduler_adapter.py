# Lifecycle: created=2026-05-24; last_reviewed=2026-08-17; last_reused=2026-08-17
# Purpose: Current single-live scheduler set and causal executor-class assignment.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-08-17
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6);
#   operator spec §7 (Scheduler adapter / executor classes).
"""PR6: registry -> scheduler executor-class assignment (pure planner, daemon wiring deferred)."""
from __future__ import annotations

import pytest


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
            assert s.executor_class not in ("io", "diagnostic_io", "heartbeat")


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


def test_retired_alternate_jobs_are_not_schedulable() -> None:
    """Single-live semantics must not silently revive retired alternate writers."""
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert "ingest_uma_resolution_listener" not in by_id
    assert "ingest_calibration_auto_promote" not in by_id
    assert by_id["ingest_harvester_truth_writer"].executor_class == "settlement_db"


def test_executor_class_assignments_by_role() -> None:
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert by_id["ingest_harvester_truth_writer"].executor_class == "settlement_db"
    assert by_id["ingest_market_scan"].executor_class == "market_topology_db"
    assert by_id["ingest_k2_forecasts_daily"].executor_class == "forecast_archive_db"
    assert by_id["ingest_opendata_daily_mx2t6"].executor_class == "forecast_source_db"
    assert by_id["ingest_replacement_availability_poll"].executor_class == "forecast_clock_db"
    assert by_id["ingest_replacement_maintenance"].executor_class == "derived_db"
    assert by_id["ingest_day0_oracle_anomaly"].executor_class == "oracle_guard_db"
    assert by_id["ingest_k2_obs_fast_tick"].executor_class == "observation_db"
    assert by_id["ingest_tigge_archive_backfill"].executor_class == "backfill_db"
    assert by_id["ingest_heartbeat"].executor_class == "heartbeat"
    assert by_id["ingest_source_health_probe"].executor_class == "health_io"


def test_unclassified_live_db_writer_fails_closed() -> None:
    import pytest

    from src.data.scheduler_adapter import executor_class_for
    from src.data.source_job_registry import SourceJobSpec

    unknown = SourceJobSpec("new_live_writer", "ingest_main", "live", "default", True)
    with pytest.raises(ValueError, match="no explicit causal executor lane"):
        executor_class_for(unknown)


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

    def _maintenance_kwargs() -> dict:
        for _fn, trigger, kwargs in ingest_main._ingest_main_job_specs():
            if kwargs.get("id") == "ingest_replacement_maintenance":
                assert trigger == "interval"
                return kwargs
        raise AssertionError("ingest_replacement_maintenance spec missing")

    monkeypatch.delenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, raising=False)
    kwargs = _poll_kwargs()
    assert kwargs["seconds"] == 15
    assert "minutes" not in kwargs
    assert kwargs["misfire_grace_time"] == 120
    assert kwargs["next_run_time"] is not None
    assert _maintenance_kwargs()["seconds"] == 60

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


@pytest.mark.parametrize(
    ("source_status", "source_error", "expected_status", "expected_failed"),
    (
        (
            "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
            None,
            "SOURCE_CLOCK_POLL_CURRENT",
            False,
        ),
        (
            "SOURCE_CLOCK_MODEL_UPDATES_DEGRADED_CACHE",
            "metadata transport unavailable",
            "SOURCE_CLOCK_MODEL_UPDATES_DEGRADED_CACHE",
            True,
        ),
    ),
)
def test_replacement_availability_fast_poll_skips_heavy_path_when_source_clock_current(
    monkeypatch, source_status, source_error, expected_status, expected_failed
) -> None:
    """The source-clock poll must stay lightweight when no public run changed."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _NoChange:
        updated_sources = ()

        def as_dict(self):
            return {
                "status": source_status,
                "updated_sources": [],
                "affected_cities": [],
                "error": source_error,
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

    assert result["status"] == expected_status
    assert result["source_clock_status"] == source_status
    assert ingest_main._classify_result(result)[0] is expected_failed
    assert result["source_clock_updated_sources"] == []
    assert result["maintenance_status"] == "REPLACEMENT_MAINTENANCE_DECOUPLED"
    assert current_target_calls == []
    assert probe_kwargs == [{"advance_cursor": False}]
    assert call_order == ["probe"]


def test_replacement_materializer_default_limit_matches_seed_burst(monkeypatch) -> None:
    """Defaults keep both capacity and the canonical live repair lane available."""
    import src.data.replacement_forecast_production as prod
    from src.config import STATE_DIR

    source = prod.settings._data if hasattr(prod.settings, "_data") else prod.settings
    monkeypatch.setitem(source, "replacement_forecast_live", {})

    cfg = prod._replacement_forecast_live_materialization_queue_config()

    assert cfg["seed_discovery_limit"] == 80
    assert cfg["seed_limit"] == 80
    assert cfg["limit"] == 80
    assert cfg["poll_batch_limit"] == 8
    assert cfg["limit"] >= cfg["seed_limit"]
    assert cfg["forecast_db"] == STATE_DIR / "zeus-forecasts.db"
    assert cfg["raw_manifest_dir"] == (
        STATE_DIR / "replacement_forecast_live" / "raw_manifests"
    )


def test_replacement_materialize_poll_uses_configured_micro_batch(monkeypatch) -> None:
    """Every hot-queue branch must use the configured bounded micro-batch."""
    import src.data.replacement_forecast_production as prod
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "request_dir": "requests",
        "seed_dir": "seeds",
        "poll_batch_limit": 8,
    }
    pending = {"request_dir": False, "seed_dir": False, "inflight": False}
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_queue_pending",
        lambda _cfg, key: pending[key],
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_inflight_pending",
        lambda _cfg: pending["inflight"],
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_materialize_job",
        lambda **kwargs: calls.append(kwargs),
    )

    pending["request_dir"] = True
    daemon._replacement_forecast_materialize_poll_job()
    pending["request_dir"] = False
    pending["seed_dir"] = True
    daemon._replacement_forecast_materialize_poll_job()
    pending["seed_dir"] = False
    pending["inflight"] = True
    daemon._replacement_forecast_materialize_poll_job()

    assert calls == [
        {"discover": False, "limit": 8, "seed_limit": 0},
        {"discover": False, "limit": 8, "seed_limit": 8},
        {"discover": False, "limit": 8, "seed_limit": 0},
    ]


def test_replacement_discovery_is_not_limited_by_poll_claim_size(
    monkeypatch, tmp_path
) -> None:
    """Discovery may queue the configured burst; the poller still claims it incrementally."""
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "raw_manifest_dir": tmp_path / "raw",
        "seed_dir": tmp_path / "seeds",
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "claims",
        "seed_discovery_limit": 80,
        "poll_batch_limit": 8,
    }
    calls: list[dict[str, object]] = []

    class _Report:
        status = "NO_ELIGIBLE_TARGETS"
        discovered_count = 80

        @staticmethod
        def as_dict() -> dict[str, object]:
            return {}

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_discovery_revision",
        lambda _cfg: ("revision",),
    )
    monkeypatch.setattr(
        discovery,
        "discover_replacement_forecast_materialization_seeds",
        lambda **kwargs: calls.append(kwargs) or _Report(),
    )
    monkeypatch.setattr(daemon, "_replacement_forecast_last_discovery_revision", None)

    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert calls == [
        {
            "forecast_db": cfg["forecast_db"],
            "raw_manifest_dir": cfg["raw_manifest_dir"],
            "seed_dir": cfg["seed_dir"],
            "request_dir": cfg["request_dir"],
            "inflight_dir": cfg["inflight_dir"],
            "limit": 80,
        }
    ]
    assert daemon._replacement_forecast_last_discovery_revision is None

    _Report.discovered_count = 7
    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert daemon._replacement_forecast_last_discovery_revision == ("revision",)


def test_replacement_discovery_revision_advances_on_fast_observation_print(
    monkeypatch, tmp_path
) -> None:
    """A new fast METAR must invalidate Day0 materialization discovery."""
    import sqlite3

    import src.ingest.forecast_live_daemon as daemon
    import src.state.db as state_db

    forecast_db = tmp_path / "forecast.db"
    forecast = sqlite3.connect(forecast_db)
    forecast.executescript(
        """
        CREATE TABLE market_events (event_id INTEGER PRIMARY KEY);
        CREATE TABLE raw_model_forecasts (raw_model_forecast_id INTEGER PRIMARY KEY);
        CREATE TABLE raw_forecast_artifacts (artifact_id INTEGER PRIMARY KEY);
        CREATE TABLE source_run_coverage (source_run_id TEXT);
        CREATE TABLE readiness_state (expires_at TEXT);
        """
    )
    forecast.commit()
    forecast.close()

    world_db = tmp_path / "world.db"
    world = sqlite3.connect(world_db)
    world.executescript(
        """
        CREATE TABLE observation_instants (id INTEGER PRIMARY KEY);
        CREATE TABLE observation_prints (id INTEGER PRIMARY KEY);
        INSERT INTO observation_instants(id) VALUES (7);
        INSERT INTO observation_prints(id) VALUES (11);
        """
    )
    world.commit()
    world.close()
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_db)

    cfg = {"forecast_db": forecast_db}
    before = daemon._replacement_forecast_discovery_revision(cfg)

    world = sqlite3.connect(world_db)
    world.execute("INSERT INTO observation_prints(id) VALUES (12)")
    world.commit()
    world.close()
    after = daemon._replacement_forecast_discovery_revision(cfg)

    assert before is not None and after is not None
    assert before[-3:] == (7, 11, before[-1])
    assert after[-3:] == (7, 12, after[-1])
    assert before != after


def test_replacement_discovery_runs_with_backlog_and_retries_pending_family(
    monkeypatch, tmp_path
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "raw_manifest_dir": tmp_path / "raw",
        "seed_dir": tmp_path / "seeds",
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "claims",
        "seed_discovery_limit": 10,
    }
    cfg["request_dir"].mkdir()
    (cfg["request_dir"] / "unrelated.json").write_text("{}")
    calls: list[dict[str, object]] = []

    class _Report:
        status = "NO_ELIGIBLE_TARGETS"
        discovered_count = 0
        reason_codes = (
            "REPLACEMENT_SEED_DISCOVERY_TARGET_ALREADY_PENDING_SKIPPED",
        )

        @staticmethod
        def as_dict() -> dict[str, object]:
            return {}

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_discovery_revision",
        lambda _cfg: ("revision",),
    )
    monkeypatch.setattr(
        discovery,
        "discover_replacement_forecast_materialization_seeds",
        lambda **kwargs: calls.append(kwargs) or _Report(),
    )
    monkeypatch.setattr(daemon, "_replacement_forecast_last_discovery_revision", None)

    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert len(calls) == 1
    assert calls[0]["request_dir"] == cfg["request_dir"]
    assert calls[0]["inflight_dir"] == cfg["inflight_dir"]
    assert daemon._replacement_forecast_last_discovery_revision is None


def test_replacement_availability_fast_poll_passes_changed_source_clock_report(monkeypatch) -> None:
    """A scoped commit must run one broad catch-up without duplicating its markers."""
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
        on_source_commit(
            "icon_global",
            {
                "written_row_count": 9,
                "committed_families": (
                    ("Seoul", "2026-07-03", "high"),
                    ("Wellington", "2026-07-03", "high"),
                ),
            },
        )
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
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {
            ("Seoul", "2026-07-03", "high"): 0,
            ("Wellington", "2026-07-03", "high"): 1,
        },
    )
    anchor_calls: list[dict[str, object]] = []

    def _download_anchor(_cfg, **kwargs):
        call_order.append("anchor_scope_download")
        anchor_calls.append(kwargs)
        city = kwargs["required_scopes"][0][0]
        return {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "available_cycle": "2026-07-02T12:00:00+00:00",
            "written_manifest_count": 1,
            "written_manifests": [f"/tmp/{city.lower()}-high.manifest.json"],
            "coverage": {
                "status": "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE",
                "target_count": 2,
                "covered_count": 2,
                "missing_coverage_count": 0,
                "can_seed_count": 0,
                "missing_openmeteo_manifest_count": 0,
                "day0_observed_extreme_required_count": 0,
            },
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _download_anchor,
    )
    fusion_calls: list[dict[str, object]] = []
    raw_revision = "icon_global:2026-07-03T12:00:00Z"
    all_changed_scopes = (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
        ("Paris", "2026-07-03", "high"),
    )
    markers: set[tuple[tuple[str, str, str], str]] = set()

    def _fusion_reseed(_cfg, **kwargs):
        call_order.append("fusion_reseed")
        fusion_calls.append(kwargs)
        scopes = tuple(kwargs.get("scopes") or all_changed_scopes)
        enqueued = [
            scope for scope in scopes if (scope, raw_revision) not in markers
        ]
        markers.update((scope, raw_revision) for scope in enqueued)
        return {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": len(enqueued),
        }

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", _fusion_reseed)
    cycle_calls: list[dict[str, object]] = []

    def _cycle_reseed(_cfg, **kwargs):
        call_order.append("cycle_reseed")
        cycle_calls.append(kwargs)
        return {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 2,
            "advances_detected": 2,
            "held_advances_detected": 1,
            "freshest_materializable_cycle": "2026-07-02T12:00:00+00:00",
        }

    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", _cycle_reseed)

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["status"] == "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert result["source_clock_updated_sources"] == ["icon_global"]
    assert "current_target_download" not in result
    assert result["fusion_upgrade_seeds_enqueued"] == 2
    assert result["broad_fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert result["cycle_advance_detail"]["held_advances_detected"] == 1
    assert result["source_clock_cursor_advanced_sources"] == ("icon_global",)
    assert result["source_clock_cursor_deferred_sources"] == ()
    assert probe_kwargs == [{"advance_cursor": False}]
    assert fusion_calls[0]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[0]["changed_sources"] == ("icon_global",)
    assert fusion_calls[0]["manifest_snapshot"] is cycle_calls[0]["manifest_snapshot"]
    assert fusion_calls[0]["manifest_snapshot"]["manifest_paths"] == (
        "/tmp/seoul-high.manifest.json",
        "/tmp/wellington-high.manifest.json",
    )
    assert len(anchor_calls) == 2
    assert anchor_calls[0]["required_scopes"] == (
        ("Seoul", "2026-07-03", "high"),
    )
    assert anchor_calls[0]["quota_critical"] is True
    assert 0.0 < anchor_calls[0]["max_wall_clock_seconds"] <= 10.0
    assert anchor_calls[1]["required_scopes"] == (
        ("Wellington", "2026-07-03", "high"),
    )
    assert "quota_critical" not in anchor_calls[1]
    assert 0.0 < anchor_calls[1]["max_wall_clock_seconds"] <= 10.0
    assert cycle_calls[0]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[1] == {"changed_sources": None}
    assert markers == {
        (scope, raw_revision) for scope in all_changed_scopes
    }
    assert call_order == [
        "probe",
        "scoped_download",
        "anchor_scope_download",
        "anchor_scope_download",
        "fusion_reseed",
        "cycle_reseed",
        "scoped_download_complete",
        "fusion_reseed",
        "cursor",
    ]
    assert result["reseed_maintenance_status"] == (
        "SOURCE_COMMIT_RESEEDS_PUBLISHED"
    )


def test_replacement_availability_pending_callback_runs_broad_fusion_catchup(
    monkeypatch,
) -> None:
    """A callback that outlives the poll cannot suppress the one broad fusion catch-up."""
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Seoul", "Wellington"],
                "error": None,
            }

    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_errors: list[BaseException] = []
    late_callback: threading.Thread | None = None

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        nonlocal late_callback
        assert on_source_commit is not None

        def _late_callback() -> None:
            callback_started.set()
            release_callback.wait(timeout=5)
            try:
                on_source_commit(
                    "icon_global",
                    {
                        "written_row_count": 9,
                        "committed_families": (
                            ("Seoul", "2026-07-03", "high"),
                            ("Wellington", "2026-07-03", "high"),
                        ),
                    },
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
                callback_errors.append(exc)

        late_callback = threading.Thread(target=_late_callback, daemon=True)
        late_callback.start()
        assert callback_started.wait(timeout=2)
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_commit_notifications": 0,
            "source_commit_notifications_pending": 1,
            "source_commit_notification_errors": (),
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: ("icon_global",),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda _report, *, sources=None: tuple(sources or ()),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "written_manifest_count": 1,
            "written_manifests": ["/tmp/seoul-high.manifest.json"],
        },
    )
    fusion_calls: list[dict[str, object]] = []
    cycle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kwargs: fusion_calls.append(kwargs)
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **kwargs: cycle_calls.append(kwargs)
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )

    try:
        result = ingest_main._replacement_availability_poll_tick.__wrapped__()

        assert result["reseed_maintenance_status"] == (
            "SOURCE_COMMIT_RESEEDS_DEFERRED"
        )
        assert result["source_clock_cursor_advanced_sources"] == ()
        assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
        assert fusion_calls == [{"changed_sources": None}]
        assert cycle_calls == []
    finally:
        release_callback.set()
        if late_callback is not None:
            late_callback.join(timeout=5)

    assert late_callback is not None and not late_callback.is_alive()
    assert callback_errors == []
    assert len(fusion_calls) == 2
    assert fusion_calls[1]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[1]["changed_sources"] == ("icon_global",)
    assert len(cycle_calls) == 1


def test_pending_callback_broad_trigger_persists_missed_revision_before_cursor(
    monkeypatch,
    tmp_path,
) -> None:
    """The real broad trigger durably queues a missed raw revision before cursor advance."""
    import json
    import sqlite3
    import threading
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace

    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_fusion_upgrade_trigger as fusion_trigger
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main
    from src.data.replacement_forecast_readiness import SOURCE_ID
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    db = tmp_path / "forecasts.db"
    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    carrier = "2026-07-28T06:00:00+00:00"
    newer = "2026-07-28T12:00:00+00:00"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('icon_global', 'London', '2026-07-30', 'low', ?, ?, ?, 2, 18.0,
                'single_runs')
        """,
        (carrier, carrier, carrier),
    )
    old_raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    provenance = {
        "bayes_precision_fusion": {
            "used_models": ["icon_global"],
            "current_value_serving": {
                "icon_global": {"raw_model_forecast_id": old_raw_id},
            },
            "source_clock_one_scheme": {
                "configured_sources": ["icon_global"],
            },
        },
    }
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date,
             temperature_metric, source_cycle_time, source_available_at,
             computed_at, q_json, q_lcb_json, posterior_method,
             dependency_source_run_ids_json, provenance_json,
             runtime_layer, training_allowed)
        VALUES (?, 'pid', 'dv', 'London', '2026-07-30', 'low', ?, ?, ?,
                '{}', '{}', ?, '{}', ?, 'live', 0)
        """,
        (
            SOURCE_ID,
            carrier,
            carrier,
            "2026-07-28T10:00:00+00:00",
            SOURCE_ID,
            json.dumps(provenance),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('icon_global', 'London', '2026-07-30', 'low', ?, ?, ?, 2, 17.0,
                'single_runs')
        """,
        (newer, newer, newer),
    )
    new_raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["London", "Seoul"],
                "error": None,
            }

    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_thread: threading.Thread | None = None

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        nonlocal callback_thread
        assert on_source_commit is not None

        def _late_callback() -> None:
            callback_started.set()
            release_callback.wait(timeout=5)
            on_source_commit(
                "icon_global",
                {
                    "written_row_count": 2,
                    "committed_families": (
                        ("Seoul", "2026-07-30", "low"),
                    ),
                },
            )

        callback_thread = threading.Thread(target=_late_callback, daemon=True)
        callback_thread.start()
        assert callback_started.wait(timeout=2)
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "source_commit_notifications": 0,
            "source_commit_notifications_pending": 1,
            "source_commit_notification_errors": (),
        }

    def _build_private(_conn, **build_kwargs):
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(
            json.dumps(
                {
                    "scope": "London-low",
                    "raw_revision": new_raw_id,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return stage

    cursor_evidence: list[tuple[str, str]] = []

    def _advance_cursor(_report, *, sources=None):
        evidence_conn = sqlite3.connect(db)
        marker = evidence_conn.execute(
            """
            SELECT capturable_family_set, seed_file
            FROM fusion_upgrade_enqueues
            WHERE city = 'London' AND target_date = '2026-07-30' AND metric = 'low'
            """
        ).fetchone()
        evidence_conn.close()
        assert marker is not None
        assert f"|input_revision=icon_global:{new_raw_id}" in marker[0]
        assert Path(marker[1]).is_file()
        seed_payload = json.loads(Path(marker[1]).read_text(encoding="utf-8"))
        assert seed_payload["raw_revision"] == new_raw_id
        assert len(list(seed_dir.glob("*.json"))) == 1
        cursor_evidence.append((marker[0], marker[1]))
        return tuple(sources or ())

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "download_current_targets_enabled": True,
            "forecast_db": db,
            "seed_dir": seed_dir,
            "raw_manifest_dir": raw_dir,
            "seed_limit": 4,
        },
    )
    monkeypatch.setattr(
        prod,
        "_prepared_reseed_manifests",
        lambda *_args, **_kwargs: (
            datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc),
            (),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY",
            reason_codes=(),
            rows=(
                SimpleNamespace(
                    city="London",
                    target_date="2026-07-30",
                    temperature_metric="low",
                    day0_observed_extreme_required=False,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        fusion_trigger,
        "_build_and_write_upgrade_seed",
        _build_private,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: ("icon_global",),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        _advance_cursor,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "written_manifest_count": 0,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )

    try:
        result = ingest_main._replacement_availability_poll_tick.__wrapped__()
    finally:
        release_callback.set()
        if callback_thread is not None:
            callback_thread.join(timeout=5)

    assert result["reseed_maintenance_status"] == "SOURCE_COMMIT_RESEEDS_DEFERRED"
    assert result["broad_fusion_upgrade_seeds_enqueued"] == 1
    assert result["source_clock_cursor_advanced_sources"] == ()
    assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
    assert cursor_evidence == []
    assert callback_thread is not None and not callback_thread.is_alive()


def test_ecmwf_source_clock_captures_anchor_before_single_runs_fanout(monkeypatch) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("ecmwf_ifs",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["ecmwf_ifs"],
                "affected_cities": ["Shanghai"],
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
    held_scope = ("Dallas", "2026-08-17", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {held_scope: 0},
    )

    def _anchor(_cfg, **kwargs):
        if kwargs.get("quota_critical"):
            calls.append("held_anchor")
            assert kwargs == {
                "max_wall_clock_seconds": 10.0,
                "required_scopes": (held_scope,),
                "quota_critical": True,
            }
            return {
                "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
                "written_manifest_count": 0,
            }
        calls.append("anchor")
        assert kwargs == {"max_wall_clock_seconds": 10.0}
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "written_manifest_count": 2,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _anchor,
    )

    def _scoped(_cfg, **_kwargs):
        calls.append("scoped_download")
        assert calls == [
            "probe",
            "held_anchor",
            "anchor",
            "anchor_fusion_reseed",
            "anchor_cycle_reseed",
            "scoped_download",
        ]
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "written_row_count": 0,
        }

    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **_kwargs: calls.append("anchor_fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: calls.append("anchor_cycle_reseed")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: (),
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["source_clock_anchor_download"] == {
        "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
        "fusion_upgrade_status": "FUSION_UPGRADE_TRIGGER",
        "fusion_upgrade_seeds_enqueued": 1,
        "cycle_advance_status": "CYCLE_ADVANCE_TRIGGER",
        "cycle_advance_seeds_enqueued": 1,
    }
    assert result["source_clock_held_anchor_download"] == {
        "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
    }
    assert result["reseed_maintenance_status"] == "SOURCE_ANCHOR_RESEEDS_PUBLISHED"
    assert calls == [
        "probe",
        "held_anchor",
        "anchor",
        "anchor_fusion_reseed",
        "anchor_cycle_reseed",
        "scoped_download",
    ]


def test_source_commit_reseed_triggers_share_one_manifest_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    import src.data.replacement_cycle_advance_trigger as cycle_trigger
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.data.replacement_fusion_upgrade_trigger as fusion_trigger

    loaded = (object(),)
    load_calls = []
    trigger_calls = []
    monkeypatch.setattr(
        discovery,
        "_load_manifests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scoped source commit must not scan manifest inventory")
        ),
    )
    monkeypatch.setattr(
        discovery,
        "_load_manifest_files",
        lambda paths, *, computed_at: load_calls.append((paths, computed_at)) or loaded,
    )
    monkeypatch.setattr(
        fusion_trigger,
        "enqueue_fusion_upgrade_reseeds",
        lambda **kwargs: trigger_calls.append(("fusion", kwargs)) or {},
    )
    monkeypatch.setattr(
        cycle_trigger,
        "enqueue_cycle_advance_reseeds",
        lambda **kwargs: trigger_calls.append(("cycle", kwargs)) or {},
    )
    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
        "limit": 8,
    }
    manifest_path = tmp_path / "anchor.manifest.json"
    snapshot = {"manifest_paths": (str(manifest_path),)}

    prod._enqueue_fusion_upgrade_reseeds_if_needed(
        cfg,
        scopes=(("Paris", "2026-07-18", "high"),),
        changed_sources=("ecmwf_ifs",),
        manifest_snapshot=snapshot,
    )
    prod._enqueue_cycle_advance_reseeds_if_needed(
        cfg,
        scopes=(("Paris", "2026-07-18", "high"),),
        manifest_snapshot=snapshot,
    )

    assert len(load_calls) == 1
    assert load_calls[0][0] == (str(manifest_path),)
    assert trigger_calls[0][1]["manifests"] is loaded
    assert trigger_calls[1][1]["manifests"] is loaded
    assert trigger_calls[1][1]["include_missing_posterior"] is True
    assert trigger_calls[0][1]["computed_at"] == trigger_calls[1][1]["computed_at"]


def test_replacement_availability_notification_error_keeps_global_reseed(
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

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        try:
            on_source_commit(
                "icon_global",
                {
                    "written_row_count": 1,
                    "committed_families": (
                        ("Munich", "2026-07-03", "high"),
                    ),
                },
            )
        except RuntimeError as exc:
            errors = (f"icon_global:RuntimeError: {exc}",)
        else:
            errors = ()
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_commit_notification_errors": errors,
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 3)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        999.0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda _report, *, sources=None: tuple(sources or ()),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("anchor unavailable")
        ),
    )
    fusion_calls: list[dict[str, object]] = []
    cycle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kwargs: fusion_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **kwargs: cycle_calls.append(kwargs) or None,
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["source_commit_notification_errors"]
    assert fusion_calls == [{"changed_sources": None}]
    assert cycle_calls == [{}]
    assert result["reseed_maintenance_status"] == (
        "SOURCE_BROAD_RESEEDS_RETRYABLE"
    )
    assert result["reseed_errors"] == (
        "fusion_upgrade:RESEED_CONFIGURATION_UNAVAILABLE",
        "cycle_advance:RESEED_CONFIGURATION_UNAVAILABLE",
    )
    assert result["source_clock_cursor_advanced_sources"] == ()
    assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 0
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC == 0.0


def test_replacement_availability_cooldown_keeps_metadata_probe_alive_but_suppresses_reseeds(
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
        lambda _report, **_kwargs: (),
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
        lambda _cfg, **_kwargs: calls.append("fusion_reseed")
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


def test_replacement_maintenance_tick_throttles_timeboxed_repair(monkeypatch) -> None:
    """A timeboxed repair defers broad reseeds instead of multiplying the tick budget."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.observability.scheduler_health as scheduler_health

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
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS",
        },
    )
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg: None)
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 3, "advances_detected": 0},
    )
    health: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: health.append(
            {"job_name": job_name, **kwargs}
        ),
    )

    result = ingest_main._replacement_maintenance_tick()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["maintenance_errors"] == (
        "current_target:CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
    )
    assert result["current_target_download"]["status"] == "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
    assert result["current_target_download"]["timeboxed_incomplete"] is True
    assert result["current_target_download"]["unattempted_target_count"] == 2
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_RESEEDS_DEFERRED_DEADLINE"
    )
    assert "cycle_advance_seeds_enqueued" not in result
    assert health[-1] == {
        "job_name": "ingest_replacement_maintenance",
        "failed": True,
        "reason": "replacement_maintenance_partial",
    }

    second = ingest_main._replacement_maintenance_tick()
    assert second["status"] == "REPLACEMENT_MAINTENANCE_NOT_DUE"
    assert calls == [pytest.approx(1.0, abs=0.01)]


def test_replacement_maintenance_uses_one_parent_deadline(monkeypatch) -> None:
    """Current-target work cannot grant BPF and broad reseeds fresh full budgets."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(
        ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV,
        "10",
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    budgets: list[tuple[str, float]] = []

    def _current(_cfg, *, max_wall_clock_seconds):
        budgets.append(("current", max_wall_clock_seconds))
        now[0] += 7.0
        return {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"}

    def _extras(_cfg, *, max_wall_clock_seconds):
        budgets.append(("extras", max_wall_clock_seconds))
        now[0] += max_wall_clock_seconds
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "written_row_count": 2,
            "committed_families": (
                ("Shanghai", "2026-08-12", "high"),
                ("Munich", "2026-08-13", "high"),
            ),
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _current,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _extras,
    )
    reseeds: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("fusion", scopes, limit))
            or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 2}
        ),
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("cycle", scopes, limit))
            or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2}
        ),
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert budgets == [("current", 10.0), ("extras", 3.0)]
    scopes = (
        ("Munich", "2026-08-13", "high"),
        ("Shanghai", "2026-08-12", "high"),
    )
    assert reseeds == [("fusion", scopes, 2), ("cycle", scopes, 2)]
    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_COMMITTED_RESEEDS_PUBLISHED"
    )
    assert result["committed_family_count"] == 2


def test_replacement_maintenance_does_not_publish_failsoft_committed_reseed(
    monkeypatch,
) -> None:
    """A trigger error remains retryable; it is never evidence that q was reseeded."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(
        ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV,
        "10",
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )

    def _current(_cfg, *, max_wall_clock_seconds):
        now[0] += 7.0
        return {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"}

    def _extras(_cfg, *, max_wall_clock_seconds):
        now[0] += max_wall_clock_seconds
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "written_row_count": 1,
            "committed_families": (("Shanghai", "2026-08-12", "high"),),
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _current,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _extras,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED",
            "error": "seed writer unavailable",
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 1,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_RESEEDS_DEFERRED_DEADLINE"
    )
    assert result["committed_fusion_upgrade_status"] == (
        "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"
    )
    assert result["committed_cycle_advance_status"] == "CYCLE_ADVANCE_TRIGGER"
    assert "committed_fusion_upgrade:FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED" in (
        result["maintenance_errors"]
    )


def test_replacement_maintenance_broad_none_is_retryable(monkeypatch) -> None:
    """Missing broad trigger configuration cannot disappear as a completed repair."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"
        },
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["maintenance_errors"] == (
        "fusion_upgrade:RESEED_CONFIGURATION_UNAVAILABLE",
    )
    assert result["cycle_advance_status"] == "CYCLE_ADVANCE_TRIGGER"


def test_replacement_maintenance_quota_cooldown_is_partial_but_reseeds(
    monkeypatch,
) -> None:
    """Global download cooldown defers transport, not independent durable reseed drains."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main
    import src.observability.scheduler_health as scheduler_health

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 120,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )

    def _unexpected_download(*_args, **_kwargs):
        raise AssertionError("quota cooldown must defer download transport")

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _unexpected_download,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _unexpected_download,
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )
    health: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: health.append(
            {"job_name": job_name, **kwargs}
        ),
    )

    result = ingest_main._replacement_maintenance_tick()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["cooldown_seconds"] == 120
    assert result["maintenance_errors"] == (
        "bayes_precision_fusion_extra:"
        "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
    )
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert reseeds == ["fusion", "cycle"]
    assert health[-1] == {
        "job_name": "ingest_replacement_maintenance",
        "failed": True,
        "reason": "replacement_maintenance_partial",
    }


def test_replacement_maintenance_repairs_full_extras_before_reseed(
    monkeypatch,
) -> None:
    """The sole maintenance owner heals missing extras without a source-clock change."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    calls: list[str] = []
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: calls.append("current_targets")
        or {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: calls.append("full_extras")
        or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 2,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: calls.append("fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: calls.append("cycle_reseed")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert calls == [
        "current_targets",
        "full_extras",
        "fusion_reseed",
        "cycle_reseed",
    ]
    assert result["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )
    assert result["bayes_precision_fusion_extra_rows_written"] == 2
    assert result["fusion_upgrade_seeds_enqueued"] == 1


def test_replacement_maintenance_backs_off_only_zero_progress_bpf_fanout(
    monkeypatch,
) -> None:
    """A transient broad fan-out cannot spend quota every minute without new rows."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    current_calls: list[float] = []
    extras_reports = [
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "written_row_count": 0,
        },
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 2,
        },
    ]
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: current_calls.append(now[0])
        or {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: extras_reports.pop(0),
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion") or None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle") or None,
    )

    first = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert first["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    )
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 1

    now[0] = 160.0
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    second = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert second["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_NO_PROGRESS_BACKOFF_SKIPPED"
    )
    assert len(extras_reports) == 1
    assert current_calls == [100.0, 160.0]
    assert reseeds == ["fusion", "cycle", "fusion", "cycle"]

    now[0] = 401.0
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    third = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert third["bayes_precision_fusion_extra_rows_written"] == 2
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 0
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC == 0.0


@pytest.mark.parametrize(
    ("lane", "status"),
    (
        ("current_target", "CURRENT_TARGET_DOWNLOAD_TIMEOUT"),
        ("current_target", "CURRENT_TARGET_DOWNLOAD_FAILSOFT"),
        ("current_target", "CURRENT_TARGET_RAW_INPUTS_TRANSPORT_RETRYABLE"),
        ("current_target", "CURRENT_TARGET_DOWNLOAD_INFLIGHT_SKIP"),
        ("current_target", "CYCLE_PROBE_UNRESOLVED_SKIP"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"),
    ),
)
def test_replacement_maintenance_retryable_status_contract_runs_reseeds(
    monkeypatch,
    lane,
    status,
) -> None:
    """Known incomplete inner statuses are explicit PARTIAL, never healthy substring guesses."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    current_status = (
        status if lane == "current_target" else "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"
    )
    extras_status = (
        status if lane == "extras" else "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {"status": current_status},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {"status": extras_status},
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    expected_lane = (
        "current_target" if lane == "current_target" else "bayes_precision_fusion_extra"
    )
    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["maintenance_errors"] == (f"{expected_lane}:{status}",)
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert reseeds == ["fusion", "cycle"]
    assert ingest_main._classify_result(result) == (
        True,
        "replacement_maintenance_partial",
    )


def test_replacement_maintenance_isolates_reseed_failures(monkeypatch) -> None:
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS",
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("fusion busy")),
    )
    cycle_calls: list[bool] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: cycle_calls.append(True)
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert cycle_calls == [True]
    assert result["maintenance_errors"][0].startswith("fusion_upgrade:RuntimeError")


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
            "hko_source_clock_db",
            "hko_final_source_clock_db",
            "forecast_clock_db",
            "oracle_guard_db",
            "observation_db",
            "forecast_source_db",
            "forecast_archive_db",
            "market_topology_db",
            "settlement_db",
            "venue_event_db",
            "backfill_db",
            "derived_db",
            "io",
            "health_io",
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
    assert by_id["ingest_k2_hko_tick"]["executor"] == "hko_source_clock_db"
    assert (
        by_id["ingest_k2_hko_daily_final"]["executor"]
        == "hko_final_source_clock_db"
    )
    assert by_id["ingest_replacement_availability_poll"]["executor"] == "forecast_clock_db"
    assert by_id["ingest_replacement_maintenance"]["executor"] == "derived_db"
    assert "ingest_day0_metar_commit_retry" not in by_id
    assert by_id["ingest_day0_oracle_anomaly"]["executor"] == "oracle_guard_db"
    assert by_id["ingest_harvester_truth_writer"]["executor"] == "settlement_db"
    assert by_id["ingest_market_scan"]["executor"] == "market_topology_db"
    assert by_id["ingest_k2_forecasts_daily"]["executor"] == "forecast_archive_db"
    assert by_id["ingest_k2_obs_fast_tick"]["executor"] == "observation_db"
    assert by_id["ingest_k2_hourly_instants"]["executor"] == "backfill_db"
    assert by_id["ingest_heartbeat"]["executor"] == "heartbeat"
    assert by_id["ingest_status_rollup"]["executor"] == "health_io"
    assert "ingest_uma_resolution_listener" not in by_id
    assert "ingest_calibration_auto_promote" not in by_id


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
    from src.data.scheduler_adapter import REGISTRY_OWNED_KWARGS

    specs = fld.forecast_live_job_specs(startup_run_date=datetime(2026, 5, 24, tzinfo=timezone.utc))

    # legacy view: id -> (trigger, sorted trigger-only kwargs)
    owned = REGISTRY_OWNED_KWARGS
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
