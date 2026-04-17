# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: Zeus Dual-Track Metric Spine Refactor Phase 4.6;
#                  R-AA invariant; docs/operations/.../r_letter_namespace_ruling.md;
#                  team-lead Phase 4.6 dispatch 2026-04-17.
"""Phase 4.6 R-AA tests: cities cross-validate

R-AA: extractor startup must cross-validate config/cities.json (Zeus authority)
against the TIGGE coordinate manifest. Drift > ±0.01° or name-set mismatch
raises CityManifestDriftError (fail-closed). Matching manifests pass silently.

Sources of truth:
  team-lead Phase 4.6 dispatch (2026-04-17)
  exec-dan A2A pre-alignment (2026-04-17)
  scripts/extract_tigge_mx2t6_localday_max.py (authoritative implementation)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.extract_tigge_mx2t6_localday_max import (
    CityManifestDriftError,
    _cross_validate_city_manifests,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, cities: list[dict]) -> Path:
    """Write a synthetic TIGGE manifest JSON to tmp_path."""
    manifest = {"cities": cities}
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _city_entry(name: str, lat: float, lon: float) -> dict:
    """Minimal TIGGE manifest city entry."""
    return {"city": name, "lat": lat, "lon": lon}


def _zeus_city(name: str, lat: float, lon: float) -> dict:
    """Minimal Zeus cities_config entry (with lat/lon)."""
    return {"name": name, "lat": lat, "lon": lon, "timezone": "America/New_York", "unit": "F"}


def _zeus_city_no_coords(name: str) -> dict:
    """Zeus cities_config entry WITHOUT lat/lon (11 US °F cities — skipped in cross-validate)."""
    return {"name": name, "timezone": "America/New_York", "unit": "F"}


# ---------------------------------------------------------------------------
# R-AA.1: coordinate drift > 0.01° raises CityManifestDriftError
# ---------------------------------------------------------------------------

class TestR_AA_CoordinateDrift:
    """R-AA.1: lat/lon drift > ±0.01° for any city that has coords in Zeus
    cities_config must raise CityManifestDriftError with the offending city
    name in the error message.
    """

    def test_rejection_lat_drift_above_threshold_raises(self, tmp_path):
        """Drift of 0.02° lat (> 0.01° threshold) must raise CityManifestDriftError."""
        cities_config = [_zeus_city("London", lat=51.5074, lon=-0.1278)]
        # TIGGE manifest has lat offset by +0.02° (above threshold)
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("London", lat=51.5074 + 0.02, lon=-0.1278)
        ])
        with pytest.raises(CityManifestDriftError) as exc_info:
            _cross_validate_city_manifests(cities_config, manifest_path)
        assert "London" in str(exc_info.value), (
            f"CityManifestDriftError message must name the offending city, got: {exc_info.value}"
        )

    def test_rejection_lon_drift_above_threshold_raises(self, tmp_path):
        """Drift of 0.05° lon (> 0.01° threshold) must raise CityManifestDriftError."""
        cities_config = [_zeus_city("Tokyo", lat=35.6762, lon=139.6503)]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("Tokyo", lat=35.6762, lon=139.6503 + 0.05)
        ])
        with pytest.raises(CityManifestDriftError) as exc_info:
            _cross_validate_city_manifests(cities_config, manifest_path)
        assert "Tokyo" in str(exc_info.value)

    def test_acceptance_drift_exactly_at_threshold_does_not_raise(self, tmp_path):
        """Drift of exactly 0.01° is within tolerance — must not raise."""
        cities_config = [_zeus_city("Paris", lat=48.8566, lon=2.3522)]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("Paris", lat=48.8566 + 0.01, lon=2.3522)
        ])
        # Should NOT raise — 0.01° is the boundary, within tolerance
        _cross_validate_city_manifests(cities_config, manifest_path)

    def test_acceptance_drift_below_threshold_does_not_raise(self, tmp_path):
        """Drift of 0.005° (below 0.01° threshold) must pass silently."""
        cities_config = [_zeus_city("Sydney", lat=-33.8688, lon=151.2093)]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("Sydney", lat=-33.8688 + 0.005, lon=151.2093)
        ])
        _cross_validate_city_manifests(cities_config, manifest_path)

    def test_acceptance_cities_without_coords_in_zeus_are_skipped(self, tmp_path):
        """Cities with no lat/lon in Zeus config must be skipped (no drift check possible)."""
        cities_config = [_zeus_city_no_coords("SomeUSCity")]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("SomeUSCity", lat=40.0, lon=-75.0)
        ])
        # Must not raise — Zeus has no coords to compare against
        _cross_validate_city_manifests(cities_config, manifest_path)


# ---------------------------------------------------------------------------
# R-AA.2: name-set mismatch raises CityManifestDriftError
# ---------------------------------------------------------------------------

class TestR_AA_NameSetMismatch:
    """R-AA.2: if Zeus cities_config contains a city not in the TIGGE manifest
    (or vice versa), CityManifestDriftError must be raised.
    """

    def test_rejection_extra_zeus_city_not_in_manifest_raises(self, tmp_path):
        """Zeus has 'NewCity' not present in TIGGE manifest → CityManifestDriftError."""
        cities_config = [
            _zeus_city("London", lat=51.5074, lon=-0.1278),
            _zeus_city("NewCity", lat=10.0, lon=10.0),
        ]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("London", lat=51.5074, lon=-0.1278),
            # NewCity absent from manifest
        ])
        with pytest.raises(CityManifestDriftError) as exc_info:
            _cross_validate_city_manifests(cities_config, manifest_path)
        assert "NewCity" in str(exc_info.value), (
            f"Error must name the missing city, got: {exc_info.value}"
        )

    def test_rejection_extra_manifest_city_not_in_zeus_raises(self, tmp_path):
        """TIGGE manifest has 'GhostCity' not in Zeus cities_config → CityManifestDriftError."""
        cities_config = [_zeus_city("London", lat=51.5074, lon=-0.1278)]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("London", lat=51.5074, lon=-0.1278),
            _city_entry("GhostCity", lat=0.0, lon=0.0),
        ])
        with pytest.raises(CityManifestDriftError):
            _cross_validate_city_manifests(cities_config, manifest_path)


# ---------------------------------------------------------------------------
# R-AA.3: all-match passes silently
# ---------------------------------------------------------------------------

class TestR_AA_AllMatchPassesSilently:
    """R-AA.3: when city names match exactly and all coords are within tolerance,
    _cross_validate_city_manifests must return without raising.
    """

    def test_acceptance_identical_manifests_pass(self, tmp_path):
        """Identical city names + coords (zero drift) must pass silently."""
        cities_config = [
            _zeus_city("London", lat=51.5074, lon=-0.1278),
            _zeus_city("Tokyo", lat=35.6762, lon=139.6503),
        ]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("London", lat=51.5074, lon=-0.1278),
            _city_entry("Tokyo", lat=35.6762, lon=139.6503),
        ])
        _cross_validate_city_manifests(cities_config, manifest_path)

    def test_acceptance_mixed_zeus_cities_with_and_without_coords(self, tmp_path):
        """Cities with coords checked; cities without coords skipped. Both present — no raise."""
        cities_config = [
            _zeus_city("London", lat=51.5074, lon=-0.1278),
            _zeus_city_no_coords("SomeUSCity"),
        ]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("London", lat=51.5074, lon=-0.1278),
            _city_entry("SomeUSCity", lat=40.0, lon=-75.0),
        ])
        _cross_validate_city_manifests(cities_config, manifest_path)


# ---------------------------------------------------------------------------
# R-AA.4: integration — CityManifestDriftError is importable and is RuntimeError
# ---------------------------------------------------------------------------

class TestR_AA_ExceptionContract:
    """R-AA.4: CityManifestDriftError must be a RuntimeError subclass so callers
    can catch it with `except RuntimeError` at the extractor startup boundary.
    """

    def test_city_manifest_drift_error_is_runtime_error_subclass(self):
        """CityManifestDriftError must subclass RuntimeError (fail-closed contract)."""
        assert issubclass(CityManifestDriftError, RuntimeError), (
            "CityManifestDriftError must subclass RuntimeError for fail-closed startup guard"
        )

    def test_city_manifest_drift_error_message_is_string(self, tmp_path):
        """Raised exception message must be a non-empty string."""
        cities_config = [_zeus_city("Nowhere", lat=0.0, lon=0.0)]
        manifest_path = _write_manifest(tmp_path, [
            _city_entry("Nowhere", lat=0.0 + 0.05, lon=0.0)
        ])
        with pytest.raises(CityManifestDriftError) as exc_info:
            _cross_validate_city_manifests(cities_config, manifest_path)
        msg = str(exc_info.value)
        assert isinstance(msg, str) and msg.strip(), (
            f"CityManifestDriftError message must be non-empty string, got {msg!r}"
        )
