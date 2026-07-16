# Lifecycle: created=2026-07-16; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: Prove the blind-window diagnostic is read-only and labels only heuristic exposure.
# Reuse: Re-audit the diagnostic query and fixture schema before using these tests as evidence.
# Authority basis: 5997ee49d — observation_revisions blind-window sizing pass.
"""Tests for scripts/audit_observation_revisions_blind_window_exposure.py."""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.audit_observation_revisions_blind_window_exposure import (
    _baseline_comparison,
    scan_blind_window_exposure,
)
from src.data.observation_instants_writer import ObsV2Row, insert_rows
from src.state.schema.v2_schema import apply_canonical_schema


def _valid_provenance(**overrides) -> str:
    data = {
        "tier": "WU_ICAO",
        "station_id": "KORD",
        "payload_hash": "sha256:" + "a" * 64,
        "source_url": "https://api.weather.com/v1/location/KORD:9:US/observations/historical.json",
        "parser_version": "test_v1",
    }
    data.update(overrides)
    return json.dumps(data, sort_keys=True)


def _row(*, target_date: str = "2026-06-10", **overrides) -> ObsV2Row:
    """Chicago WU row at local 08:00 (UTC 13:00) on ``target_date``.

    Passing ``target_date`` keeps utc_timestamp/local_timestamp self-
    consistent (the writer's A1 identity check rejects a mismatch) — any
    caller that wants a different day should go through this param, not
    override utc_timestamp/local_timestamp piecemeal.
    """
    base = dict(
        city="Chicago",
        target_date=target_date,
        source="wu_icao_history",
        timezone_name="America/Chicago",
        local_hour=8.0,
        local_timestamp=f"{target_date}T08:00:00-05:00",
        utc_timestamp=f"{target_date}T13:00:00+00:00",
        utc_offset_minutes=-300,
        time_basis="utc_hour_aligned",
        temp_unit="F",
        imported_at=f"{target_date}T13:05:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        temp_current=None,
        running_max=70.0,
        running_min=70.0,
        observation_count=1,
        station_id="KORD",
    )
    base.update(overrides)
    return ObsV2Row(**base)


@pytest.fixture
def db_path(tmp_path) -> str:
    path = tmp_path / "world.db"
    conn = sqlite3.connect(str(path))
    apply_canonical_schema(conn)
    conn.close()
    return str(path)


def _ro_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


class TestScanBlindWindowExposure:
    def test_finds_single_report_never_widened_cell(self, db_path):
        conn = sqlite3.connect(db_path)
        insert_rows(conn, [_row()])
        conn.commit()
        conn.close()

        cells = scan_blind_window_exposure(_ro_conn(db_path), start="2026-05-28", end="2026-07-16")

        assert len(cells) == 1
        assert cells[0]["city"] == "Chicago"
        assert cells[0]["observation_count"] == 1

    def test_excludes_multi_report_cells(self, db_path):
        conn = sqlite3.connect(db_path)
        insert_rows(conn, [_row(observation_count=3)])
        conn.commit()
        conn.close()

        assert scan_blind_window_exposure(_ro_conn(db_path), start="2026-05-28", end="2026-07-16") == []

    def test_excludes_cells_outside_the_window(self, db_path):
        conn = sqlite3.connect(db_path)
        insert_rows(conn, [_row(target_date="2026-05-01")])
        conn.commit()
        conn.close()

        assert scan_blind_window_exposure(_ro_conn(db_path), start="2026-05-28", end="2026-07-16") == []

    def test_excludes_cells_with_widening_applied_revision(self, db_path):
        from src.data.observation_instants_writer import _insert_revision, _payload_hash_from_provenance, _row_to_dict

        conn = sqlite3.connect(db_path)
        first = _row()
        insert_rows(conn, [first])
        conn.commit()

        wider = _row(running_max=75.0, observation_count=2, provenance_json=_valid_provenance(payload_hash="sha256:" + "b" * 64))
        from src.data.observation_instants_writer import _fetch_existing
        existing_dict = _fetch_existing(conn, {"city": "Chicago", "source": "wu_icao_history", "utc_timestamp": first.utc_timestamp})
        _insert_revision(
            conn,
            existing=existing_dict,
            incoming=_row_to_dict(wider),
            existing_payload_hash=_payload_hash_from_provenance(existing_dict["provenance_json"]),
            incoming_payload_hash="sha256:" + "b" * 64,
            reason="payload_hash_mismatch_monotone_widening_applied",
        )
        conn.commit()
        conn.close()

        assert scan_blind_window_exposure(_ro_conn(db_path), start="2026-05-28", end="2026-07-16") == []


class TestBaselineComparison:
    def test_flags_a_genuinely_elevated_city(self, db_path):
        conn = sqlite3.connect(db_path)
        rows = []
        # Baseline period (2026-05-21..05-25): mostly count=2 (healthy).
        for day in range(1, 6):
            rows.append(_row(target_date=f"2026-05-{20 + day:02d}", observation_count=2))
        # Window period (2026-06-01..06-05): mostly count=1 (exposed) — a real elevation.
        for day in range(1, 6):
            rows.append(_row(target_date=f"2026-06-{day:02d}", observation_count=1))
        insert_rows(conn, rows)
        conn.commit()
        conn.close()

        ro = _ro_conn(db_path)
        cells = scan_blind_window_exposure(ro, start="2026-05-28", end="2026-06-05")
        comparison = _baseline_comparison(ro, cells, start="2026-05-28", end="2026-06-05")

        assert comparison["Chicago"]["elevated"] is True
        assert comparison["Chicago"]["window_rate"] > comparison["Chicago"]["baseline_rate"]
