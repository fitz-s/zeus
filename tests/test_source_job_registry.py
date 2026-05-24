# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Tests that the job registry mirrors the scheduler + efficiency audit flags.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR3);
#   operator spec §"Job registry" + §4 (ownership map).
"""Relationship tests for the job registry + inventory/audit CLIs (PR3, advisory).

Key antibody: the registry must MIRROR the scheduler — a scheduled add_job id that is not
declared in JOB_REGISTRY fails --check, so the inventory can never silently go stale.
"""
from __future__ import annotations


def test_every_scheduled_job_is_registered() -> None:
    """ANTIBODY: every id passed to .add_job() (or a *_JOB_ID constant) in the two daemons
    must be declared in JOB_REGISTRY. This is the registry-mirrors-reality gate."""
    from scripts.data_collection_inventory import _scheduled_job_ids
    from src.data.source_job_registry import JOB_REGISTRY

    scheduled = _scheduled_job_ids()
    assert scheduled, "expected to extract scheduled job ids from the daemon modules"
    missing = scheduled - set(JOB_REGISTRY)
    assert not missing, f"scheduled jobs missing from registry: {sorted(missing)}"


def test_inventory_check_passes() -> None:
    """The --check CLI returns 0 when the registry covers every scheduled job."""
    from scripts.data_collection_inventory import cmd_check

    assert cmd_check() == 0


def test_all_data_collection_add_jobs_across_ingest_forecast_and_main_are_registered() -> None:
    """PR #329 review B acceptance: scan ALL THREE daemons — ingest_main, forecast_live_daemon,
    AND src/main — and require every data-collection job to be registered. For src/main the
    data-collection set is declared (not inferred); each declared id must be in JOB_REGISTRY.
    RED before this PR: src/main collectors (market_discovery/venue_heartbeat/harvester/wu_daily)
    were not in JOB_REGISTRY at all."""
    from scripts.data_collection_inventory import (
        _SRC_MAIN_DATA_COLLECTION_JOB_IDS,
        _scheduled_job_ids,
    )
    from src.data.source_job_registry import JOB_REGISTRY

    # ingest_main + forecast_live: every scheduled id registered (strict mirror)
    assert _scheduled_job_ids() <= set(JOB_REGISTRY)
    # src/main declared data-collection jobs: all registered
    assert _SRC_MAIN_DATA_COLLECTION_JOB_IDS <= set(JOB_REGISTRY), (
        f"src/main data-collection jobs missing from registry: "
        f"{sorted(_SRC_MAIN_DATA_COLLECTION_JOB_IDS - set(JOB_REGISTRY))}"
    )
    # the long-running user-WS ingestor (not an add_job) is covered too:
    assert "user_ws_ingestor" in JOB_REGISTRY
    assert JOB_REGISTRY["user_ws_ingestor"].dispatch_kind == "long_running"


def test_src_main_partition_requires_every_scheduled_job_classified() -> None:
    """ANTIBODY (advisor point 4): every scheduled add_job id in src/main must be in exactly one
    of {data-collection (registered), explicit non-collection}. A new unclassified job fails
    --check, forcing a conscious decision — so a future data collector added to the trading daemon
    can't silently escape registry coverage."""
    from scripts.data_collection_inventory import (
        _SRC_MAIN_DATA_COLLECTION_JOB_IDS,
        _SRC_MAIN_FILE,
        _SRC_MAIN_NON_COLLECTION_JOB_IDS,
        _scheduled_ids_in,
        _src_main_partition_violations,
    )

    assert _src_main_partition_violations() == []
    # the partition is total over src/main's statically-resolvable scheduled ids:
    scheduled = _scheduled_ids_in((_SRC_MAIN_FILE,))
    known = _SRC_MAIN_DATA_COLLECTION_JOB_IDS | _SRC_MAIN_NON_COLLECTION_JOB_IDS
    assert scheduled <= known, f"unclassified src/main jobs: {sorted(scheduled - known)}"
    # execution/chain ops are explicitly NON-collection (not falsely registered as collectors):
    assert {"redeem_submitter", "wrap_submitter", "deployment_freshness"} <= _SRC_MAIN_NON_COLLECTION_JOB_IDS


def test_dict_unpacked_forecast_live_ids_are_extracted() -> None:
    """ANTIBODY (PR #329 review P1): forecast_live_daemon schedules via
    ``add_job(func, trigger, **kwargs)`` where the id lives inside a job-spec DICT
    (``{"id": CONST, "max_instances": 1, ...}``), not as an add_job keyword. The id extractor
    must harvest those — otherwise --check is blind to all forecast-live drift and reports a
    false-clean mirror. Lock that the eight forecast_live ids are detected as SCHEDULED."""
    from scripts.data_collection_inventory import _scheduled_job_ids
    from src.ingest.forecast_live_daemon import FORECAST_LIVE_JOB_IDS

    scheduled = _scheduled_job_ids()
    missing = set(FORECAST_LIVE_JOB_IDS) - scheduled
    assert not missing, (
        f"dict-unpacked forecast_live ids NOT extracted (regex/keyword-only blind spot): "
        f"{sorted(missing)}"
    )


def test_audit_flags_fast_executor_db_writer() -> None:
    """The efficiency audit must surface the UMA listener (DB write on the file-only fast
    executor) — the audit-confirmed structural fault."""
    from scripts.data_collection_efficiency_audit import run_audit
    from src.data.source_job_registry import fast_executor_db_writers

    writers = {j.job_id for j in fast_executor_db_writers()}
    assert "ingest_uma_resolution_listener" in writers

    faults = run_audit()
    assert any("fast_executor_db_writer" in f and "uma_resolution_listener" in f for f in faults)


def test_file_only_fast_jobs_not_flagged_as_db_writers() -> None:
    """Heartbeat / status-rollup / source-health on fast are file-only and must NOT be
    flagged as DB writers."""
    from src.data.source_job_registry import fast_executor_db_writers

    flagged = {j.job_id for j in fast_executor_db_writers()}
    for ok in ("ingest_heartbeat", "ingest_status_rollup", "ingest_source_health_probe"):
        assert ok not in flagged


def test_opendata_producers_span_both_daemons() -> None:
    """OpenData live producers are declared in BOTH daemons (env-gated at runtime). The audit
    surfaces this so PR4 can enforce a runtime singleton."""
    from src.data.source_job_registry import opendata_owners

    owners = opendata_owners()
    daemons = {j.owner_daemon for j in owners}
    assert daemons == {"ingest_main", "forecast_live_daemon"}
    assert all(j.owner_gated for j in owners), "OpenData producers must be env-gated"


def test_job_registry_uses_canonical_source_ids() -> None:
    """F8: job registry source_id must be the canonical data-source ID (wu_icao_history), not a
    short alias (wu_icao), so a future join to source contracts/frontier does not lose it."""
    from src.data.source_job_registry import JOB_REGISTRY

    daily = JOB_REGISTRY["ingest_k2_daily_obs"]
    assert daily.source_id == "wu_icao_history"
    # no remaining short 'wu_icao' alias:
    assert not any(j.source_id == "wu_icao" for j in JOB_REGISTRY.values())


def test_all_source_ids_includes_primary_and_secondaries() -> None:
    """F8: all_source_ids unions primary source_id + source_ids (dedup, single field to read)."""
    from src.data.source_job_registry import JOB_REGISTRY

    scan = JOB_REGISTRY["ingest_market_scan"]
    assert set(scan.all_source_ids) == {"polymarket_gamma", "polymarket_clob"}
    daily = JOB_REGISTRY["ingest_k2_daily_obs"]
    assert daily.all_source_ids == ("wu_icao_history",)
    obs = JOB_REGISTRY["ingest_k2_obs_v2"]
    assert set(obs.all_source_ids) == {"wu_icao_history", "ogimet_metar"}
