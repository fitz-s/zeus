from __future__ import annotations

# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: zero-trade root-cause 2026-06-09 (iron rule 1). The
#   replacement-forecast cutover flag ``disable_legacy_opendata_forecast_live_jobs``
#   silently disabled the SOLE producer of the FSR trigger's source and froze the
#   live pipeline for ~2 days with no alarm.
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
# This test fails loudly the moment someone re-disables the opendata producer in
# the committed settings — turning a silent 2-day freeze into a CI red.

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_committed_settings_keep_opendata_fsr_producer_enabled():
    """The committed settings MUST keep the opendata baseline producer enabled,
    because the FSR trigger / EDLI reactor consume its source_run and nothing else
    produces it. (The replacement forecast is overlay-only — see header.)"""
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    shadow = settings.get("replacement_forecast_shadow", {})
    assert isinstance(shadow, dict), "replacement_forecast_shadow section missing"
    disabled = shadow.get("disable_legacy_opendata_forecast_live_jobs", False)
    assert disabled is False, (
        "disable_legacy_opendata_forecast_live_jobs=True starves the FSR trigger: it "
        "kills the SOLE producer (ecmwf_open_data source_run/ensemble_snapshots) that "
        "ForecastSnapshotReadyTrigger consumes. The replacement forecast is an overlay "
        "and produces no source_run — it cannot replace the baseline as the FSR source."
    )


def test_active_forecast_live_jobs_include_opendata_producers_under_committed_settings(monkeypatch):
    """Runtime check: under the committed settings (and no disabling env override),
    the daemon's active-job set MUST include the opendata producer cron jobs, not the
    heartbeat-only set. This tests the actual gate function, not just the raw JSON."""
    from src.ingest import forecast_live_daemon as fld

    # Ensure the env override is not what makes (or breaks) this assertion.
    monkeypatch.delenv(fld.FORECAST_LIVE_DISABLE_OPENDATA_ENV, raising=False)

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
