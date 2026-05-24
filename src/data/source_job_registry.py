# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   (Job registry) + §4 (scheduler/ownership map); docs/operations/current/plans/data_temporal_kernel/PLAN.md;
#   extracted from src/ingest_main.py + src/ingest/forecast_live_daemon.py add_job() calls (2026-05-24).
"""Machine-readable inventory of every scheduled data-collection job — PR3 (advisory).

PR3 does NOT replace the scheduler. It declares, in one typed place, every job the two
daemons (``src/ingest_main.py``, ``src/ingest/forecast_live_daemon.py``) currently register —
owner, role, current executor, whether it writes a DB, lock key — so:

  * the inventory CLI can render the source/job/table matrix,
  * the efficiency audit can flag structural problems (a DB writer on the file-only "fast"
    executor; OpenData registered by BOTH daemons; jobs scheduled but unregistered here),
  * PR6 can later GENERATE the APScheduler from this registry instead of hand-coded add_job.

This is a *mirror* of current reality plus a classification. ``current_executor`` records what
the code does today; ``writes_db`` records the audited write behaviour. The gap between them
(e.g. UMA listener on ``fast`` yet writing the DB via record_resolution) is exactly what the
efficiency audit surfaces — it is data, not a runtime change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["live", "backfill", "shadow", "settlement", "derived", "diagnostic"]
Executor = Literal["default", "fast"]  # the CURRENT two APScheduler executors in ingest_main


@dataclass(frozen=True)
class SourceJobSpec:
    """One scheduled job's declared identity + classification (mirror of current reality)."""

    job_id: str
    owner_daemon: str                       # "ingest_main" | "forecast_live_daemon"
    role: Role
    current_executor: Executor
    writes_db: bool                         # audited: does the tick write a canonical DB?
    source_id: Optional[str] = None         # the data source this job serves, if any
    callable_ref: Optional[str] = None      # function name in the owner module
    file_only: bool = False                 # writes only files/JSON (safe for fast/io executor)
    owner_gated: bool = False               # registration is conditional (e.g. OpenData ownership env)
    notes: str = ""


# ---------------------------------------------------------------------------
# CURRENT inventory. Extracted from add_job() calls 2026-05-24. Keep in sync when
# scheduler jobs change (the inventory --check CLI fails when a scheduled id is missing here).
# ---------------------------------------------------------------------------

_INGEST_MAIN: tuple[SourceJobSpec, ...] = (
    SourceJobSpec("ingest_k2_daily_obs", "ingest_main", "live", "default", True,
                  source_id="wu_icao_history", callable_ref="_k2_daily_obs_tick"),
    SourceJobSpec("ingest_k2_hourly_instants", "ingest_main", "live", "default", True,
                  callable_ref="_k2_hourly_instants_tick"),
    SourceJobSpec("ingest_k2_solar_daily", "ingest_main", "derived", "default", True,
                  source_id="openmeteo_archive", callable_ref="_k2_solar_daily_tick"),
    SourceJobSpec("ingest_k2_forecasts_daily", "ingest_main", "live", "default", True,
                  callable_ref="_k2_forecasts_daily_tick"),
    SourceJobSpec("ingest_k2_hole_scanner", "ingest_main", "backfill", "default", True,
                  callable_ref="_k2_hole_scanner_tick"),
    SourceJobSpec("ingest_k2_obs_v2", "ingest_main", "live", "default", True,
                  callable_ref="_k2_obs_v2_tick"),
    SourceJobSpec("ingest_k2_hko_tick", "ingest_main", "live", "default", True,
                  source_id="hko_daily_api", callable_ref="_k2_hko_tick",
                  notes="job id ingest_k2_hko_tick (aligned to callable by upstream #324 HKO "
                        "job-id boot-crash fix); callable _k2_hko_tick"),
    SourceJobSpec("ingest_etl_recalibrate", "ingest_main", "derived", "default", True,
                  callable_ref="_etl_recalibrate"),
    SourceJobSpec("ingest_harvester_truth_writer", "ingest_main", "settlement", "default", True,
                  source_id="polymarket_gamma", callable_ref="_harvester_truth_writer_tick"),
    SourceJobSpec("ingest_automation_analysis", "ingest_main", "derived", "default", True,
                  callable_ref="_automation_analysis_cycle"),
    SourceJobSpec("ingest_opendata_daily_mx2t6", "ingest_main", "live", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_mx2t6_cycle", owner_gated=True,
                  notes="registered only when ingest_main owns OpenData (ZEUS_FORECAST_LIVE_OWNER!=forecast_live)"),
    SourceJobSpec("ingest_opendata_daily_mn2t6", "ingest_main", "live", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_mn2t6_cycle", owner_gated=True,
                  notes="registered only when ingest_main owns OpenData"),
    SourceJobSpec("ingest_tigge_archive_backfill", "ingest_main", "backfill", "default", True,
                  source_id="tigge", callable_ref="_tigge_archive_backfill_cycle"),
    SourceJobSpec("ingest_k2_startup_catch_up", "ingest_main", "backfill", "default", True,
                  callable_ref="_k2_startup_catch_up"),
    SourceJobSpec("ingest_tigge_startup_catch_up", "ingest_main", "backfill", "default", True,
                  source_id="tigge", callable_ref="_tigge_startup_catch_up"),
    SourceJobSpec("ingest_opendata_startup_catch_up", "ingest_main", "backfill", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_startup_catch_up", owner_gated=True),
    SourceJobSpec("ingest_source_health_probe", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_source_health_probe_tick", file_only=True,
                  notes="writes state/source_health.json only"),
    SourceJobSpec("ingest_station_migration_probe", "ingest_main", "backfill", "default", True,
                  callable_ref="_station_migration_probe_tick"),
    SourceJobSpec("ingest_drift_detector", "ingest_main", "derived", "default", True,
                  callable_ref="_drift_detector_tick"),
    SourceJobSpec("ingest_status_rollup", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_ingest_status_rollup_tick", file_only=True),
    SourceJobSpec("ingest_heartbeat", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_write_ingest_heartbeat", file_only=True,
                  notes="writes state/daemon-heartbeat-ingest.json only"),
    SourceJobSpec("ingest_uma_resolution_listener", "ingest_main", "settlement", "fast", True,
                  source_id="polymarket_uma_oo_v2", callable_ref="_uma_resolution_listener_tick",
                  notes="AUDIT FLAG: writes DB (record_resolution) on the file-only 'fast' executor; "
                        "historical UMA era (pre-2026-02-21). PR8 moves DB-write off fast."),
    SourceJobSpec("ingest_etl_forecast_skill", "ingest_main", "derived", "default", True,
                  callable_ref="_etl_forecast_skill_tick"),
    SourceJobSpec("ingest_market_scan", "ingest_main", "live", "default", True,
                  source_id="polymarket_gamma", callable_ref="_market_scan_tick"),
    SourceJobSpec("ingest_oracle_bridge", "ingest_main", "derived", "fast", False,
                  callable_ref="_bridge_oracle_tick", file_only=True,
                  notes="writes data/oracle_error_rates.json artifact; verify file-only (PR8)"),
    SourceJobSpec("ingest_oracle_bridge_startup_catch_up", "ingest_main", "derived", "fast", False,
                  callable_ref="_bridge_oracle_startup_catch_up", file_only=True),
    SourceJobSpec("ingest_calibration_auto_promote", "ingest_main", "derived", "default", True,
                  callable_ref="_calibration_auto_promote_tick"),
)

_FORECAST_LIVE: tuple[SourceJobSpec, ...] = (
    SourceJobSpec("forecast_live_opendata_daily_mx2t6", "forecast_live_daemon", "live", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True, notes="00Z trigger; owns OpenData when ZEUS_FORECAST_LIVE_OWNER=forecast_live"),
    SourceJobSpec("forecast_live_opendata_daily_mx2t6_12z", "forecast_live_daemon", "live", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True, notes="12Z trigger"),
    SourceJobSpec("forecast_live_opendata_daily_mn2t6", "forecast_live_daemon", "live", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True, notes="00Z trigger"),
    SourceJobSpec("forecast_live_opendata_daily_mn2t6_12z", "forecast_live_daemon", "live", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True, notes="12Z trigger"),
    SourceJobSpec("forecast_live_opendata_startup_catch_up", "forecast_live_daemon", "backfill", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True),
    SourceJobSpec("forecast_live_opendata_safe_cycle_poll", "forecast_live_daemon", "live", "default", True,
                  source_id="ecmwf_open_data", owner_gated=True),
    SourceJobSpec("forecast_live_heartbeat", "forecast_live_daemon", "diagnostic", "fast", False,
                  file_only=True, notes="writes forecast-live heartbeat JSON only"),
    SourceJobSpec("forecast_live_source_health_probe", "forecast_live_daemon", "diagnostic", "fast", False,
                  file_only=True),
)

JOB_REGISTRY: dict[str, SourceJobSpec] = {j.job_id: j for j in (*_INGEST_MAIN, *_FORECAST_LIVE)}


def jobs_by_owner(owner_daemon: str) -> list[SourceJobSpec]:
    return [j for j in JOB_REGISTRY.values() if j.owner_daemon == owner_daemon]


def fast_executor_db_writers() -> list[SourceJobSpec]:
    """Jobs on the 'fast' (file-only) executor that nonetheless write a DB — a structural fault.

    The 'fast' executor is documented file-only (so DB jobs don't starve heartbeats behind the
    single-writer lock). Any job here with writes_db=True violates that contract.
    """
    return [j for j in JOB_REGISTRY.values() if j.current_executor == "fast" and j.writes_db]


def opendata_owners() -> list[SourceJobSpec]:
    """OpenData live producers across both daemons (ownership must be a singleton at runtime)."""
    return [
        j for j in JOB_REGISTRY.values()
        if j.source_id == "ecmwf_open_data" and j.role == "live" and not j.job_id.endswith("startup_catch_up")
    ]


# ---------------------------------------------------------------------------
# OpenData ownership authority (PR4). Single source of truth for "which daemon owns
# OpenData live production", mirroring ingest_main's historical env switch. PR4 routes
# ingest_main._ingest_main_owns_opendata through active_opendata_owner so the registry and
# the daemons can never disagree; PR6 generates the scheduler from the same function.
# ---------------------------------------------------------------------------

OPENDATA_FORECAST_LIVE_TOKEN = "forecast_live"


def active_opendata_owner(forecast_live_owner_env: str) -> str:
    """The daemon that owns OpenData live production for the given env value.

    Mirrors ingest_main exactly: env == 'forecast_live' -> 'forecast_live_daemon',
    anything else (incl. unset/default 'ingest_main') -> 'ingest_main'.
    """
    token = (forecast_live_owner_env or "").strip().lower()
    return "forecast_live_daemon" if token == OPENDATA_FORECAST_LIVE_TOKEN else "ingest_main"


def active_opendata_jobs(forecast_live_owner_env: str) -> list[SourceJobSpec]:
    """OpenData live jobs that are ACTIVE under the given owner env (the singleton's set)."""
    owner = active_opendata_owner(forecast_live_owner_env)
    return [j for j in opendata_owners() if j.owner_daemon == owner]


def assert_opendata_singleton(forecast_live_owner_env: str) -> str:
    """Confirm exactly one daemon owns OpenData under this env; return its name.

    Fail-closed: raises RuntimeError if the resolved owner has NO registered OpenData live
    jobs (a mis-registration that would silently leave OpenData unproduced).
    """
    owner = active_opendata_owner(forecast_live_owner_env)
    active = active_opendata_jobs(forecast_live_owner_env)
    if not active:
        all_owners = sorted({j.owner_daemon for j in opendata_owners()})
        raise RuntimeError(
            f"OpenData singleton violation: env owner={owner!r} has no registered OpenData "
            f"live jobs (registry owners={all_owners}). Refusing to proceed with no producer."
        )
    return owner
