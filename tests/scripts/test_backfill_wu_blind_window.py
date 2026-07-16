# Lifecycle: created=2026-07-16; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: Prove WU blind-window recovery is chunked, dry-run by default, and writes only through the live writer path.
# Reuse: Re-audit upstream WU and observation-writer contracts before relying on apply-path coverage.
# Authority basis: 5997ee49d — observation_revisions blind-window recovery.
"""Tests for scripts/backfill_wu_blind_window.py."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from scripts.backfill_wu_blind_window import (
    BLIND_WINDOW_END,
    BLIND_WINDOW_START,
    WU_CHUNK_DAYS,
    _chunk_date_range,
    _fetch_chunk,
    run_wu_blind_window_backfill,
)
from src.data.wu_hourly_client import HourlyObservation, WuHourlyFetchResult
from src.state.schema.v2_schema import apply_canonical_schema


def _obs(**overrides) -> HourlyObservation:
    base = dict(
        city="Chicago",
        target_date="2026-06-10",
        local_hour=8.0,
        local_timestamp="2026-06-10T08:00:00-05:00",
        utc_timestamp="2026-06-10T13:00:00+00:00",
        utc_offset_minutes=-300,
        dst_active=1,
        is_ambiguous_local_hour=0,
        is_missing_local_hour=0,
        time_basis="utc_hour_bucket_extremum",
        hour_max_temp=70.0,
        hour_min_temp=68.0,
        hour_max_raw_ts="2026-06-10T13:15:00+00:00",
        hour_min_raw_ts="2026-06-10T13:45:00+00:00",
        temp_unit="F",
        station_id="KORD",
        observation_count=2,
    )
    base.update(overrides)
    return HourlyObservation(**base)


class TestChunkDateRange:
    def test_blind_window_splits_into_two_chunks(self):
        chunks = _chunk_date_range(BLIND_WINDOW_START, BLIND_WINDOW_END, WU_CHUNK_DAYS)

        total_days = (BLIND_WINDOW_END - BLIND_WINDOW_START).days + 1
        assert sum((c[1] - c[0]).days + 1 for c in chunks) == total_days
        assert all((c[1] - c[0]).days + 1 <= WU_CHUNK_DAYS for c in chunks)
        assert chunks[0][0] == BLIND_WINDOW_START
        assert chunks[-1][1] == BLIND_WINDOW_END
        # Chunks are contiguous, no gaps or overlaps.
        for (start_a, end_a), (start_b, _end_b) in zip(chunks, chunks[1:]):
            assert (start_b - end_a).days == 1

    def test_short_range_is_a_single_chunk(self):
        chunks = _chunk_date_range(date(2026, 6, 1), date(2026, 6, 5), WU_CHUNK_DAYS)

        assert chunks == [(date(2026, 6, 1), date(2026, 6, 5))]


class TestFetchChunk:
    def test_failed_fetch_reports_failure_reason_and_no_rows(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(failure_reason="HTTP_429", retryable=True),
        )

        chunk = _fetch_chunk("Chicago", start=date(2026, 6, 1), end=date(2026, 6, 5), conn_ro=None)

        assert chunk["failure_reason"] == "HTTP_429"
        assert chunk["rows_ready"] == 0
        assert chunk["_rows"] == []

    def test_would_widen_detected_against_current_row(self, monkeypatch, tmp_path):
        db_path = tmp_path / "world.db"
        conn = sqlite3.connect(str(db_path))
        apply_canonical_schema(conn)
        conn.execute(
            "INSERT INTO observation_instants (city, target_date, source, timezone_name, local_timestamp, "
            "utc_timestamp, utc_offset_minutes, time_basis, temp_unit, imported_at, authority, data_version, "
            "provenance_json, running_max, running_min, observation_count) VALUES "
            "('Chicago','2026-06-10','wu_icao_history','America/Chicago','2026-06-10T08:00:00-05:00',"
            "'2026-06-10T13:00:00+00:00',-300,'utc_hour_aligned','F','2026-06-10T13:05:00+00:00','VERIFIED',"
            "'v1.wu-native.pilot','{}',65.0,65.0,1)"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(observations=[_obs()], raw_observation_count=1),
        )

        conn_ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            chunk = _fetch_chunk("Chicago", start=date(2026, 6, 10), end=date(2026, 6, 10), conn_ro=conn_ro)
        finally:
            conn_ro.close()

        assert chunk["rows_ready"] == 1
        # Fetched hour_max_temp=70.0 > stored running_max=65.0 -> widens.
        assert chunk["would_widen"] == 1
        assert chunk["missing_locally"] == 0
        # Two prints per observation (max + min timestamps).
        assert len(chunk["_prints"]) == 2

    def test_missing_locally_when_no_current_row(self, monkeypatch, tmp_path):
        db_path = tmp_path / "world.db"
        conn = sqlite3.connect(str(db_path))
        apply_canonical_schema(conn)
        conn.close()

        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(observations=[_obs()], raw_observation_count=1),
        )

        conn_ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            chunk = _fetch_chunk("Chicago", start=date(2026, 6, 10), end=date(2026, 6, 10), conn_ro=conn_ro)
        finally:
            conn_ro.close()

        assert chunk["missing_locally"] == 1
        assert chunk["would_widen"] == 0


class TestRunWuBlindWindowBackfill:
    def test_dry_run_never_writes(self, monkeypatch, tmp_path):
        db_path = tmp_path / "world.db"
        conn = sqlite3.connect(str(db_path))
        apply_canonical_schema(conn)
        conn.close()

        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(observations=[_obs()], raw_observation_count=1),
        )

        results = run_wu_blind_window_backfill(
            start=date(2026, 6, 10), end=date(2026, 6, 10),
            city_filter=["Chicago"], apply=False, db_path=db_path, sleep_seconds=0.0,
        )

        assert results[0]["city"] == "Chicago"
        assert results[0]["chunks"][0]["rows_ready"] == 1
        assert results[0]["chunks"][0]["rows_written"] == 0
        conn = sqlite3.connect(str(db_path))
        assert conn.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 0
        conn.close()

    def test_apply_writes_through_insert_rows(self, monkeypatch, tmp_path):
        db_path = tmp_path / "world.db"
        conn = sqlite3.connect(str(db_path))
        apply_canonical_schema(conn)
        conn.close()

        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(observations=[_obs()], raw_observation_count=1),
        )

        results = run_wu_blind_window_backfill(
            start=date(2026, 6, 10), end=date(2026, 6, 10),
            city_filter=["Chicago"], apply=True, db_path=db_path, sleep_seconds=0.0,
        )

        assert results[0]["chunks"][0]["rows_written"] == 1
        conn = sqlite3.connect(str(db_path))
        assert conn.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 1
        conn.close()

    def test_only_wu_icao_cities_are_included(self, monkeypatch, tmp_path):
        db_path = tmp_path / "world.db"
        conn = sqlite3.connect(str(db_path))
        apply_canonical_schema(conn)
        conn.close()

        monkeypatch.setattr(
            "scripts.backfill_wu_blind_window.fetch_wu_hourly",
            lambda **kw: WuHourlyFetchResult(observations=[], raw_observation_count=0),
        )

        results = run_wu_blind_window_backfill(
            start=date(2026, 6, 10), end=date(2026, 6, 10),
            city_filter=["Hong Kong"], apply=False, db_path=db_path, sleep_seconds=0.0,
        )

        assert results == []
