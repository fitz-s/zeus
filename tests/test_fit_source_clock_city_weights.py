#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-09-20
# Authority basis: docs/evidence/upstream_physical_2026_07_17/consult_freshness_decoupling_verdict.txt
#   (basket-governance data-availability tiers >=60/30-59/<30); docs/evidence/
#   upstream_physical_2026_07_17/combo_experiments_report.md (walk-forward no-leak discipline).
"""Unit tests for scripts/fit_source_clock_city_weights.py — no live DB required.

Covers: (1) weight math parity vs src.forecast.center.raw_second_moment_weights (imported,
not reimplemented); (2) determinism (two runs of the same DB state + as_of are byte-
identical); (3) the walk-forward boundary (a settlement dated exactly on as_of must never
be used to train); (4) the >=60 / 30-59 / <30 paired-date data-availability tiers select
CITY_SPECIFIC / REGION_POOLED / GLOBAL_CORE respectively.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fit_source_clock_city_weights as fscw  # noqa: E402

from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS  # noqa: E402
from src.data.openmeteo_client import PREVIOUS_RUNS_URL  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    SINGLE_RUNS_FORECAST_URL,
    STANDARD_FORECAST_URL,
)
from src.forecast.center import raw_second_moment_weights  # noqa: E402


_TEST_CITIES: dict[str, SimpleNamespace] = {}


def _canonical_request_identity(base_url: str, params: dict[str, object]) -> tuple[str, str]:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return canonical, hashlib.sha256(f"{base_url}?{canonical}".encode("utf-8")).hexdigest()


def _city(name: str, *, unit: str = "C") -> SimpleNamespace:
    return _TEST_CITIES.setdefault(
        name,
        SimpleNamespace(
            name=name,
            settlement_source_type="noaa",
            previous_settlement_source_type="wu_icao",
            settlement_source_type_effective_date="2026-02-21",
            wu_station="KTEST",
            settlement_unit=unit,
            settlement_page_view="all",
            lat=10.0,
            lon=20.0,
            timezone="UTC",
        ),
    )


@pytest.fixture(autouse=True)
def _current_resolver_city_map(monkeypatch: pytest.MonkeyPatch) -> None:
    _TEST_CITIES.clear()
    monkeypatch.setattr(fscw, "runtime_cities_by_name", lambda: _TEST_CITIES)


def _make_db(rows: list[dict]) -> sqlite3.Connection:
    """rows: each dict has model, city, metric, target_date, lead_days, forecast_value_c,
    settlement_value_c (settlement is always inserted as unit='C' — F-conversion is exercised
    by inserting a raw settlement_value + unit='F' explicitly where needed)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT, city TEXT, target_date TEXT, metric TEXT, source_cycle_time TEXT,
            source_available_at TEXT, captured_at TEXT, lead_days INTEGER,
            forecast_value_c REAL, endpoint TEXT, training_allowed INTEGER,
            recorded_at TEXT, coverage_status TEXT, source_id TEXT, source_family TEXT,
            product_id TEXT, request_url_hash TEXT, model_name TEXT, provider TEXT,
            endpoint_mode TEXT, request_params_json TEXT, latitude_requested REAL,
            longitude_requested REAL, timezone_requested TEXT
        );
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            temperature_metric TEXT, winning_bin TEXT, settlement_value REAL,
            settlement_source TEXT, settled_at TEXT, authority TEXT,
            provenance_json TEXT, recorded_at TEXT, settlement_unit TEXT,
            outcome_type INTEGER, resolution_state TEXT
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, source TEXT,
            station_id TEXT, unit TEXT, data_source_version TEXT, high_temp REAL,
            low_temp REAL, high_fetch_utc TEXT, low_fetch_utc TEXT,
            high_provenance_metadata TEXT, low_provenance_metadata TEXT
        );
        """
    )
    truth_by_city_date: dict[tuple[str, str], dict[str, dict]] = {}
    for r in rows:
        unit = r.get("settlement_unit", "C")
        _city(r["city"], unit=unit)
        city_config = _city(r["city"], unit=unit)
        endpoint = r.get("endpoint", "single_runs")
        endpoint_mode = r.get("endpoint_mode", endpoint)
        model_name = r.get("model_name", OPENMETEO_MODEL_IDS.get(r["model"], r["model"]))
        target_instant = f"{r['target_date']}T12:00:00+00:00"
        prior_instant = (
            f"{date.fromisoformat(r['target_date']) - timedelta(days=1)}T12:00:00+00:00"
        )
        source_id = r.get(
            "source_id",
            f"{r['model']}_standard_meta_stamped"
            if endpoint_mode == "standard_api_meta_stamped"
            else f"{r['model']}_previous_runs"
            if endpoint == "previous_runs"
            else f"{r['model']}_single_runs",
        )
        source_family = r.get(
            "source_family",
            "openmeteo_standard_meta_stamped"
            if endpoint_mode == "standard_api_meta_stamped"
            else "openmeteo_previous_runs"
            if endpoint == "previous_runs"
            else "openmeteo_single_runs",
        )
        product_id = r.get(
            "product_id",
            f"{model_name}::previous_runs"
            if endpoint == "previous_runs"
            else f"{model_name}::single_runs"
            if endpoint_mode == "single_runs"
            else (
                f"{model_name}::standard_api_meta_stamped::"
                f"run={r.get('source_cycle_time', prior_instant)}::"
                "modified=2026-02-28T01:00:00+00:00"
            ),
        )
        request_params = {
            "latitude": city_config.lat,
            "longitude": city_config.lon,
            "hourly": "temperature_2m",
            "models": model_name,
            "temperature_unit": "celsius",
            "timezone": city_config.timezone,
            "cell_selection": "land",
        }
        if endpoint == "previous_runs":
            request_params.update(
                hourly=f"temperature_2m_previous_day{r['lead_days']}",
                start_date=r["target_date"],
                end_date=r["target_date"],
            )
        if endpoint_mode == "standard_api_meta_stamped":
            request_params["forecast_hours"] = r.get("forecast_hours", 120)
        supplied_params = r.get("request_params_json")
        if supplied_params is not None:
            request_params = json.loads(str(supplied_params))
        base_url = (
            STANDARD_FORECAST_URL
            if endpoint_mode == "standard_api_meta_stamped"
            else PREVIOUS_RUNS_URL
            if endpoint == "previous_runs"
            else SINGLE_RUNS_FORECAST_URL
        )
        request_params_json, canonical_request_hash = _canonical_request_identity(
            base_url, request_params
        )
        request_url_hash = r.get("request_url_hash", canonical_request_hash)
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time, source_available_at,
                captured_at, lead_days, forecast_value_c, endpoint, training_allowed,
                recorded_at, coverage_status, source_id, source_family, product_id,
                request_url_hash, model_name, provider, endpoint_mode, request_params_json,
                latitude_requested, longitude_requested, timezone_requested
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["model"], r["city"], r["target_date"], r["metric"],
                r.get("source_cycle_time", prior_instant),
                r.get("source_available_at", prior_instant),
                r.get("captured_at", prior_instant),
                r["lead_days"], r["forecast_value_c"], endpoint,
                r.get("training_allowed", 0), r.get("recorded_at", target_instant),
                r.get("coverage_status", "COVERED"), source_id, source_family,
                product_id, request_url_hash, model_name,
                r.get("provider", "open-meteo"), endpoint_mode, request_params_json,
                r.get("latitude_requested", city_config.lat),
                r.get("longitude_requested", city_config.lon),
                r.get("timezone_requested", city_config.timezone),
            ),
        )
        truth_by_city_date.setdefault((r["city"], r["target_date"]), {})[r["metric"]] = r
    for observation_id, ((city, target_date), by_metric) in enumerate(
        sorted(truth_by_city_date.items()), start=1
    ):
        unit = next(iter(by_metric.values())).get("settlement_unit", "C")
        values = {
            metric: item.get("settlement_value", item.get("settlement_value_c"))
            for metric, item in by_metric.items()
        }
        instant = f"{target_date}T12:00:00+00:00"
        metadata = json.dumps({"settlement_page_view": "all", "station": "KTEST"})
        conn.execute(
            """INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                observation_id, city, target_date, "noaa_wrh_ktest", "KTEST", unit,
                "noaa_wrh_timeseries_v1", values.get("high"), values.get("low"),
                instant if "high" in values else None, instant if "low" in values else None,
                metadata if "high" in values else None, metadata if "low" in values else None,
            ),
        )
        for metric, item in by_metric.items():
            value = values[metric]
            conn.execute(
                """INSERT INTO settlement_outcomes (
                    city, target_date, temperature_metric, winning_bin, settlement_value,
                    settlement_source, settled_at, authority, provenance_json, recorded_at,
                    settlement_unit, resolution_state
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    city, target_date, metric, "bin", value,
                    "https://weather.gov/wrh/timeseries?site=KTEST", instant,
                    item.get("authority", "VERIFIED"),
                    json.dumps({
                        "era": "internal_resolver_post_2026_02_21",
                        "era_start_date_utc": "2026-02-21",
                        "source_family": "noaa",
                        "settlement_source_type": "noaa",
                        "obs_id": observation_id,
                        "obs_source": "noaa_wrh_ktest",
                        "data_version": "noaa_wrh_timeseries_v1",
                        "rounding_rule": "wmo_half_up",
                    }),
                    instant, unit, "VENUE_RESOLVED",
                ),
            )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _rows_for_city(city: str, metric: str, n: int, *, models: dict[str, float]) -> list[dict]:
    """``models`` maps model id -> constant residual offset (forecast - settlement, degC).
    Produces ``n`` distinct settled target_dates, one 2026 calendar day apart, with lead_days=1
    for every row (a single archived lead; exact-lead dedup is tested separately)."""
    rows = []
    for i in range(n):
        month = 3 + (i // 28)
        day = 1 + (i % 28)
        date = f"2026-{month:02d}-{day:02d}"
        settle_c = 10.0 + float(i % 7)
        for model, offset in models.items():
            rows.append({
                "model": model, "city": city, "metric": metric, "target_date": date,
                "lead_days": 1, "forecast_value_c": settle_c + offset,
                "settlement_value_c": settle_c,
            })
    return rows


def _cities_json(tmp_path: Path, cities: list[dict]) -> Path:
    path = tmp_path / "cities.json"
    path.write_text(json.dumps({"cities": cities}), encoding="utf-8")
    return path


def test_weight_math_matches_center_raw_second_moment_weights() -> None:
    """The generator's per-model raw second moment feeds src.forecast.center's OWN weight
    formula unmodified — this is a parity check against reimplementation drift."""
    rows = _rows_for_city("TestCity", "high", 40, models={"A": 0.5, "B": 2.0})
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31T00:00:00+00:00")
    obs = loaded["obs"][("TestCity", "high")]
    settle = loaded["settle"][("TestCity", "high")]
    stats = fscw.residual_stats_by_model(obs, settle)

    # Known-closed-form raw second moments: constant residual +0.5 => m2=0.25; +2.0 => m2=4.0.
    assert stats["A"][0] == 0.25 and stats["A"][1] == 40
    assert stats["B"][0] == 4.0 and stats["B"][1] == 40

    got = raw_second_moment_weights(stats, unit="C")
    expected = raw_second_moment_weights({"A": (0.25, 40), "B": (4.0, 40)}, unit="C")
    assert got == expected
    assert got["A"] > got["B"]  # the tighter model (smaller m2) gets more weight


def test_fixed_lead_does_not_substitute_other_archived_leads() -> None:
    """The source-skill fit uses lead 1 only; lead 0/2 never silently substitute."""
    rows = [
        {"model": "A", "city": "C1", "metric": "high", "target_date": "2026-03-01",
         "lead_days": 2, "forecast_value_c": 99.0, "settlement_value_c": 10.0},
        {"model": "A", "city": "C1", "metric": "high", "target_date": "2026-03-01",
         "lead_days": 0, "forecast_value_c": 10.5, "settlement_value_c": 10.0},
        {"model": "A", "city": "C1", "metric": "high", "target_date": "2026-03-01",
         "lead_days": 1, "forecast_value_c": 11.5, "settlement_value_c": 10.0},
    ]
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31T00:00:00+00:00")
    obs = loaded["obs"][("C1", "high")]
    assert obs["2026-03-01"] == {"A": 11.5}


def test_settlement_unit_f_converted_to_celsius() -> None:
    rows = [{
        "model": "A", "city": "F-City", "metric": "high", "target_date": "2026-03-01",
        "lead_days": 1, "forecast_value_c": 20.0, "settlement_value": 68.0,
        "settlement_unit": "F",  # 68F == 20C
    }]
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31T00:00:00+00:00")
    settle = loaded["settle"][("F-City", "high")]
    assert abs(settle["2026-03-01"] - 20.0) < 1e-9


def test_walk_forward_boundary_excludes_as_of_date() -> None:
    """A settlement dated exactly ``as_of`` must never enter training (strict target_date <
    as_of, per docs/authority Time law / no-look-ahead)."""
    rows = _rows_for_city("C1", "high", 5, models={"A": 0.1})
    # add a row settled exactly on the boundary date
    boundary_date = "2026-05-15"
    rows.append({
        "model": "A", "city": "C1", "metric": "high", "target_date": boundary_date,
        "lead_days": 1, "forecast_value_c": 15.0, "settlement_value_c": 15.0,
    })
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of=f"{boundary_date}T00:00:00+00:00")
    settle = loaded["settle"][("C1", "high")]
    assert boundary_date not in settle
    assert len(settle) == 5  # only the strictly-prior dates


def _basic_setup(tmp_path: Path, *, n_city_specific=70, n_region=45, n_global=10):
    """Three cities in the same OTHER region: one clears the CITY_SPECIFIC tier (>=60), one
    lands in REGION_POOLED (30-59), one falls to GLOBAL_CORE (<30)."""
    rows = []
    rows += _rows_for_city("CitySpecific", "high", n_city_specific, models={"A": 0.3, "B": 1.0})
    rows += _rows_for_city("RegionCity", "high", n_region, models={"A": 0.4, "B": 0.9})
    rows += _rows_for_city("GlobalCity", "high", n_global, models={"X": 0.2})
    conn = _make_db(rows)
    cities = [
        {"name": "CitySpecific", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0},
        {"name": "RegionCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -17.0, "lon": 177.0},
        {"name": "GlobalCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -16.0, "lon": 176.0},
    ]
    cities_path = _cities_json(tmp_path, cities)
    frozen_csv_path = tmp_path / "nonexistent_frozen.csv"  # absent -> mae_vs_frozen_delta is None
    return conn, cities_path, frozen_csv_path


def test_data_availability_tiers_select_expected_basket_source(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    artifact = fscw.build_artifact(
        conn, as_of="2026-12-31T00:00:00+00:00", generated_at="FIXED", cities_path=cities_path,
        frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
    )
    cities = artifact["cities"]
    assert cities["CitySpecific"]["high"]["basket_provenance"]["tier"] == "CITY_SPECIFIC"
    assert cities["CitySpecific"]["high"]["basket_provenance"]["region_fallback"] is False
    assert cities["RegionCity"]["high"]["basket_provenance"]["tier"] == "REGION_POOLED"
    assert cities["RegionCity"]["high"]["basket_provenance"]["region_fallback"] is True
    assert cities["GlobalCity"]["high"]["basket_provenance"]["tier"] == "GLOBAL_CORE"
    assert set(cities["GlobalCity"]["high"]["models"]) == set(fscw.GLOBAL_CORE_BASKET)


def test_determinism_same_db_state_and_as_of_byte_identical(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)

    def _run() -> str:
        artifact = fscw.build_artifact(
            conn, as_of="2026-12-31T00:00:00+00:00", generated_at="FIXED", cities_path=cities_path,
            frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
        )
        return json.dumps(artifact, sort_keys=True, indent=2)

    first = _run()
    second = _run()
    assert first == second
    contract = json.loads(first)["training_contract"]
    assert contract == {
        "name": fscw.TRAINING_CONTRACT,
        "fixed_lead_days": 1,
        "label_authority": "current_resolver_settlement_history",
        "oos_or_economic_advantage_claim": False,
    }


def test_weights_keyed_by_exact_model_id_never_positional(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    artifact = fscw.build_artifact(
        conn, as_of="2026-12-31T00:00:00+00:00", generated_at="FIXED", cities_path=cities_path,
        frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
    )
    models = artifact["cities"]["CitySpecific"]["high"]["models"]
    assert set(models) <= {"A", "B"}
    assert all(isinstance(k, str) for k in models)
    assert abs(sum(models.values()) - 1.0) < 1e-6


def test_city_specific_greedy_keeps_second_provider_without_mae_gain(tmp_path: Path) -> None:
    """A dominant first model cannot collapse an entry basket to one provider."""
    rows = _rows_for_city(
        "TwoProviderCity",
        "high",
        70,
        models={"A": 0.01, "B": 4.0},
    )
    rows += _rows_for_city(
        "TwoProviderCity",
        "low",
        70,
        models={"A": 0.01, "B": 4.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "TwoProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31T00:00:00+00:00",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
        servable=frozenset({"A", "B"}),
    )

    models = artifact["cities"]["TwoProviderCity"]["high"]["models"]
    assert set(models) == {"A", "B"}
    assert all(weight > 0.0 for weight in models.values())


def test_artifact_refuses_second_provider_rounded_out_of_publication(tmp_path: Path) -> None:
    """The published positive-weight map, not the pre-rounding basket, is authority."""
    rows = _rows_for_city(
        "RoundedProviderCity",
        "high",
        70,
        models={"A": 0.01, "B": 10_000.0},
    )
    rows += _rows_for_city(
        "RoundedProviderCity",
        "low",
        70,
        models={"A": 0.01, "B": 10_000.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "RoundedProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    with pytest.raises(ValueError, match=r"sources=\('A',\).*families=\('a',\)"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31T00:00:00+00:00",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"A", "B"}),
        )


def test_artifact_refuses_single_servable_domain_provider(tmp_path: Path) -> None:
    """A one-provider candidate set must fail before any artifact can be published."""
    rows = _rows_for_city("OneProviderCity", "high", 70, models={"A": 0.1})
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "OneProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    with pytest.raises(ValueError, match="at least 2 distinct servable/domain provider families"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31T00:00:00+00:00",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"A"}),
        )


def test_greedy_uses_ecmwf_not_second_icon_alias_for_second_family(tmp_path: Path) -> None:
    """Two DWD ICON aliases cannot satisfy the independent-provider requirement."""
    rows = _rows_for_city(
        "Munich",
        "high",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02, "ecmwf_ifs": 4.0},
    )
    rows += _rows_for_city(
        "Munich",
        "low",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02, "ecmwf_ifs": 4.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "Munich", "timezone": "Europe/Berlin", "country_code": "DE", "lat": 48.1351, "lon": 11.5820}],
    )

    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31T00:00:00+00:00",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
        servable=frozenset({"icon_d2", "icon_eu", "ecmwf_ifs"}),
    )

    sources = artifact["cities"]["Munich"]["high"]["models"]
    assert set(sources) == {"icon_d2", "ecmwf_ifs"}
    assert {fscw.provider_family_for_source(source) for source in sources} == {"dwd_icon", "ecmwf"}


def test_artifact_refuses_two_models_from_one_provider_family(tmp_path: Path) -> None:
    """Two source ids from the same DWD ICON family are still one provider."""
    rows = _rows_for_city(
        "Munich",
        "high",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02},
    )
    rows += _rows_for_city(
        "Munich",
        "low",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "Munich", "timezone": "Europe/Berlin", "country_code": "DE", "lat": 48.1351, "lon": 11.5820}],
    )

    # The single-family city basket degrades to the global-core fallback; with no
    # global-core model servable either, the cell still REFUSES publication (empty
    # sources) — a one-provider universe can never silently publish an entry cell.
    with pytest.raises(ValueError, match=r"sources=\(\).*families=\(\)"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31T00:00:00+00:00",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"icon_d2", "icon_eu"}),
        )


def test_default_servable_filter_excludes_retired_archive_models(tmp_path: Path) -> None:
    """A retired archive-only model (e.g. gfs_global, dropped from the live fetch
    2026-06-17) must never enter a basket: a basket naming it would be permanently
    unservable at decision time and, if single-model, would blank the city at the
    serving renormalizer's PRESENT_WEIGHT_FLOOR — the incident class this artifact
    exists to prevent."""
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    loaded = fscw.load_walk_forward_rows(
        conn, as_of="2026-12-31T00:00:00+00:00", servable=frozenset({"A"})
    )
    for by_date in loaded["obs"].values():
        for models in by_date.values():
            assert set(models) <= {"A"}
    assert "gfs_global" in ("gfs_global",)  # retired set documented in LIVE_SERVABLE_MODELS comment
    assert "gfs_global" not in fscw.LIVE_SERVABLE_MODELS
    assert "gem_global" not in fscw.LIVE_SERVABLE_MODELS
    assert "jma_seamless" not in fscw.LIVE_SERVABLE_MODELS
    assert "icon_seamless" not in fscw.LIVE_SERVABLE_MODELS
    assert "ecmwf_ifs" in fscw.LIVE_SERVABLE_MODELS


def test_artifact_excludes_models_outside_each_city_domain(tmp_path: Path) -> None:
    """Archived rows cannot license a model the current downloader will never request.

    Cover both city-specific and region-pooled selection: the latter also proves the
    pooled-basket cache is scoped by the target city's physically eligible model set.
    """
    rows = _rows_for_city(
        "Amsterdam",
        "high",
        70,
        models={
            "meteofrance_arome_france_hd": 0.05,
            "ukmo_global_deterministic_10km": 0.8,
            "ecmwf_ifs": 1.0,
        },
    )
    rows += _rows_for_city(
        "Lagos",
        "high",
        45,
        models={"ncep_nbm_conus": 0.05, "ecmwf_ifs": 0.8, "icon_global": 1.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [
            {
                "name": "Amsterdam",
                "timezone": "Europe/Amsterdam",
                "country_code": "NL",
                "lat": 52.3105,
                "lon": 4.7683,
            },
            {
                "name": "Lagos",
                "timezone": "Africa/Lagos",
                "country_code": "NG",
                "lat": 6.5774,
                "lon": 3.3212,
            },
        ],
    )
    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31T00:00:00+00:00",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
    )

    amsterdam = artifact["cities"]["Amsterdam"]["high"]
    assert amsterdam["basket_provenance"]["tier"] == "CITY_SPECIFIC"
    assert set(amsterdam["models"]) == {"ukmo_global_deterministic_10km", "ecmwf_ifs"}

    lagos = artifact["cities"]["Lagos"]["high"]
    assert lagos["basket_provenance"]["tier"] == "REGION_POOLED"
    assert set(lagos["models"]) == {"ecmwf_ifs", "icon_global"}


@pytest.mark.parametrize("thin_n", [1, 2])
def test_thin_model_cannot_bypass_finite_evidence_floor(
    tmp_path: Path,
    thin_n: int,
) -> None:
    """Neither a provider-family fallback nor zero empirical SE is evidence.

    One or two identical paired deltas can make a sample SE vanish, but they do
    not establish a population improvement. Until the fitter has a non-
    degenerate finite-evidence bound, a model below MIN_SETTLED_N cannot enter
    the served basket through either path.
    """
    deep_n = 70
    rows = _rows_for_city(
        "ThinEvidenceCity",
        "high",
        deep_n,
        models={"A": 0.9, "B": 1.1},
    )
    rows += _rows_for_city(
        "ThinEvidenceCity",
        "low",
        deep_n,
        models={"A": 0.9, "B": 1.1},
    )
    thin = _rows_for_city(
        "ThinEvidenceCity",
        "high",
        deep_n,
        models={"N": 0.02},
    )
    rows += thin[-thin_n:]
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{
            "name": "ThinEvidenceCity",
            "timezone": "Pacific/Fiji",
            "country_code": "FJ",
            "lat": -18.0,
            "lon": 178.0,
        }],
    )

    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31T00:00:00+00:00",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
        servable=frozenset({"A", "B", "N"}),
    )

    assert "N" not in artifact["cities"]["ThinEvidenceCity"]["high"]["models"]


def test_observation_only_rows_do_not_supply_training_labels() -> None:
    """The fitter requires a current-resolver settlement outcome, never an observation fallback."""
    rows = _rows_for_city("ObsCity", "low", 2, models={"A": 0.3, "B": 1.2})
    conn = _make_db(rows)
    conn.execute("DELETE FROM settlement_outcomes")
    conn.commit()

    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31T00:00:00+00:00")

    assert ("ObsCity", "low") not in loaded["settle"]
    assert loaded["excluded_reason_counts"]["RAW_LABEL_NOT_CURRENT_RESOLVER_ELIGIBLE"] == 4


def test_previous_runs_only_row_cannot_train_fixed_run_daily_weight() -> None:
    row = {
        "model": "A", "city": "PreviousOnlyCity", "metric": "high",
        "target_date": "2026-03-01", "lead_days": 1,
        "forecast_value_c": 99.0, "settlement_value_c": 20.0,
        "endpoint": "previous_runs",
    }

    loaded = fscw.load_walk_forward_rows(
        _make_db([row]), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"] == {}
    assert loaded["settle"] == {}


def test_previous_runs_cannot_train_fixed_run_daily_weight() -> None:
    rows = [
        {
            "model": "ecmwf_ifs", "city": "AnchorCity", "metric": "high",
            "target_date": "2026-03-01", "lead_days": 1,
            "forecast_value_c": 99.0, "settlement_value_c": 20.0,
            "endpoint": "previous_runs", "model_name": "ecmwf_ifs025",
            "source_id": "ecmwf_previous_runs",
            "source_family": "openmeteo_previous_runs",
            "product_id": "ecmwf_ifs025::previous_runs",
        },
        {
            "model": "ecmwf_ifs", "city": "AnchorCity", "metric": "high",
            "target_date": "2026-03-01", "lead_days": 1,
            "forecast_value_c": 20.5, "settlement_value_c": 20.0,
        },
    ]
    loaded = fscw.load_walk_forward_rows(
        _make_db(rows), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"][("AnchorCity", "high")]["2026-03-01"] == {"ecmwf_ifs": 20.5}
    assert "RAW_PRODUCT_NOT_CURRENT_LIVE_EQUIVALENT" not in loaded["excluded_reason_counts"]


def test_standard_meta_stamped_single_runs_product_is_current_equivalent() -> None:
    row = {
        "model": "ncep_nbm_conus", "city": "StampedCity", "metric": "high",
        "target_date": "2026-03-01", "lead_days": 1,
        "forecast_value_c": 20.5, "settlement_value_c": 20.0,
        "endpoint": "single_runs", "endpoint_mode": "standard_api_meta_stamped",
        "source_cycle_time": "2026-02-28T00:00:00+00:00",
        "source_available_at": "2026-02-28T06:00:00+00:00",
        "captured_at": "2026-02-28T06:01:00+00:00",
        "source_id": "ncep_nbm_conus_standard_meta_stamped",
        "source_family": "openmeteo_standard_meta_stamped",
        "product_id": (
            "ncep_nbm_conus::standard_api_meta_stamped::"
            "run=2026-02-28T00:00:00+00:00::modified=2026-02-28T01:00:00+00:00"
        ),
    }
    loaded = fscw.load_walk_forward_rows(
        _make_db([row]), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"][("StampedCity", "high")]["2026-03-01"] == {
        "ncep_nbm_conus": 20.5
    }


def test_request_params_cannot_relabel_a_different_physical_product() -> None:
    row = {
        "model": "A", "city": "RequestCity", "metric": "high",
        "target_date": "2026-03-01", "lead_days": 1,
        "forecast_value_c": 20.5, "settlement_value_c": 20.0,
        "request_params_json": json.dumps({
            "latitude": 10.0, "longitude": 20.0,
            "hourly": "temperature_2m", "models": "A",
            "temperature_unit": "celsius", "timezone": "UTC",
            "cell_selection": "nearest",
        }),
    }
    loaded = fscw.load_walk_forward_rows(
        _make_db([row]), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"] == {}
    assert loaded["excluded_reason_counts"]["RAW_PRODUCT_NOT_CURRENT_LIVE_EQUIVALENT"] == 1


def test_loader_uses_latest_eligible_actual_source_cycle_per_model_target() -> None:
    rows = [
        {
            "model": "A", "city": "LatestCity", "metric": "high",
            "target_date": "2026-03-01", "lead_days": 1,
            "forecast_value_c": 19.0, "settlement_value_c": 20.0,
            "source_cycle_time": "2026-02-27T00:00:00+00:00",
            "source_available_at": "2026-02-27T06:00:00+00:00",
            "captured_at": "2026-02-27T06:01:00+00:00",
        },
        {
            "model": "A", "city": "LatestCity", "metric": "high",
            "target_date": "2026-03-01", "lead_days": 1,
            "forecast_value_c": 20.5, "settlement_value_c": 20.0,
            "source_cycle_time": "2026-02-28T00:00:00+00:00",
            "source_available_at": "2026-02-28T06:00:00+00:00",
            "captured_at": "2026-02-28T06:01:00+00:00",
        },
    ]
    loaded = fscw.load_walk_forward_rows(
        _make_db(rows), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"][("LatestCity", "high")]["2026-03-01"] == {"A": 20.5}


def test_as_of_date_means_explicit_utc_midnight() -> None:
    assert fscw._as_of_utc("2026-03-02").isoformat() == "2026-03-02T00:00:00+00:00"
    with pytest.raises(ValueError, match="UTC offset"):
        fscw._as_of_utc("2026-03-02T00:00:00")


@pytest.mark.parametrize(
    ("raw_overrides", "expected_reason"),
    [
        ({"training_allowed": 1}, "RAW_TRAINING_ALLOWED_MARKER_INVALID"),
        ({"coverage_status": "PARTIAL"}, "RAW_COVERAGE_NOT_COVERED"),
        ({"source_id": ""}, "RAW_SOURCE_IDENTITY_MISSING"),
        ({"captured_at": "2026-12-31T00:00:00+00:00"},
         "RAW_AVAILABILITY_NOT_STRICTLY_BEFORE_AS_OF"),
    ],
)
def test_raw_input_contract_rejects_unverified_or_late_forecast_rows(
    raw_overrides: dict[str, object], expected_reason: str,
) -> None:
    row = {
        "model": "A", "city": "RawContractCity", "metric": "high",
        "target_date": "2026-03-01", "lead_days": 1,
        "forecast_value_c": 20.5, "settlement_value_c": 20.0,
        **raw_overrides,
    }
    loaded = fscw.load_walk_forward_rows(
        _make_db([row]), as_of="2026-04-01T00:00:00+00:00"
    )

    assert loaded["obs"] == {}
    assert loaded["settle"] == {}
    assert loaded["excluded_reason_counts"][expected_reason] == 1


def test_loader_preserves_current_label_and_raw_input_availability() -> None:
    rows = [{
        "model": "A", "city": "TimingCity", "metric": "high", "target_date": "2026-03-01",
        "lead_days": 1, "forecast_value_c": 20.5, "settlement_value_c": 20.0,
        "captured_at": "2026-02-28T12:00:00+00:00",
        "recorded_at": "2026-02-28 12:01:00",
        "source_available_at": "2026-02-28T11:00:00+00:00",
    }]
    conn = _make_db(rows)

    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31T00:00:00+00:00")

    label_key = ("TimingCity", "high", "2026-03-01")
    raw_key = (*label_key, "A")
    assert loaded["label_availability"][label_key] == "2026-03-01T12:00:00+00:00"
    assert loaded["forecast_availability"][raw_key] == {
        "captured_at": "2026-02-28T12:00:00+00:00",
        "recorded_at": "2026-02-28T12:01:00+00:00",
        "source_available_at": "2026-02-28T11:00:00+00:00",
        "source_id": "A_single_runs",
        "source_family": "openmeteo_single_runs",
        "product_id": "A::single_runs",
        "request_url_hash": _canonical_request_identity(
            SINGLE_RUNS_FORECAST_URL,
            {
                "latitude": 10.0, "longitude": 20.0, "hourly": "temperature_2m",
                "models": "A", "temperature_unit": "celsius", "timezone": "UTC",
                "cell_selection": "land",
            },
        )[1],
    }


def test_main_preserves_same_day_active_bytes_until_explicit_atomic_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    forecast_db = tmp_path / "forecast.db"
    sqlite3.connect(forecast_db).close()
    out_dir = tmp_path / "candidates"
    out_dir.mkdir()
    active = out_dir / "ACTIVE.json"
    legacy_candidate = out_dir / "city_weights_20260302.json"
    legacy_bytes = b'{"served":"legacy"}\n'
    legacy_candidate.write_bytes(legacy_bytes)
    legacy_pointer = {
        "artifact": legacy_candidate.name,
        "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
        "as_of": "2026-03-02T00:00:00+00:00",
    }
    active.write_text(json.dumps(legacy_pointer, sort_keys=True, indent=2), encoding="utf-8")
    active_before = active.read_bytes()

    artifact = {"settlement_rows_used": 1, "cities": {"candidate": {}}}
    monkeypatch.setattr(fscw, "build_artifact", lambda *_args, **_kwargs: artifact)
    monkeypatch.setattr(fscw, "_git_sha", lambda: "FIXED")
    real_atomic_write = fscw.write_json_atomic
    writes: list[str] = []

    def record_atomic_write(path: Path, payload: object, **kwargs: object) -> dict[str, object]:
        writes.append(Path(path).name)
        return real_atomic_write(path, payload, **kwargs)

    monkeypatch.setattr(fscw, "write_json_atomic", record_atomic_write)
    argv = [
        "--fcst", str(forecast_db), "--as-of", "2026-03-02", "--generated-at", "FIXED",
        "--out-dir", str(out_dir),
    ]

    assert fscw.main(argv) == 0
    candidates = sorted(out_dir.glob("city_weights_20260302_*.json"))
    assert len(candidates) == 1
    candidate = candidates[0]
    candidate_bytes = candidate.read_bytes()
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    assert candidate.name.endswith(f"_{candidate_sha}.json")
    assert legacy_candidate.read_bytes() == legacy_bytes
    assert active.read_bytes() == active_before
    assert writes == [candidate.name]

    # Repeating the same candidate is idempotent and cannot rewrite either artifact.
    assert fscw.main(argv) == 0
    assert writes == [candidate.name]
    assert sorted(out_dir.glob("city_weights_20260302_*.json")) == [candidate]

    # Explicit activation writes the immutable artifact first, then atomically swaps ACTIVE.
    assert fscw.main([*argv, "--activate"]) == 0
    pointer = json.loads(active.read_text(encoding="utf-8"))
    assert pointer == {
        "artifact": candidate.name,
        "sha256": candidate_sha,
        "as_of": "2026-03-02T00:00:00+00:00",
    }
    assert candidate.read_bytes() == candidate_bytes
    assert writes == [candidate.name, "ACTIVE.json"]
