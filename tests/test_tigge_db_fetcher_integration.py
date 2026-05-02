# Created: 2026-05-01
# Last reused/audited: 2026-05-02
# Authority basis: live-blockers session 2026-05-01 — TIGGE DB-backed entry evidence relationship;
#                  PR 37 review: registered-ingest cache TTL must not use source capture time.
"""Relationship antibody: TIGGE DB rows → trading-side ensemble evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ISSUE_TIME = "2026-04-29T00:00:00+00:00"
AVAILABLE_AT = "2026-04-29T00:00:00+00:00"
DB_FETCH_TIME = "2026-05-01T14:04:22+00:00"
RECORDED_AT = "2026-05-01 14:04:22"
DECISION_TIME = datetime(2026, 5, 1, 23, 44, 21, tzinfo=timezone.utc)


@pytest.fixture
def fake_city():
    return SimpleNamespace(
        name="London",
        timezone="Europe/London",
        lat=51.5,
        lon=-0.13,
        settlement_unit="C",
    )


@pytest.fixture
def staged_world_db(tmp_path: Path, monkeypatch):
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
    for metric, physical, field, members, data_version in (
        (
            "high",
            "mx2t6_local_calendar_day_max",
            "high_temp",
            high_members,
            "tigge_mx2t6_local_calendar_day_max_v1",
        ),
        (
            "low",
            "mn2t6_local_calendar_day_min",
            "low_temp",
            low_members,
            "tigge_mn2t6_local_calendar_day_min_v1",
        ),
    ):
        conn.execute(
            """INSERT INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
             data_version, causality_status, authority, recorded_at, members_unit)
            VALUES (?, ?, ?, ?, ?, ?, '2026-05-03T00:00:00+00:00', ?, ?, 72.0, ?,
                    ?, 'OK', 'VERIFIED', ?, 'degC')""",
            (
                "London",
                "2026-05-03",
                metric,
                physical,
                field,
                ISSUE_TIME,
                AVAILABLE_AT,
                DB_FETCH_TIME,
                json.dumps(members),
                data_version,
                RECORDED_AT,
            ),
        )
    conn.commit()
    conn.close()

    import src.data.tigge_db_fetcher as tdf
    import src.state.db as state_db

    def _fake_get_world_connection(*_args, **_kwargs):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_world_connection", _fake_get_world_connection)
    monkeypatch.setattr(tdf, "get_world_connection", _fake_get_world_connection)
    return db_path


def test_db_payload_preserves_high_low_extrema_and_source_times(fake_city, staged_world_db):
    from src.data.tigge_client import _fetch_db_payload

    bundle = _fetch_db_payload(fake_city, DECISION_TIME)

    assert bundle is not None
    assert bundle.source_id == "tigge"
    assert bundle.run_init_utc.isoformat() == ISSUE_TIME
    assert bundle.captured_at.isoformat() == DB_FETCH_TIME
    raw = bundle.raw_payload
    assert isinstance(raw, dict)
    assert raw["issue_time"] == ISSUE_TIME
    assert raw["available_at"] == AVAILABLE_AT
    assert raw["fetch_time"] == DB_FETCH_TIME
    assert len(raw["times"]) == 24
    assert len(raw["members_hourly"]) == 51
    assert raw["members_hourly"][0][6] == pytest.approx(10.0)
    assert raw["members_hourly"][0][18] == pytest.approx(20.0)


def test_db_payload_returns_none_when_no_rows(fake_city, tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            issue_time TEXT,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            members_json TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            causality_status TEXT NOT NULL DEFAULT 'OK',
            data_version TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            members_unit TEXT NOT NULL DEFAULT 'degC'
        );
        """
    )
    conn.commit()
    conn.close()

    import src.data.tigge_db_fetcher as tdf
    import src.state.db as state_db

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_world_connection", _fake_conn)
    monkeypatch.setattr(tdf, "get_world_connection", _fake_conn)

    from src.data.tigge_client import _fetch_db_payload

    assert _fetch_db_payload(fake_city, DECISION_TIME) is None


def test_fetch_ensemble_tigge_satisfies_entry_evidence_contract(
    fake_city, staged_world_db, monkeypatch
):
    """Cross-module invariant: DB-backed TIGGE output must pass entry evidence."""
    import src.data.ensemble_client as ec
    import src.data.tigge_client as tc
    from src.data.tigge_client import TIGGEIngest
    from src.engine.evaluator import _entry_forecast_evidence_errors

    ec._clear_cache()
    monkeypatch.setattr(
        ec,
        "gate_source",
        lambda _source_id: SimpleNamespace(
            source_id="tigge",
            authority_tier="FORECAST",
            degradation_level="OK",
            ingest_class=TIGGEIngest,
        ),
    )
    monkeypatch.setattr(ec, "gate_source_role", lambda _spec, _role: None)
    monkeypatch.setattr(tc, "_operator_gate_open", lambda **_kwargs: True)

    result = ec.fetch_ensemble(
        fake_city,
        forecast_days=4,
        model="tigge",
        role="entry_primary",
    )

    assert result is not None
    assert result["source_id"] == "tigge"
    assert result["model"] == "tigge"
    assert result["forecast_source_role"] == "entry_primary"
    assert result["authority_tier"] == "FORECAST"
    assert result["degradation_level"] == "OK"
    assert result["available_at"] == AVAILABLE_AT
    assert result["fetch_time"].isoformat() == DB_FETCH_TIME
    assert _entry_forecast_evidence_errors(result, "2026-05-03", DECISION_TIME) == []


def test_fetch_ensemble_tigge_cache_uses_retrieval_time_not_source_capture(
    fake_city, staged_world_db, monkeypatch
):
    import src.data.ensemble_client as ec
    import src.data.tigge_client as tc
    from src.data.tigge_client import TIGGEIngest

    ec._clear_cache()
    monkeypatch.setattr(
        ec,
        "gate_source",
        lambda _source_id: SimpleNamespace(
            source_id="tigge",
            authority_tier="FORECAST",
            degradation_level="OK",
            ingest_class=TIGGEIngest,
        ),
    )
    monkeypatch.setattr(ec, "gate_source_role", lambda _spec, _role: None)
    monkeypatch.setattr(tc, "_operator_gate_open", lambda **_kwargs: True)
    calls = {"count": 0}
    original_fetch = TIGGEIngest.fetch

    def counted_fetch(self, fetch_time, lead_hours):
        calls["count"] += 1
        return original_fetch(self, fetch_time, lead_hours)

    monkeypatch.setattr(TIGGEIngest, "fetch", counted_fetch)

    first = ec.fetch_ensemble(fake_city, forecast_days=4, model="tigge", role="entry_primary")
    second = ec.fetch_ensemble(fake_city, forecast_days=4, model="tigge", role="entry_primary")

    assert calls["count"] == 1
    assert first is not None
    assert second is not None
    assert first["fetch_time"].isoformat() == DB_FETCH_TIME
    assert first["captured_at"] == DB_FETCH_TIME
    assert "_cache_stored_at" not in first
    assert second["fetch_time"].isoformat() == DB_FETCH_TIME
