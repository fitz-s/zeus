# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant F
#   (station-migration drift detection). When a Polymarket gamma URL points
#   to a different station than config/cities.json::wu_station, the daemon
#   writes an alert + bumps source_health degraded_since for the city. It
#   NEVER auto-rewrites cities.json — operator approves migrations consciously.
"""Antibody for Invariant F — station-migration drift detection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.station_migration_probe import (
    compare_cities_against_gamma,
    parse_station_from_url,
    run_probe,
)


def test_parse_trailing_station_codes():
    assert parse_station_from_url(
        "https://www.wunderground.com/history/daily/uk/london/EGLL"
    ) == "EGLL"
    assert parse_station_from_url(
        "https://www.wunderground.com/history/daily/fr/paris/LFPB"
    ) == "LFPB"
    # Trailing slash variant.
    assert parse_station_from_url(
        "https://www.wunderground.com/history/daily/jp/tokyo/RJTT/"
    ) == "RJTT"
    assert parse_station_from_url(None) is None
    assert parse_station_from_url("") is None
    # No trailing station-shaped slug (suffix < 3 chars).
    assert parse_station_from_url("https://example.com/x/") is None
    # The probe is intentionally permissive: any 3-5 char trailing slug is
    # treated as a candidate station, then compared against the configured
    # station. False positives from generic URLs surface as ALERT or WARN
    # downstream rather than being silently dropped — Fitz Constraint #4
    # data provenance: untrusted URL is NOT silently classified as 'no
    # station present'.


def test_no_alerts_when_aligned():
    cities = [
        {"name": "London", "wu_station": "EGLL"},
        {"name": "Paris", "wu_station": "LFPB"},
    ]
    gamma_lookup = {
        "London": "https://www.wunderground.com/history/daily/uk/london/EGLL",
        "Paris": "https://www.wunderground.com/history/daily/fr/paris/LFPB",
    }
    alerts = compare_cities_against_gamma(cities=cities, gamma_lookup=gamma_lookup)
    assert alerts == []


def test_synthetic_tokyo_mismatch_raises_alert():
    """Tokyo is configured RJAA but gamma now reports RJTT — must alert."""
    cities = [{"name": "Tokyo", "wu_station": "RJAA"}]
    gamma_lookup = {
        "Tokyo": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
    }
    alerts = compare_cities_against_gamma(cities=cities, gamma_lookup=gamma_lookup)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["city"] == "Tokyo"
    assert alert["configured_station"] == "RJAA"
    assert alert["gamma_station"] == "RJTT"
    assert alert["severity"] == "ALERT"


def test_unparseable_gamma_url_warns():
    cities = [{"name": "London", "wu_station": "EGLL"}]
    gamma_lookup = {"London": "https://example.com/some/other/format"}
    alerts = compare_cities_against_gamma(cities=cities, gamma_lookup=gamma_lookup)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "WARN"
    assert alerts[0]["reason"] == "gamma_url_not_parseable"


def test_missing_gamma_entry_is_silent():
    """Cities with no current open market do not raise alerts."""
    cities = [{"name": "London", "wu_station": "EGLL"}]
    gamma_lookup = {}  # no entry for London
    alerts = compare_cities_against_gamma(cities=cities, gamma_lookup=gamma_lookup)
    assert alerts == []


def test_run_probe_writes_alerts_file_and_does_not_rewrite_cities(tmp_path: Path, monkeypatch):
    cities_json = tmp_path / "cities.json"
    cities_json.write_text(json.dumps({
        "cities": [
            {"name": "Tokyo", "wu_station": "RJAA"},
            {"name": "London", "wu_station": "EGLL"},
        ]
    }))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def synthetic_gamma(city_names):
        return {
            "Tokyo": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
            "London": "https://www.wunderground.com/history/daily/uk/london/EGLL",
        }

    pre_text = cities_json.read_text()

    summary = run_probe(
        cities_json_path=cities_json,
        state_dir=state_dir,
        gamma_fetcher=synthetic_gamma,
    )
    assert summary["status"] == "ok"
    assert summary["alerts_count"] == 1
    alerts_path = state_dir / "station_migration_alerts.json"
    assert alerts_path.exists()
    payload = json.loads(alerts_path.read_text())
    assert payload["alerts_count"] == 1
    assert payload["alerts"][0]["city"] == "Tokyo"
    # Idempotent rule: cities.json must NOT be rewritten by the probe.
    post_text = cities_json.read_text()
    assert pre_text == post_text, "Probe must never auto-rewrite cities.json"


def test_run_probe_handles_gamma_exception(tmp_path: Path):
    cities_json = tmp_path / "cities.json"
    cities_json.write_text(json.dumps({
        "cities": [{"name": "London", "wu_station": "EGLL"}]
    }))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def broken_gamma(city_names):
        raise RuntimeError("polymarket gamma 503")

    summary = run_probe(
        cities_json_path=cities_json,
        state_dir=state_dir,
        gamma_fetcher=broken_gamma,
    )
    assert summary["status"] == "gamma_fetch_failed"
    assert summary["alerts_count"] == 0
