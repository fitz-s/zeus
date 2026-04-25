# Created: 2026-04-25
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Protect P3 obs_v2 reader gates for canonical diurnal analytics.
# Reuse: Run with tests/test_truth_surface_health.py when changing obs_v2 read predicates.
# Last reused/audited: 2026-04-25
# Authority basis: P3 4.5.B-lite observation_instants_v2 reader gate packet.
"""Regression coverage for obs_v2 reader-gate consumers."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import etl_diurnal_curves
from src.state.db import init_schema


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_provenance() -> str:
    return json.dumps(
        {
            "tier": "WU_ICAO",
            "station_id": "KNYC",
            "payload_hash": "sha256:" + "a" * 64,
            "source_url": "https://api.weather.com/redacted",
            "parser_version": "test_obs_v2_reader_gate_v1",
        },
        sort_keys=True,
    )


UNSAFE_READER_GATE_CASES = [
    pytest.param(
        {"authority": "UNVERIFIED"},
        id="authority",
    ),
    pytest.param(
        {"training_allowed": 0},
        id="training_allowed",
    ),
    pytest.param(
        {"source_role": "fallback_evidence"},
        id="source_role",
    ),
    pytest.param(
        {"causality_status": "UNKNOWN"},
        id="causality_status",
    ),
    pytest.param(
        {"provenance_json": "{}"},
        id="provenance_json_empty",
    ),
    pytest.param(
        {
            "provenance_json": json.dumps(
                {
                    "tier": "WU_ICAO",
                    "station_id": "KNYC",
                    "payload_hash": "sha256:" + "b" * 64,
                },
                sort_keys=True,
            )
        },
        id="provenance_json_missing_parser_source",
    ),
    pytest.param(
        {"provenance_json": "{not-json"},
        id="provenance_json_malformed",
    ),
]


def _seed_instant(
    conn: sqlite3.Connection,
    *,
    day: int,
    temp: float,
    source: str = "wu_icao_history",
    authority: str = "VERIFIED",
    training_allowed: int = 1,
    source_role: str = "historical_hourly",
    causality_status: str = "OK",
    data_version: str = "v1.wu-native",
    provenance_json: str | None = None,
) -> None:
    target_date = f"2026-01-{day:02d}"
    conn.execute(
        """
        INSERT INTO observation_instants_v2 (
            city, target_date, source, timezone_name, local_hour,
            local_timestamp, utc_timestamp, utc_offset_minutes,
            time_basis, temp_current, running_max, temp_unit, station_id,
            imported_at, authority, data_version, provenance_json,
            training_allowed, causality_status, source_role
        ) VALUES (
            'NYC', ?, ?, 'America/New_York', 10,
            ?, ?, -300,
            'utc_hour_bucket_extremum', ?, ?, 'F', 'KNYC',
            '2026-04-25T00:00:00+00:00', ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            target_date,
            source,
            f"{target_date}T10:00:00-05:00",
            f"{target_date}T15:00:00+00:00",
            temp,
            temp,
            authority,
            data_version,
            _safe_provenance() if provenance_json is None else provenance_json,
            training_allowed,
            causality_status,
            source_role,
        ),
    )


def _seed_world(db_path: Path, unsafe_overrides: dict[str, object]) -> None:
    conn = _connect(db_path)
    init_schema(conn)
    conn.execute(
        "UPDATE zeus_meta SET value='v1.wu-native' "
        "WHERE key='observation_data_version'"
    )
    for offset, temp in enumerate([50.0, 51.0, 52.0, 53.0, 54.0], start=1):
        _seed_instant(conn, day=offset, temp=temp)
        _seed_instant(
            conn,
            day=offset,
            temp=100.0 + offset,
            source="unsafe_fallback_source",
            **unsafe_overrides,
        )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("unsafe_overrides", UNSAFE_READER_GATE_CASES)
def test_diurnal_etl_excludes_current_rows_that_fail_reader_gate(
    tmp_path,
    monkeypatch,
    unsafe_overrides,
):
    db_path = tmp_path / "world.db"
    _seed_world(db_path, unsafe_overrides)

    monkeypatch.setattr(
        etl_diurnal_curves,
        "get_connection",
        lambda: _connect(db_path),
    )

    result = etl_diurnal_curves.run_etl()

    assert result["stored"] == 1
    conn = _connect(db_path)
    row = conn.execute(
        """
        SELECT avg_temp, n_samples
        FROM diurnal_curves
        WHERE city='NYC' AND season='DJF' AND hour=10
        """
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["n_samples"] == 5
    assert row["avg_temp"] == pytest.approx(52.0)


@pytest.mark.parametrize("unsafe_overrides", UNSAFE_READER_GATE_CASES)
def test_diurnal_etl_fails_closed_when_current_rows_are_not_reader_safe(
    tmp_path,
    monkeypatch,
    unsafe_overrides,
):
    db_path = tmp_path / "world.db"
    conn = _connect(db_path)
    init_schema(conn)
    conn.execute(
        "UPDATE zeus_meta SET value='v1.wu-native' "
        "WHERE key='observation_data_version'"
    )
    for offset in range(1, 6):
        _seed_instant(
            conn,
            day=offset,
            temp=70.0 + offset,
            source="unsafe_fallback_source",
            **unsafe_overrides,
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        etl_diurnal_curves,
        "get_connection",
        lambda: _connect(db_path),
    )

    result = etl_diurnal_curves.run_etl()

    assert result == {
        "stored": 0,
        "error": "no_reader_safe_observation_instants_current",
        "current_rows": 5,
    }
