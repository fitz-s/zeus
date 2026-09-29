# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 — cell_selection and elevation/downscaling are first-class product identity and must be persisted alongside every raw_model_forecasts row.
# Reuse: Run with pytest; update if product-identity columns or cell_selection/elevation handling in the BAYES_PRECISION_FUSION downloader changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 + Fitz Constraint #4. Open-Meteo's cell_selection
#   (nearest vs land vs sea) and elevation/downscaling materially change the returned 2m
#   temperature: the SAME lat/lon with cell_selection=land vs nearest can pick a DIFFERENT
#   grid cell -> a different physical product. These are first-class product identity, not
#   cosmetic params, and MUST be persisted so a stored value proves which cell produced it.
"""BLOCKER 4 — cell_selection + elevation/downscaling are persisted product identity.

The download writer must record cell_selection, elevation_param, downscaling_policy, and
model_domain_hash on every raw_model_forecasts row. Two captures of the same model/city/date at
DIFFERENT cell_selection are DIFFERENT products and must be distinguishable (different
model_domain_hash) so a residual history never mixes two physical cells.
"""
from __future__ import annotations

import sqlite3
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _target():
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                             lead_days=1, latitude=48.967, longitude=2.428,
                             timezone_name="Europe/Paris")


def test_cell_selection_and_elevation_persisted(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, tzinfo=UTC), targets=[_target()],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 19.5,
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT cell_selection, elevation_param, downscaling_policy, model_domain_hash,
                  coverage_status
           FROM raw_model_forecasts"""
    ).fetchall()
    conn.close()
    assert rows
    for r in rows:
        assert r["cell_selection"], "cell_selection must be recorded"
        assert r["elevation_param"], "elevation_param must be recorded"
        assert r["downscaling_policy"], "downscaling_policy must be recorded"
        assert r["model_domain_hash"], "model_domain_hash must be recorded"
        assert r["coverage_status"], "coverage_status must be recorded"


def test_different_cell_selection_yields_different_model_domain_hash(tmp_path) -> None:
    """The model_domain_hash binds (provider, model_name, cell_selection, elevation_param,
    downscaling_policy, endpoint_mode). Changing cell_selection changes the hash -> two
    physical cells are never conflated under one identity."""
    from src.data.bayes_precision_fusion_download import _model_domain_hash

    base = dict(
        provider="open-meteo", model_name="gfs_global", cell_selection="nearest",
        elevation_param="requested", downscaling_policy="none",
        endpoint_mode="previous_runs",
    )
    h_nearest = _model_domain_hash(**base)
    h_land = _model_domain_hash(**{**base, "cell_selection": "land"})
    h_elev = _model_domain_hash(**{**base, "elevation_param": "30"})
    assert h_nearest != h_land, "cell_selection must change the domain hash"
    assert h_nearest != h_elev, "elevation must change the domain hash"
    # Deterministic + stable for the same inputs.
    assert h_nearest == _model_domain_hash(**base)


def _current_rows(tmp_path, monkeypatch, *, metric="high"):
    from src.data import bayes_precision_fusion_download as dl

    target = replace(_target(), metric=metric)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {
        target.city: SimpleNamespace(
            name=target.city, lat=target.latitude, lon=target.longitude,
            timezone=target.timezone_name,
        ),
    })
    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, tzinfo=UTC)
    _download_time(monkeypatch, dl, cycle.replace(hour=4))
    _mock_single_model_http(monkeypatch, dl, value=20.0)
    dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=[target],
        models=("icon_global", "ukmo_global_deterministic_10km"),
        include_previous_runs=False, prune_after=False,
    )
    conn = sqlite3.connect(db)
    return conn, target, cycle


def _mock_single_model_http(monkeypatch, dl, *, value):
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()

    def fetch(url, params, **kwargs):
        assert "," not in params["models"]
        day = datetime(2026, 6, 9)
        payload = {"latitude": 48.95, "longitude": 2.45, "elevation": 123,
            "timezone": "Europe/Paris", "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [(day + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                       "temperature_2m": [value] * 24}}
        body = (json.dumps(payload, indent=2) + "\n").encode()
        kwargs["capture_entity_body"](body, dl.datetime.now(UTC).timestamp())
        return json.loads(body)

    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)


def _download_time(monkeypatch, module, when):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("column,value", (
    ("latitude_requested", 0.0), ("longitude_requested", 0.0),
    ("timezone_requested", "UTC"), ("model_name", "ecmwf_ifs025"),
    ("provider", "other"), ("source_family", "other"),
    ("product_id", "other"), ("cell_selection", "nearest"),
    ("elevation_param", "nan"), ("downscaling_policy", "none"),
    ("model_domain_hash", "bad-domain"), ("request_url_hash", "bad-request"),
    ("request_params_json", '{"elevation":"nan"}'),
))
def test_wrong_provider_product_cannot_serve_current_cohort_or_frontier(
    tmp_path, monkeypatch, metric, column, value,
):
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema, read_current_instrument_values,
        read_current_instrument_frontier_identity, read_freshest_coherent_instrument_values,
    )

    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    conn.execute(f"UPDATE raw_model_forecasts SET {column}=? WHERE model='icon_global'", (value,))
    decision = cycle.replace(hour=5).isoformat()
    scope = dict(city=target.city, metric=metric, target_date=target.target_date)
    served = read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.isoformat(), decision_time_iso=decision,
    )
    assert set(served) == {"ukmo_global_deterministic_10km"}
    models = ("icon_global", "ukmo_global_deterministic_10km")
    assert read_freshest_coherent_instrument_values(
        conn, **scope, decision_time_iso=decision, models=models, cohort_window_hours=6,
    ) == {}
    frontier = dict(read_current_instrument_frontier_identity(
        conn, **scope, decision_time_iso=decision, models=models,
        schema=current_value_serving_schema(conn),
    ))
    assert frontier["icon_global"] is None
    assert frontier["ukmo_global_deterministic_10km"] == served["ukmo_global_deterministic_10km"].raw_model_forecast_id
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_ordinary_new_cycle_drains_superseded_default_dem_label(tmp_path, monkeypatch, metric):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values

    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    conn.execute("UPDATE raw_model_forecasts SET elevation_param='requested', downscaling_policy='none'")
    conn.commit()
    scope = dict(city=target.city, metric=metric, target_date=target.target_date)
    assert read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=5).isoformat(),
    ) == {}
    for run in (cycle, cycle.replace(hour=6)):
        _download_time(monkeypatch, dl, run.replace(hour=run.hour + 4))
        _mock_single_model_http(monkeypatch, dl, value=21.0)
        report = dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=Path(conn.execute("PRAGMA database_list").fetchone()[2]),
            cycle=run, targets=[target],
            models=("icon_global", "ukmo_global_deterministic_10km"),
            include_previous_runs=False, prune_after=False,
        )
        assert report["written_row_count"] == (0 if run == cycle else 2)
    served = read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.replace(hour=6).isoformat(),
        decision_time_iso=cycle.replace(hour=11).isoformat(),
    )
    assert set(served) == {"icon_global", "ukmo_global_deterministic_10km"}
    assert {row.served_cycle for row in served.values()} == {cycle.replace(hour=6).isoformat()}
    old = conn.execute("SELECT downscaling_policy FROM raw_model_forecasts WHERE source_cycle_time=?", (cycle.isoformat(),)).fetchall()
    assert old == [("none",), ("none",)]
    conn.close()


def test_registered_station_products_keep_their_agency_authority(monkeypatch):
    from src.data.replacement_current_value_serving import _source_clock_product_has_authority

    for model, provider in (
        ("hko_fnd", "hong_kong_observatory"),
        ("cwa_township_hourly_high", "cwa_taiwan"),
        ("cwa_township_hourly_low", "cwa_taiwan"),
    ):
        row = dict(
            model=model, provider=provider, source_family="station_official_forecast",
            source_id=f"{model}_single_runs", model_name=model,
            product_id=f"{model}::single_runs", endpoint="single_runs", endpoint_mode="single_runs",
            downscaling_policy="agency_mos", elevation_param="station",
        )
        assert _source_clock_product_has_authority(json.dumps(row), lead_days=1)
        assert not _source_clock_product_has_authority(json.dumps({**row, "product_id": "other"}), lead_days=1)
        assert not _source_clock_product_has_authority(json.dumps({**row, "model": "hko_unregistered"}), lead_days=1)
