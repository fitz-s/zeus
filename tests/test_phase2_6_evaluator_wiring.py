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
    assert r["data_version"] == "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"


def test_parse_response_populates_data_version_for_known_source_low():
    r = _parse_response(
        _mock_payload(),
        "ecmwf_ifs025",
        datetime(2026, 5, 4, tzinfo=timezone.utc),
        source_id="ecmwf_open_data",
        temperature_metric="low",
    )
    assert r["data_version"] == "ecmwf_opendata_mn2t6_local_calendar_day_min_v1"


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


# ---- BLOCKER 6: evaluator wiring asserts ------------------------------------


_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "src" / "engine" / "evaluator.py"
_EVALUATOR_SRC = _EVALUATOR_PATH.read_text(encoding="utf-8")


def test_evaluator_imports_evaluate_calibration_transfer():
    """The new Phase 2.5 transfer evaluator must be referenced in evaluator.py."""
    assert (
        "evaluate_calibration_transfer" in _EVALUATOR_SRC
    ), "BLOCKER 6 regression: evaluate_calibration_transfer no longer referenced"


def test_evaluator_calls_evaluate_calibration_transfer_in_gate():
    """The transfer call site must be inside _evaluate_market_v2's flow.

    Look for the call pattern preceded by the OpenData domain check — that
    is the gate path BLOCKER 6 added. A bare import that's never invoked
    would leave the call dead just like the original BLOCKER 6 state.
    """
    # The actual gate uses a call expression — assert the call exists, not
    # just an import line.
    call_pattern = re.compile(
        r"evaluate_calibration_transfer\s*\(\s*conn\b",
        re.MULTILINE,
    )
    assert call_pattern.search(_EVALUATOR_SRC) is not None, (
        "BLOCKER 6 regression: evaluate_calibration_transfer is not actually "
        "called with conn from evaluator.py. The Phase 2.5 evidence gate is dead code."
    )


def test_evaluator_rejects_with_calibration_transfer_shadow_only_stage():
    """The rejection_stage tag must include the new SHADOW_ONLY label.

    Phase 2.5's whole point is making 'no evidence' → operator-visible
    SHADOW_ONLY rejection (instead of silent calibration). A regression
    that swapped to a generic stage (like SIGNAL_QUALITY) would lose the
    operational signal even if the gate fires.
    """
    assert (
        "CALIBRATION_TRANSFER_SHADOW_ONLY" in _EVALUATOR_SRC
    ), "Phase 2.5 SHADOW_ONLY rejection stage label missing from evaluator.py"


def test_evaluator_threads_temperature_metric_to_fetch_ensemble():
    """fetch_ensemble must be called with temperature_metric kwarg.

    This is what causes ens_result.data_version to be populated on the live
    fetch path — without it, BLOCKER 1's _parse_response logic stays
    starved of metric info and the gate at line ~2238 silently skips.
    """
    # Match `fetch_ensemble(` ... `temperature_metric=` within a small window;
    # nested parens (int(...), ensemble_primary_model()) inside the call mean a
    # naive `[^)]*` won't span the call. Greedy + non-greedy with line-bound
    # cap keeps it cheap.
    pattern = re.compile(
        r"fetch_ensemble\((?:.|\n){0,400}?temperature_metric\s*="
    )
    matches = pattern.findall(_EVALUATOR_SRC)
    assert len(matches) >= 2, (
        f"BLOCKER 1 regression: fetch_ensemble callsites in evaluator.py "
        f"missing temperature_metric kwarg ({len(matches)} matches; expected ≥2)"
    )


# ---- BLOCKER 2: rebuild script wires stratification ------------------------


_REBUILD_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "rebuild_calibration_pairs_v2.py"
)
_REBUILD_SRC = _REBUILD_PATH.read_text(encoding="utf-8")


def test_rebuild_script_passes_cycle_source_id_horizon_profile():
    """rebuild_calibration_pairs_v2.py must pass stratification kwargs.

    Without these, every rebuild row falls into the schema-default branch
    of add_calibration_pair_v2 (cycle='00', source_id='tigge_mars',
    horizon_profile='full') — silently mis-labeling OpenData snapshots as
    TIGGE. critic ATTACK 2 named this exact silent contamination path.
    """
    assert "cycle=_rb_cycle" in _REBUILD_SRC
    assert "source_id=_rb_source_id" in _REBUILD_SRC
    assert "horizon_profile=_rb_horizon_profile" in _REBUILD_SRC


def test_rebuild_script_imports_derive_source_id_from_data_version():
    assert "derive_source_id_from_data_version" in _REBUILD_SRC, (
        "BLOCKER 2 regression: rebuild script no longer imports the source_id "
        "derivation helper."
    )


# ---- monitor_refresh wiring (BLOCKER 1 collateral) -------------------------


_MONITOR_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "engine" / "monitor_refresh.py"
)
_MONITOR_SRC = _MONITOR_PATH.read_text(encoding="utf-8")


def test_monitor_refresh_threads_temperature_metric_to_fetch_ensemble():
    """Both monitor_refresh fetch_ensemble call sites must pass metric.

    critic ATTACK 3 flagged that monitor exit lanes were unprotected — the
    same fix needs to land here so cached ens_result has data_version too.
    """
    # Match `fetch_ensemble(` ... `temperature_metric=` within a small window;
    # nested parens (int(...), ensemble_primary_model()) inside the call mean a
    # naive `[^)]*` won't span the call. Greedy + non-greedy with line-bound
    # cap keeps it cheap.
    pattern = re.compile(
        r"fetch_ensemble\((?:.|\n){0,400}?temperature_metric\s*="
    )
    matches = pattern.findall(_MONITOR_SRC)
    assert len(matches) >= 2, (
        f"BLOCKER 1 collateral regression: monitor_refresh.py fetch_ensemble "
        f"callsites missing temperature_metric kwarg ({len(matches)} matches; "
        "expected ≥2)"
    )
