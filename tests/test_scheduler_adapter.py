# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Executor-class assignment (no DB writer on file-only executor; UMA->backfill_db).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6);
#   operator spec §7 (Scheduler adapter / executor classes).
"""PR6: registry -> scheduler executor-class assignment (pure planner, daemon wiring deferred)."""
from __future__ import annotations


def test_registry_scheduler_is_default_legacy_is_opt_out(monkeypatch) -> None:
    """PR #329 review A: the registry-built scheduler is now the DEFAULT (replacement is real, not
    deferred). Legacy is reachable only via an explicit opt-out."""
    from src.data import scheduler_adapter as sa

    for var in (sa.DATA_COLLECTION_MODE_FLAG, sa.LEGACY_DATA_COLLECTION_FLAG, sa.SCHEDULER_REGISTRY_FLAG):
        monkeypatch.delenv(var, raising=False)
    assert sa.data_collection_mode() == "registry"          # default
    assert sa.registry_scheduler_active() is True

    monkeypatch.setenv(sa.LEGACY_DATA_COLLECTION_FLAG, "1")  # explicit rollback
    assert sa.data_collection_mode() == "legacy"
    assert sa.registry_scheduler_active() is False

    monkeypatch.delenv(sa.LEGACY_DATA_COLLECTION_FLAG, raising=False)
    monkeypatch.setenv(sa.DATA_COLLECTION_MODE_FLAG, "legacy")
    assert sa.data_collection_mode() == "legacy"


def test_registry_scheduler_and_legacy_scheduler_are_mutually_exclusive(monkeypatch) -> None:
    """PR #329 review F: registry and legacy modes cannot both be active. Contradictory env fails
    fast at boot (you must pick exactly one), and an invalid mode value is rejected."""
    import pytest

    from src.data import scheduler_adapter as sa

    # registry mode + legacy-force flag = contradiction
    monkeypatch.setenv(sa.DATA_COLLECTION_MODE_FLAG, "registry")
    monkeypatch.setenv(sa.LEGACY_DATA_COLLECTION_FLAG, "1")
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        sa.assert_single_collection_mode()

    # legacy mode + old registry-enable flag = contradiction
    monkeypatch.setenv(sa.DATA_COLLECTION_MODE_FLAG, "legacy")
    monkeypatch.delenv(sa.LEGACY_DATA_COLLECTION_FLAG, raising=False)
    monkeypatch.setenv(sa.SCHEDULER_REGISTRY_FLAG, "1")
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        sa.assert_single_collection_mode()

    # invalid mode value rejected
    monkeypatch.delenv(sa.SCHEDULER_REGISTRY_FLAG, raising=False)
    monkeypatch.setenv(sa.DATA_COLLECTION_MODE_FLAG, "banana")
    with pytest.raises(RuntimeError, match="invalid"):
        sa.assert_single_collection_mode()


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
        assert j["executor"] in ("live_db", "backfill_db", "derived_db", "io", "heartbeat")
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

    cfg = dict(settings._data.get("replacement_forecast_shadow", {}))
    cfg["disable_legacy_opendata_forecast_live_jobs"] = False
    monkeypatch.setitem(settings._data, "replacement_forecast_shadow", cfg)

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

    cfg = dict(settings._data.get("replacement_forecast_shadow", {}))
    cfg["disable_legacy_opendata_forecast_live_jobs"] = False
    monkeypatch.setitem(settings._data, "replacement_forecast_shadow", cfg)

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
