# Created: 2026-07-17
# Purpose: Protect the blocked-attempt fingerprint's cycle scoping (2026-07-13/14 incident gap B).
# Authority basis: incident finding — the raw_model_forecasts watermark was TARGET-WIDE (all
#   cycles/leads), so during active ingest a new row lands every few minutes and the fingerprint
#   never settles: 0 of 277 blocked attempts were suppressed. The immutable decision-time
#   source-clock frontier suppresses future/unrelated churn while still retrying when a provider
#   value that THIS exact request could consume advances independently of its ENS carrier.
"""Blocked-attempt fingerprints follow the request's causal source-clock frontier."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import src.data.replacement_forecast_live_materialization_queue as queue_mod
from src.state.schema.v2_schema import apply_canonical_schema

_CITY = "Nowhereville"
_TARGET_DATE = "2026-07-14"
_METRIC = "high"
_OWN_CYCLE = "2026-07-13T06:00:00+00:00"
_OTHER_CYCLE = "2026-07-13T12:00:00+00:00"
_DECISION_TIME = "2026-07-13T07:00:00+00:00"


def _make_forecast_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(str(db_path))
    apply_canonical_schema(conn, forecast_tables=True)
    conn.commit()
    conn.close()
    return db_path


def _insert_raw(
    db_path: Path,
    *,
    source_cycle_time: str,
    model: str = "ecmwf_ifs",
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint, request_params_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model, _CITY, _TARGET_DATE, _METRIC, source_cycle_time, source_cycle_time,
            source_cycle_time, 1, 20.0, "single_runs", "{}",
        ),
    )
    conn.commit()
    conn.close()


def _payload(source_cycle_time: str) -> dict:
    return {
        "city": _CITY,
        "target_date": _TARGET_DATE,
        "temperature_metric": _METRIC,
        "source_cycle_time": source_cycle_time,
        "computed_at": _DECISION_TIME,
    }


def test_fingerprint_unaffected_by_future_row_at_different_cycle(tmp_path: Path) -> None:
    """A row unavailable at the request decision instant cannot heal it or cause churn."""
    db_path = _make_forecast_db(tmp_path)
    input_json = tmp_path / "request.json"
    input_json.write_text("{}")
    payload = _payload(_OWN_CYCLE)

    _insert_raw(db_path, source_cycle_time=_OWN_CYCLE)
    fp1 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp1 is not None

    _insert_raw(db_path, source_cycle_time=_OTHER_CYCLE)
    fp2 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp2 == fp1


def test_fingerprint_changes_on_new_row_at_same_cycle(tmp_path: Path) -> None:
    """A new raw row at the request's OWN cycle IS potential healing input and must retry
    (fingerprint changes so the blocked-attempt dedup does not suppress it)."""
    db_path = _make_forecast_db(tmp_path)
    input_json = tmp_path / "request.json"
    input_json.write_text("{}")
    payload = _payload(_OWN_CYCLE)

    _insert_raw(db_path, source_cycle_time=_OWN_CYCLE, model="ecmwf_ifs")
    fp1 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp1 is not None

    _insert_raw(db_path, source_cycle_time=_OWN_CYCLE, model="gfs_global")
    fp2 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp2 != fp1


def test_fingerprint_changes_on_consumable_provider_advance(tmp_path: Path) -> None:
    """A newer provider cycle already possessed at computed_at heals a fixed carrier request."""
    db_path = _make_forecast_db(tmp_path)
    input_json = tmp_path / "request.json"
    input_json.write_text("{}")
    payload = _payload(_OWN_CYCLE)

    _insert_raw(db_path, source_cycle_time=_OWN_CYCLE, model="icon_global")
    fp1 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp1 is not None

    _insert_raw(
        db_path,
        source_cycle_time="2026-07-13T06:30:00+00:00",
        model="icon_global",
    )
    fp2 = queue_mod._blocked_attempt_fingerprint(
        input_json=input_json, forecast_db=db_path, payload=payload
    )
    assert fp2 != fp1
