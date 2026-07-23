# Created: 2026-05-17
# Lifecycle: created=2026-05-17; last_reviewed=2026-07-23; last_reused=2026-07-23
# Last reused or audited: 2026-07-23
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
from datetime import date, timedelta
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

    def fake_tick(city_name, conn, *, start_date, end_date, dry_run):
        assert not lock_held, f"{city_name} fetch/build ran while writer lock was held"
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
    )

    assert [r.rows_written for r in results] == [1, 1]
    assert lock_entries == 2
    assert not lock_held


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
