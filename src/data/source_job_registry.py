# Created: 2026-05-24
# Last reused or audited: 2026-06-08 (Step 4 CLEANUP: purge the stale ``wu_daily`` references
#   from the module docstrings — the order-daemon WU collector spec was deleted by §8 Step 4,
#   so the doc must not still advertise a ``main``-owned wu_daily job; data-ingest's
#   ingest_k2_daily_obs is the sole live WU owner. Earlier same-day: P3 lift repointed the
#   user-WS ingestor dispatch-kind doc-comment from src.main to src.ingest.price_channel_ingest.)
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   + docs/architecture/system_decomposition_plan.md §8 Step 3 (P3 price-channel lift)
#   (Job registry) + §4 (scheduler/ownership map); docs/operations/current/plans/data_temporal_kernel/PLAN.md;
#   extracted from src/ingest_main.py + src/ingest/forecast_live_daemon.py add_job() calls (2026-05-24).
"""Machine-readable inventory of every data-collection job — PR3 + PR #329 review B.

SCOPE: this registry now covers ALL THREE daemons' data-collection jobs —
``src/ingest_main.py``, ``src/ingest/forecast_live_daemon.py`` (the K2 ingest daemons) AND the
data-collection jobs of ``src/main.py`` (the trading daemon): ``market_discovery`` (Gamma
topology + CLOB executable snapshots), ``venue_heartbeat``, the user-WS ingestor (a long-running
thread, ``dispatch_kind='long_running'``), ``harvester``. (The order daemon's ``wu_daily``
collector was REMOVED by system_decomposition_plan §8 Step 4 — data-ingest's ``ingest_k2_daily_obs``
is now the SOLE live owner of WU daily observations; no ``main``-owned WU spec remains.) The
trading-cycle modes
(opening_hunt / day0_capture / imminent_open_capture / update_reaction) and the execution/chain
ops (redeem_* / wrap_* / deployment_freshness / heartbeat) are NOT data collection and are NOT
registered here — they are the explicit non-collection set in data_collection_inventory.py.

COVER vs BUILD (PR #329 review A): registry coverage of ``src/main`` is for inventory + frontier
+ singleton enforcement only. ``src/main`` keeps its hand-coded trading scheduler — only the two
ingest daemons build their scheduler FROM this registry. The trading loop is never rebuilt.

This registry declares, in one typed place, every data-collection job across the three daemons —
owner, role, current executor, whether it writes a DB, source(s), family, dispatch kind — so:

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

Role = Literal["live", "backfill", "settlement", "derived", "diagnostic"]
Executor = Literal["default", "fast"]  # the CURRENT two APScheduler executors in ingest_main
# How the daemon dispatches the job (PR #329 review B; advisor point 2). The user-WS ingestor is
# NOT an add_job — it is a long-running thread. As of the process-topology refactor
# (system_decomposition_plan §8 Step 3, P3, 2026-06-08) it was LIFTED out of the order daemon
# into src/ingest/price_channel_ingest._start_user_channel_ingestor_if_enabled (started by the
# P3 price-channel-ingest daemon), so frontier/singleton coverage must model it as a
# long_running thread on the price_channel daemon, not pretend it is a scheduled src.main job.
DispatchKind = Literal["scheduled", "long_running", "startup"]

# Data family for cross-source coverage (PR #329 review C). Each family has its OWN truth table /
# freshness / readiness semantics — the frontier federates over these, it does NOT force every
# family onto the forecast source_run model.
Family = Literal[
    "forecast", "observation", "solar", "market_topology", "executable_market",
    "venue_user_ws", "settlement", "diagnostic",
]


@dataclass(frozen=True)
class SourceJobSpec:
    """One scheduled job's declared identity + classification (mirror of current reality)."""

    job_id: str
    owner_daemon: str                       # "ingest_main" | "forecast_live_daemon" | "main"
    role: Role
    current_executor: Executor
    writes_db: bool                         # audited: does the tick write a canonical DB?
    source_id: Optional[str] = None         # primary data source (singular convenience accessor)
    source_ids: tuple[str, ...] = ()        # ALL canonical sources this job touches (PR review #329 H);
                                            # multi-source jobs (obs_v2, market_scan) declare several
    callable_ref: Optional[str] = None      # function name in the owner module
    file_only: bool = False                 # writes only files/JSON (safe for fast/io executor)
    owner_gated: bool = False               # registration is conditional (e.g. OpenData ownership env)
    misfire_grace_time: Optional[int] = None  # real APScheduler grace (sec); None = adapter default
    dispatch_kind: DispatchKind = "scheduled"  # scheduled add_job | long_running thread | startup one-shot
    family: Optional[Family] = None         # data family (coverage/frontier federation key, PR #329 C)
    # COVER vs BUILD at job grain (2026-06-11): False = the owner daemon schedules this job
    # ITSELF (dedicated executor lane / custom trigger; e.g. the replacement-forecast
    # production jobs on the replacement_download/replacement_production lanes) and the
    # registry covers it for inventory/frontier/singleton ONLY. Such jobs are excluded from
    # the registry-BUILD expected set (expected_registry_job_ids) and from build_job_specs —
    # otherwise the boot assert counts them as "expected but not built" and refuses to boot
    # the daemon (built 8 vs expected 12 -> total forecast-collection outage).
    registry_built: bool = True
    notes: str = ""

    @property
    def all_source_ids(self) -> tuple[str, ...]:
        """Canonical de-duplicated source set (PR review #329 H/R3): primary source_id plus any
        secondary source_ids, so a consumer reads ONE field regardless of single/multi-source."""
        ordered = (*self.source_ids, *(( self.source_id,) if self.source_id else ()))
        return tuple(dict.fromkeys(ordered))


# ---------------------------------------------------------------------------
# CURRENT inventory. Extracted from add_job() calls 2026-05-24. Keep in sync when
# scheduler jobs change (the inventory --check CLI fails when a scheduled id is missing here).
# ---------------------------------------------------------------------------

_INGEST_MAIN: tuple[SourceJobSpec, ...] = (
    SourceJobSpec("ingest_k2_daily_obs", "ingest_main", "live", "default", True,
                  source_id="wu_icao_history", callable_ref="_k2_daily_obs_tick", family="observation"),
    SourceJobSpec("ingest_k2_hourly_instants", "ingest_main", "backfill", "default", True,
                  callable_ref="_k2_hourly_instants_tick", family="observation",
                  notes="rolling archive of completed local days; isolated from live_db so "
                        "Open-Meteo retries cannot starve current-source guards"),
    SourceJobSpec("ingest_k2_solar_daily", "ingest_main", "derived", "default", True,
                  source_id="openmeteo_archive", callable_ref="_k2_solar_daily_tick", family="solar"),
    SourceJobSpec("ingest_k2_forecasts_daily", "ingest_main", "live", "default", True,
                  callable_ref="_k2_forecasts_daily_tick", family="forecast"),
    SourceJobSpec("ingest_k2_hole_scanner", "ingest_main", "backfill", "default", True,
                  callable_ref="_k2_hole_scanner_tick"),
    SourceJobSpec("ingest_k2_obs", "ingest_main", "live", "default", True,
                  source_ids=("wu_icao_history", "ogimet_metar"),
                  callable_ref="_k2_obs_tick", family="observation",
                  notes="multi-source: WU ICAO + Ogimet METAR via tier router"),
    SourceJobSpec("ingest_k2_hko_tick", "ingest_main", "live", "default", True,
                  source_id="hko_daily_api", callable_ref="_k2_hko_tick", family="observation",
                  notes="job id ingest_k2_hko_tick (aligned to callable by upstream #324 HKO "
                        "job-id boot-crash fix); callable _k2_hko_tick"),
    SourceJobSpec("ingest_k2_obs_fast_tick", "ingest_main", "live", "default", True,
                  source_ids=("wu_icao_history", "ogimet_metar"),
                  callable_ref="_k2_obs_fast_tick", family="observation",
                  notes="day0 obs fast lane (Option C, 2026-06-12): 15-min METAR tick for "
                        "cities inside their [local-midnight, peak+6h] trading window; "
                        "advisory lock 'obs_fast' (separate from 'obs'); registration was "
                        "missed at deploy -> registry guard crash-looped data-ingest until "
                        "this entry (boot RuntimeError job-set mismatch)"),
    SourceJobSpec("ingest_day0_metar_source_clock", "ingest_main", "live", "default", True,
                  source_id="aviationweather_metar",
                  callable_ref="_day0_metar_source_clock_tick", family="observation",
                  misfire_grace_time=10,
                  notes="5s default source-clock batch poll; HTTP precedes the bounded live "
                        "world-writer attempt, unchanged publication identities perform no DB "
                        "work, and committed Day0 extreme events wake the canonical reactor"),
    SourceJobSpec("ingest_day0_metar_commit_retry", "ingest_main", "live", "default", True,
                  source_id="aviationweather_metar",
                  callable_ref="_day0_metar_commit_retry_tick", family="observation",
                  misfire_grace_time=1,
                  registry_built=False,
                  notes="contention-triggered one-shot retry of an already-fetched Day0 METAR "
                        "canonical write; performs no network I/O and runs on source_clock_db"),
    SourceJobSpec("ingest_day0_oracle_anomaly", "ingest_main", "live", "default", True,
                  source_ids=("aviationweather_metar", "wu_icao_history"),
                  callable_ref="_day0_oracle_anomaly_tick", family="observation",
                  misfire_grace_time=30,
                  notes="10s round-robin WU-vs-METAR guard; reads the source-clock process "
                        "cache without another AWC request and persists only changed guard actions"),
    SourceJobSpec("ingest_etl_recalibrate", "ingest_main", "derived", "default", True,
                  callable_ref="_etl_recalibrate"),
    SourceJobSpec("ingest_harvester_truth_writer", "ingest_main", "settlement", "default", True,
                  source_id="polymarket_gamma", callable_ref="_harvester_truth_writer_tick", family="settlement"),
    SourceJobSpec("ingest_automation_analysis", "ingest_main", "derived", "default", True,
                  callable_ref="_automation_analysis_cycle"),
    SourceJobSpec("ingest_oracle_snapshot", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_oracle_snapshot_tick", file_only=True, family="diagnostic",
                  misfire_grace_time=600,
                  notes="daily oracle-time snapshot listener; file-only raw/oracle_time_snapshots output"),
    SourceJobSpec("ingest_oracle_bridge", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_bridge_oracle_tick", file_only=True, family="diagnostic",
                  misfire_grace_time=600,
                  notes="daily oracle-to-calibration artifact bridge; file-only oracle artifact outputs"),
    SourceJobSpec("ingest_oracle_bridge_startup_catch_up", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_bridge_oracle_startup_catch_up", file_only=True, family="diagnostic",
                  dispatch_kind="startup",
                  notes="boot catch-up for oracle bridge when snapshot artifacts are newer"),
    SourceJobSpec("ingest_replacement_availability_poll", "ingest_main", "live", "default", True,
                  source_ids=("ecmwf_open_data", "openmeteo_ecmwf_ifs_9km"),
                  callable_ref="_replacement_availability_poll_tick", family="forecast",
                  misfire_grace_time=120,
                  notes="operator directive 2026-06-11: weather downloading lives in ITS OWN "
                        "daemon (data-ingest), decoupled from forecast-live/trading restarts. "
                        "Probe-resolved anchor raw-input fetch + bayes_precision_fusion extras; first fire "
                        "IMMEDIATE at boot (next_run_time=now), then on the fast source-clock cadence "
                        "(default 15s; ZEUS_REPLACEMENT_AVAILABILITY_POLL_SECONDS override); "
                        "unchanged-source maintenance is isolated on ingest_replacement_maintenance."),
    SourceJobSpec("ingest_replacement_maintenance", "ingest_main", "derived", "default", True,
                  callable_ref="_replacement_maintenance_tick", family="forecast",
                  misfire_grace_time=120,
                  notes="minute-bounded current-target repair and broad reseed catch-up; isolated "
                        "from the 15s replacement publication clock on derived_db"),
    SourceJobSpec("ingest_opendata_daily_mx2t6", "ingest_main", "live", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_mx2t6_cycle", owner_gated=True,
                  misfire_grace_time=3600, family="forecast",
                  notes="registered only when ingest_main owns OpenData (ZEUS_FORECAST_LIVE_OWNER!=forecast_live)"),
    SourceJobSpec("ingest_opendata_daily_mn2t6", "ingest_main", "live", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_mn2t6_cycle", owner_gated=True,
                  misfire_grace_time=3600, family="forecast",
                  notes="registered only when ingest_main owns OpenData"),
    SourceJobSpec("ingest_tigge_archive_backfill", "ingest_main", "backfill", "default", True,
                  source_id="tigge", callable_ref="_tigge_archive_backfill_cycle", misfire_grace_time=3600),
    SourceJobSpec("ingest_k2_startup_catch_up", "ingest_main", "backfill", "default", True,
                  callable_ref="_k2_startup_catch_up"),
    SourceJobSpec("ingest_tigge_startup_catch_up", "ingest_main", "backfill", "default", True,
                  source_id="tigge", callable_ref="_tigge_startup_catch_up"),
    SourceJobSpec("ingest_opendata_startup_catch_up", "ingest_main", "backfill", "default", True,
                  source_id="ecmwf_open_data", callable_ref="_opendata_startup_catch_up", owner_gated=True),
    SourceJobSpec("ingest_source_health_probe", "ingest_main", "diagnostic", "fast", False,
                  callable_ref="_source_health_probe_tick", file_only=True, family="diagnostic",
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
                  family="settlement",
                  notes="AUDIT FLAG: writes DB (record_resolution) on the file-only 'fast' executor; "
                        "historical UMA era (pre-2026-02-21). PR8 moves DB-write off fast."),
    SourceJobSpec("ingest_etl_forecast_skill", "ingest_main", "derived", "default", True,
                  callable_ref="_etl_forecast_skill_tick"),
    SourceJobSpec("ingest_market_scan", "ingest_main", "live", "default", True,
                  source_id="polymarket_gamma", source_ids=("polymarket_gamma", "polymarket_clob"),
                  callable_ref="_market_scan_tick", family="market_topology",
                  notes="multi-source: Gamma topology + CLOB snapshots"),
    SourceJobSpec("ingest_calibration_auto_promote", "ingest_main", "derived", "default", True,
                  callable_ref="_calibration_auto_promote_tick"),
    SourceJobSpec("ingest_artifact_refit", "ingest_main", "derived", "default", False,
                  callable_ref="_artifact_refit_tick", file_only=True,
                  notes="weekly Mon 06:00 UTC walk-forward refit of the four fitted serving "
                        "artifacts (source-clock weights, staleness variance, shape-age sigma, "
                        "ens member dependence); fitter subprocesses are read-only over "
                        "zeus-forecasts.db and write only state/<name>/ artifact + ACTIVE.json; "
                        "consumers hot-reload on pointer mtime"),
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
    # Replacement-forecast production jobs (operator directive 2026-06-11: moved to this
    # daemon from src/main so downloads never share a lifecycle with trading restarts).
    # registry_built=False: these are scheduled by _register_replacement_forecast_production_
    # jobs on dedicated single-worker executor lanes ("replacement_download" /
    # "replacement_production") in EVERY scheduler branch — they are NOT built from the
    # registry, so they must not enter the boot-assert expected set. Registered here for
    # the inventory mirror (--check) + frontier/singleton coverage only.
    SourceJobSpec("replacement_forecast_download", "forecast_live_daemon", "live", "default", False,
                  source_ids=("openmeteo_ecmwf_ifs_9km",),
                  callable_ref="_replacement_forecast_download_job",
                  misfire_grace_time=3600, family="forecast", registry_built=False,
                  notes="probe-resolved OpenMeteo anchor raw-input pre-fetch; cron at publish times; "
                        "runs on dedicated replacement_download executor lane"),
    SourceJobSpec("replacement_forecast_download_startup_catch_up", "forecast_live_daemon", "backfill", "default", False,
                  source_ids=("openmeteo_ecmwf_ifs_9km",),
                  callable_ref="_replacement_forecast_download_job",
                  dispatch_kind="startup", family="forecast", registry_built=False,
                  notes="one-shot date trigger 90s after boot; same download job as cron path"),
    SourceJobSpec("replacement_forecast_live_materialize", "forecast_live_daemon", "live", "default", True,
                  callable_ref="_replacement_forecast_materialize_poll_job",
                  misfire_grace_time=120, family="forecast", registry_built=False,
                  notes="fast explicit seed/request drain only; dedicated "
                        "replacement_production lane"),
    SourceJobSpec("replacement_forecast_live_discovery", "forecast_live_daemon", "live", "default", False,
                  callable_ref="_replacement_forecast_discovery_job",
                  misfire_grace_time=120, family="forecast", registry_built=False,
                  notes="periodic recovery discovery on already-downloaded manifests; isolated "
                        "on the lower-priority replacement_download lane"),
    SourceJobSpec("anchor_meta_stamp_cross_check", "forecast_live_daemon", "diagnostic", "default", False,
                  source_ids=("openmeteo_ecmwf_ifs_9km",),
                  callable_ref="_anchor_meta_stamp_cross_check_job",
                  misfire_grace_time=600, family="forecast", registry_built=False,
                  notes="hourly belt-and-suspenders: re-verify meta-stamped anchor artifacts "
                        "against single-runs API (K4.0b(f)); file-writes only, no DB write"),
)

# ---------------------------------------------------------------------------
# src/main.py — the TRADING daemon. PR #329 review B: registry must cover its data-collection
# jobs too (it previously excluded them). These are COVERED for inventory/frontier/singleton —
# src/main keeps its hand-coded scheduler (only ingest_main + forecast_live build-from-registry;
# the trading loop is NOT rebuilt). Only the operator-listed data-collection surfaces are here:
# market_discovery, venue_heartbeat, user-WS, harvester. (wu_daily was REMOVED — §8 Step 4 dedup;
# data-ingest's ingest_k2_daily_obs is the sole live WU owner.) The trading-cycle modes
# (opening_hunt/day0_capture/imminent_open_capture/update_reaction) and the execution/chain ops
# (redeem_*/wrap_*/deployment_freshness/heartbeat) are NOT data collection — they are declared as
# the explicit non-collection set in scripts/data_collection_inventory.py, not registered here.
# ---------------------------------------------------------------------------
_SRC_MAIN: tuple[SourceJobSpec, ...] = (
    # PROCESS-TOPOLOGY REFACTOR P2 (2026-06-08, system_decomposition_plan §8 Step 1):
    # market_discovery was LIFTED out of the order daemon (`main`) into its own process,
    # the P2 substrate observer. owner_daemon is repointed to "substrate_observer" so the
    # data_collection_inventory orphan-check resolves _market_discovery_cycle against the
    # new daemon (src/data/substrate_observer.py via src/ingest/substrate_observer_daemon.py)
    # instead of the now-callable-less src/main.py.
    SourceJobSpec("market_discovery", "substrate_observer", "live", "default", True,
                  source_id="polymarket_gamma", source_ids=("polymarket_gamma", "polymarket_clob"),
                  callable_ref="_market_discovery_cycle", family="executable_market",
                  notes="Gamma topology + CLOB executable-market snapshots -> trade DB "
                        "(lifted to the P2 substrate-observer daemon, 2026-06-08)"),
    SourceJobSpec("venue_heartbeat", "main", "live", "default", False,
                  source_id="polymarket_clob", callable_ref="_start_venue_heartbeat_loop_if_needed",
                  family="venue_user_ws", file_only=True,
                  notes="CLOB auth/readiness/venue status loop starter; writes venue-status state"),
    # PROCESS-TOPOLOGY REFACTOR P4 (2026-06-08, system_decomposition_plan §8 Step 2):
    # the harvester resolver was LIFTED out of the order daemon (`main`) into the P4
    # post-trade-capital daemon. owner_daemon repointed to "post_trade_capital" so the
    # data_collection_inventory orphan-check resolves _harvester_cycle against the new daemon
    # (src/execution/post_trade_capital.py via src/ingest/post_trade_capital_daemon.py)
    # instead of the now-import-only src/main.py reference.
    SourceJobSpec("harvester", "post_trade_capital", "derived", "default", True,
                  source_id="polymarket_gamma", callable_ref="_harvester_cycle", family=None,
                  notes="trading-side P&L resolver: READS forecasts.settlements (produced by "
                        "ingest_harvester_truth_writer) + writes decision_log. CONSUMER of settlement "
                        "truth, NOT a producer — so it does not own the settlement family (verified "
                        "2026-05-24: producer/consumer split, not dual-production). Lifted to the P4 "
                        "post-trade-capital daemon, 2026-06-08."),
    # PROCESS-TOPOLOGY REFACTOR STEP 4 (2026-06-08, system_decomposition_plan §8 Step 4): the
    # `main`-owned wu_daily SourceJobSpec (callable_ref="_wu_daily_dispatch") was REMOVED — the
    # order daemon no longer collects WU daily observations. It was a VERIFIED-DUPLICATE of the
    # data-ingest collector (ingest_k2_daily_obs -> daily_obs_append.daily_tick), which remains
    # the SOLE live owner of the (observation, wu_icao_history) family/source. Keeping a stale
    # spec here would mirror a deleted callable (an orphan the data_collection_inventory
    # mirror-gate surfaces), so the entry is deleted, not repointed. The OPERATOR OWNERSHIP
    # DECISION that was pending in _KNOWN_OPEN_DUPLICATE_LIVE_OWNERS (remove main.wu_daily vs add
    # a lock to run_wu_daily_dispatch) is hereby RESOLVED as "ingest_main owns WU daily".
    # PROCESS-TOPOLOGY REFACTOR P3 (2026-06-08, system_decomposition_plan §8 Step 3): the
    # user-channel WS ingestor was LIFTED out of the order daemon (`main`) into its own
    # process, the P3 price-channel-ingest daemon. owner_daemon is repointed to
    # "price_channel" so the data_collection_inventory orphan-check resolves
    # _start_user_channel_ingestor_if_enabled against the new daemon
    # (src/ingest/price_channel_ingest.py via src/ingest/price_channel_daemon.py). The WS
    # thread is the ws_gap_guard latch WRITER; lifting it kills the reduce_only-forever
    # latch in the order daemon (§9).
    SourceJobSpec("user_ws_ingestor", "price_channel", "live", "default", True,
                  source_id="polymarket_user_ws", callable_ref="_start_user_channel_ingestor_if_enabled",
                  dispatch_kind="long_running", family="venue_user_ws",
                  notes="long-running THREAD (not add_job): user-channel WS -> market_events order/trade "
                        "facts (lifted to the P3 price-channel-ingest daemon, 2026-06-08)"),
)

JOB_REGISTRY: dict[str, SourceJobSpec] = {
    j.job_id: j for j in (*_INGEST_MAIN, *_FORECAST_LIVE, *_SRC_MAIN)
}


def jobs_by_owner(owner_daemon: str) -> list[SourceJobSpec]:
    return [j for j in JOB_REGISTRY.values() if j.owner_daemon == owner_daemon]


def fast_executor_db_writers() -> list[SourceJobSpec]:
    """Jobs on the 'fast' (file-only) executor that nonetheless write a DB — a structural fault.

    The 'fast' executor is documented file-only (so DB jobs don't starve heartbeats behind the
    single-writer lock). Any job here with writes_db=True violates that contract.
    """
    return [j for j in JOB_REGISTRY.values() if j.current_executor == "fast" and j.writes_db]


def live_producing_jobs() -> list[SourceJobSpec]:
    """Jobs that LIVE-produce a data family (role live or settlement; not backfill/startup).

    Excludes owner_gated jobs (OpenData): those are intentionally registered on BOTH ingest
    daemons but are mutually exclusive at runtime via active_opendata_owner / assert_opendata_
    singleton — the OpenData singleton already guarantees one live owner, so they must not be
    double-counted as a cross-family duplicate here (PR #329 review E)."""
    return [
        j for j in JOB_REGISTRY.values()
        if j.role in ("live", "settlement")
        and not j.owner_gated
        and j.dispatch_kind != "startup"
        and not j.job_id.endswith("startup_catch_up")
    ]


# Duplicate-live-owner classification (PR #329 review E). A (family, source_id) produced live by
# >1 daemon is one of three things. We keep TWO explicit allow-lists so the check stays honest:
#
#   _ACKNOWLEDGED_SAFE — verified NOT a violation (idempotent-by-design, no authority conflict).
#       Empty today: the only family-grain candidates were either a producer/consumer split
#       (main.harvester reads settlement, does not produce it -> family=None) or distinct primary
#       families (market_scan=market_topology vs market_discovery=executable_market).
#
#   _KNOWN_OPEN — a REAL active duplicate that is NOT yet fixed, tracked here with its verdict so
#       it is surfaced (not silently passed) while an ownership decision is pending. Putting a dup
#       here is an admission of a live bug, never an excuse — every entry must name the decision.
#
# Anything in NEITHER list is an UNTRACKED violation and fails the E gate fail-closed.
_ACKNOWLEDGED_SAFE_DUPLICATE_LIVE_OWNERS: dict[tuple[str, str], str] = {
    # ingest_main.ingest_replacement_availability_poll probes provider publication state and
    # triggers lightweight extras fetches; forecast_live_daemon.replacement_forecast_download
    # does the OpenMeteo anchor raw-input pre-fetch.  Both touch openmeteo_ecmwf_ifs_9km as a
    # source, but they are complementary operations on a shared source — not a competing
    # authority conflict.  The poll runs in the data-ingest daemon (independent of trading
    # restarts); the download runs in the forecast-live daemon on a dedicated lane.  Verified
    # safe 2026-06-11: probe→download separation was the explicit operator directive that
    # decoupled the ~365MB download from the 5-min materialize cycle.
    ("forecast", "openmeteo_ecmwf_ifs_9km"): (
        "ACKNOWLEDGED_SAFE: ingest_main.ingest_replacement_availability_poll (probe+extras) + "
        "forecast_live_daemon.replacement_forecast_download (heavy pre-fetch) are complementary "
        "operations on the same source; operator directive 2026-06-11 explicitly split them."
    ),
}

_KNOWN_OPEN_DUPLICATE_LIVE_OWNERS: dict[tuple[str, str], str] = {
    # RESOLVED 2026-06-08 (system_decomposition_plan §8 Step 4): the WU daily active-duplicate
    # — ("observation", "wu_icao_history"), produced live by BOTH ingest_main.ingest_k2_daily_obs
    # AND main.wu_daily -> run_wu_daily_dispatch — is FIXED. main.wu_daily was removed from the
    # order daemon (_wu_daily_dispatch + its add_job + its registry SourceJobSpec deleted). The
    # ownership decision (pending since 2026-05-24) is closed: data-ingest is the SOLE live owner.
    # The wu_icao slice was RE-VERIFIED set-equivalent before removal (identical iteration/filter/
    # gate/target/writer; idempotent writer => zero coverage loss; the per-hour double WU-API
    # fetch + rebuild_run_id clobber are gone). Per the gate's contract, a resolved duplicate
    # vanishes from BOTH the live detection map AND this known-open list, so the entry is removed.
    # Empty again until the next triaged duplicate is logged (an UNTRACKED dup fails the E gate).
}


def duplicate_live_family_owners() -> dict[tuple[str, str], list[str]]:
    """(family, source_id) pairs produced LIVE by >1 distinct owner_daemon (PR #329 review E).

    Returns the FULL map of candidate duplicates (safe + known-open + untracked) keyed by
    (family, source_id) -> sorted distinct owner daemons. Only jobs that declare a ``family``
    are considered (an untyped live job cannot be checked for cross-family ownership)."""
    owners: dict[tuple[str, str], set[str]] = {}
    for j in live_producing_jobs():
        if j.family is None:
            continue
        for src in (j.all_source_ids or ((j.source_id,) if j.source_id else ())):
            owners.setdefault((j.family, src), set()).add(j.owner_daemon)
    return {k: sorted(v) for k, v in owners.items() if len(v) > 1}


def open_duplicate_live_owner_violations() -> dict[tuple[str, str], list[str]]:
    """Detected duplicates that are tracked as KNOWN-OPEN real bugs (pending an ownership fix)."""
    return {
        k: v for k, v in duplicate_live_family_owners().items()
        if k in _KNOWN_OPEN_DUPLICATE_LIVE_OWNERS
    }


def unacknowledged_duplicate_live_owners() -> dict[tuple[str, str], list[str]]:
    """UNTRACKED duplicate live producers — in neither the safe nor the known-open list.

    This is the fail-closed E gate: a NEW (family, source) gaining a second live owner daemon
    fails immediately, forcing a conscious classification (verify safe, track as open bug, or fix).
    Known-open bugs are excluded here (they are surfaced via open_duplicate_live_owner_violations)
    so the gate does not block on an already-triaged issue — but it can never silently pass a NEW
    duplication."""
    tracked = set(_ACKNOWLEDGED_SAFE_DUPLICATE_LIVE_OWNERS) | set(_KNOWN_OPEN_DUPLICATE_LIVE_OWNERS)
    return {k: v for k, v in duplicate_live_family_owners().items() if k not in tracked}


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
