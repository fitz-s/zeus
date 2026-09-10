# Created: 2026-09-09
# Last reused/audited: 2026-09-09
# Authority basis: active OpenData coordinate-bound dataset identity contract.
"""Live-entry status must count only the current sealed OpenData identities."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import src.data.live_entry_status as live_status
from src.config import (
    EntryForecastCalibrationPolicyId,
    EntryForecastConfig,
    EntryForecastSourceTransport,
)
from src.data.forecast_fetch_plan import data_version_for_track


UTC = timezone.utc
MANIFEST_A = '{"coordinate_basis":"A"}'
MANIFEST_B = '{"coordinate_basis":"B"}'


def _config() -> EntryForecastConfig:
    return EntryForecastConfig(
        source_id="ecmwf_open_data",
        source_transport=EntryForecastSourceTransport.ENSEMBLE_SNAPSHOTS_V2_DB_READER,
        authority_family="entry_forecast",
        high_track="mx2t6_high_full_horizon",
        low_track="mn2t6_low_full_horizon",
        target_horizon_days=1,
        warm_horizon_days=1,
        source_cycle_policy="current",
        calibration_policy_id=EntryForecastCalibrationPolicyId.ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1,
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots (
            source_id TEXT,
            source_transport TEXT,
            source_run_id TEXT,
            release_calendar_key TEXT,
            source_cycle_time TEXT,
            source_release_time TEXT,
            dataset_id TEXT
        );
        CREATE TABLE readiness_state (
            strategy_key TEXT,
            source_id TEXT,
            track TEXT,
            data_version TEXT,
            status TEXT,
            reason_codes_json TEXT,
            expires_at TEXT
        );
        """
    )
    return conn


def _insert_snapshot(conn: sqlite3.Connection, dataset_id: str, suffix: str) -> None:
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, dataset_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ecmwf_open_data",
            "ensemble_snapshots_db_reader",
            f"run-{suffix}",
            "cycle",
            "2026-09-09T00:00:00+00:00",
            "2026-09-09T01:00:00+00:00",
            dataset_id,
        ),
    )


def _insert_readiness(
    conn: sqlite3.Connection,
    *,
    track: str,
    dataset_id: str,
    status: str = "LIVE_ELIGIBLE",
) -> None:
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    conn.execute(
        """
        INSERT INTO readiness_state (
            strategy_key, source_id, track, data_version, status,
            reason_codes_json, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "producer_readiness",
            "ecmwf_open_data",
            track,
            dataset_id,
            status,
            json.dumps(["READY"]),
            expires,
        ),
    )


def test_count_and_readiness_use_one_sealed_manifest_and_exclude_legacy_rows(monkeypatch) -> None:
    conn = _conn()
    cfg = _config()
    high_a = data_version_for_track(cfg.high_track, MANIFEST_A)
    low_a = data_version_for_track(cfg.low_track, MANIFEST_A)
    high_b = data_version_for_track(cfg.high_track, MANIFEST_B)
    _insert_snapshot(conn, high_a, "high-a")
    _insert_snapshot(conn, low_a, "low-a")
    _insert_snapshot(conn, high_b, "high-b")
    _insert_snapshot(conn, "ecmwf_opendata_mx2t6_local_calendar_day_max", "legacy")
    _insert_readiness(conn, track=cfg.high_track, dataset_id=high_a)
    _insert_readiness(conn, track=cfg.low_track, dataset_id=low_a)
    _insert_readiness(conn, track=cfg.high_track, dataset_id=low_a)
    _insert_readiness(conn, track=cfg.high_track, dataset_id=high_b)
    _insert_readiness(
        conn,
        track=cfg.high_track,
        dataset_id="ecmwf_opendata_mx2t6_local_calendar_day_max",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        live_status,
        "runtime_coordinate_manifest_json",
        lambda: calls.append(MANIFEST_A) or MANIFEST_A,
    )

    result = live_status.build_live_entry_forecast_status(
        conn,
        config=cfg,
        now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )

    assert calls == [MANIFEST_A]
    assert result.status == "LIVE_ELIGIBLE"
    assert result.executable_row_count == 2
    assert result.producer_readiness_count == 2
    assert result.producer_live_eligible_count == 2
    conn.close()


def test_current_manifest_change_excludes_previous_coordinate_identity(monkeypatch) -> None:
    conn = _conn()
    cfg = _config()
    old_high = data_version_for_track(cfg.high_track, MANIFEST_A)
    _insert_snapshot(conn, old_high, "old-a")
    _insert_readiness(conn, track=cfg.high_track, dataset_id=old_high)
    monkeypatch.setattr(live_status, "runtime_coordinate_manifest_json", lambda: MANIFEST_B)

    result = live_status.build_live_entry_forecast_status(
        conn,
        config=cfg,
        now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )

    assert result.status == "BLOCKED"
    assert result.executable_row_count == 0
    assert result.producer_readiness_count == 0
    assert result.producer_live_eligible_count == 0
    assert "ZERO_EXECUTABLE_OPENDATA_ROWS" in result.blockers
    assert "NO_FUTURE_TARGET_DATE_COVERAGE" in result.blockers
    conn.close()
