# Created: 2026-04-21
# Last reused/audited: 2026-04-27
# Lifecycle: created=2026-04-21; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Protect Open-Meteo previous-runs backfill aggregation and forecasts writes.
# Reuse: Run before changing previous-runs source mapping, forecasts schema, or onboarding forecast backfill.
# Authority basis: R3 F1 forecast provenance wiring + historical backfill packet.
from __future__ import annotations

from pathlib import Path

from scripts import backfill_openmeteo_previous_runs as backfill
from scripts import onboard_cities
from src.config import City
from src.state.db import get_connection, init_schema


def _city() -> City:
    return City(
        name="Auckland",
        lat=-36.8509,
        lon=174.7645,
        timezone="Pacific/Auckland",
        settlement_unit="C",
        cluster="Oceania-Maritime",
        aliases=["Auckland"],
        slug_names=["auckland"],
        wu_station="NZAA",
    )


def test_rows_from_previous_runs_payload_aggregates_daily_highs_by_lead():
    payload = {
        "hourly": {
            "time": [
                "2024-01-03T00:00",
                "2024-01-03T12:00",
                "2024-01-04T00:00",
                "2024-01-04T12:00",
            ],
            "temperature_2m_previous_day1": [18.0, 21.5, 19.0, 20.0],
            "temperature_2m_previous_day2": [17.0, 20.0, None, 22.0],
        }
    }

    rows, counters = backfill._rows_from_payload(
        _city(),
        payload,
        leads=(1, 2),
        models=("best_match",),
        retrieved_at="2026-04-11T00:00:00+00:00",
        imported_at="2026-04-11T00:00:00+00:00",
    )

    by_key = {(row.target_date, row.lead_days): row for row in rows}
    assert counters["temperature_2m_previous_day2_best_match_null"] == 1
    assert by_key[("2024-01-03", 1)].forecast_high == 21.5
    assert by_key[("2024-01-03", 1)].forecast_low == 18.0
    assert by_key[("2024-01-03", 1)].forecast_basis_date == "2024-01-02"
    assert by_key[("2024-01-03", 2)].forecast_basis_date == "2024-01-01"
    assert by_key[("2024-01-04", 2)].forecast_high == 22.0
    assert by_key[("2024-01-04", 2)].forecast_low == 22.0
    assert by_key[("2024-01-03", 1)].source_id == "openmeteo_previous_runs"
    assert by_key[("2024-01-03", 1)].raw_payload_hash
    assert by_key[("2024-01-03", 1)].captured_at == "2026-04-11T00:00:00+00:00"
    assert by_key[("2024-01-03", 1)].authority_tier == "FORECAST"


def test_rows_from_previous_runs_payload_preserves_model_source():
    payload = {
        "hourly": {
            "time": ["2025-01-03T00:00", "2025-01-03T12:00"],
            "temperature_2m_previous_day1_gfs_global": [18.0, 23.0],
            "temperature_2m_previous_day1_ecmwf_ifs025": [17.0, 22.0],
        }
    }

    rows, counters = backfill._rows_from_payload(
        _city(),
        payload,
        leads=(1,),
        models=("gfs_global", "ecmwf_ifs025"),
        retrieved_at="2026-04-11T00:00:00+00:00",
        imported_at="2026-04-11T00:00:00+00:00",
    )

    by_source = {row.source: row for row in rows}
    assert not counters
    assert by_source["gfs_previous_runs"].forecast_high == 23.0
    assert by_source["ecmwf_previous_runs"].forecast_high == 22.0


def test_run_backfill_writes_forecasts_idempotently(tmp_path, monkeypatch):
    db_path = tmp_path / "previous-runs.db"

    def _conn():
        conn = get_connection(db_path)
        init_schema(conn)
        return conn

    payload = {
        "hourly": {
            "time": ["2024-01-03T00:00", "2024-01-03T12:00"],
            "temperature_2m_previous_day1": [18.0, 21.5],
        }
    }
    monkeypatch.setattr(backfill, "get_world_connection", _conn)
    monkeypatch.setattr(backfill, "_resolve_cities", lambda *args, **kwargs: [_city()])
    monkeypatch.setattr(backfill, "_fetch_previous_runs_chunk", lambda *args, **kwargs: payload)

    first = backfill.run_backfill(
        city_names=["Auckland"],
        start_date="2024-01-03",
        end_date="2024-01-03",
        leads=(1,),
        sleep_seconds=0.0,
    )
    second = backfill.run_backfill(
        city_names=["Auckland"],
        start_date="2024-01-03",
        end_date="2024-01-03",
        leads=(1,),
        sleep_seconds=0.0,
    )

    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM forecasts").fetchone()
    conn.close()

    assert first["forecasts_added"] == 1
    assert second["forecasts_added"] == 0
    assert row["city"] == "Auckland"
    assert row["source"] == backfill.SOURCE
    assert row["target_date"] == "2024-01-03"
    assert row["forecast_basis_date"] == "2024-01-02"
    assert row["lead_days"] == 1
    assert row["forecast_high"] == 21.5
    assert row["forecast_low"] == 18.0
    assert row["temp_unit"] == "C"
    assert row["source_id"] == backfill.SOURCE
    assert row["raw_payload_hash"]
    assert row["captured_at"]
    assert row["authority_tier"] == "FORECAST"


def test_onboarding_pipeline_materializes_forecast_surfaces_after_source_backfill():
    step_ids = [step["id"] for step in onboard_cities.PIPELINE_STEPS]
    steps = {step["id"]: step for step in onboard_cities.PIPELINE_STEPS}

    assert step_ids.index("openmeteo_previous_runs") < step_ids.index("forecast_skill")
    assert step_ids.index("forecast_skill") < step_ids.index("historical_forecasts")
    assert "900" in steps["wu_daily"]["extra_args"]
    assert "900" in steps["hourly_openmeteo"]["extra_args"]
    assert "900" in steps["openmeteo_previous_runs"]["extra_args"]
    assert "ukmo_global_deterministic_10km" in ",".join(
        steps["openmeteo_previous_runs"]["extra_args"]
    )
    assert "forecasts" in onboard_cities._verification_tables()
    assert "forecast_skill" in onboard_cities._verification_tables()
    assert "model_bias" in onboard_cities._verification_tables()
