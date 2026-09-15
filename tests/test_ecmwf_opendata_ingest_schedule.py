# Created: 2026-05-23
# Last reused/audited: 2026-09-14
# Lifecycle: created=2026-05-23; last_reviewed=2026-09-14; last_reused=2026-09-14
# Authority basis: a0d51d480b507f324 root-cause + docs/operations/live_review_may23.md
# Purpose: Regression antibody — ECMWF OpenData cron triggers must fire after safe_fetch windows for both 00z and 12z cycles.
# Reuse: Run when forecast_live_daemon.py cron schedule or source_release_calendar.yaml safe_fetch lag changes.
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

from contextlib import contextmanager
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import sqlite3
import time

import pytest

from src.data.ecmwf_open_data import SOURCE_ID as ECMWF_SOURCE_ID
from src.data.release_calendar import get_entry, cycle_profile_for_hour


def _job_run_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE job_run (
            job_run_id TEXT,
            job_name TEXT,
            source_id TEXT,
            track TEXT,
            scheduled_for TEXT,
            release_calendar_key TEXT,
            recorded_at TEXT,
            finished_at TEXT,
            status TEXT,
            rows_written INTEGER,
            source_run_id TEXT
        )
        """
    )
    return conn


def _insert_job_run(conn: sqlite3.Connection, identity: dict, *, status: str, recorded_at: datetime, job_run_id: str | None = None) -> None:
    from src.ingest import forecast_live_daemon as daemon

    conn.execute(
        """
        INSERT INTO job_run (
            job_run_id, job_name, source_id, track, scheduled_for,
            release_calendar_key, recorded_at, finished_at, status, rows_written, source_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_run_id or daemon._job_run_id(identity),
            identity["job_name"],
            identity["source_id"],
            identity["track"],
            identity["scheduled_for"].isoformat(),
            identity["release_calendar_key"],
            recorded_at.isoformat(),
            recorded_at.isoformat(),
            status,
            0,
            None,
        ),
    )
    conn.commit()


def test_safe_cycle_poll_detects_release_within_one_minute():
    """Safe-fetch remains the gate; post-release detection is bounded to 60s."""

    from src.ingest import forecast_live_daemon as daemon

    specs = daemon.forecast_live_job_specs(
        startup_run_date=datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    )
    poll_specs = [
        (trigger, kwargs)
        for _fn, trigger, kwargs in specs
        if kwargs.get("id") == daemon.FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID
    ]
    assert poll_specs == [
        (
            "interval",
            {
                "seconds": 60,
                "id": daemon.FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 120,
            },
        )
    ]


def test_safe_cycle_dispatch_does_not_let_one_track_block_its_sibling():
    """A running LOW fetch cannot suppress the next HIGH release check."""

    from src.ingest import forecast_live_daemon as daemon

    completed_high: Future[dict] = Future()
    completed_high.set_result({"status": "current_cycle_already_journaled"})
    running_low: Future[dict] = Future()
    inflight = {
        "mx2t6_high": completed_high,
        "mn2t6_low": running_low,
    }

    class RecordingExecutor:
        def __init__(self):
            self.submitted: list[str] = []

        def submit(self, _runner, track: str) -> Future[dict]:
            self.submitted.append(track)
            return Future()

    executor = RecordingExecutor()
    report = daemon._dispatch_due_opendata_tracks(
        _runner=lambda track: {"status": track},
        _executor=executor,
        _inflight=inflight,
    )

    assert executor.submitted == ["mx2t6_high"]
    assert report == {
        "mx2t6_high": {
            "status": "submitted",
            "previous_status": "current_cycle_already_journaled",
        },
        "mn2t6_low": {"status": "in_flight"},
    }
    assert inflight["mn2t6_low"] is running_low
    assert inflight["mx2t6_high"] is not completed_high


def test_not_released_newest_cycle_retries_one_exact_failed_predecessor(monkeypatch, tmp_path):
    """A newer provider 404 revives one cooled-down exact prior failed cycle."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    older = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 6, tzinfo=timezone.utc),
        now_utc=now,
    )
    assert current["scheduled_for"] > prior["scheduled_for"]
    conn = _job_run_conn()
    _insert_job_run(conn, prior, status="FAILED", recorded_at=now - timedelta(seconds=61))
    _insert_job_run(conn, older, status="FAILED", recorded_at=now - timedelta(seconds=61))
    calls: list[dict] = []

    def run(track, **kwargs):
        calls.append(kwargs)
        if kwargs.get("_identity") is None:
            return {"status": "skipped_not_released", "track": track}
        return {"status": "ok", "track": track, "snapshots_inserted": 7}

    monkeypatch.setattr(daemon, "run_opendata_track", run)
    lock_dir = tmp_path / "locks"
    result = daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _job_conn=conn,
        _locks_dir_override=lock_dir,
        _now_utc=now,
        _poll_deadline_monotonic=time.monotonic() + 10,
    )

    assert result["status"] == "ok"
    assert result["newest_cycle_status"] == "skipped_not_released"
    assert result["retry_debt"]["failed_job_run_id"] == daemon._job_run_id(prior)
    assert len(calls) == 2
    assert "_cycle_deadline_monotonic" not in calls[0]
    assert calls[1]["_identity"] == prior
    assert calls[1]["_locks_dir_override"] == lock_dir
    assert calls[1]["_cycle_deadline_monotonic"] > time.monotonic()


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_safe_poll_not_released_probe_allocates_remaining_window_to_prior_debt(monkeypatch, track):
    """Both tracks give the same poll's remaining budget to their exact prior failure."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity(track, now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        track,
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    conn = _job_run_conn()
    _insert_job_run(conn, prior, status="FAILED", recorded_at=now - timedelta(seconds=61))
    calls: list[dict] = []

    def run(_track, **kwargs):
        calls.append(kwargs)
        return {"status": "partial", "source_run_status": "PARTIAL"}

    monkeypatch.setattr(daemon, "run_opendata_track", run)
    result = daemon._run_opendata_track_if_due(
        track,
        _job_conn=conn,
        _now_utc=now,
        _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_args, **_kwargs: {"status": "not_released"},
    )

    assert current["scheduled_for"] > prior["scheduled_for"]
    assert result["status"] == "partial"
    assert len(calls) == 1
    assert calls[0]["_identity"] == prior
    assert calls[0]["_cycle_deadline_monotonic"] > time.monotonic()
    assert conn.execute("SELECT COUNT(*) AS n FROM job_run").fetchone()["n"] == 1


def test_safe_poll_released_probe_prioritizes_current_cycle_over_older_debt(monkeypatch):
    """A fully evidenced newest index always uses the normal current collector first."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    conn = _job_run_conn()
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon,
        "run_opendata_track",
        lambda _track, **kwargs: calls.append(kwargs) or {"status": "ok"},
    )

    result = daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _job_conn=conn,
        _now_utc=now,
        _use_availability_probe=True,
        _availability_probe=lambda *_args, **_kwargs: {"status": "released"},
    )

    assert result["status"] == "ok"
    assert len(calls) == 1
    assert "_identity" not in calls[0]


def test_safe_poll_unknown_probe_runs_newest_collector_without_authorizing_older_retry(monkeypatch):
    """Slow or unknown availability keeps newest data reachable but never chooses old debt."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    conn = _job_run_conn()
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon,
        "run_opendata_track",
        lambda _track, **kwargs: calls.append(kwargs) or {"status": "skipped_not_released"},
    )

    result = daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _job_conn=conn,
        _now_utc=now,
        _use_availability_probe=True,
        _availability_probe=lambda *_args, **_kwargs: {"status": "unknown", "reason": "NETWORK"},
    )

    assert result["status"] == "skipped_not_released"
    assert result["availability_probe"]["reason"] == "NETWORK"
    assert len(calls) == 1
    assert "_identity" not in calls[0]


@pytest.mark.parametrize(
    ("status", "age_seconds", "job_run_id"),
    (
        ("SUCCESS", 61, None),
        ("FAILED", 30, None),
        ("FAILED", 61, "wrong-exact-identity"),
    ),
)
def test_failed_prior_retry_has_no_success_cooldown_or_identity_debt(
    monkeypatch, status, age_seconds, job_run_id
):
    """A success, active cooldown, or non-exact journal row cannot schedule retry debt."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    conn = _job_run_conn()
    _insert_job_run(
        conn,
        prior,
        status=status,
        recorded_at=now - timedelta(seconds=age_seconds),
        job_run_id=job_run_id,
    )

    assert daemon._retry_identity_for_failed_prior_run(
        conn, current_identity=current, now_utc=now
    ) is None


def test_partial_prior_cycle_remains_retryable_debt_across_polls():
    """A failed cycle that later becomes PARTIAL still needs its next bounded retry."""
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    conn = _job_run_conn()
    _insert_job_run(conn, prior, status="FAILED", recorded_at=now - timedelta(seconds=61))

    first = daemon._retry_identity_for_failed_prior_run(
        conn, current_identity=current, now_utc=now
    )
    assert first is not None and first[0] == prior

    conn.execute(
        "UPDATE job_run SET status = 'PARTIAL', finished_at = ? WHERE job_run_id = ?",
        ((now - timedelta(seconds=61)).isoformat(), daemon._job_run_id(prior)),
    )
    conn.commit()
    second = daemon._retry_identity_for_failed_prior_run(
        conn, current_identity=current, now_utc=now
    )

    assert second is not None and second[0] == prior


def test_locked_older_retry_preserves_failed_debt_for_the_next_poll(monkeypatch):
    """A fallback lock miss is not allowed to replace the older failed journal row."""
    from src.data import job_lock
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    conn = _job_run_conn()
    _insert_job_run(conn, prior, status="FAILED", recorded_at=now - timedelta(seconds=61))

    @contextmanager
    def lock_held(*_args, **_kwargs):
        yield False, "opendata_track:mx2t6_high"

    monkeypatch.setattr(job_lock, "acquire_opendata_track_lock", lock_held)
    result = daemon.run_opendata_track(
        "mx2t6_high",
        _identity=prior,
        _job_conn=conn,
        _now_utc=now,
        _source_paused=lambda _source: False,
        _collector=lambda **_kwargs: pytest.fail("lock-held fallback must not collect"),
    )

    assert result["status"] == "skipped_lock_held"
    assert conn.execute(
        "SELECT status FROM job_run WHERE job_run_id = ?", (daemon._job_run_id(prior),)
    ).fetchone()["status"] == "FAILED"
    assert daemon._retry_identity_for_failed_prior_run(
        conn, current_identity=current, now_utc=now
    )[0] == prior


def test_production_retry_rechecks_older_calendar_eligibility_after_newest_probe(monkeypatch):
    """An older cycle that expires during the newest probe is not retried on stale time."""
    from src.ingest import forecast_live_daemon as daemon

    poll_started = datetime(2026, 8, 25, 17, 59, tzinfo=timezone.utc)
    after_probe = datetime(2026, 8, 25, 18, 1, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=poll_started)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=poll_started,
    )
    conn = _job_run_conn()
    _insert_job_run(
        conn,
        prior,
        status="FAILED",
        recorded_at=poll_started - timedelta(seconds=61),
    )
    calls: list[dict] = []
    clock_values = iter((poll_started, after_probe))
    monkeypatch.setattr(daemon, "_utcnow", lambda: next(clock_values))
    monkeypatch.setattr(
        daemon,
        "run_opendata_track",
        lambda _track, **kwargs: calls.append(kwargs) or {"status": "skipped_not_released"},
    )

    result = daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _job_conn=conn,
        _poll_deadline_monotonic=time.monotonic() + 10,
    )

    assert current["scheduled_for"] > prior["scheduled_for"]
    assert result["status"] == "skipped_not_released"
    assert len(calls) == 1


@pytest.mark.parametrize("decision", ("STALE_BLOCKED", "HORIZON_OUT_OF_RANGE"))
def test_failed_prior_retry_rejects_stale_or_unauthorized_calendar_candidate(monkeypatch, decision):
    """Persisted failure never bypasses current release/authorization evaluation."""
    from src.data.release_calendar import FetchDecision
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 8, 24, 23, 31, tzinfo=timezone.utc)
    current = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    prior = daemon._forecast_work_identity_for_cycle(
        "mx2t6_high",
        cycle_time=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        now_utc=now,
    )
    conn = _job_run_conn()
    _insert_job_run(conn, prior, status="FAILED", recorded_at=now - timedelta(seconds=61))
    rejected = {**prior, "decision": FetchDecision[decision]}
    monkeypatch.setattr(daemon, "_forecast_work_identity_for_cycle", lambda *_args, **_kwargs: rejected)

    assert daemon._retry_identity_for_failed_prior_run(
        conn, current_identity=current, now_utc=now
    ) is None


# ---------------------------------------------------------------------------
# Helpers: derive safe_fetch windows from the live release calendar
# ---------------------------------------------------------------------------

def _safe_fetch_utc(cycle_hour: int, track: str = "mx2t6_high") -> datetime:
    """Return the earliest UTC time at which a same-day cycle can be fetched.

    Uses config/source_release_calendar.yaml — reads the full-horizon
    cycle profile for the given track and adds default_lag_minutes.

    Parameters
    ----------
    cycle_hour : 0 or 12
    track : release-calendar track id, e.g. "mx2t6_high" or "mn2t6_low"
    """
    entry = get_entry(ECMWF_SOURCE_ID, track)
    assert entry is not None, f"release calendar entry missing for {ECMWF_SOURCE_ID} / {track}"
    profile = cycle_profile_for_hour(entry, cycle_hour)
    assert profile is not None, f"no cycle profile for hour {cycle_hour} in track {track}"
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

    @pytest.mark.parametrize("track_label,calendar_track,job_id_00z,job_id_12z", [
        (
            "mx2t6",
            "mx2t6_high",
            "forecast_live_opendata_daily_mx2t6",
            "forecast_live_opendata_daily_mx2t6_12z",
        ),
        (
            "mn2t6",
            "mn2t6_low",
            "forecast_live_opendata_daily_mn2t6",
            "forecast_live_opendata_daily_mn2t6_12z",
        ),
    ])
    def test_cron_trigger_after_00z_safe_fetch(self, track_label, calendar_track, job_id_00z, job_id_12z):
        """At least one cron trigger for the 00z job fires >= 08:05 UTC (safe_fetch_at)."""
        safe_fetch_00z = _safe_fetch_utc(0, track=calendar_track)
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

    @pytest.mark.parametrize("track_label,calendar_track,job_id_00z,job_id_12z", [
        (
            "mx2t6",
            "mx2t6_high",
            "forecast_live_opendata_daily_mx2t6",
            "forecast_live_opendata_daily_mx2t6_12z",
        ),
        (
            "mn2t6",
            "mn2t6_low",
            "forecast_live_opendata_daily_mn2t6",
            "forecast_live_opendata_daily_mn2t6_12z",
        ),
    ])
    def test_cron_trigger_after_12z_safe_fetch(self, track_label, calendar_track, job_id_00z, job_id_12z):
        """At least one cron trigger for the 12z job fires >= 20:05 UTC (safe_fetch_at)."""
        safe_fetch_12z = _safe_fetch_utc(12, track=calendar_track)
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

@pytest.mark.parametrize("track", ["mx2t6_high", "mn2t6_low"])
def test_safe_fetch_calendar_sanity(track):
    """Confirm safe_fetch windows are 08:05 UTC (00z) and 20:05 UTC (12z) for both tracks.

    If these values change in source_release_calendar.yaml, the tests above
    adjust automatically. This test documents the current expected values
    and catches unintended calendar edits or track divergence.
    """
    sf_00z = _safe_fetch_utc(0, track=track)
    sf_12z = _safe_fetch_utc(12, track=track)
    # 00z + 485 min = 08:05 UTC
    assert sf_00z.hour == 8 and sf_00z.minute == 5, (
        f"[{track}] Expected 00z safe_fetch at 08:05 UTC, got {sf_00z.strftime('%H:%M')} UTC. "
        f"source_release_calendar.yaml may have changed."
    )
    # 12z + 485 min = 20:05 UTC
    assert sf_12z.hour == 20 and sf_12z.minute == 5, (
        f"[{track}] Expected 12z safe_fetch at 20:05 UTC, got {sf_12z.strftime('%H:%M')} UTC. "
        f"source_release_calendar.yaml may have changed."
    )
