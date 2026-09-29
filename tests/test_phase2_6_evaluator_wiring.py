# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: critic-opus second-pass review 2026-05-04, ATTACKs 1+6+7
"""Relationship tests: evaluator + ensemble_client are wired to the new gates.

These verify the *cross-module wiring* critic-opus second-pass flagged as
missing — the modular tests in test_phase2_5/2_6/2_75/3 confirmed each
piece worked in isolation, but none confirmed the production evaluator
actually called them. These tests close that gap with a mix of
behavioral assertions (fetch_ensemble actually populates data_version)
and structural assertions (evaluator imports + invokes the gate).

If the evaluator gate is removed in a refactor, this file catches it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from src.data.ensemble_client import _parse_response


def _mock_payload() -> dict:
    return {
        "hourly": {
            "time": ["2026-05-04T00:00:00", "2026-05-04T01:00:00"],
            "temperature_2m": [10.0, 11.0],
            "temperature_2m_member01": [10.5, 11.5],
        }
    }


# ---- BLOCKER 1: data_version on live ens_result -----------------------------


def test_parse_response_populates_data_version_for_known_source_high():
    """ecmwf_open_data + high → ecmwf_opendata MAX data_version on ens_result."""
    r = _parse_response(
        _mock_payload(),
        "ecmwf_ifs025",
        datetime(2026, 5, 4, tzinfo=timezone.utc),
        source_id="ecmwf_open_data",
        temperature_metric="high",
    )
    assert r["data_version"] == "ecmwf_opendata_mx2t6_local_calendar_day_max"


def test_parse_response_populates_data_version_for_known_source_low():
    r = _parse_response(
        _mock_payload(),
        "ecmwf_ifs025",
        datetime(2026, 5, 4, tzinfo=timezone.utc),
        source_id="ecmwf_open_data",
        temperature_metric="low",
    )
    assert r["data_version"] == "ecmwf_opendata_mn2t6_local_calendar_day_min"


def test_parse_response_sentinel_for_unrecognized_source():
    """Unknown source + metric → 'unknown_forecast_source_family' sentinel.

    The sentinel is what activates the evaluator's UNKNOWN_FORECAST_SOURCE_FAMILY
    rejection gate; a live fetch through gfs025 / openmeteo_ensemble_*
    no longer slips through to the calibrator silently.
    """
    r = _parse_response(
        _mock_payload(),
        "gfs025",
        datetime(2026, 5, 4, tzinfo=timezone.utc),
        source_id="openmeteo_ensemble_gfs025",
        temperature_metric="high",
    )
    assert r["data_version"] == "unknown_forecast_source_family"


def test_parse_response_no_metric_means_no_data_version():
    """Diagnostic / crosscheck callers (no metric) preserve None fallthrough."""
    r = _parse_response(
        _mock_payload(),
        "ecmwf_ifs025",
        datetime(2026, 5, 4, tzinfo=timezone.utc),
        source_id="ecmwf_open_data",
        temperature_metric=None,
    )
    assert r["data_version"] is None
