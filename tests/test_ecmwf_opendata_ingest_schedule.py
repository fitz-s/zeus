# Created: 2026-05-23
# Last reused/audited: 2026-09-27
# Lifecycle: created=2026-05-23; last_reviewed=2026-09-27; last_reused=2026-09-27
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
from types import SimpleNamespace

import pytest

from src.data.ecmwf_open_data import SOURCE_ID as ECMWF_SOURCE_ID, _forecast_track_for_profile
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
            started_at TEXT,
            lock_acquired_at TEXT,
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
            release_calendar_key, recorded_at, started_at, lock_acquired_at,
            finished_at, status, rows_written, source_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            recorded_at.isoformat(),
            recorded_at.isoformat(),
            status,
            0,
            None,
        ),
    )
    conn.commit()


def _held_revision_coverage(
    conn, track: str, *, now: datetime, cycle: datetime,
    target: str = "2026-09-27", city_name: str = "Hong Kong", city=None,
) -> dict:
    from src.contracts.ensemble_snapshot_provenance import (
        ECMWF_OPENDATA_HIGH_DATA_VERSION_V2,
        ECMWF_OPENDATA_LOW_DATA_VERSION_V2,
        coordinate_bound_data_version,
        opendata_source_run_revision_suffix,
    )
    from src.ingest import forecast_live_daemon as daemon
    from src.config import runtime_cities_by_name
    from src.data.forecast_target_contract import compute_target_local_day_window_utc

    metric = "high" if track == "mx2t6_high" else "low"
    city = city or runtime_cities_by_name()[city_name]
    window = compute_target_local_day_window_utc(
        city_timezone=city.timezone, target_local_date=datetime.fromisoformat(target).date(),
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT, source_id TEXT, track TEXT, release_calendar_key TEXT,
            source_cycle_time TEXT,
            status TEXT, completeness_status TEXT
        );
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            source_run_id TEXT, data_version TEXT, release_calendar_key TEXT,
            city_id TEXT, city TEXT,
            city_timezone TEXT, target_local_date TEXT, temperature_metric TEXT,
            track TEXT, source_id TEXT, source_transport TEXT,
            completeness_status TEXT, readiness_status TEXT, expires_at TEXT,
            target_window_start_utc TEXT, target_window_end_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_scope
            ON source_run_coverage(city_id, city_timezone, target_local_date,
                temperature_metric, source_id, source_transport, data_version);
        """
    )
    old_identity = daemon._forecast_work_identity_for_cycle(track, cycle_time=cycle, now_utc=now)
    forecast_track = _forecast_track_for_profile(
        ingest_track=track,
        horizon_profile=old_identity["release_calendar_key"].rsplit(":", 1)[-1],
    )
    old_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_HIGH_DATA_VERSION_V2 if metric == "high"
        else ECMWF_OPENDATA_LOW_DATA_VERSION_V2, "a" * 64,
    )
    old_run_id = (
        f"ecmwf_open_data:{track}:{cycle:%Y-%m-%dT%HZ}:coordsha:{'a' * 64}"
        f"{opendata_source_run_revision_suffix(old_version)}"
    )
    conn.execute(
        "INSERT INTO source_run VALUES (?, 'ecmwf_open_data', ?, ?, ?, 'SUCCESS', 'COMPLETE')",
        (old_run_id, forecast_track, old_identity["release_calendar_key"], cycle.isoformat()),
    )
    conn.execute(
        """INSERT INTO source_run_coverage VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?,
         'ecmwf_open_data', 'ensemble_snapshots_db_reader', 'COMPLETE',
         'LIVE_ELIGIBLE', ?, ?, ?)""",
        (old_run_id, old_version, old_identity["release_calendar_key"],
         city.name.upper().replace(" ", "_"), city_name, city.timezone,
         target, metric, forecast_track, (now + timedelta(hours=5)).isoformat(),
         window.start_utc.isoformat(), window.end_utc.isoformat()),
    )
    _insert_job_run(conn, old_identity, status="SUCCESS", recorded_at=now - timedelta(hours=1), job_run_id="old-v2-success")
    return {"source_run_id": old_run_id, "metric": metric, "forecast_track": forecast_track}


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_revision_migration_refetches_exact_old_complete_cycle(monkeypatch, track):
    """Old v2 SUCCESS is a cycle hint, not a replacement for fresh v3 proof."""
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    old_cycle = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(conn, track, now=now, cycle=old_cycle)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    assert newest["scheduled_for"] > old_cycle
    _insert_job_run(conn, newest, status="SUCCESS", recorded_at=now)
    conn.execute(
        "UPDATE job_run SET rows_written = 1, source_run_id = ? WHERE job_run_id = ?",
        (daemon._expected_source_run_id(newest), daemon._job_run_id(newest)),
    )
    # A journal entry for the older migration may be recorded later than the
    # newest cycle; it must not make the newest cycle look unjournaled.
    conn.execute("UPDATE job_run SET recorded_at = ? WHERE job_run_id = 'old-v2-success'",
        ((now + timedelta(seconds=1)).isoformat(),))
    assert daemon._latest_job_run_current_for_identity(conn, newest)[0]
    calls = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok", "source_run_id": daemon._expected_source_run_id(kw["_identity"])},
    )
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
    )
    assert result["status"] == "ok"
    assert result["revision_migration_debt"]["old_source_run_id"] == old["source_run_id"]
    assert len(calls) == 1
    assert calls[0]["_identity"]["scheduled_for"] == old_cycle
    assert calls[0]["_identity"]["data_version"].endswith("__coordsha_" + calls[0]["_identity"]["coordinate_manifest_sha"])
    assert daemon._expected_source_run_id(calls[0]["_identity"]).endswith(
        ":high_boundary_land_grid_v3" if old["metric"] == "high" else ":low_window_land_grid_v3"
    )


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_migration_poll_bounds_selection_not_whole_collector(monkeypatch, tmp_path, track):
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="SUCCESS", recorded_at=now)
    conn.execute(
        "UPDATE job_run SET rows_written = 1, source_run_id = ? WHERE job_run_id = ?",
        (daemon._expected_source_run_id(newest), daemon._job_run_id(newest)),
    )
    collected: list[dict] = []
    poll_deadline = time.monotonic() + 0.5

    def collector(**kwargs):
        collected.append(kwargs)
        # The selected native cycle may outlive the short scheduling poll;
        # the collector still owns its ordinary per-step/extract deadlines.
        time.sleep(max(0.0, poll_deadline - time.monotonic()) + 0.01)
        return {"status": "download_failed", "reason": "TEST_NO_NETWORK"}

    monkeypatch.setattr(daemon, "_write_job_run", lambda *_args, **_kwargs: None)
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now,
        _locks_dir_override=tmp_path / "locks",
        _collector=collector, _source_paused=lambda _: False,
        _poll_deadline_monotonic=poll_deadline,
    )
    assert result["revision_migration_debt"]["old_source_run_id"] == old["source_run_id"]
    assert len(collected) == 1
    assert collected[0]["run_date"].isoformat() == "2026-09-26"
    assert collected[0]["run_hour"] == 12
    assert "cycle_deadline_monotonic" not in collected[0]
    assert time.monotonic() >= poll_deadline

    collected.clear()
    # A decision deadline still prevents starting the migration at all.
    assert daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now,
        _locks_dir_override=tmp_path / "locks",
        _collector=collector, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() - 1,
    )["status"] == "current_cycle_already_journaled"
    assert not collected

    # Caller-supplied collector deadlines remain honored; only poll-derived
    # limits must not be imposed on a full native ENS source run.
    explicit_deadline = time.monotonic() + 10
    daemon.run_opendata_track(
        track, _identity=newest,
        _locks_dir_override=tmp_path / "locks",
        _collector=collector, _source_paused=lambda _: False,
        _cycle_deadline_monotonic=explicit_deadline,
    )
    assert collected[-1]["cycle_deadline_monotonic"] == explicit_deadline


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("latest_status", ("FAILED", "PARTIAL"))
def test_held_migration_gets_next_turn_after_real_latest_failure(
    monkeypatch, track, latest_status,
):
    """An eligible newest cycle can fail forever; its held predecessor still gets a turn."""
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    old_cycle = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(conn, track, now=now, cycle=old_cycle)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(
        conn, newest, status=latest_status, recorded_at=now - timedelta(seconds=5),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok"},
    )
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_a, **_k: {"status": "released"},
    )
    assert result["revision_migration_debt"]["old_source_run_id"] == old["source_run_id"]
    assert len(calls) == 1
    assert calls[0]["_identity"]["scheduled_for"] == old_cycle


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_newest_release_gets_first_turn_even_with_held_migration_debt(
    monkeypatch, track,
):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok"},
    )
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_a, **_k: {"status": "released"},
    )
    assert result["status"] == "ok"
    assert len(calls) == 1
    assert "_identity" not in calls[0]


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_prelock_failure_is_not_a_collector_turn(monkeypatch, track):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="FAILED", recorded_at=now - timedelta(seconds=5))
    conn.execute(
        "UPDATE job_run SET lock_acquired_at = NULL WHERE job_run_id = ?",
        (daemon._job_run_id(newest),),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok"},
    )
    daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_a, **_k: {"status": "released"},
    )
    assert len(calls) == 1
    assert "_identity" not in calls[0]


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("migration_status", ("FAILED", "PARTIAL"))
def test_migration_prelock_failure_does_not_permanently_suppress_held_turn(
    monkeypatch, track, migration_status,
):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="FAILED", recorded_at=now - timedelta(seconds=5))
    migration, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    _insert_job_run(
        conn, migration, status=migration_status,
        recorded_at=now - timedelta(seconds=61),
    )
    conn.execute(
        "UPDATE job_run SET lock_acquired_at = NULL WHERE job_run_id = ?",
        (daemon._job_run_id(migration),),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok"},
    )
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
    )
    assert result["revision_migration_debt"]["old_source_run_id"] == old["source_run_id"]
    assert len(calls) == 1
    assert calls[0]["_identity"] == migration


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("bad_field", ("lock_acquired_at", "finished_at", "inverted"))
def test_malformed_migration_collector_clock_cannot_suppress_held_turn(monkeypatch, track, bad_field):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="FAILED", recorded_at=now - timedelta(seconds=5))
    migration, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    _insert_job_run(
        conn, migration, status="FAILED", recorded_at=now - timedelta(seconds=61),
    )
    if bad_field == "inverted":
        conn.execute(
            "UPDATE job_run SET lock_acquired_at = ?, finished_at = ? WHERE job_run_id = ?",
            ((now - timedelta(seconds=60)).isoformat(),
             (now - timedelta(seconds=61)).isoformat(), daemon._job_run_id(migration)),
        )
    else:
        conn.execute(
            f"UPDATE job_run SET {bad_field} = 'not-a-clock' WHERE job_run_id = ?",
            (daemon._job_run_id(migration),),
        )
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "ok"},
    )
    daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
    )
    assert len(calls) == 1
    assert calls[0]["_identity"] == migration


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_revision_migration_uses_actual_short_profile_track(monkeypatch, track):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 11, 30, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 6, tzinfo=timezone.utc),
    )
    assert old["forecast_track"] == f"{track}_short_horizon"
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    migration, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    assert migration["scheduled_for"] == datetime(2026, 9, 26, 6, tzinfo=timezone.utc)
    conn.execute(
        "UPDATE source_run SET track = ? WHERE source_run_id = ?",
        (f"{track}_full_horizon", old["source_run_id"]),
    )
    conn.execute(
        "UPDATE source_run_coverage SET track = ? WHERE source_run_id = ?",
        (f"{track}_full_horizon", old["source_run_id"]),
    )
    assert daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    ) is None  # A short release key cannot certify a falsely full-marked source.


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_revision_migration_prioritizes_earlier_local_day_deadline(monkeypatch, track):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    cape = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 18, tzinfo=timezone.utc),
        city_name="Cape Town",
    )
    hong_kong = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Cape Town", "2026-09-27", cape["metric"]): 0,
        ("Hong Kong", "2026-09-27", hong_kong["metric"]): 0,
        ("Hong Kong", "invalid-date", hong_kong["metric"]): 0,
    })
    candidate, debt = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    assert debt["city"] == "Hong Kong"  # Ends at 16Z; Cape Town at 22Z.
    assert candidate["scheduled_for"] == datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def test_held_revision_migration_uses_utc_deadline_before_calendar_date(monkeypatch):
    from src.config import runtime_cities_by_name
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.ingest import forecast_live_daemon as daemon
    import src.config as config

    now = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)
    cycle = datetime(2026, 9, 26, 6, tzinfo=timezone.utc)
    conn = _job_run_conn()
    pago = SimpleNamespace(name="Pago", timezone="Pacific/Pago_Pago")
    kir = SimpleNamespace(name="Kiritimati", timezone="Pacific/Kiritimati")
    manifest_json = config.runtime_coordinate_manifest_json()
    cities = {**runtime_cities_by_name(), pago.name: pago, kir.name: kir}
    monkeypatch.setattr(config, "runtime_cities_by_name", lambda: cities)
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: manifest_json)
    _held_revision_coverage(
        conn, "mx2t6_high", now=now, cycle=cycle,
        target="2026-09-26", city_name=pago.name, city=pago,
    )
    _held_revision_coverage(
        conn, "mx2t6_high", now=now, cycle=cycle,
        target="2026-09-27", city_name=kir.name, city=kir,
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        (pago.name, "2026-09-26", "high"): 0,  # Ends 9/27 11Z.
        (kir.name, "2026-09-27", "high"): 0,  # Ends 9/27 10Z.
    })
    result = daemon._held_revision_migration_identity(
        conn, track="mx2t6_high", now_utc=now,
        deadline_monotonic=time.monotonic() + 10,
    )
    assert result is not None
    assert result[1]["city"] == kir.name


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_earliest_held_revision_cooldown_reserves_its_turn(monkeypatch, track):
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    earliest = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    later = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 18, tzinfo=timezone.utc),
        city_name="Cape Town",
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", earliest["metric"]): 0,
        ("Cape Town", "2026-09-27", later["metric"]): 0,
    })
    migration, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    _insert_job_run(conn, migration, status="FAILED", recorded_at=now - timedelta(seconds=20))
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="PARTIAL", recorded_at=now - timedelta(seconds=5))
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track", lambda _track, **kwargs: calls.append(kwargs) or {"status": "ok"},
    )

    cooled = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now,
        _source_paused=lambda _: False, _poll_deadline_monotonic=time.monotonic() + 10,
    )
    assert cooled["status"] == "revision_migration_retry_not_due"
    assert cooled["revision_migration_debt"]["old_source_run_id"] == earliest["source_run_id"]
    assert not calls  # Neither a later held cycle nor latest PARTIAL jumps this due turn.

    due = now + timedelta(seconds=41)
    selected = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=due,
        _source_paused=lambda _: False, _poll_deadline_monotonic=time.monotonic() + 10,
    )
    assert selected["revision_migration_debt"]["old_source_run_id"] == earliest["source_run_id"]
    assert calls[0]["_identity"]["scheduled_for"] == datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    conn.execute(
        "UPDATE source_run_coverage SET expires_at = ? WHERE source_run_id = ?",
        ((now - timedelta(seconds=1)).isoformat(), earliest["source_run_id"]),
    )
    next_eligible = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    assert next_eligible is not None
    assert next_eligible[1]["old_source_run_id"] == later["source_run_id"]


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("latest_status", ("SUCCESS", "PARTIAL", "FAILED"))
def test_held_cooldown_preserves_newest_vs_migration_turn_order(
    monkeypatch, track, latest_status,
):
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    conn = _job_run_conn()
    earliest = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
    )
    later = _held_revision_coverage(
        conn, track, now=now, cycle=datetime(2026, 9, 26, 18, tzinfo=timezone.utc),
        city_name="Cape Town",
    )
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", earliest["metric"]): 0,
        ("Cape Town", "2026-09-27", later["metric"]): 0,
    })
    migration, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    _insert_job_run(conn, migration, status="FAILED", recorded_at=now - timedelta(seconds=20))
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(
        conn, newest, status=latest_status,
        recorded_at=now - timedelta(seconds=80 if latest_status != "SUCCESS" else 5),
    )
    if latest_status == "SUCCESS":
        conn.execute(
            "UPDATE job_run SET rows_written = 1, source_run_id = ? WHERE job_run_id = ?",
            (daemon._expected_source_run_id(newest), daemon._job_run_id(newest)),
        )
    calls: list[dict] = []
    monkeypatch.setattr(daemon, "run_opendata_track",
        lambda _track, **kwargs: calls.append(kwargs) or {"status": "ok"})
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now,
        _source_paused=lambda _: False, _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_a, **_k: {"status": "released"},
    )
    if latest_status == "SUCCESS":
        assert result["status"] == "revision_migration_retry_not_due"
        assert result["revision_migration_debt"]["old_source_run_id"] == earliest["source_run_id"]
        assert not calls
    else:
        # Migration just took the more recent genuine collector turn. The
        # newest terminal failure is owed the next one, even during cooldown.
        assert result["status"] == "ok"
        assert len(calls) == 1 and "_identity" not in calls[0]


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_migration_failure_yields_next_turn_to_latest(monkeypatch, track):
    """Use existing exact job attempts for alternating fairness, not a new latch."""
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    old_cycle = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(conn, track, now=now, cycle=old_cycle)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0,
    })
    newest = daemon._forecast_work_identity(track, now_utc=now)
    _insert_job_run(conn, newest, status="PARTIAL", recorded_at=now - timedelta(seconds=80))
    candidate, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    _insert_job_run(
        conn, candidate, status="FAILED", recorded_at=now - timedelta(seconds=61),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        daemon, "run_opendata_track",
        lambda _track, **kw: calls.append(kw) or {"status": "partial"},
    )
    result = daemon._run_opendata_track_if_due(
        track, _job_conn=conn, _now_utc=now, _source_paused=lambda _: False,
        _poll_deadline_monotonic=time.monotonic() + 10,
        _use_availability_probe=True,
        _availability_probe=lambda *_a, **_k: {"status": "released"},
    )
    assert result["status"] == "partial"
    assert len(calls) == 1
    assert "_identity" not in calls[0]


@pytest.mark.parametrize("prior_status", ("RUNNING", "FAILED", "PARTIAL", "SUCCESS"))
def test_competing_safe_poll_lock_miss_does_not_replace_collector_attempt(
    monkeypatch, prior_status,
):
    """SKIPPED_LOCK_HELD is a spectator, never the exact job's attempt clock."""
    from src.data import job_lock
    from src.ingest import forecast_live_daemon as daemon

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    identity = daemon._forecast_work_identity("mx2t6_high", now_utc=now)
    conn = _job_run_conn()
    _insert_job_run(conn, identity, status=prior_status, recorded_at=now - timedelta(seconds=30))

    @contextmanager
    def lock_held(*_args, **_kwargs):
        yield False, "opendata_live_forecast_mx2t6_high"

    monkeypatch.setattr(job_lock, "acquire_opendata_track_lock", lock_held)
    result = daemon.run_opendata_track(
        "mx2t6_high", _job_conn=conn, _now_utc=now,
        _source_paused=lambda _: False,
        _collector=lambda **_kwargs: pytest.fail("lock miss must not collect"),
    )
    assert result["status"] == "skipped_lock_held"
    assert conn.execute(
        "SELECT status FROM job_run WHERE job_run_id = ?", (daemon._job_run_id(identity),)
    ).fetchone()["status"] == prior_status


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_revision_migration_requires_current_complete_proof_and_cools_failure(monkeypatch, track):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    old_cycle = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(conn, track, now=now, cycle=old_cycle)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0
    })
    candidate, _ = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    assert candidate["scheduled_for"] == old_cycle
    _insert_job_run(conn, candidate, status="FAILED", recorded_at=now - timedelta(seconds=20))
    cooling, cooling_debt = daemon._held_revision_migration_identity(
        conn, track=track, now_utc=now, deadline_monotonic=time.monotonic() + 10,
    )
    assert cooling == candidate
    assert cooling_debt["status"] == "revision_migration_retry_not_due"
    conn.execute(
        "UPDATE job_run SET finished_at = ?, recorded_at = ? WHERE job_run_id = ?",
        ((now - timedelta(seconds=61)).isoformat(),) * 2 + (daemon._job_run_id(candidate),),
    )
    assert daemon._held_revision_migration_identity(conn, track=track, now_utc=now,
        deadline_monotonic=time.monotonic() + 10) is not None
    conn.execute(
        "UPDATE job_run SET status = 'SUCCESS', rows_written = 1, source_run_id = ? WHERE job_run_id = ?",
        (daemon._expected_source_run_id(candidate), daemon._job_run_id(candidate)),
    )
    # A SUCCESS for other cities is not proof for this held Hong Kong family.
    assert daemon._held_revision_migration_identity(conn, track=track, now_utc=now,
        deadline_monotonic=time.monotonic() + 10) is not None
    conn.execute("INSERT INTO source_run VALUES (?, 'ecmwf_open_data', ?, ?, ?, 'SUCCESS', 'COMPLETE')",
        (daemon._expected_source_run_id(candidate), old["forecast_track"], candidate["release_calendar_key"], old_cycle.isoformat()))
    conn.execute(
        """INSERT INTO source_run_coverage VALUES
        (?, ?, ?, 'HONG_KONG', 'Hong Kong', 'Asia/Hong_Kong', '2026-09-27', ?, ?,
         'ecmwf_open_data', 'ensemble_snapshots_db_reader', 'COMPLETE',
         'LIVE_ELIGIBLE', ?, '2026-09-26T16:00:00+00:00',
         '2026-09-27T16:00:00+00:00')""",
        (daemon._expected_source_run_id(candidate), candidate["data_version"],
         candidate["release_calendar_key"], old["metric"], old["forecast_track"],
         (now + timedelta(hours=5)).isoformat()),
    )
    conn.execute(
        "UPDATE source_run SET completeness_status = 'PARTIAL' WHERE source_run_id = ?",
        (daemon._expected_source_run_id(candidate),),
    )
    assert daemon._held_revision_migration_identity(conn, track=track, now_utc=now,
        deadline_monotonic=time.monotonic() + 10) is None
    conn.execute(
        "UPDATE job_run SET status = 'PARTIAL' WHERE job_run_id = ?",
        (daemon._job_run_id(candidate),),
    )
    assert daemon._held_revision_migration_identity(conn, track=track, now_utc=now,
        deadline_monotonic=time.monotonic() + 10) is None


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_held_revision_migration_rejects_ended_stale_and_incomplete_native_day(monkeypatch, track):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_seed_discovery as discovery

    now = datetime(2026, 9, 27, 12, 46, tzinfo=timezone.utc)
    old_cycle = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    conn = _job_run_conn()
    old = _held_revision_coverage(conn, track, now=now, cycle=old_cycle)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda **_: {
        ("Hong Kong", "2026-09-27", old["metric"]): 0
    })
    def candidate(at):
        return daemon._held_revision_migration_identity(conn, track=track, now_utc=at,
            deadline_monotonic=time.monotonic() + 10)

    assert candidate(now) is not None
    query_plan = [str(row[3]) for row in conn.execute(
        """EXPLAIN QUERY PLAN SELECT coverage.source_run_id
             FROM source_run_coverage coverage INDEXED BY idx_source_run_coverage_scope
            WHERE coverage.city_id = 'HONG_KONG'
              AND coverage.city_timezone = 'Asia/Hong_Kong'
              AND coverage.target_local_date = '2026-09-27'
              AND coverage.temperature_metric = ?
              AND coverage.source_id = 'ecmwf_open_data'
              AND coverage.source_transport = 'ensemble_snapshots_db_reader'""",
        (old["metric"],),
    )]
    assert any("USING INDEX idx_source_run_coverage_scope" in line for line in query_plan)
    conn.execute("UPDATE source_run SET status = 'PARTIAL'")
    assert candidate(now) is not None  # Target-local COMPLETE coverage remains the authority.
    conn.execute("UPDATE source_run SET status = 'SUCCESS', completeness_status = 'PARTIAL'")
    assert candidate(now) is not None
    conn.execute("UPDATE source_run SET completeness_status = 'COMPLETE'")
    assert candidate(datetime(2026, 9, 27, 16, tzinfo=timezone.utc)) is None
    conn.execute("UPDATE source_run_coverage SET expires_at = ?", ((now - timedelta(seconds=1)).isoformat(),))
    assert candidate(now) is None
    conn.execute("UPDATE source_run_coverage SET expires_at = ?", ((now + timedelta(hours=5)).isoformat(),))
    conn.execute("UPDATE source_run SET source_cycle_time = ?", (datetime(2026, 9, 27, 0, tzinfo=timezone.utc).isoformat(),))
    assert candidate(now) is None  # Later cycle omits 9/27 00:00-08:00 HKT.
    conn.execute("UPDATE source_run SET source_cycle_time = ?", (old_cycle.isoformat(),))
    conn.execute("UPDATE source_run_coverage SET completeness_status = 'PARTIAL'")
    assert candidate(now) is None


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
    assert "_cycle_deadline_monotonic" not in calls[1]


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_safe_poll_not_released_probe_selects_prior_debt_without_truncating_collect(monkeypatch, track):
    """The poll bounds selection; both tracks retain normal collector timeouts."""
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
    assert "_cycle_deadline_monotonic" not in calls[0]
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
