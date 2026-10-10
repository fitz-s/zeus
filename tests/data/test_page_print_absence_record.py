# Created: 2026-10-08
# Last audited: 2026-10-08
# Authority basis: G10 (task_2026-10-06_fast_obs_closure): page-row deletion after first
#   appearance was unobservable; the intraday noaa_wrh tick records a held page clock that
#   a covering fetch omitted, in world fact_revocations, only on absence.
# Purpose: Absence is recorded once per omitted clock, only inside the covered span, never
#   per fetch, and never blocks the print write.
# Reuse: Run when record_page_print_absences, the physical-current tick or the report
#   script changes.
"""A covering page fetch that omits a held clock leaves exactly one absence record."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.state.fact_revocation import (
    OBSERVATION_PRINTS_TABLE,
    REASON_PAGE_PRINT_ABSENT,
    record_page_print_absences,
)
from src.state.schema.fact_revocations_schema import ensure_table as ensure_revocations
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _conn(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    ensure_table(conn)
    ensure_revocations(conn)
    return conn


def _hold(conn, minutes: list[int], *, receipt=T0 + timedelta(hours=3)):
    for minute in minutes:
        append_print(conn, city="Austin", station_id="KAUS", source_channel="noaa_wrh_kaus",
                     publish_ts_utc=(T0 + timedelta(minutes=minute)).isoformat(), value_native=75.02,
                     unit="F", fetched_at_utc=receipt.isoformat(), raw_report=None)


def _record(conn, minutes: list[int], fetched=T0 + timedelta(hours=4)) -> int:
    return record_page_print_absences(
        conn, city="Austin", station_id="KAUS", source_channel="noaa_wrh_kaus",
        returned_clocks=[(T0 + timedelta(minutes=m)).isoformat() for m in minutes],
        fetched_at_utc=fetched.isoformat())


def _absent_clocks(conn) -> list[str]:
    return [row[0] for row in conn.execute(
        """SELECT p.publish_ts_utc FROM fact_revocations r
             JOIN observation_prints p ON p.id = CAST(r.row_id AS INTEGER)
            WHERE r.table_name = ? AND r.reason_code = ? ORDER BY p.publish_ts_utc""",
        (OBSERVATION_PRINTS_TABLE, REASON_PAGE_PRINT_ABSENT))]


def test_a_held_clock_omitted_inside_the_returned_span_is_recorded_once():
    conn = _conn()
    _hold(conn, [0, 30, 53, 113])
    assert _record(conn, [0, 53, 113]) == 1
    assert _absent_clocks(conn) == [(T0 + timedelta(minutes=30)).isoformat()]
    # The same omission in every later covering fetch writes nothing more.
    assert _record(conn, [0, 53, 113], fetched=T0 + timedelta(hours=5)) == 0
    assert conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0] == 1


def test_clocks_outside_the_span_and_present_clocks_write_nothing():
    conn = _conn()
    _hold(conn, [-60, 0, 53, 180])
    # -60 and 180 lie outside [0, 53]: a short window is not evidence of deletion.
    assert _record(conn, [0, 53]) == 0
    assert _record(conn, [0, 20, 53]) == 0  # a new clock is not an absence either
    assert _record(conn, [53]) == 0  # one returned clock spans nothing
    assert conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0] == 0


def test_the_record_tags_the_clocks_newest_version():
    conn = _conn()
    clock = (T0 + timedelta(minutes=5)).isoformat()
    for value, receipt in ((73.94, T0 + timedelta(minutes=9)), (73.4, T0 + timedelta(minutes=12))):
        append_print(conn, city="Austin", station_id="KAUS", source_channel="noaa_wrh_kaus",
                     publish_ts_utc=clock, value_native=value, unit="F",
                     fetched_at_utc=receipt.isoformat(), raw_report=None)
    _hold(conn, [0, 53])
    assert _record(conn, [0, 53]) == 1
    tagged = conn.execute("SELECT CAST(row_id AS INTEGER) FROM fact_revocations").fetchone()[0]
    newest = conn.execute("SELECT MAX(id) FROM observation_prints WHERE publish_ts_utc = ?",
                          (clock,)).fetchone()[0]
    assert tagged == newest


def test_other_channels_and_stations_are_never_judged_by_this_fetch():
    conn = _conn()
    _hold(conn, [0, 53])
    append_print(conn, city="Austin", station_id="KAUS", source_channel="aviationweather_metar",
                 publish_ts_utc=(T0 + timedelta(minutes=30)).isoformat(), value_native=24.0,
                 unit="C", fetched_at_utc=T0.isoformat(), raw_report="KAUS 071230Z 24/20")
    assert _record(conn, [0, 53]) == 0


def test_tick_records_page_absence_in_the_print_transaction(monkeypatch, tmp_path):
    from src.config import cities_by_name
    from src.data import replacement_forecast_production as production
    from src.data import station_temperature_adapters as adapters
    from src.data.physical_current_sources import load_physical_current_sources
    from src.state import db, write_coordinator as coordinator
    import src.ingest_main as ingest

    city = cities_by_name["Austin"]
    route = next(r for r in load_physical_current_sources()[0]
                 if r.provider == "noaa_wrh" and r.station_id == "KAUS")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=90)
    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        ensure_revocations(conn)
        for minute in (0, 30, 60):
            append_print(conn, city="Austin", station_id="KAUS", source_channel=route.source_channel,
                         publish_ts_utc=(start + timedelta(minutes=minute)).isoformat(), value_native=75.02,
                         unit="F", fetched_at_utc=(start + timedelta(minutes=minute + 4)).isoformat(),
                         raw_report=None)
    samples = tuple(adapters._sample(route, start + timedelta(minutes=minute), 75.02, now, "a" * 64)
                    for minute in (0, 60))

    class Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def record_commit(self, **_):
            pass

    monkeypatch.setattr(adapters, "fetch_station_temperature", lambda *a, **k: samples)
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr(db, "get_world_connection", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lambda *a, **k: Lease()))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: {"status": "FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr("src.data.physical_current_delivery.current_temperature_priority_families",
                        lambda: {})
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", {})
    result = ingest._day0_current_temperature_source_tick(city, route)
    assert result["status"] == "COMMITTED"
    with sqlite3.connect(path) as conn:
        assert _absent_clocks(conn) == [(start + timedelta(minutes=30)).isoformat()]


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("disk I/O error"), ValueError("bad clock")])
def test_absence_failure_never_costs_the_print_write(monkeypatch, tmp_path, failure):
    from src.config import cities_by_name
    from src.state import fact_revocation
    from src.data import replacement_forecast_production as production
    from src.data import station_temperature_adapters as adapters
    from src.data.physical_current_sources import load_physical_current_sources
    from src.state import db, write_coordinator as coordinator
    import src.ingest_main as ingest

    city = cities_by_name["Austin"]
    route = next(r for r in load_physical_current_sources()[0]
                 if r.provider == "noaa_wrh" and r.station_id == "KAUS")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
    samples = tuple(adapters._sample(route, now - timedelta(minutes=m), 75.02, now, "a" * 64)
                    for m in (60, 0))
    calls = []

    def failing_record(conn, **kw):
        # Write inside the savepoint first, so the rollback has something to undo.
        conn.execute("CREATE TABLE absence_probe (x)")
        calls.append(kw)
        raise failure

    monkeypatch.setattr(fact_revocation, "record_page_print_absences", failing_record)

    class Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def record_commit(self, **_):
            pass

    monkeypatch.setattr(adapters, "fetch_station_temperature", lambda *a, **k: samples)
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr(db, "get_world_connection", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lambda *a, **k: Lease()))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: {"status": "FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr("src.data.physical_current_delivery.current_temperature_priority_families",
                        lambda: {})
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", {})
    result = ingest._day0_current_temperature_source_tick(city, route)
    assert len(calls) == 1, "the absence record must have been attempted"
    assert result["status"] == "COMMITTED" and result["inserted"] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'absence_probe'"
        ).fetchone()[0] == 0, "the savepoint rollback must undo the failed absence write"


def test_report_counts_absences_per_station_and_unit(tmp_path, capsys):
    from scripts.audit_page_print_absences import main

    path = tmp_path / "world.sqlite"
    conn = _conn(path)
    _hold(conn, [0, 30, 45, 53])
    assert _record(conn, [0, 53]) == 2
    conn.commit()
    conn.close()
    assert main(["--world-db", str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].split("\t")[:3] == ["KAUS", "F", "2"]
    assert out[-1] == "total\t\t2"
