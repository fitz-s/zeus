from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_audit_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "51 source data"
        / "scripts"
        / "audit_tigge_manifest_against_cities.py"
    )
    spec = importlib.util.spec_from_file_location("audit_tigge_manifest_against_cities_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_audit_defaults_are_repo_local():
    module = _load_audit_module()
    repo_root = Path(__file__).resolve().parents[1]

    assert module.DEFAULT_CITIES == repo_root / "config" / "cities.json"
    assert module.DEFAULT_MANIFEST == repo_root / "51 source data" / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    assert module.DEFAULT_AUTHORITY == repo_root / "51 source data" / "docs" / "weather_settlement_station_authority_coordinates.json"
    assert ".openclaw/workspace-venus" not in str(module.DEFAULT_CITIES)
    assert module.DEFAULT_COORDINATE_THRESHOLD_M == 100.0


def test_manifest_audit_fails_coordinates_over_100m(tmp_path):
    module = _load_audit_module()

    cities = {
        "cities": [
            {
                "name": "Hong Kong",
                "lat": 22.303611,
                "lon": 114.171944,
                "timezone": "Asia/Hong_Kong",
                "unit": "C",
                "settlement_source_type": "hko",
                "hko_station": "HKO",
            }
        ]
    }
    manifest = {
        "cities": [
            {
                "city": "Hong Kong",
                "lat": 22.303611,
                "lon": 114.171944,
                "timezone": "Asia/Hong_Kong",
                "unit": "C",
                "settlement_source_kind": "hko_daily_extract",
                "station": "HKO",
            }
        ]
    }
    authority = {
        "cities": [
            {
                "city": "Hong Kong",
                "authority_lat": 22.301944444444445,
                "authority_lon": 114.17416666666666,
                "authority_url": "https://www.weather.gov.hk/en/cis/stn.htm",
            }
        ]
    }
    cities_path = tmp_path / "cities.json"
    manifest_path = tmp_path / "manifest.json"
    authority_path = tmp_path / "authority.json"
    cities_path.write_text(json.dumps(cities), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    result = module.audit(
        cities_path,
        manifest_path,
        authority_path=authority_path,
        coordinate_threshold_m=100.0,
    )

    assert result["ok"] is False
    assert result["authority_coordinate_mismatches"]
    mismatch = result["authority_coordinate_mismatches"][0]
    assert mismatch["city"] == "Hong Kong"
    assert mismatch["distance_m"] > 100.0
