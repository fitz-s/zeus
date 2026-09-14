# Created: 2026-05-17
# Lifecycle: created=2026-05-17; last_reviewed=2026-09-01; last_reused=2026-09-01
# Last reused or audited: 2026-09-01
# Authority basis: docs/archive/2026-Q2/task_2026-05-17_post_karachi_remediation/F44_INVESTIGATION.md
#   Antibody for F44: observation_instants writer dead since 2026-05-10.
#   These tests catch the "dead-writer" category permanently by asserting
#   MAX(target_date) is within a defined SLA window.
#   CI-runnable, no live DB dependency — parametrized over a fixture DB.
"""Antibody tests for observation_instants freshness SLA.

F44 root cause: no live-tick writer existed. The table was populated only by
one-time backfill scripts. These tests catch the dead-writer category by:

1. Asserting MAX(target_date) within 48h SLA on a fixture DB.
2. Asserting the new obs_v2_live_tick module imports cleanly.
3. Asserting that ingest_main.py registers an 'ingest_k2_obs' scheduler job.
4. Asserting the live-tick script does NOT write openmeteo_archive_hourly rows
   (source-tier violation; would be rejected by A2 but we catch it at design time).
"""
from __future__ import annotations

import sqlite3
import tempfile
import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_v2_db(tmp_path: Path) -> Path:
    """Fixture DB with observation_instants containing fresh rows (today)."""
    db_path = tmp_path / "test_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO observation_instants (city, target_date, source, utc_timestamp, authority, data_version, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Karachi", today, "wu_icao_history", f"{today}T12:00:00+00:00", "VERIFIED", "v1.wu-native", f"{today}T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def stale_v2_db(tmp_path: Path) -> Path:
    """Fixture DB simulating F44: MAX(target_date) = 7 days ago (stale beyond SLA)."""
    db_path = tmp_path / "stale_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    stale_date = (date.today() - timedelta(days=7)).isoformat()
    conn.execute(
        "INSERT INTO observation_instants (city, target_date, source, utc_timestamp, authority, data_version, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Karachi", stale_date, "wu_icao_history", f"{stale_date}T12:00:00+00:00", "VERIFIED", "v1.wu-native", f"{stale_date}T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def empty_v2_db(tmp_path: Path) -> Path:
    """Fixture DB simulating F44 at forecasts.db: zero rows."""
    db_path = tmp_path / "empty_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Freshness SLA helpers (parametrizable for future use)
# ---------------------------------------------------------------------------

SLA_HOURS = 48  # maximum acceptable staleness


def _max_target_date(db_path: Path) -> date | None:
    """Return MAX(target_date) from observation_instants, or None if empty."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(target_date) FROM observation_instants").fetchone()
        if row and row[0]:
            return date.fromisoformat(row[0])
        return None
    finally:
        conn.close()


def _check_freshness(db_path: Path, *, sla_hours: int = SLA_HOURS) -> tuple[bool, str]:
    """Return (is_fresh, message) for the given DB."""
    max_date = _max_target_date(db_path)
    if max_date is None:
        return False, "observation_instants is empty (zero rows)"
    today = date.today()
    staleness_days = (today - max_date).days
    staleness_hours = staleness_days * 24
    if staleness_hours > sla_hours:
        return False, (
            f"MAX(target_date)={max_date} is {staleness_days}d ({staleness_hours}h) old, "
            f"exceeds {sla_hours}h SLA. "
            f"Root cause: observation_instants writer not running (F44 category)."
        )
    return True, f"MAX(target_date)={max_date} is {staleness_days}d old, within {sla_hours}h SLA"


# ---------------------------------------------------------------------------
# SLA tests
# ---------------------------------------------------------------------------

def test_freshness_check_passes_for_recent_data(fresh_v2_db: Path) -> None:
    """Freshness helper reports OK when MAX(target_date) = today."""
    is_fresh, msg = _check_freshness(fresh_v2_db)
    assert is_fresh, f"Expected fresh DB to pass SLA check: {msg}"


def test_freshness_check_fails_for_stale_data(stale_v2_db: Path) -> None:
    """Freshness helper catches F44-category staleness (7-day gap > 48h SLA)."""
    is_fresh, msg = _check_freshness(stale_v2_db)
    assert not is_fresh, "Expected stale DB (7-day gap) to fail SLA check"
    assert "F44" in msg or "SLA" in msg, f"Error message should mention SLA/F44: {msg}"


def test_freshness_check_fails_for_empty_table(empty_v2_db: Path) -> None:
    """Freshness helper catches empty table (F44 worst case: no rows at all)."""
    is_fresh, msg = _check_freshness(empty_v2_db)
    assert not is_fresh, "Expected empty DB to fail SLA check"
    assert "empty" in msg.lower() or "zero" in msg.lower(), f"Message should say empty: {msg}"


def test_exactly_48h_boundary_is_fresh(tmp_path: Path) -> None:
    """Exactly at SLA boundary (2 days ago) should pass."""
    db_path = tmp_path / "boundary.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE observation_instants (city TEXT, target_date TEXT, source TEXT, utc_timestamp TEXT, authority TEXT, data_version TEXT, imported_at TEXT)")
    boundary_date = (date.today() - timedelta(days=2)).isoformat()
    conn.execute("INSERT INTO observation_instants VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("London", boundary_date, "wu_icao_history", f"{boundary_date}T00:00:00+00:00", "VERIFIED", "v1.wu-native", f"{boundary_date}T00:00:00+00:00"))
    conn.commit()
    conn.close()
    is_fresh, msg = _check_freshness(db_path, sla_hours=48)
    assert is_fresh, f"2 days ago (48h) should be within SLA: {msg}"


def test_beyond_48h_boundary_is_stale(tmp_path: Path) -> None:
    """Three days ago (>48h) should fail."""
    db_path = tmp_path / "beyond.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE observation_instants (city TEXT, target_date TEXT, source TEXT, utc_timestamp TEXT, authority TEXT, data_version TEXT, imported_at TEXT)")
    old_date = (date.today() - timedelta(days=3)).isoformat()
    conn.execute("INSERT INTO observation_instants VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("London", old_date, "wu_icao_history", f"{old_date}T00:00:00+00:00", "VERIFIED", "v1.wu-native", f"{old_date}T00:00:00+00:00"))
    conn.commit()
    conn.close()
    is_fresh, msg = _check_freshness(db_path, sla_hours=48)
    assert not is_fresh, f"3 days ago (>48h) should fail SLA: {msg}"


# ---------------------------------------------------------------------------
# Structural antibody: live-tick module importability
# ---------------------------------------------------------------------------

def test_obs_v2_live_tick_imports_cleanly() -> None:
    """obs_v2_live_tick.py must import without errors.

    Catches regressions where the module's imports are broken (e.g. a
    refactor renames a function the tick depends on).
    """
    from scripts.obs_live_tick import run_live_tick, TickResult, DATA_VERSION
    assert callable(run_live_tick), "run_live_tick must be callable"
    assert DATA_VERSION.startswith("v1."), f"DATA_VERSION must match v1.* pattern, got {DATA_VERSION!r}"


def test_obs_v2_and_hko_live_ticks_use_runtime_state_dir(monkeypatch, tmp_path: Path) -> None:
    """Live tick defaults must follow ZEUS_PRIMARY_ROOT, not the deploy worktree."""
    import importlib
    import sys

    import src.config as config_mod

    monkeypatch.setenv("ZEUS_PRIMARY_ROOT", str(tmp_path))
    reloaded_config = importlib.reload(config_mod)
    for module_name in ("scripts.obs_live_tick", "scripts.hko_ingest_tick"):
        sys.modules.pop(module_name, None)

    obs_tick = importlib.import_module("scripts.obs_live_tick")
    hko_tick = importlib.import_module("scripts.hko_ingest_tick")

    assert obs_tick.DEFAULT_DB_PATH == reloaded_config.STATE_DIR / "zeus-world.db"
    assert obs_tick.DEFAULT_LOG_PATH == reloaded_config.STATE_DIR / "obs_v2_live_tick_log.jsonl"
    assert hko_tick.DEFAULT_DB_PATH == reloaded_config.STATE_DIR / "zeus-world.db"
    assert hko_tick.DEFAULT_LOG_PATH == reloaded_config.STATE_DIR / "hko_ingest_log.jsonl"

    monkeypatch.delenv("ZEUS_PRIMARY_ROOT", raising=False)
    importlib.reload(config_mod)
    for module_name in ("scripts.obs_live_tick", "scripts.hko_ingest_tick"):
        sys.modules.pop(module_name, None)


def test_obs_v2_live_tick_uses_city_local_fetch_window_for_day0() -> None:
    """East-of-UTC cities must fetch the already-started local day."""
    from datetime import datetime, timezone

    from scripts.obs_live_tick import _city_local_fetch_window

    start_date, end_date = _city_local_fetch_window(
        "Tokyo",
        now_utc=datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc),
        days_back=1,
    )

    assert start_date.isoformat() == "2026-06-07"
    assert end_date.isoformat() == "2026-06-08"


def test_obs_v2_live_tick_connection_carries_busy_timeout(monkeypatch, tmp_path: Path) -> None:
    from scripts.obs_live_tick import _open_obs_tick_connection

    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "12345")
    db_path = tmp_path / "world.db"

    conn = _open_obs_tick_connection(db_path)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 12345
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("city_name", "tick_name", "fetch_name"),
    [
        ("Chicago", "_tick_wu_city", "fetch_wu_hourly"),
        ("Karachi", "_tick_ogimet_city", "fetch_ogimet_hourly"),
    ],
)
def test_obs_live_tick_stamps_possession_only_after_fetch(
    monkeypatch, city_name: str, tick_name: str, fetch_name: str
) -> None:
    """A resumed fetch must not backdate observations to its pre-request clock."""
    import scripts.obs_live_tick as obs_tick

    fetch_returned = False

    def fake_fetch(**_kwargs):
        nonlocal fetch_returned
        fetch_returned = True
        return SimpleNamespace(failed=False, observations=[])

    def possession_time(_captured):
        assert fetch_returned, "possession clock was captured before fetch returned"
        return "2026-07-23T20:00:00+00:00"

    monkeypatch.setattr(obs_tick, fetch_name, fake_fetch)
    monkeypatch.setattr(obs_tick, "proof_of_possession_available_at", possession_time)

    result = getattr(obs_tick, tick_name)(
        city_name,
        None,
        start_date=date(2026, 7, 23),
        end_date=date(2026, 7, 23),
        dry_run=True,
    )

    assert result.failure_reason is None


def test_obs_v2_live_tick_retries_sqlite_lock_per_city(monkeypatch) -> None:
    from scripts.obs_live_tick import TickResult, _run_city_with_sqlite_retry

    class Conn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    conn = Conn()
    calls = []
    sleeps = []

    def _tick(city_name, conn, *, start_date, end_date, dry_run):
        calls.append(city_name)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return TickResult(city=city_name, tier="WU_ICAO", rows_ready=1, rows_written=1)

    monkeypatch.setenv("ZEUS_OBS_LIVE_TICK_SQLITE_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr("scripts.obs_live_tick.time.sleep", lambda delay: sleeps.append(delay))

    result = _run_city_with_sqlite_retry(
        _tick,
        "Tokyo",
        conn,
        start_date=date(2026, 6, 26),
        end_date=date(2026, 6, 26),
        dry_run=False,
    )

    assert result.failure_reason is None
    assert result.rows_written == 1
    assert calls == ["Tokyo", "Tokyo"]
    assert sleeps == [0.01]
    assert conn.rollbacks == 1
    assert conn.commits == 1


def test_obs_v2_live_tick_does_not_hold_writer_lock_across_city_fetch(monkeypatch, tmp_path: Path) -> None:
    """The rolling obs tick must not hold the world writer lock across upstream fetches."""
    import contextlib
    from types import SimpleNamespace

    import scripts.obs_live_tick as obs_tick

    lock_held = False
    lock_entries = 0
    family_admission = lambda _observation: True

    @contextlib.contextmanager
    def fake_db_writer_lock(_path, _write_class):
        nonlocal lock_held, lock_entries
        assert not lock_held
        lock_entries += 1
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    class FakeConn:
        def __init__(self):
            self.committed = False
            self.closed = False

        def execute(self, _sql, *_params):
            return None

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("rollback should not be needed")

        def close(self):
            self.closed = True

    def fake_tick(
        city_name,
        conn,
        *,
        start_date,
        end_date,
        dry_run,
        day0_family_admission=None,
    ):
        assert not lock_held, f"{city_name} fetch/build ran while writer lock was held"
        assert day0_family_admission is family_admission
        written = obs_tick._write_rows(conn, [object()])
        return obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_ready=1, rows_written=written)

    monkeypatch.setattr(obs_tick, "cities_by_name", {
        "Auckland": SimpleNamespace(timezone="UTC"),
        "Tokyo": SimpleNamespace(timezone="UTC"),
    })
    monkeypatch.setattr(obs_tick, "tier_for_city", lambda _name: obs_tick.Tier.WU_ICAO)
    monkeypatch.setattr(obs_tick, "db_writer_lock", fake_db_writer_lock)
    monkeypatch.setattr(obs_tick, "_open_obs_tick_connection", lambda _path: FakeConn())
    monkeypatch.setattr(obs_tick, "insert_rows", lambda _conn, _rows: len(_rows))
    monkeypatch.setattr(obs_tick, "_tick_wu_city", fake_tick)
    monkeypatch.setattr(obs_tick.time, "sleep", lambda _delay: None)

    results = obs_tick.run_live_tick(
        city_filter=["Auckland", "Tokyo"],
        db_path=tmp_path / "world.db",
        log_path=tmp_path / "obs_log.jsonl",
        day0_family_admission=family_admission,
    )

    assert [r.rows_written for r in results] == [1, 1]
    assert lock_entries == 2
    assert not lock_held


def test_ogimet_live_tick_shards_cover_each_noaa_city_once_daily() -> None:
    import scripts.obs_live_tick as obs_tick

    names = sorted(
        name
        for name in obs_tick.cities_by_name
        if obs_tick.tier_for_city(name) is obs_tick.Tier.OGIMET_METAR
    )
    seen: list[str] = []
    for hour in range(24):
        seen.extend(
            obs_tick._ogimet_city_shard_for_hour(
                names,
                datetime(2026, 9, 2, hour, tzinfo=timezone.utc),
            )
        )
    assert seen == names
    assert len(seen) == len(set(seen)) == 48


def test_fast_obs_tick_can_exclude_slow_ogimet_mirror(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {"Istanbul": SimpleNamespace(timezone="Europe/Istanbul")},
    )
    monkeypatch.setattr(
        obs_tick,
        "tier_for_city",
        lambda _name: obs_tick.Tier.OGIMET_METAR,
    )
    monkeypatch.setattr(
        obs_tick,
        "_tick_ogimet_city",
        lambda *_args, **_kwargs: pytest.fail("slow Ogimet lane must be excluded"),
    )

    results = obs_tick.run_live_tick(
        city_filter=["Istanbul"],
        dry_run=True,
        log_path=tmp_path / "obs.jsonl",
        include_ogimet=False,
    )

    assert results == []


def test_ingest_fast_tick_uses_direct_metar_lane_not_slow_ogimet() -> None:
    import src.ingest_main as ingest_main

    source = inspect.getsource(ingest_main._k2_obs_fast_tick)
    assert "include_ogimet=False" in source


def test_obs_v2_live_tick_does_not_use_openmeteo_source() -> None:
    """The live-tick script must not use openmeteo_archive_hourly as a source.

    openmeteo_archive_hourly is NOT in any city's allowed_sources set (A2 rule).
    Using it would cause all writes to be rejected by the v2 writer.
    This is the design constraint that motivated F44's fix shape (not a simple
    dual-write from hourly_instants_append.py).
    """
    import ast
    tick_path = Path(__file__).resolve().parent.parent / "scripts" / "obs_live_tick.py"
    source_text = tick_path.read_text()
    assert "openmeteo_archive_hourly" not in source_text, (
        "obs_v2_live_tick.py must not reference 'openmeteo_archive_hourly'. "
        "This source is rejected by v2 writer A2 validation for all cities. "
        "Use wu_icao_history (WU_ICAO tier) or ogimet_metar_* (OGIMET_METAR tier)."
    )


# ---------------------------------------------------------------------------
# Structural antibody: ingest_main.py registers v2 tick job
# ---------------------------------------------------------------------------

def test_ingest_main_registers_obs_v2_job() -> None:
    """ingest_main.py must register 'ingest_k2_obs' as a scheduler job.

    Catches regressions where the scheduler wiring is accidentally removed.
    This is the F44 fix — if this assertion fails, the writer is dead again.
    """
    ingest_main_path = Path(__file__).resolve().parent.parent / "src" / "ingest_main.py"
    source_text = ingest_main_path.read_text()
    assert "ingest_k2_obs" in source_text, (
        "ingest_main.py must register 'ingest_k2_obs' scheduler job. "
        "This is the F44 fix. If this job is missing, observation_instants "
        "will go stale (same root cause: no live-tick writer)."
    )
    assert "_k2_obs_tick" in source_text, (
        "ingest_main.py must define _k2_obs_tick function. "
        "This is the F44 fix entry point."
    )
    assert '_REPO_ROOT / "state" / "zeus-world.db"' not in source_text
    assert 'STATE_DIR / "zeus-world.db"' in source_text


def test_obs_tick_all_city_failures_mark_scheduler_failed() -> None:
    """A total obs ingest outage must not be reported as scheduler success."""
    from src.ingest_main import _raise_if_all_obs_tick_attempts_failed

    with pytest.raises(RuntimeError, match="all attempted observation cities failed"):
        _raise_if_all_obs_tick_attempts_failed(
            "ingest_k2_obs",
            [
                SimpleNamespace(city="Paris", skipped_hko=False, failure_reason="no such table: observation_instants"),
                SimpleNamespace(city="Tokyo", skipped_hko=False, failure_reason="no such table: observation_instants"),
                SimpleNamespace(city="Hong Kong", skipped_hko=True, failure_reason=None),
            ],
        )


def test_obs_tick_partial_city_failures_do_not_fail_whole_job() -> None:
    """Partial provider failures are logged by city, not escalated to total outage."""
    from src.ingest_main import _raise_if_all_obs_tick_attempts_failed

    _raise_if_all_obs_tick_attempts_failed(
        "ingest_k2_obs_fast_tick",
        [
            SimpleNamespace(city="Paris", skipped_hko=False, failure_reason=None),
            SimpleNamespace(city="Tokyo", skipped_hko=False, failure_reason="provider timeout"),
        ],
    )


# ---------------------------------------------------------------------------
# catch_up_missing_instants — hole-scanner drain for the live-tick sources
# (2026-09-14 London/Miami/NYC host-DNS-outage incident: a live-tick miss
# had no repair path except that city's next fixed daily Ogimet slot).
# ---------------------------------------------------------------------------


def _coverage_db() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _seed_instants_missing(conn: sqlite3.Connection, *, city: str, data_source: str, target_date: str) -> None:
    from src.state.data_coverage import DataTable, record_missing

    record_missing(
        conn,
        data_table=DataTable.OBSERVATION_INSTANTS,
        city=city,
        data_source=data_source,
        target_date=target_date,
    )


def test_catch_up_missing_instants_drains_ogimet_miss(monkeypatch) -> None:
    """A scanner-recorded OBSERVATION_INSTANTS MISSING row for a NOAA/Ogimet
    city must invoke `_tick_ogimet_city` exactly once for that city and date
    range -- never `_tick_wu_city`."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    _seed_instants_missing(conn, city="London", data_source="ogimet_metar_eglc", target_date="2026-09-13")

    ogimet_calls: list[tuple] = []

    def fake_ogimet_city(city_name, _conn, *, start_date, end_date, dry_run):
        ogimet_calls.append((city_name, start_date, end_date, dry_run))
        return obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=24)

    def fail_wu_city(*_args, **_kwargs):
        raise AssertionError("_tick_wu_city must not be called for an Ogimet-tier hole")

    monkeypatch.setattr(obs_tick, "_tick_ogimet_city", fake_ogimet_city)
    monkeypatch.setattr(obs_tick, "_tick_wu_city", fail_wu_city)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30)

    assert ogimet_calls == [("London", date(2026, 9, 13), date(2026, 9, 13), False)]
    assert totals["ogimet_cities_touched"] == 1
    assert totals["ogimet_rows_written"] == 24
    assert totals["ogimet_cities_failed"] == 0


def test_catch_up_missing_instants_noop_without_holes(monkeypatch) -> None:
    """No OBSERVATION_INSTANTS holes -> no fetcher call at all (idempotent)."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()

    def fail(*_args, **_kwargs):
        raise AssertionError("fetcher must not be called when there are no pending fills")

    monkeypatch.setattr(obs_tick, "_tick_ogimet_city", fail)
    monkeypatch.setattr(obs_tick, "_tick_wu_city", fail)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30)

    assert totals["ogimet_cities_touched"] == 0
    assert totals["wu_cities_touched"] == 0


def test_catch_up_missing_instants_routes_wu_and_skips_non_drainable_tier(monkeypatch) -> None:
    """A WU-tier hole routes to `_tick_wu_city`, never `_tick_ogimet_city`;
    a hole for a tier this drain does not own (HKO has its own accumulator)
    is skipped rather than mis-routed."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    _seed_instants_missing(conn, city="Taipei", data_source="wu_icao_history", target_date="2026-09-13")
    _seed_instants_missing(conn, city="Hong Kong", data_source="hko_daily_api", target_date="2026-09-13")

    wu_calls: list[str] = []

    def fake_wu_city(city_name, _conn, *, start_date, end_date, dry_run):
        wu_calls.append(city_name)
        return obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_written=24)

    def fail_ogimet_city(*_args, **_kwargs):
        raise AssertionError("_tick_ogimet_city must not be called for a WU/HKO-tier hole")

    monkeypatch.setattr(obs_tick, "_tick_wu_city", fake_wu_city)
    monkeypatch.setattr(obs_tick, "_tick_ogimet_city", fail_ogimet_city)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30)

    assert wu_calls == ["Taipei"]
    assert totals["wu_cities_touched"] == 1
    assert totals["skipped_non_drainable_tier"] == 1


def test_catch_up_missing_instants_one_city_failure_does_not_abort_others(monkeypatch) -> None:
    """One city's fetcher raising must not abort the drain for the rest of
    the missing set; the failure is recorded in the existing totals shape."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    _seed_instants_missing(conn, city="London", data_source="ogimet_metar_eglc", target_date="2026-09-13")
    _seed_instants_missing(conn, city="Miami", data_source="ogimet_metar_kmia", target_date="2026-09-13")

    def flaky_ogimet_city(city_name, _conn, *, start_date, end_date, dry_run):
        if city_name == "London":
            raise RuntimeError("NETWORK_ERROR")
        return obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=24)

    monkeypatch.setattr(obs_tick, "_tick_ogimet_city", flaky_ogimet_city)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30, max_ogimet_cities=2)

    assert totals["ogimet_cities_failed"] == 1
    assert totals["ogimet_cities_touched"] == 1
    assert totals["ogimet_rows_written"] == 24
