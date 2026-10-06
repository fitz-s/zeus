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
from zoneinfo import ZoneInfo

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


def _instants_db(tmp_path: Path, name: str = "instants.db") -> Path:
    """Fixture DB with the exact column set
    ``_complete_raw_hourly_coverage`` (day0_hard_fact_exit.py)
    requires -- the exit authority's completeness predicate is what the
    selector now calls directly, so tests exercise the real schema/filters,
    not a hand-rolled lookalike."""
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, source TEXT, station_id TEXT, target_date TEXT,
            utc_timestamp TEXT, time_basis TEXT, running_max REAL,
            running_min REAL, temp_unit TEXT, imported_at TEXT,
            authority TEXT, causality_status TEXT, source_role TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _write_complete_ogimet_day(
    db_path: Path,
    *,
    city: str,
    station: str,
    timezone_name: str,
    target_date: str,
    hours: int = 24,
    unit: str = "C",
    authority: str = "VERIFIED",
    causality: str = "OK",
    source_role: str = "historical_hourly",
    include_next_day_boundary: bool = True,
) -> None:
    """Write an Ogimet hourly ledger through the SAME row shape/quality tags
    the exit authority requires -- UTC timestamps are the city's own local
    midnight-anchored hour buckets, not a naive UTC-offset-0 assumption."""
    conn = sqlite3.connect(str(db_path))
    source = f"ogimet_metar_{station.lower()}"
    target = date.fromisoformat(target_date)
    tz = ZoneInfo(timezone_name)
    start = datetime.combine(target, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(
        target + timedelta(days=1), datetime.min.time(), tzinfo=tz
    ).astimezone(timezone.utc)
    total_hours = int((end - start).total_seconds() // 3600)
    for i in range(min(hours, total_hours)):
        ts = (start + timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                city, source, station, target_date, ts, "utc_hour_bucket_extremum",
                20.0, 10.0, unit, ts, authority, causality, source_role,
            ),
        )
    if include_next_day_boundary:
        following = (target + timedelta(days=1)).isoformat()
        ts = end.isoformat()
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                city, source, station, following, ts, "utc_hour_bucket_extremum",
                20.0, 10.0, unit, ts, authority, causality, source_role,
            ),
        )
    conn.commit()
    conn.close()


def _beijing_city() -> SimpleNamespace:
    return SimpleNamespace(
        name="Beijing", timezone="Asia/Shanghai", wu_station="ZBAA",
        settlement_source_type="noaa", settlement_unit="C",
    )


def test_complete_raw_hourly_coverage_accepts_a_fully_valid_day(
    tmp_path: Path,
) -> None:
    """A complete raw ledger stops coverage retries without finality authority."""
    from src.execution.day0_hard_fact_exit import (
        _complete_raw_hourly_coverage,
    )

    db_path = _instants_db(tmp_path)
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13",
    )
    conn = sqlite3.connect(str(db_path))
    result = _complete_raw_hourly_coverage(
        city=_beijing_city(), target_date="2026-09-13", metric="high",
        now=datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc), conn=conn,
    )
    conn.close()

    assert result is True


@pytest.mark.parametrize(
    "column,bad_value",
    [
        ("authority", "UNVERIFIED"),
        ("causality_status", "SUSPECT"),
        ("source_role", "realtime_current"),
        ("time_basis", "utc_instant"),
        ("temp_unit", "F"),
    ],
)
def test_complete_raw_hourly_coverage_rejects_one_disqualified_row(
    tmp_path: Path, column: str, bad_value: str,
) -> None:
    """Every row-level filter the exit authority applies
    (day0_hard_fact_exit.py:426-437 -- source/station/time_basis/unit/
    authority/causality/source_role) disqualifies its one row from
    ``target_values``, which breaks the exact expected-hours SET match --
    the predicate returns None (not-complete), the same as a hole in the
    ledger, rather than promoting on a partially trustworthy day."""
    from src.execution.day0_hard_fact_exit import (
        _complete_raw_hourly_coverage,
    )

    db_path = _instants_db(tmp_path)
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13",
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"""
        UPDATE observation_instants SET {column} = ?
         WHERE target_date = ?
           AND utc_timestamp = (
               SELECT MIN(utc_timestamp) FROM observation_instants
                WHERE target_date = ?
           )
        """,
        (bad_value, "2026-09-13", "2026-09-13"),
    )
    conn.commit()

    result = _complete_raw_hourly_coverage(
        city=_beijing_city(), target_date="2026-09-13", metric="high",
        now=datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc), conn=conn,
    )
    conn.close()

    assert result is False


def test_complete_raw_hourly_coverage_rejects_a_missing_hour(
    tmp_path: Path,
) -> None:
    """23 of the 24 expected local hours present: ``set(target_values) !=
    expected_hours`` even though every present row is otherwise valid --
    exact-set completeness, not a row count, is what the predicate checks."""
    from src.execution.day0_hard_fact_exit import (
        _complete_raw_hourly_coverage,
    )

    db_path = _instants_db(tmp_path)
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13", hours=23,
    )
    conn = sqlite3.connect(str(db_path))
    result = _complete_raw_hourly_coverage(
        city=_beijing_city(), target_date="2026-09-13", metric="high",
        now=datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc), conn=conn,
    )
    conn.close()

    assert result is False


def test_complete_raw_hourly_coverage_rejects_missing_following_day_boundary(
    tmp_path: Path,
) -> None:
    """All 24 target-day hours present but no following-day boundary row:
    the source has not yet proven it advanced past the target day, so the
    predicate must not promote."""
    from src.execution.day0_hard_fact_exit import (
        _complete_raw_hourly_coverage,
    )

    db_path = _instants_db(tmp_path)
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13", include_next_day_boundary=False,
    )
    conn = sqlite3.connect(str(db_path))
    result = _complete_raw_hourly_coverage(
        city=_beijing_city(), target_date="2026-09-13", metric="high",
        now=datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc), conn=conn,
    )
    conn.close()

    assert result is False


def test_ogimet_local_day_end_selection_picks_only_cities_whose_local_day_ended(
    monkeypatch, tmp_path: Path
) -> None:
    """Mixed-timezone roster, single instant: a city whose local-day-end +1h
    buffer has already elapsed is selected; a city whose local day ended
    only minutes ago (buffer not yet satisfied) is not -- fixed-offset zones
    (no DST) isolate exactly the day-end-relative-to-now boundary."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            # UTC+8: local day ended 2026-09-13T16:00Z; now (17:15Z) is
            # 1h15m later -- past the 1h buffer.
            "PlusEight": SimpleNamespace(
                name="PlusEight", timezone="Etc/GMT-8", wu_station="ZZZ8",
                settlement_source_type="noaa", settlement_unit="C",
            ),
            # UTC+7: local day ended 2026-09-13T17:00Z; now (17:15Z) is only
            # 15min later -- inside the 1h publish-lag buffer.
            "PlusSeven": SimpleNamespace(
                name="PlusSeven", timezone="Etc/GMT-7", wu_station="ZZZ7",
                settlement_source_type="noaa", settlement_unit="C",
            ),
        },
    )
    db_path = _instants_db(tmp_path)
    now_utc = datetime(2026, 9, 13, 17, 15, tzinfo=timezone.utc)

    due = obs_tick._ogimet_cities_due_for_completion(
        ["PlusEight", "PlusSeven"], now_utc, db_path=db_path
    )

    assert due.cities == ["PlusEight"]
    assert not due.fail_open


def test_ogimet_local_day_end_selection_seattle_09_13_replay(monkeypatch, tmp_path: Path) -> None:
    """Seattle's local day ends 07:00Z; it must be selected at the 08:15Z
    tick (day-end + 1h + the :15 cron minute) and NOT at 17:15Z (the OLD
    fixed-shard slot)."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "Seattle": SimpleNamespace(
                name="Seattle", timezone="America/Los_Angeles", wu_station="KSEA",
                settlement_source_type="noaa", settlement_unit="C",
            )
        },
    )
    db_path = _instants_db(tmp_path)

    too_early = datetime(2026, 9, 13, 7, 30, tzinfo=timezone.utc)  # day-end + 30min only
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Seattle"], too_early, db_path=db_path
    ).cities == []

    at_anchor = datetime(2026, 9, 13, 8, 15, tzinfo=timezone.utc)  # day-end + 1h15m
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Seattle"], at_anchor, db_path=db_path
    ).cities == ["Seattle"]


def test_ogimet_local_day_end_selection_dst_transition(monkeypatch, tmp_path: Path) -> None:
    """DST fall-back (US, 2026-11-01 02:00 local -> 01:00 local, Chicago):
    the local day just ended is 25 wall-clock hours long. The selection must
    still gate on the correct UTC day-end instant, not a naive +24h guess."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "Chicago": SimpleNamespace(
                name="Chicago", timezone="America/Chicago", wu_station="KORD",
                settlement_source_type="noaa", settlement_unit="C",
            )
        },
    )
    db_path = _instants_db(tmp_path)

    # 2026-11-01 local midnight (CDT, UTC-5) is 2026-11-01T05:00Z; the DST
    # fall-back happens later that local day, so the local day 2026-11-01
    # ends at 2026-11-02T06:00Z (CST, UTC-6) -- 25h after it started.
    before_end = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)  # only 30min past day-end
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Chicago"], before_end, db_path=db_path
    ).cities == []

    after_anchor = datetime(2026, 11, 2, 7, 15, tzinfo=timezone.utc)  # day-end + 1h15m
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Chicago"], after_anchor, db_path=db_path
    ).cities == ["Chicago"]


def test_ogimet_local_day_end_selection_dst_spring_forward(monkeypatch, tmp_path: Path) -> None:
    """DST spring-forward (US, 2026-03-08 02:00 local -> 03:00 local,
    Chicago): the local day just ended is 23 wall-clock hours long."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "Chicago": SimpleNamespace(
                name="Chicago", timezone="America/Chicago", wu_station="KORD",
                settlement_source_type="noaa", settlement_unit="C",
            )
        },
    )
    db_path = _instants_db(tmp_path)

    # 2026-03-08 local midnight (CST, UTC-6) is 2026-03-08T06:00Z; the
    # spring-forward happens later that local day, so 2026-03-08 ends at
    # 2026-03-09T05:00Z (CDT, UTC-5) -- 23h after it started.
    before_end = datetime(2026, 3, 9, 5, 30, tzinfo=timezone.utc)  # only 30min past day-end
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Chicago"], before_end, db_path=db_path
    ).cities == []

    after_anchor = datetime(2026, 3, 9, 6, 15, tzinfo=timezone.utc)  # day-end + 1h15m
    assert obs_tick._ogimet_cities_due_for_completion(
        ["Chicago"], after_anchor, db_path=db_path
    ).cities == ["Chicago"]


def test_ogimet_local_day_end_selection_skips_already_complete_city(
    monkeypatch, tmp_path: Path
) -> None:
    """Idempotency: a rerun after success must not re-select the city --
    completeness is derived from observation_instants itself, not a flag."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "Beijing": SimpleNamespace(
                name="Beijing", timezone="Asia/Shanghai", wu_station="ZBAA",
                settlement_source_type="noaa", settlement_unit="C",
            )
        },
    )
    db_path = _instants_db(tmp_path)
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13",
    )
    now_utc = datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc)

    due = obs_tick._ogimet_cities_due_for_completion(["Beijing"], now_utc, db_path=db_path)

    assert due.cities == []


def test_ogimet_local_day_end_selection_retries_incomplete_city_next_tick(
    monkeypatch, tmp_path: Path
) -> None:
    """Carry-forward: a failed/partial attempt (fewer than the expected
    hourly rows, or no next-day advancement proof) stays selected on the
    following hourly tick -- no persisted retry state needed."""
    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "Beijing": SimpleNamespace(
                name="Beijing", timezone="Asia/Shanghai", wu_station="ZBAA",
                settlement_source_type="noaa", settlement_unit="C",
            )
        },
    )
    db_path = _instants_db(tmp_path)
    # Only 23 of 24 expected hours written, and no next-day advancement row --
    # simulates a fetch that failed partway through.
    _write_complete_ogimet_day(
        db_path, city="Beijing", station="ZBAA", timezone_name="Asia/Shanghai",
        target_date="2026-09-13", hours=23, include_next_day_boundary=False,
    )

    now_utc = datetime(2026, 9, 13, 18, 15, tzinfo=timezone.utc)
    due_first = obs_tick._ogimet_cities_due_for_completion(["Beijing"], now_utc, db_path=db_path)
    assert due_first.cities == ["Beijing"]

    next_tick = now_utc + timedelta(hours=1)
    due_next = obs_tick._ogimet_cities_due_for_completion(["Beijing"], next_tick, db_path=db_path)
    assert due_next.cities == ["Beijing"]


def test_ogimet_local_day_end_full_roster_sweep_selects_each_city_exactly_once(
    tmp_path: Path,
) -> None:
    """25 consecutive hourly ticks (one full 24h rotation, endpoints
    inclusive) over the REAL 48-city roster: every OGIMET_METAR city's
    local-day-end+1h buffer boundary occurs at exactly one hour-of-day, so
    a 24h-wide sweep crosses it exactly once per city, regardless of where
    the sweep starts (a city whose trigger hour coincides with the
    sweep's own start hour appears at both endpoints 24h apart -- the
    pre-seed step below neutralizes the start-of-window one as an
    already-fulfilled leftover, leaving exactly the in-window occurrence).
    Each selected city is immediately marked complete (simulating a
    successful fetch) so a later tick within the SAME sweep must not
    re-select it -- proving 'exactly once, never after complete' end to
    end against the live roster, not a synthetic one.
    """
    import scripts.obs_live_tick as obs_tick
    from src.engine.time_context import city_local_day_end_target_date

    names = sorted(
        name
        for name in obs_tick.cities_by_name
        if obs_tick.tier_for_city(name) is obs_tick.Tier.OGIMET_METAR
    )
    assert len(names) >= 40  # sanity: this must run against the real roster

    db_path = _instants_db(tmp_path)
    start = datetime(2026, 9, 13, 0, 15, tzinfo=timezone.utc)

    # Pre-seed: a warm system has already fulfilled whatever target_date each
    # city was due for BEFORE this window opens (its own prior local-day-end
    # trigger, which happened before `start`). Without this, every city's
    # leftover pre-window obligation would show up as a spurious extra
    # selection at hour=0 -- a cold-start artifact, not the in-window,
    # forward-going "selected exactly once" behavior this test checks.
    for name in names:
        city = obs_tick.cities_by_name[name]
        leftover = city_local_day_end_target_date(city.timezone, start, buffer_hours=1.0)
        if leftover is not None:
            _write_complete_ogimet_day(
                db_path, city=name, station=city.wu_station,
                timezone_name=city.timezone, target_date=leftover.isoformat(),
                unit=city.settlement_unit,
            )

    selected_count: dict[str, int] = {name: 0 for name in names}
    for hour in range(25):
        tick = start + timedelta(hours=hour)
        result = obs_tick._ogimet_cities_due_for_completion(names, tick, db_path=db_path)
        assert not result.fail_open
        for city_name in result.cities:
            selected_count[city_name] += 1
            city = obs_tick.cities_by_name[city_name]
            target_date = city_local_day_end_target_date(city.timezone, tick, buffer_hours=1.0)
            assert target_date is not None
            _write_complete_ogimet_day(
                db_path, city=city_name, station=city.wu_station,
                timezone_name=city.timezone, target_date=target_date.isoformat(),
                unit=city.settlement_unit,
            )

    never_selected = [name for name in names if selected_count[name] == 0]
    selected_twice_or_more = {
        name: count for name, count in selected_count.items() if count > 1
    }
    assert not never_selected, f"never selected within the 24-tick sweep: {never_selected}"
    assert not selected_twice_or_more, (
        f"selected more than once within the 24-tick sweep: {selected_twice_or_more}"
    )


def test_ogimet_local_day_end_fail_open_selects_all_pending_within_budget(
    monkeypatch,
) -> None:
    """When the world DB is unreadable at tick time, the selector fails
    OPEN (every pending, eligible city selected) rather than silently
    skipping -- bounded by 48 x OGIMET_MIN_INTERVAL_SECONDS (~16.8min),
    comfortably inside the hourly tick cadence."""
    import scripts.obs_live_tick as obs_tick
    from src.data.ogimet_hourly_client import OGIMET_MIN_INTERVAL_SECONDS

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "PlusEight": SimpleNamespace(
                name="PlusEight", timezone="Etc/GMT-8", wu_station="ZZZ8",
                settlement_source_type="noaa", settlement_unit="C",
            ),
            "PlusSeven": SimpleNamespace(
                name="PlusSeven", timezone="Etc/GMT-7", wu_station="ZZZ7",
                settlement_source_type="noaa", settlement_unit="C",
            ),
        },
    )
    now_utc = datetime(2026, 9, 13, 17, 15, tzinfo=timezone.utc)
    unreadable_db_path = Path("/nonexistent/definitely/not/a/real/path.db")

    selection = obs_tick._ogimet_cities_due_for_completion(
        ["PlusEight", "PlusSeven"], now_utc, db_path=unreadable_db_path,
    )

    # Only PlusEight is past its 1h buffer at this instant (see the mixed-tz
    # boundary test above); fail-open must select exactly the eligible set,
    # never the whole roster regardless of eligibility.
    assert selection.cities == ["PlusEight"]
    assert selection.fail_open

    worst_case_seconds = 48 * OGIMET_MIN_INTERVAL_SECONDS
    assert worst_case_seconds < 3600.0  # fits the hourly ingest_k2_obs cadence


def test_ogimet_local_day_end_fail_open_is_logged_by_run_live_tick(
    monkeypatch, tmp_path: Path, caplog,
) -> None:
    """The ledger-visible signal: run_live_tick's own log line names
    ``ogimet_fail_open`` so an operator can tell a DB-outage selection
    burst apart from normal completeness-gated selection from the log
    alone (an unreadable world DB is not itself a per-city fetch
    failure). The selection function's own fail-open branch is exercised
    deterministically by the test above; this one isolates the logging
    wiring in ``run_live_tick`` from real-wall-clock eligibility timing by
    fixing what the selector returns."""
    import logging

    import scripts.obs_live_tick as obs_tick

    monkeypatch.setattr(
        obs_tick,
        "cities_by_name",
        {
            "PlusEight": SimpleNamespace(
                name="PlusEight", timezone="Etc/GMT-8", wu_station="ZZZ8",
                settlement_source_type="noaa", settlement_unit="C",
            ),
        },
    )
    monkeypatch.setattr(obs_tick, "tier_for_city", lambda _name: obs_tick.Tier.OGIMET_METAR)
    monkeypatch.setattr(
        obs_tick,
        "_ogimet_cities_due_for_completion",
        lambda *_a, **_k: obs_tick.OgimetSelection(cities=["PlusEight"], fail_open=True),
    )
    monkeypatch.setattr(
        obs_tick, "_tick_ogimet_city",
        lambda *_a, **_k: obs_tick.TickResult(city="PlusEight", tier="OGIMET_METAR"),
    )

    with caplog.at_level(logging.INFO, logger=obs_tick.logger.name):
        obs_tick.run_live_tick(
            city_filter=["PlusEight"],
            dry_run=True,
            db_path=tmp_path / "world.db",
            log_path=tmp_path / "obs.jsonl",
        )

    assert any("FAIL_OPEN" in r.message for r in caplog.records)
    assert any("ogimet_fail_open=True" in r.message for r in caplog.records)


def test_ogimet_local_day_end_worst_cluster_fits_the_hourly_tick_budget() -> None:
    """The worst UTC-hour cluster of cities anchored to the SAME local-day-end
    (e.g. ~10-12 Chinese cities all ending their local day at 16:00Z) must
    fit, serialized at the provider's 21s cadence, comfortably inside the
    hourly ``ingest_k2_obs`` tick cadence (3600s) -- no spreading/ladder
    mechanism is needed."""
    import scripts.obs_live_tick as obs_tick
    from src.data.ogimet_hourly_client import OGIMET_MIN_INTERVAL_SECONDS

    names = sorted(
        name
        for name in obs_tick.cities_by_name
        if obs_tick.tier_for_city(name) is obs_tick.Tier.OGIMET_METAR
    )
    ref_date = date(2026, 9, 13)
    clusters: dict[int, int] = {}
    for name in names:
        tz = ZoneInfo(obs_tick.cities_by_name[name].timezone)
        day_end_utc = datetime.combine(
            ref_date + timedelta(days=1), datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        trigger_hour = (day_end_utc + timedelta(hours=1)).hour
        clusters[trigger_hour] = clusters.get(trigger_hour, 0) + 1

    worst = max(clusters.values())
    worst_case_seconds = worst * OGIMET_MIN_INTERVAL_SECONDS
    # Half the hourly tick cadence as a safety margin against WU cities and
    # per-city HTTP overhead sharing the same job invocation.
    assert worst_case_seconds < 1800.0, (
        f"worst Ogimet cluster ({worst} cities) needs {worst_case_seconds:.0f}s, "
        "too close to the hourly tick cadence"
    )


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

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30)

    assert totals["ogimet_cities_failed"] == 1
    assert totals["ogimet_cities_touched"] == 1
    assert totals["ogimet_rows_written"] == 24


def test_catch_up_missing_instants_derives_ogimet_budget_from_deadline(monkeypatch) -> None:
    """No invented cap: the number of Ogimet cities drained this run is
    floor(remaining_seconds / OGIMET_MIN_INTERVAL_SECONDS) -- the caller's
    own deadline against the provider's documented per-IP interval. With
    65s remaining and a 21s slot, exactly 3 of 4 pending cities drain; the
    4th is left as a MISSING row for the next scan (carry-forward)."""
    import scripts.obs_live_tick as obs_tick
    from src.data.ogimet_hourly_client import OGIMET_MIN_INTERVAL_SECONDS

    assert OGIMET_MIN_INTERVAL_SECONDS == 21.0  # pins the constant this test's arithmetic assumes

    conn = _coverage_db()
    cities = ["London", "Miami", "NYC", "Sao Paulo"]
    sources = {
        "London": "ogimet_metar_eglc",
        "Miami": "ogimet_metar_kmia",
        "NYC": "ogimet_metar_klga",
        "Sao Paulo": "ogimet_metar_sbgr",
    }
    for i, city in enumerate(cities):
        # Oldest-first ordering: London's hole is oldest, Sao Paulo's newest.
        _seed_instants_missing(
            conn, city=city, data_source=sources[city],
            target_date=(date(2026, 9, 10) + timedelta(days=i)).isoformat(),
        )

    drained: list[str] = []

    def fake_ogimet_city(city_name, _conn, *, start_date, end_date, dry_run):
        drained.append(city_name)
        return obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=24)

    monkeypatch.setattr(obs_tick, "_tick_ogimet_city", fake_ogimet_city)

    # 2026-09-13's ordinal % 4 == 0, so the fairness rotation this drain
    # applies whenever budget < population (see the dedicated rotation
    # test below) is a no-op here -- this test isolates the budget
    # arithmetic alone.
    frozen_now = datetime(2026, 9, 13, 4, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(obs_tick, "datetime", SimpleNamespace(now=lambda tz=None: frozen_now))
    deadline = frozen_now + timedelta(seconds=65)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30, deadline=deadline)

    assert drained == ["London", "Miami", "NYC"]
    assert totals["ogimet_cities_touched"] == 3
    assert totals["ogimet_cities_deferred"] == 1


def test_catch_up_missing_instants_zero_budget_drains_nothing_but_wu_still_runs(monkeypatch) -> None:
    """Remaining time smaller than one Ogimet slot -> zero Ogimet cities
    drained (every hole deferred to the next scan), but WU has no provider
    rate limit and still drains fully in the same call."""
    import scripts.obs_live_tick as obs_tick
    from src.data.ogimet_hourly_client import OGIMET_MIN_INTERVAL_SECONDS

    conn = _coverage_db()
    _seed_instants_missing(conn, city="London", data_source="ogimet_metar_eglc", target_date="2026-09-13")
    _seed_instants_missing(conn, city="Taipei", data_source="wu_icao_history", target_date="2026-09-13")

    ogimet_calls: list[str] = []
    wu_calls: list[str] = []
    monkeypatch.setattr(
        obs_tick, "_tick_ogimet_city",
        lambda city_name, *_a, **_k: ogimet_calls.append(city_name)
        or obs_tick.TickResult(city=city_name, tier="OGIMET_METAR"),
    )
    monkeypatch.setattr(
        obs_tick, "_tick_wu_city",
        lambda city_name, *_a, **_k: wu_calls.append(city_name)
        or obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_written=24),
    )

    frozen_now = datetime(2026, 9, 13, 4, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(obs_tick, "datetime", SimpleNamespace(now=lambda tz=None: frozen_now))
    deadline = frozen_now + timedelta(seconds=OGIMET_MIN_INTERVAL_SECONDS - 1)  # < one slot

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30, deadline=deadline)

    assert ogimet_calls == []
    assert wu_calls == ["Taipei"]
    assert totals["ogimet_cities_touched"] == 0
    assert totals["ogimet_cities_deferred"] == 1
    assert totals["wu_cities_touched"] == 1


def test_catch_up_missing_instants_rotates_only_when_budget_below_population(monkeypatch) -> None:
    """When the budget cannot cover every pending Ogimet city, the drain
    applies the same day-rotation-offset fairness formula
    daily_obs_append.catch_up_missing already uses for this identical API,
    so a chronic shortfall cannot let the same subset of cities always win.
    A sufficient budget (tested above) stays pure oldest-first with no
    rotation."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    cities = ["London", "Miami", "NYC", "Sao Paulo"]
    sources = {
        "London": "ogimet_metar_eglc", "Miami": "ogimet_metar_kmia",
        "NYC": "ogimet_metar_klga", "Sao Paulo": "ogimet_metar_sbgr",
    }
    for i, city in enumerate(cities):
        _seed_instants_missing(
            conn, city=city, data_source=sources[city],
            target_date=(date(2026, 9, 10) + timedelta(days=i)).isoformat(),
        )

    drained: list[str] = []
    monkeypatch.setattr(
        obs_tick, "_tick_ogimet_city",
        lambda city_name, *_a, **_k: drained.append(city_name)
        or obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=24),
    )

    # 2026-09-15's ordinal % 4 == 2 (see the offset table in the sibling
    # budget test) -- oldest-first order is [London, Miami, NYC, Sao Paulo];
    # rotating by 2 gives [NYC, Sao Paulo, London, Miami], and budget=3
    # takes the first 3 of THAT: NYC, Sao Paulo, London -- not the pure
    # oldest-first [London, Miami, NYC] a sufficient-budget run would pick.
    frozen_now = datetime(2026, 9, 15, 4, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(obs_tick, "datetime", SimpleNamespace(now=lambda tz=None: frozen_now))
    deadline = frozen_now + timedelta(seconds=65)  # budget=3 < population=4

    obs_tick.catch_up_missing_instants(conn, days_back=30, deadline=deadline)

    assert drained == ["NYC", "Sao Paulo", "London"]


def test_catch_up_missing_instants_writes_through_db_path_not_a_bare_connection(monkeypatch, tmp_path) -> None:
    """The drain must write via db_path (a Path), exactly like
    run_live_tick's production call (ingest_k2_obs_tick passes
    db_path=STATE_DIR/'zeus-world.db', i.e. this module's own
    DEFAULT_DB_PATH) -- never a bare sqlite3.Connection from
    get_world_connection. A bare connection makes _write_rows take its
    direct-insert branch, skipping the db_writer_lock(db_path,
    WriteClass.BULK) + BEGIN IMMEDIATE branch that fires only for a Path
    -- the discipline the live tick relies on against its own concurrent
    writes to this table."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    _seed_instants_missing(conn, city="Taipei", data_source="wu_icao_history", target_date="2026-09-13")
    _seed_instants_missing(conn, city="London", data_source="ogimet_metar_eglc", target_date="2026-09-13")

    received: list = []
    monkeypatch.setattr(
        obs_tick, "_tick_wu_city",
        lambda city_name, target, *_a, **_k: received.append(target)
        or obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_written=1),
    )
    monkeypatch.setattr(
        obs_tick, "_tick_ogimet_city",
        lambda city_name, target, *_a, **_k: received.append(target)
        or obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=1),
    )

    db_path = tmp_path / "zeus-world.db"
    obs_tick.catch_up_missing_instants(conn, days_back=30, db_path=db_path)

    assert len(received) == 2
    for target in received:
        assert target == db_path
        assert isinstance(target, Path)
        assert not isinstance(target, sqlite3.Connection)


def test_catch_up_missing_instants_drained_row_matches_live_tick_stamping(monkeypatch, tmp_path) -> None:
    """A row written by the drain must carry byte-identical
    authority/source_role/training_allowed/causality_status stamping to a
    row written by run_live_tick's own direct call for the same (city,
    source, target_date). Both compute these deterministically from
    source_role_assessment_for_city_source(city, source, date) inside
    insert_rows, and both now write through the identical db_path-based
    _write_rows branch (see the db_path routing test above), so the two
    rows must match exactly."""
    import sqlite3 as _sqlite3
    from src.data.wu_hourly_client import HourlyObservation
    from src.state.data_coverage import DataTable, record_missing
    from src.state.db import init_schema
    import scripts.obs_live_tick as obs_tick

    # Taipei, not Chicago: Chicago's settlement_source_type is actually
    # "noaa" (OGIMET_METAR tier) -- expected_source_for_city resolves the
    # row's `source` from the city's real tier regardless of which tick
    # function is called, so a WU-tier assertion needs a genuinely
    # WU_ICAO-tier city. Taipei is confirmed WU_ICAO via tier_resolver.
    obs_kwargs = dict(
        city="Taipei",
        target_date="2026-09-13",
        local_hour=20.0,
        local_timestamp="2026-09-13T20:00:00+08:00",
        utc_timestamp="2026-09-13T12:00:00+00:00",
        utc_offset_minutes=480,
        dst_active=0,
        is_ambiguous_local_hour=0,
        is_missing_local_hour=0,
        time_basis="utc_hour_bucket_extremum",
        hour_max_temp=31.0,
        hour_min_temp=29.0,
        hour_max_raw_ts="2026-09-13T12:00:00+00:00",
        hour_min_raw_ts="2026-09-13T12:00:00+00:00",
        temp_unit="C",
        station_id="RCSS",
        observation_count=1,
        latest_raw_ts="2026-09-13T12:53:00+00:00",
        latest_temp=30.0,
    )

    def fake_fetch(**_kwargs):
        return SimpleNamespace(failed=False, observations=[HourlyObservation(**obs_kwargs)])

    monkeypatch.setattr(obs_tick, "fetch_wu_hourly", fake_fetch)
    monkeypatch.setattr(
        obs_tick, "proof_of_possession_available_at", lambda _t: "2026-09-14T04:00:00+00:00",
    )

    stamp_cols = ("authority", "source_role", "training_allowed", "causality_status")

    def _stamped_row(db_path):
        c = _sqlite3.connect(str(db_path))
        row = c.execute(
            f"SELECT {', '.join(stamp_cols)} FROM observation_instants "
            "WHERE city='Taipei' AND source='wu_icao_history' AND target_date='2026-09-13'"
        ).fetchone()
        c.close()
        return row

    # (1) The live tick's own direct call -- run_live_tick's production path.
    live_db = tmp_path / "live.db"
    init_schema(_sqlite3.connect(str(live_db)))
    obs_tick._tick_wu_city(
        "Taipei", live_db, start_date=date(2026, 9, 13), end_date=date(2026, 9, 13), dry_run=False,
    )
    live_row = _stamped_row(live_db)
    assert live_row is not None, "setup failure: live-tick direct call wrote no row"

    # (2) The drain, through catch_up_missing_instants -- must reach the
    # same _tick_wu_city call via db_path.
    drain_db = tmp_path / "drain.db"
    init_schema(_sqlite3.connect(str(drain_db)))
    read_conn = _sqlite3.connect(":memory:")
    read_conn.row_factory = _sqlite3.Row
    init_schema(read_conn)
    record_missing(
        read_conn, data_table=DataTable.OBSERVATION_INSTANTS,
        city="Taipei", data_source="wu_icao_history", target_date="2026-09-13",
    )
    obs_tick.catch_up_missing_instants(read_conn, days_back=30, db_path=drain_db)
    drained_row = _stamped_row(drain_db)
    assert drained_row is not None, "setup failure: drain wrote no row"

    assert tuple(drained_row) == tuple(live_row)


def test_catch_up_missing_instants_routes_by_tier_at_the_holes_own_date(monkeypatch) -> None:
    """A city whose effective tier differs between two dates -- the real
    precedent is Taipei's 2026-04-15 settlement_source_type migration,
    tier_resolver.py's own comment -- must have EACH hole routed through
    the fetcher for THAT date's tier, not the city's current tier.
    build_expected_set already resolves per-city sources this way
    (settlement_source_type_for_city(city, target_date)); this drain must
    call tier_for_city with the hole's own target_date to match, or a
    migrated city's old holes become a permanent phantom hole routed to
    the wrong fetcher every scan forever."""
    import scripts.obs_live_tick as obs_tick
    from src.data.tier_resolver import Tier as _Tier

    conn = _coverage_db()
    _seed_instants_missing(conn, city="Testville", data_source="ogimet_metar_xxxx", target_date="2026-04-10")
    _seed_instants_missing(conn, city="Testville", data_source="wu_icao_history", target_date="2026-04-20")

    def fake_tier_for_city(city_name, target_date=None):
        assert target_date is not None, (
            "must resolve tier at the hole's own target_date, not the city's current default"
        )
        return _Tier.OGIMET_METAR if str(target_date) < "2026-04-15" else _Tier.WU_ICAO

    monkeypatch.setattr(obs_tick, "tier_for_city", fake_tier_for_city)

    wu_calls: list[str] = []
    ogimet_calls: list[str] = []
    monkeypatch.setattr(
        obs_tick, "_tick_wu_city",
        lambda city_name, *_a, **_k: wu_calls.append(city_name)
        or obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_written=1),
    )
    monkeypatch.setattr(
        obs_tick, "_tick_ogimet_city",
        lambda city_name, *_a, **_k: ogimet_calls.append(city_name)
        or obs_tick.TickResult(city=city_name, tier="OGIMET_METAR", rows_written=1),
    )

    # Both seeded holes are ~5 months before "today" (whenever the suite
    # runs); a large days_back keeps this test independent of wall-clock
    # date rather than freezing datetime.now for a single-purpose check.
    obs_tick.catch_up_missing_instants(conn, days_back=9999)

    assert wu_calls == ["Testville"]
    assert ogimet_calls == ["Testville"]


def test_catch_up_missing_instants_retries_sqlite_lock_not_a_hard_failure(monkeypatch) -> None:
    """A transient SQLITE_BUSY from a concurrently-running tick (e.g.
    ingest_k2_obs_fast_tick, a different advisory lock) must be retried
    via _run_city_with_sqlite_retry -- the same wrapper run_live_tick uses
    for every city -- not recorded as a permanent failure on first
    contention."""
    import scripts.obs_live_tick as obs_tick

    conn = _coverage_db()
    _seed_instants_missing(conn, city="Taipei", data_source="wu_icao_history", target_date="2026-09-13")

    calls = {"n": 0}

    def flaky_then_ok(city_name, _conn, *, start_date, end_date, dry_run):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return obs_tick.TickResult(city=city_name, tier="WU_ICAO", rows_written=24)

    monkeypatch.setenv("ZEUS_OBS_LIVE_TICK_SQLITE_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr(obs_tick, "_tick_wu_city", flaky_then_ok)
    monkeypatch.setattr(obs_tick.time, "sleep", lambda _delay: None)

    totals = obs_tick.catch_up_missing_instants(conn, days_back=30)

    assert calls["n"] == 2, "must have retried after the first BUSY, not given up"
    assert totals["wu_cities_touched"] == 1
    assert totals["wu_cities_failed"] == 0
    assert totals["wu_rows_written"] == 24
