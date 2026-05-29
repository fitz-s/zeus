# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: DAY0-P1 task brief; src/data/ecmwf_open_data_ingest.py _query_metric
#   fix to prefer FULL_CONTRIBUTOR (contributes_to_target_extrema=1) over MAX(snapshot_id).
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: DAY0-P1 antibody tests — FULL_CONTRIBUTOR run selection correctness for
#          _query_metric (issue_time DESC + central POSITIVE set filters).
# Reuse: Run when modifying _query_metric, FULL_CONTRIBUTOR selection, or day0 shadow path.
"""
DAY0-P1 forecast-authority parity antibody.

Defect: _query_metric selects MAX(snapshot_id) = latest-inserted snapshot, which
for far-east same-day targets is the cold post-peak 12Z run.  The correct
selection is the FULL_CONTRIBUTOR run (contributes_to_target_extrema=1,
forecast_window_attribution_status in POSITIVE set, boundary_ambiguous=0),
falling back to latest only when no FULL_CONTRIBUTOR exists.

Scenario: Taipei, target_date=today (day0 candidate).
- Snapshot A: 00Z run, contributes_to_target_extrema=1, WARM members (30°C).
- Snapshot B: 12Z run, contributes_to_target_extrema=0, COLD members (15°C).
  snapshot_B.snapshot_id > snapshot_A.snapshot_id  ← MAX picks B (pre-fix).

Expected post-fix: _query_metric returns the 00Z (warm) run, NOT the 12Z (cold) run.
RED against unmodified main (selects cold 12Z), GREEN after fix (selects warm 00Z).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)  # 14Z UTC = far-east afternoon
_TARGET_DATE = "2026-05-23"  # same-day for a +8 city like Taipei
_RECORDED_AT_A = "2026-05-23 06:00:00"   # 00Z run recorded at 06Z
_RECORDED_AT_B = "2026-05-23 13:00:00"   # 12Z run recorded at 13Z (later, so MAX picks B)
_ISSUE_TIME_A = "2026-05-23T00:00:00+00:00"  # 00Z run — peak-capturing for far-east day
_ISSUE_TIME_B = "2026-05-23T12:00:00+00:00"  # 12Z run — post-peak, cold
_AVAILABLE_AT_A = "2026-05-23T06:00:00+00:00"
_AVAILABLE_AT_B = "2026-05-23T13:00:00+00:00"
_FETCH_TIME = _NOW.strftime("%Y-%m-%dT%H:%M:%S+00:00")

_WARM_DAILY_MAX = [30.0 + 0.1 * i for i in range(51)]  # 00Z: warm, peak-capturing
_COLD_DAILY_MAX = [15.0 + 0.1 * i for i in range(51)]  # 12Z: cold, post-peak

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ensemble_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    physical_quantity TEXT NOT NULL DEFAULT '',
    observation_field TEXT NOT NULL DEFAULT '',
    issue_time TEXT,
    valid_time TEXT,
    available_at TEXT NOT NULL,
    fetch_time TEXT NOT NULL,
    lead_hours REAL NOT NULL DEFAULT 72.0,
    members_json TEXT NOT NULL,
    p_raw_json TEXT,
    spread REAL,
    is_bimodal INTEGER,
    model_version TEXT,
    dataset_id TEXT NOT NULL,
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
    unit TEXT,
    source_id TEXT NOT NULL DEFAULT 'ecmwf_open_data',
    source_transport TEXT,
    source_run_id TEXT,
    forecast_window_attribution_status TEXT,
    contributes_to_target_extrema INTEGER
        CHECK (contributes_to_target_extrema IS NULL OR contributes_to_target_extrema IN (0, 1))
);
"""

_INSERT_SQL = """
INSERT INTO ensemble_snapshots
    (city, target_date, temperature_metric, available_at, fetch_time,
     members_json, dataset_id, causality_status, authority,
     recorded_at, members_unit, issue_time, source_id,
     forecast_window_attribution_status, contributes_to_target_extrema)
VALUES (?, ?, ?, ?, ?, ?, ?, 'OK', 'VERIFIED', ?, 'degC', ?, 'ecmwf_open_data', ?, ?)
"""


@pytest.fixture
def taipei_day0_db(tmp_path: Path, monkeypatch):
    """Two snapshots for Taipei/high/today:
    - snapshot A (00Z): contributes_to_target_extrema=1, WARM members  ← correct
    - snapshot B (12Z): contributes_to_target_extrema=0, COLD members  ← MAX picks this pre-fix
    B has the larger snapshot_id (inserted last), so MAX(snapshot_id) = B.
    """
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_CREATE_TABLE_SQL)

    # Insert A (00Z warm FULL_CONTRIBUTOR) first — gets lower snapshot_id
    conn.execute(
        _INSERT_SQL,
        (
            "Taipei", _TARGET_DATE, "high",
            _AVAILABLE_AT_A, _FETCH_TIME,
            json.dumps(_WARM_DAILY_MAX),
            "ecmwf_opendata_mx2t3_local_calendar_day_max",
            _RECORDED_AT_A, _ISSUE_TIME_A,
            "FULLY_INSIDE_TARGET_LOCAL_DAY",  # POSITIVE attribution → FULL_CONTRIBUTOR
            1,  # contributes_to_target_extrema=1
        ),
    )
    # Insert B (12Z cold NON_CONTRIBUTOR) second — gets higher snapshot_id
    conn.execute(
        _INSERT_SQL,
        (
            "Taipei", _TARGET_DATE, "high",
            _AVAILABLE_AT_B, _FETCH_TIME,
            json.dumps(_COLD_DAILY_MAX),
            "ecmwf_opendata_mx2t3_local_calendar_day_max",
            _RECORDED_AT_B, _ISSUE_TIME_B,
            "OUTSIDE_TARGET_LOCAL_DAY",  # NOT in POSITIVE set → NON_CONTRIBUTOR
            0,  # contributes_to_target_extrema=0
        ),
    )
    conn.commit()
    conn.close()

    import src.data.ecmwf_open_data_ingest as eod
    import src.state.db as state_db

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
    monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)
    return db_path


def _get_selected_members(db_path: Path, now_utc: datetime) -> list[float]:
    """Run _query_metric (via _fetch_db_payload) and return the selected daily-max members."""
    from src.data.ecmwf_open_data_ingest import _fetch_db_payload

    city = SimpleNamespace(
        name="Taipei",
        timezone="Asia/Taipei",
        lat=25.04,
        lon=121.53,
        settlement_unit="C",
    )
    bundle = _fetch_db_payload(city, now_utc, temperature_metric="high")
    assert bundle is not None, "_fetch_db_payload returned None — no qualifying rows"
    # members_hourly shape is (51, n_hours). For a single-day target the daily
    # extrema vector is broadcast across all 24 hours. Extract hour=0 column.
    import numpy as np
    arr = np.asarray(bundle.ensemble_members)  # (51, n_hours)
    # The array structure is list of per-member hourly arrays.
    # For a single target_date, all hours carry the same vector.
    return [float(arr[m][0]) for m in range(51)]


class TestDay0ForecastAuthorityParityRED:
    """RED test: proves the pre-fix defect — MAX(snapshot_id) selects the cold 12Z run."""

    def test_current_selects_cold_12z_run(self, taipei_day0_db: Path):
        """Pre-fix: _query_metric returns cold 12Z members (MAX snapshot_id = B).

        This test is expected to FAIL after the fix is applied.
        It documents the defect baseline.
        """
        members = _get_selected_members(taipei_day0_db, _NOW)
        # If the fix is NOT applied, MAX(snapshot_id) picks the cold 12Z run.
        # member[0] of cold run = 15.0; member[0] of warm run = 30.0.
        cold_member_0 = _COLD_DAILY_MAX[0]
        warm_member_0 = _WARM_DAILY_MAX[0]

        if abs(members[0] - cold_member_0) < 0.01:
            pytest.fail(
                f"DEFECT CONFIRMED (pre-fix baseline): selected members[0]={members[0]:.1f}°C "
                f"matches COLD 12Z run ({cold_member_0:.1f}°C). "
                f"Fix not yet applied — MAX(snapshot_id) bypasses FULL_CONTRIBUTOR selection."
            )
        elif abs(members[0] - warm_member_0) < 0.01:
            # Fix already applied — this test documents the pre-fix state.
            # We don't fail here; instead the GREEN test confirms correctness.
            pytest.skip(
                "Fix already applied: selected members[0] matches WARM 00Z run. "
                "RED baseline test is satisfied by fix application."
            )
        else:
            pytest.fail(
                f"Unexpected members[0]={members[0]:.1f}°C "
                f"(expected cold={cold_member_0:.1f} or warm={warm_member_0:.1f})"
            )


class TestDay0ForecastAuthorityParityGREEN:
    """GREEN test: asserts correct behavior after fix — FULL_CONTRIBUTOR 00Z selected."""

    def test_fixed_selects_warm_00z_full_contributor(self, taipei_day0_db: Path):
        """Post-fix: _query_metric returns warm 00Z FULL_CONTRIBUTOR members.

        Structural assertion: when a 00Z FULL_CONTRIBUTOR (contributes_to_target_extrema=1)
        and a 12Z NON_CONTRIBUTOR (contributes_to_target_extrema=0) exist for the same
        (city, target_date, temperature_metric), the 00Z run MUST be selected.
        """
        members = _get_selected_members(taipei_day0_db, _NOW)
        warm_member_0 = _WARM_DAILY_MAX[0]
        cold_member_0 = _COLD_DAILY_MAX[0]

        assert abs(members[0] - warm_member_0) < 0.01, (
            f"FAIL: selected members[0]={members[0]:.1f}°C does NOT match "
            f"WARM 00Z FULL_CONTRIBUTOR ({warm_member_0:.1f}°C). "
            f"Got cold={cold_member_0:.1f}. "
            "Fix not working: _query_metric still selects MAX(snapshot_id) instead of "
            "FULL_CONTRIBUTOR-first."
        )
        # Also verify all 51 members match the warm run
        for i, m in enumerate(members):
            assert abs(m - _WARM_DAILY_MAX[i]) < 0.01, (
                f"members[{i}]={m:.2f} != warm_run[{i}]={_WARM_DAILY_MAX[i]:.2f}"
            )


class TestDay0ForecastAuthorityFreshestFullContributor:
    """When multiple FULL_CONTRIBUTOR runs exist, freshest issue_time (DESC) wins."""

    def test_fresher_12z_full_contributor_beats_stale_00z(self, tmp_path: Path, monkeypatch):
        """Two FULL_CONTRIBUTORs for same (city, target_date, metric): 12Z selected over 00Z.

        Discriminating case: issue_time DESC rule picks latest cycle.
        - Snapshot C: 00Z, contributes=1, FULLY_INSIDE, members base=20 (cooler)
        - Snapshot D: 12Z, contributes=1, FULLY_INSIDE, members base=25 (warmer, fresher)
        Both are valid FULL_CONTRIBUTORs; D has later issue_time → D selected.
        """
        _COOL_MEMBERS = [20.0 + 0.1 * i for i in range(51)]
        _FRESH_MEMBERS = [25.0 + 0.1 * i for i in range(51)]

        db_path = tmp_path / "two_full.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_CREATE_TABLE_SQL)

        # Snapshot C: 00Z FULL_CONTRIBUTOR (lower snapshot_id)
        conn.execute(
            _INSERT_SQL,
            (
                "Taipei", _TARGET_DATE, "high",
                _AVAILABLE_AT_A, _FETCH_TIME,
                json.dumps(_COOL_MEMBERS),
                "ecmwf_opendata_mx2t3_local_calendar_day_max",
                _RECORDED_AT_A, _ISSUE_TIME_A,
                "FULLY_INSIDE_TARGET_LOCAL_DAY",
                1,
            ),
        )
        # Snapshot D: 12Z FULL_CONTRIBUTOR (higher snapshot_id, later issue_time)
        conn.execute(
            _INSERT_SQL,
            (
                "Taipei", _TARGET_DATE, "high",
                _AVAILABLE_AT_B, _FETCH_TIME,
                json.dumps(_FRESH_MEMBERS),
                "ecmwf_opendata_mx2t3_local_calendar_day_max",
                _RECORDED_AT_B, _ISSUE_TIME_B,
                "FULLY_INSIDE_TARGET_LOCAL_DAY",  # also FULL_CONTRIBUTOR
                1,
            ),
        )
        conn.commit()
        conn.close()

        import src.data.ecmwf_open_data_ingest as eod
        import src.state.db as state_db

        def _fake_conn(*_a, **_k):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
        monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)

        members = _get_selected_members(db_path, _NOW)
        assert abs(members[0] - _FRESH_MEMBERS[0]) < 0.01, (
            f"FAIL: selected members[0]={members[0]:.1f}°C — expected freshest 12Z "
            f"FULL_CONTRIBUTOR ({_FRESH_MEMBERS[0]:.1f}°C). "
            "issue_time DESC rule not applied: stale 00Z run selected instead."
        )


class TestDay0ForecastAuthorityFailClosed:
    """When no FULL_CONTRIBUTOR exists, _query_metric must fail-closed (return no rows)."""

    def test_no_full_contributor_returns_none(self, tmp_path: Path, monkeypatch):
        """If only NON_CONTRIBUTOR snapshots exist, _fetch_db_payload must return None."""
        db_path = tmp_path / "nfc.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_CREATE_TABLE_SQL)
        # Only insert the 12Z NON_CONTRIBUTOR snapshot (no FULL_CONTRIBUTOR)
        conn.execute(
            _INSERT_SQL,
            (
                "Taipei", _TARGET_DATE, "high",
                _AVAILABLE_AT_B, _FETCH_TIME,
                json.dumps(_COLD_DAILY_MAX),
                "ecmwf_opendata_mx2t3_local_calendar_day_max",
                _RECORDED_AT_B, _ISSUE_TIME_B,
                "OUTSIDE_TARGET_LOCAL_DAY",
                0,
            ),
        )
        conn.commit()
        conn.close()

        import src.data.ecmwf_open_data_ingest as eod
        import src.state.db as state_db

        def _fake_conn(*_a, **_k):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
        monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)

        from src.data.ecmwf_open_data_ingest import _fetch_db_payload

        city = SimpleNamespace(
            name="Taipei",
            timezone="Asia/Taipei",
            lat=25.04,
            lon=121.53,
            settlement_unit="C",
        )
        result = _fetch_db_payload(city, _NOW, temperature_metric="high")
        assert result is None, (
            f"Expected None (fail-closed) when only NON_CONTRIBUTOR exists, got {result!r}"
        )
