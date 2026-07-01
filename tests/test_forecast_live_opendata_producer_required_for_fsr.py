from __future__ import annotations

# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: zero-trade root-cause 2026-06-09 (iron rule 1). The
#   a replacement-forecast cutover disabled the SOLE producer of the FSR trigger's
#   source and froze the live pipeline for ~2 days with no alarm.
#
# RELATIONSHIP ANTIBODY (Fitz immune-system principle: make the error CATEGORY
# unconstructable, not the single instance).
#
# The cross-module coupling this test pins:
#
#   opendata forecast-live jobs  ──produce──▶  source_run / source_run_coverage /
#       (mx2t6_high, mn2t6_low)               ensemble_snapshots  (source_id =
#                                             'ecmwf_open_data')
#                                                     │
#                                                     ▼  consumes (sole source)
#                                  ForecastSnapshotReadyTrigger.scan_committed_snapshots
#                                                     │
#                                                     ▼ emits FORECAST_SNAPSHOT_READY
#                                            EDLI reactor → live decisions
#
# The BAYES_PRECISION_FUSION replacement forecast is an OVERLAY authority: it produces soft-anchor
# posteriors + a live-authority readiness that DEPENDS ON the baseline_b0
# (ecmwf_open_data) source_run — it writes NO source_run / ensemble_snapshots of
# its own. Therefore the replacement can never substitute for the opendata baseline
# as the FSR trigger source. Disabling the opendata producer while the FSR/reactor
# pipeline is live is a starvation guarantee, not a cutover.
#
def test_active_forecast_live_jobs_include_opendata_producers():
    """The daemon's active-job set must include the opendata producer cron jobs."""
    from src.ingest import forecast_live_daemon as fld

    active = fld._active_forecast_live_job_ids()
    required_producers = {
        fld.FORECAST_LIVE_DAILY_HIGH_JOB_ID,
        fld.FORECAST_LIVE_DAILY_HIGH_12Z_JOB_ID,
        fld.FORECAST_LIVE_DAILY_LOW_JOB_ID,
        fld.FORECAST_LIVE_DAILY_LOW_12Z_JOB_ID,
        fld.FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
        fld.FORECAST_LIVE_STARTUP_JOB_ID,
    }
    missing = sorted(required_producers - set(active))
    assert not missing, (
        "opendata FSR producer jobs absent from the active forecast-live job set: "
        f"{missing}. The FSR trigger / reactor would starve (no fresh ecmwf_open_data "
        "source_run). Active set was: " + repr(sorted(active))
    )
