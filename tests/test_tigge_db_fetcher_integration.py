# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live-blockers session 2026-05-01 — TIGGE F3 trading-side
#   loader was deliberately a "JSON-only" stub; the data-ingest daemon
#   writes ensemble_snapshots_v2 directly. This relationship antibody
#   covers the integration that closes that loop: TIGGEIngest reading
#   from DB when a city is provided and no JSON payload is configured.
"""Relationship antibody: TIGGE DB fetcher → trading-side ensemble path.

Tests the cross-module invariant that was missing on 2026-05-01:
``fetch_ensemble(model='tigge', role='entry_primary')`` MUST succeed
when the data-ingest daemon has populated ``ensemble_snapshots_v2``
for the requested city, even without a pre-staged JSON payload file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_city():
    """City stub matching the attributes the fetcher reads."""
    return SimpleNamespace(
        name="London",
        timezone="Europe/London",
        lat=51.5,
        lon=-0.13,
        settlement_unit="C",
    )


@pytest.fixture
def staged_world_db(tmp_path: Path, monkeypatch):
    """Build a minimal ensemble_snapshots_v2 with one high + one low row for London."""
    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            lead_hours REAL NOT NULL,
            members_json TEXT NOT NULL,
            p_raw_json TEXT,
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT,
            data_version TEXT NOT NULL,
            training_allowed INTEGER NOT NULL DEFAULT 1,
            causality_status TEXT NOT NULL DEFAULT 'OK',
            boundary_ambiguous INTEGER NOT NULL DEFAULT 0,
            ambiguous_member_count INTEGER NOT NULL DEFAULT 0,
            manifest_hash TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            members_unit TEXT NOT NULL DEFAULT 'degC',
            members_precision REAL,
            local_day_start_utc TEXT,
            step_horizon_hours REAL,
            unit TEXT
        );
        """
    )

    high_members = [20.0 + 0.1 * i for i in range(51)]
    low_members = [10.0 + 0.1 * i for i in range(51)]
    fresh_recorded_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO ensemble_snapshots_v2
        (city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
         data_version, causality_status, authority, recorded_at, members_unit)
        VALUES (?, ?, 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                '2026-04-29T00:00:00+00:00', '2026-05-02', ?, ?, 72.0, ?,
                'tigge_mx2t6_local_calendar_day_max_v1', 'OK', 'VERIFIED', ?, 'degC')""",
        ("London", "2026-05-02", fresh_recorded_at, fresh_recorded_at,
         json.dumps(high_members), fresh_recorded_at),
    )
    conn.execute(
        """INSERT INTO ensemble_snapshots_v2
        (city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
         data_version, causality_status, authority, recorded_at, members_unit)
        VALUES (?, ?, 'low', 'mn2t6_local_calendar_day_min', 'low_temp',
                '2026-04-29T00:00:00+00:00', '2026-05-02', ?, ?, 72.0, ?,
                'tigge_mn2t6_local_calendar_day_min_v1', 'OK', 'VERIFIED', ?, 'degC')""",
        ("London", "2026-05-02", fresh_recorded_at, fresh_recorded_at,
         json.dumps(low_members), fresh_recorded_at),
    )
    conn.commit()
    conn.close()

    import src.state.db as state_db
    monkeypatch.setattr(state_db, "WORLD_DB_PATH", db_path, raising=False)

    def _fake_get_world_connection(*_args, **_kwargs):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_world_connection", _fake_get_world_connection)
    import src.data.tigge_db_fetcher as tdf
    monkeypatch.setattr(tdf, "get_world_connection", _fake_get_world_connection)

    return db_path


def test_db_fetcher_returns_synthetic_grid_with_high_and_low_for_one_target_date(
    fake_city, staged_world_db
):
    """Invariant: with one (city, target_date) high+low row pair, the fetcher
    returns a 51 × 24 grid where afternoon hours equal the daily high and
    morning hours equal the daily low (member-wise)."""
    from src.data.tigge_client import _fetch_db_payload
    fetch_time = datetime.now(timezone.utc)

    payload = _fetch_db_payload(fake_city, fetch_time)
    assert payload is not None
    times = payload["times"]
    members_hourly = payload["members_hourly"]
    assert len(times) == 24
    assert len(members_hourly) == 51

    high_expected = 20.0
    low_expected = 10.0
    member0 = members_hourly[0]
    morning_value = member0[6]
    afternoon_value = member0[18]
    assert abs(morning_value - low_expected) < 1e-6, (
        f"Morning hour 6 must equal daily low; got {morning_value}, expected {low_expected}"
    )
    assert abs(afternoon_value - high_expected) < 1e-6, (
        f"Afternoon hour 18 must equal daily high; got {afternoon_value}, expected {high_expected}"
    )

    member_max = max(member0)
    member_min = min(member0)
    assert abs(member_max - high_expected) < 1e-6
    assert abs(member_min - low_expected) < 1e-6


def test_db_fetcher_returns_none_when_no_rows(fake_city, tmp_path, monkeypatch):
    """Invariant: empty DB → None (caller treats as 'no ensemble', skips candidate)."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            members_json TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            causality_status TEXT NOT NULL DEFAULT 'OK',
            data_version TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            issue_time TEXT,
            available_at TEXT NOT NULL,
            members_unit TEXT NOT NULL DEFAULT 'degC'
        );
        """
    )
    conn.commit()
    conn.close()

    import src.state.db as state_db
    import src.data.tigge_db_fetcher as tdf

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_world_connection", _fake_conn)
    monkeypatch.setattr(tdf, "get_world_connection", _fake_conn)

    from src.data.tigge_client import _fetch_db_payload
    payload = _fetch_db_payload(fake_city, datetime.now(timezone.utc))
    assert payload is None


def test_tigge_ingest_with_city_uses_db_when_no_json_payload(
    fake_city, staged_world_db, monkeypatch
):
    """End-to-end: TIGGEIngest(city=...) with the gate open and no JSON
    payload configured falls through to the DB fetcher and returns a
    valid bundle. This is the integration that unblocks live entries."""
    monkeypatch.setenv("ZEUS_TIGGE_INGEST_ENABLED", "1")

    from src.data.tigge_client import TIGGEIngest

    def _fake_gate_open(**_kwargs):
        return True

    import src.data.tigge_client as tc
    monkeypatch.setattr(tc, "_operator_gate_open", _fake_gate_open)

    ingest = TIGGEIngest(city=fake_city)
    bundle = ingest.fetch(datetime.now(timezone.utc), tuple(range(96)))

    assert bundle is not None
    assert bundle.source_id == "tigge"
    raw = bundle.raw_payload
    assert isinstance(raw, dict)
    assert "members_hourly" in raw
    assert len(raw["members_hourly"]) == 51
