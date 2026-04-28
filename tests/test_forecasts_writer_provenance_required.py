# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md (Slice F11.3, Q4 = raise)
"""F11.3 antibody: writer rejects ForecastRow missing availability_provenance / issue_time.

D4 + Q4 antibody from packet 2026-04-28: a future maintainer cannot
construct a ForecastRow without the typed provenance fields and have
the writer silently accept it. The wrong code becomes unwritable.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from src.data.forecasts_append import ForecastRow, _insert_rows


def _make_test_table(conn: sqlite3.Connection) -> None:
    """In-memory schema mirrors src/state/db.py's forecasts CREATE TABLE
    (relevant subset for the writer) post the plan-pre5 R3 + F11 merge.
    UNIQUE matches production exactly so relationship antibodies catch
    cross-environment drift."""
    conn.execute("""
        CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, source TEXT, forecast_basis_date TEXT,
            forecast_issue_time TEXT, lead_days INTEGER, lead_time_hours REAL,
            forecast_high REAL, forecast_low REAL, temp_unit TEXT,
            retrieved_at TEXT, imported_at TEXT,
            source_id TEXT, raw_payload_hash TEXT, captured_at TEXT, authority_tier TEXT,
            rebuild_run_id TEXT, data_source_version TEXT,
            availability_provenance TEXT,
            UNIQUE (city, target_date, source, forecast_basis_date)
        )
    """)
    conn.commit()


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    _make_test_table(conn)
    yield conn
    conn.close()


def _good_row(**overrides) -> ForecastRow:
    """Factory for ForecastRow with both F11 (availability_provenance) and
    R3 (source_id, raw_payload_hash, captured_at, authority_tier) fields
    populated. Values are realistic-shaped but not pulled from the live
    forecast_source_registry (this is an in-memory antibody test)."""
    defaults = dict(
        city="NYC",
        target_date="2026-04-30",
        source="ecmwf_previous_runs",
        forecast_basis_date="2026-04-28",
        forecast_issue_time="2026-04-28T06:48:00+00:00",  # ECMWF Day 2 = +6h48m
        lead_days=2,
        lead_time_hours=48.0,
        forecast_high=72.0,
        forecast_low=58.0,
        temp_unit="F",
        retrieved_at="2026-04-28T13:00:00+00:00",
        imported_at="2026-04-28T13:00:00+00:00",
        source_id="ecmwf_previous_runs",
        raw_payload_hash="0" * 64,
        captured_at="2026-04-28T13:00:00+00:00",
        authority_tier="non_promotion",
        rebuild_run_id="test_run",
        data_source_version=None,
        availability_provenance="derived_dissemination",
    )
    defaults.update(overrides)
    return ForecastRow(**defaults)


def test_writer_accepts_row_with_provenance(db):
    rows = [_good_row()]
    inserted = _insert_rows(db, rows)
    assert inserted == 1


def test_writer_rejects_null_provenance(db):
    bad = _good_row(availability_provenance=None)
    with pytest.raises(ValueError, match="availability_provenance"):
        _insert_rows(db, [bad])


def test_writer_rejects_null_forecast_issue_time(db):
    bad = _good_row(forecast_issue_time=None)
    with pytest.raises(ValueError, match="forecast_issue_time"):
        _insert_rows(db, [bad])


def test_writer_accepts_each_valid_provenance_tier(db):
    for tier in ("fetch_time", "recorded", "derived_dissemination", "reconstructed"):
        rows = [_good_row(target_date=f"2026-04-30-{tier}", availability_provenance=tier)]
        inserted = _insert_rows(db, rows)
        assert inserted == 1, f"failed to insert tier={tier}"


def test_writer_empty_input_returns_zero(db):
    assert _insert_rows(db, []) == 0


def test_writer_partial_failure_atomicity(db):
    """If any row in the batch is invalid, the entire batch is rejected
    before any row is written."""
    rows = [_good_row(target_date="2026-04-30"), _good_row(target_date="2026-05-01", availability_provenance=None)]
    with pytest.raises(ValueError):
        _insert_rows(db, rows)
    # No row written for either target_date.
    count = db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
    assert count == 0


def test_rows_from_payload_stamps_provenance_for_canonical_sources():
    """End-to-end: _rows_from_payload constructs rows with non-NULL provenance
    for every model in MODEL_SOURCE_MAP (no more `forecast_issue_time=None`)."""
    from src.config import cities_by_name
    from src.data.forecasts_append import _rows_from_payload, _hourly_variable_for_lead

    city = next(iter(cities_by_name.values()))
    base_var = _hourly_variable_for_lead(1)
    payload = {
        "hourly": {
            "time": [
                "2026-04-29T00:00",
                "2026-04-29T12:00",
                "2026-04-30T00:00",
                "2026-04-30T12:00",
            ],
            f"{base_var}_ecmwf_ifs025": [10.0, 15.0, 11.0, 16.0],
            f"{base_var}_gfs_global": [9.5, 14.5, 10.5, 15.5],
        }
    }
    rows, _ = _rows_from_payload(
        city,
        payload,
        leads=(1,),
        models=("ecmwf_ifs025", "gfs_global"),
        retrieved_at="2026-04-30T01:00:00+00:00",
        imported_at="2026-04-30T01:00:00+00:00",
    )
    assert len(rows) > 0
    assert all(r.forecast_issue_time is not None for r in rows)
    assert all(r.availability_provenance is not None for r in rows)
    # Both ECMWF and GFS sources carry DERIVED tier (verified primary sources):
    sources = {r.source for r in rows}
    assert sources == {"ecmwf_previous_runs", "gfs_previous_runs"}
    for r in rows:
        assert r.availability_provenance == "derived_dissemination"


def test_rows_from_payload_raises_on_unregistered_source():
    """An ad-hoc model name not registered in either the R3 forecast_source_registry
    or the F11 dissemination_schedules MUST raise — fail-fast forces explicit
    registration. Post plan-pre5 merge, registration is gated at the R3 layer
    first (SourceNotEnabled), then F11 (UnknownSourceError)."""
    from src.config import cities_by_name
    from src.data.dissemination_schedules import UnknownSourceError
    from src.data.forecast_source_registry import SourceNotEnabled
    from src.data.forecasts_append import _rows_from_payload, _hourly_variable_for_lead

    city = next(iter(cities_by_name.values()))
    base_var = _hourly_variable_for_lead(1)
    payload = {
        "hourly": {
            "time": ["2026-04-29T00:00", "2026-04-30T00:00"],
            f"{base_var}_some_new_model": [11.0, 12.0],
        }
    }
    with pytest.raises((UnknownSourceError, SourceNotEnabled)):
        _rows_from_payload(
            city,
            payload,
            leads=(1,),
            models=("some_new_model",),
            retrieved_at="2026-04-30T01:00:00+00:00",
            imported_at="2026-04-30T01:00:00+00:00",
        )
