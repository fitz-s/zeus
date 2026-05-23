# Created: 2026-05-23
# Last reused/audited: 2026-05-23
# Authority basis: a0d51d480b507f324 root-cause + docs/operations/live_review_may23.md
"""Regression test: ECMWF OpenData cron triggers must fire AFTER each cycle's
safe_fetch window opens.

Root cause (commit a0d51d480b507f324 / live_review_may23.md):
    forecast_live_daemon registered a single 07:30 UTC cron for the 00z run.
    The safe_fetch window for 00z opens at 00:00 + 485 min = 08:05 UTC.
    07:30 < 08:05 → evaluate_safe_fetch returns SKIPPED_NOT_RELEASED
    → collect_open_ens_cycle falls back to yesterday's 12z run
    → primary ECMWF issue_time ~20h stale at the 14:00 UTC US open
    → GFS-vs-ECMWF delta > 18h tolerance → crosscheck_unavailable
    → ALL non-day0 trades blocked.

Fix: two cron triggers per track —
    08:10 UTC  (catches same-day 00z; safe window opens 08:05 UTC)
    20:10 UTC  (catches same-day 12z; safe window opens 20:05 UTC)

This test MUST fail against the old 07:30 single-trigger schedule
and MUST pass after the fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.ecmwf_open_data import SOURCE_ID as ECMWF_SOURCE_ID
from src.data.release_calendar import get_entry, cycle_profile_for_hour


# ---------------------------------------------------------------------------
# Helpers: derive safe_fetch windows from the live release calendar
# ---------------------------------------------------------------------------

def _safe_fetch_utc(cycle_hour: int) -> datetime:
    """Return the earliest UTC time at which a same-day cycle can be fetched.

    Uses config/source_release_calendar.yaml — reads the full-horizon
    cycle profile for hour 0 or 12 and adds default_lag_minutes.
    """
    entry = get_entry(ECMWF_SOURCE_ID, "mx2t6_high")
    assert entry is not None, "release calendar entry missing for ecmwf_open_data / mx2t6_high"
    profile = cycle_profile_for_hour(entry, cycle_hour)
    assert profile is not None, f"no cycle profile for hour {cycle_hour}"
    base = datetime(2000, 1, 1, cycle_hour, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=profile.default_lag_minutes)


# ---------------------------------------------------------------------------
# Core invariant: every cron trigger for mx2t6 and mn2t6 fires AT OR AFTER
# the safe_fetch window for its associated cycle.
# ---------------------------------------------------------------------------

class TestOpenDataCronAfterSafeFetch:
    """For both tracks and both live cycles (00z, 12z), at least one registered
    cron trigger exists that fires at or after the safe_fetch window opens."""

    def _cron_specs_for_job_ids(self, *job_ids: str) -> list[dict]:
        """Return kwargs dicts for all cron-trigger specs whose id is in job_ids."""
        from src.ingest.forecast_live_daemon import forecast_live_job_specs

        specs = forecast_live_job_specs(
            startup_run_date=datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
        )
        return [
            kwargs
            for _fn, trigger, kwargs in specs
            if trigger == "cron" and kwargs.get("id") in job_ids
        ]

    def _trigger_utc_minutes_since_midnight(self, cron_kwargs: dict) -> int:
        """Return minutes-since-midnight UTC for a cron trigger dict."""
        h = cron_kwargs.get("hour", 0)
        m = cron_kwargs.get("minute", 0)
        return h * 60 + m

    @pytest.mark.parametrize("track_label,job_id_00z,job_id_12z", [
        (
            "mx2t6",
            "forecast_live_opendata_daily_mx2t6",
            "forecast_live_opendata_daily_mx2t6_12z",
        ),
        (
            "mn2t6",
            "forecast_live_opendata_daily_mn2t6",
            "forecast_live_opendata_daily_mn2t6_12z",
        ),
    ])
    def test_cron_trigger_after_00z_safe_fetch(self, track_label, job_id_00z, job_id_12z):
        """At least one cron trigger for the 00z job fires >= 08:05 UTC (safe_fetch_at)."""
        safe_fetch_00z = _safe_fetch_utc(0)
        safe_minutes_00z = safe_fetch_00z.hour * 60 + safe_fetch_00z.minute

        cron_specs = self._cron_specs_for_job_ids(job_id_00z)
        assert cron_specs, (
            f"{track_label}: no cron spec found with id={job_id_00z!r}. "
            f"Expected a 00z trigger registered in forecast_live_job_specs()."
        )
        trigger_minutes = [
            self._trigger_utc_minutes_since_midnight(s) for s in cron_specs
        ]
        assert any(m >= safe_minutes_00z for m in trigger_minutes), (
            f"{track_label} 00z cron fires at {trigger_minutes} UTC-minutes "
            f"but safe_fetch window opens at {safe_minutes_00z} UTC-minutes "
            f"(= {safe_fetch_00z.strftime('%H:%M')} UTC). "
            f"Cron is too early — will always get SKIPPED_NOT_RELEASED → falls back to "
            f"yesterday's 12z → staleness > 18h → ALL non-day0 trades blocked."
        )

    @pytest.mark.parametrize("track_label,job_id_00z,job_id_12z", [
        (
            "mx2t6",
            "forecast_live_opendata_daily_mx2t6",
            "forecast_live_opendata_daily_mx2t6_12z",
        ),
        (
            "mn2t6",
            "forecast_live_opendata_daily_mn2t6",
            "forecast_live_opendata_daily_mn2t6_12z",
        ),
    ])
    def test_cron_trigger_after_12z_safe_fetch(self, track_label, job_id_00z, job_id_12z):
        """At least one cron trigger for the 12z job fires >= 20:05 UTC (safe_fetch_at)."""
        safe_fetch_12z = _safe_fetch_utc(12)
        safe_minutes_12z = safe_fetch_12z.hour * 60 + safe_fetch_12z.minute

        cron_specs = self._cron_specs_for_job_ids(job_id_12z)
        assert cron_specs, (
            f"{track_label}: no cron spec found with id={job_id_12z!r}. "
            f"Expected a 12z trigger registered in forecast_live_job_specs(). "
            f"Without this, same-day 12z data is never ingested."
        )
        trigger_minutes = [
            self._trigger_utc_minutes_since_midnight(s) for s in cron_specs
        ]
        assert any(m >= safe_minutes_12z for m in trigger_minutes), (
            f"{track_label} 12z cron fires at {trigger_minutes} UTC-minutes "
            f"but safe_fetch window opens at {safe_minutes_12z} UTC-minutes "
            f"(= {safe_fetch_12z.strftime('%H:%M')} UTC). "
            f"Cron is too early — will always get SKIPPED_NOT_RELEASED for same-day 12z."
        )


# ---------------------------------------------------------------------------
# Sanity: safe_fetch times round-trip correctly from the live calendar
# ---------------------------------------------------------------------------

def test_safe_fetch_calendar_sanity():
    """Confirm safe_fetch windows are 08:05 UTC (00z) and 20:05 UTC (12z).

    If these values change in source_release_calendar.yaml, the test above
    adjusts automatically. This test documents the current expected values
    and catches unintended calendar edits.
    """
    sf_00z = _safe_fetch_utc(0)
    sf_12z = _safe_fetch_utc(12)
    # 00z + 485 min = 08:05 UTC
    assert sf_00z.hour == 8 and sf_00z.minute == 5, (
        f"Expected 00z safe_fetch at 08:05 UTC, got {sf_00z.strftime('%H:%M')} UTC. "
        f"source_release_calendar.yaml may have changed."
    )
    # 12z + 485 min = 20:05 UTC
    assert sf_12z.hour == 20 and sf_12z.minute == 5, (
        f"Expected 12z safe_fetch at 20:05 UTC, got {sf_12z.strftime('%H:%M')} UTC. "
        f"source_release_calendar.yaml may have changed."
    )
