"""K2 Slice G: Signal Quality — fail-fast and status distinction tests.

Bug #24: provenance registry load failure must set REGISTRY_DEGRADED
Bug #16: bias correction failure must report applied=False
Bug #4: missing diurnal amplitude must raise ValueError
Bug #41: unparseable endDate must skip market (return None)
Bug #66: Day0 temporal context must log distinct failure reasons
"""

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── Bug #24: provenance registry degraded flag ─────────────────────────


class TestProvenanceRegistryDegraded:
    """Registry load failure must set REGISTRY_DEGRADED = True."""

    def test_missing_yaml_sets_degraded(self, tmp_path):
        from src.contracts.provenance_registry import _load_registry

        missing = tmp_path / "nonexistent.yaml"
        registry, degraded = _load_registry(missing)
        assert registry == {}
        assert degraded is True

    def test_valid_yaml_not_degraded(self, tmp_path):
        yaml_file = tmp_path / "provenance_registry.yaml"
        yaml_file.write_text(
            "constants:\n"
            "  - constant_name: test_const\n"
            "    file_location: test.py\n"
            "    declared_target: ev\n"
            "    data_basis: manual\n"
            "    validated_at: '2026-01-01'\n"
            "    replacement_criteria: none\n"
        )
        from src.contracts.provenance_registry import _load_registry

        registry, degraded = _load_registry(yaml_file)
        assert len(registry) == 1
        assert degraded is False

    def test_empty_yaml_sets_degraded(self, tmp_path):
        yaml_file = tmp_path / "provenance_registry.yaml"
        yaml_file.write_text("")
        from src.contracts.provenance_registry import _load_registry

        registry, degraded = _load_registry(yaml_file)
        assert registry == {}
        assert degraded is True


# ── Bug #16: bias correction status ────────────────────────────────────


class TestBiasCorrectionStatus:
    """_apply_bias_correction must return (array, applied_bool)."""

    def test_correction_failure_returns_false(self):
        from src.signal.ensemble_signal import EnsembleSignal

        maxes = np.array([30.0, 31.0, 32.0])
        city = MagicMock()
        city.name = "Chicago"
        city.lat = 41.8

        with patch("src.signal.ensemble_signal.EnsembleSignal._apply_bias_correction",
                    wraps=EnsembleSignal._apply_bias_correction):
            # Force exception by patching the DB import
            with patch.dict("sys.modules", {"src.state.db": None}):
                result, applied = EnsembleSignal._apply_bias_correction(
                    maxes, city, date(2026, 7, 15)
                )
                assert not applied
                np.testing.assert_array_equal(result, maxes)

    def test_correction_success_returns_true(self):
        from src.signal.ensemble_signal import EnsembleSignal

        maxes = np.array([30.0, 31.0, 32.0])
        city = MagicMock()
        city.name = "Chicago"
        city.lat = 41.8

        mock_conn = MagicMock()
        mock_row = {"bias": 2.0, "discount_factor": 0.7, "n_samples": 50}
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("src.calibration.manager.season_from_date", return_value="summer"):
            with patch("src.state.db.get_world_connection", return_value=mock_conn):
                result, applied = EnsembleSignal._apply_bias_correction(
                    maxes, city, date(2026, 7, 15)
                )
                assert applied is True
                expected = maxes - (2.0 * 0.7)
                np.testing.assert_array_almost_equal(result, expected)


# ── Bug #4: diurnal amplitude fail-fast ────────────────────────────────


class TestDiurnalAmplitudeFailFast:
    """Missing diurnal amplitude must raise ValueError, not default to 12.0."""

    def test_missing_both_keys_raises(self):
        from src.config import _unit_diurnal_amplitude

        city_row = {"name": "TestCity"}
        with pytest.raises(ValueError, match="No diurnal amplitude"):
            _unit_diurnal_amplitude(city_row, "C")

    def test_none_values_raises(self):
        from src.config import _unit_diurnal_amplitude

        city_row = {"name": "TestCity", "diurnal_amplitude_c": None, "diurnal_amplitude_f": None}
        with pytest.raises(ValueError, match="No diurnal amplitude"):
            _unit_diurnal_amplitude(city_row, "C")

    def test_valid_preferred_key_returns_value(self):
        from src.config import _unit_diurnal_amplitude

        city_row = {"name": "TestCity", "diurnal_amplitude_c": 10.5}
        assert _unit_diurnal_amplitude(city_row, "C") == 10.5

    def test_fallback_key_returns_value(self):
        from src.config import _unit_diurnal_amplitude

        city_row = {"name": "TestCity", "diurnal_amplitude_f": 18.0}
        assert _unit_diurnal_amplitude(city_row, "C") == 18.0


# ── Bug #41: endDate parse failure skips market ────────────────────────


class TestEndDateParseSkipsMarket:
    """Unparseable endDate must return None (skip market), not default to 24h."""

    def test_unparseable_enddate_returns_none(self):
        from src.data.market_scanner import _parse_event

        event = {
            "id": "test-event",
            "slug": "will-the-high-temperature-in-chicago-be-75-or-more-on-july-15",
            "title": "Will temperature exceed 75°F?",
            "endDate": "not-a-date",
            "markets": [
                {
                    "id": "market1",
                    "outcomePrices": "[0.55, 0.45]",
                    "outcomes": '["Yes", "No"]',
                    "clobTokenIds": '["token_yes", "token_no"]',
                    "conditionId": "cond1",
                }
            ],
        }
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        result = _parse_event(event, now, min_hours=0.0)
        assert result is None

    def test_valid_enddate_parses_correctly(self):
        from src.data.market_scanner import _parse_event

        future = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        event = {
            "id": "test-event",
            "slug": "will-the-high-temperature-in-chicago-be-75-or-more-on-july-15",
            "title": "Will temperature exceed 75°F?",
            "endDate": future.isoformat(),
            "markets": [
                {
                    "id": "market1",
                    "outcomePrices": "[0.55, 0.45]",
                    "outcomes": '["Yes", "No"]',
                    "clobTokenIds": '["token_yes", "token_no"]',
                    "conditionId": "cond1",
                }
            ],
        }
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        result = _parse_event(event, now, min_hours=0.0)
        # Should not be None (parsed successfully)
        assert result is not None


# ── Bug #66: Day0 temporal context distinct failure reasons ─────────────


class TestDay0TemporalContextReasons:
    """build_day0_temporal_context must log distinct reasons for each None return."""

    def test_solar_lookup_failure_logs_specific_reason(self):
        records: list[logging.LogRecord] = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        diurnal_logger = logging.getLogger("src.signal.diurnal")
        collector = Collector()
        collector.setLevel(logging.WARNING)
        diurnal_logger.addHandler(collector)

        try:
            from src.signal.diurnal import build_day0_temporal_context

            with patch("src.signal.diurnal.get_solar_day", return_value=None):
                result = build_day0_temporal_context(
                    "Chicago", date(2026, 7, 15), "America/Chicago"
                )
                assert result is None
                assert any(
                    "solar lookup failed" in r.getMessage()
                    for r in records
                ), f"Expected 'solar lookup failed' log, got: {[r.getMessage() for r in records]}"
        finally:
            diurnal_logger.removeHandler(collector)

    def test_date_mismatch_logs_specific_reason(self):
        records: list[logging.LogRecord] = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        diurnal_logger = logging.getLogger("src.signal.diurnal")
        collector = Collector()
        collector.setLevel(logging.WARNING)
        diurnal_logger.addHandler(collector)

        try:
            from src.signal.diurnal import build_day0_temporal_context

            solar_day = MagicMock()
            wrong_date_instant = MagicMock()
            wrong_date_instant.local_timestamp.date.return_value = date(2026, 7, 14)

            with patch("src.signal.diurnal.get_solar_day", return_value=solar_day):
                with patch("src.signal.diurnal._parse_runtime_observation_instant",
                           return_value=wrong_date_instant):
                    result = build_day0_temporal_context(
                        "Chicago", date(2026, 7, 15), "America/Chicago",
                        observation_time="2026-07-14T23:00:00",
                    )
                    assert result is None
                    assert any(
                        "mismatches target_date" in r.getMessage()
                        for r in records
                    ), f"Expected 'mismatches target_date' log, got: {[r.getMessage() for r in records]}"
        finally:
            diurnal_logger.removeHandler(collector)
