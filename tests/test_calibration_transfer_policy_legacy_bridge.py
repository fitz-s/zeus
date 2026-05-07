# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: live-alignment-2026-05-07 D1 — legacy mx2t6/mn2t6 bridge mapping
"""Tests: legacy mx2t6/mn2t6 data_version keys map to same tigge calibration target.

Covers the 2026-05-07 bridge added to _TRANSFER_SOURCE_BY_OPENDATA_VERSION so that
1,568 stale ensemble_snapshots_v2 rows and 477 LIVE_ELIGIBLE readiness rows (tagged
with legacy versions) still resolve a Platt during the mx2t3/mn2t3 transition.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import entry_forecast_config
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
)
from src.data.calibration_transfer_policy import (
    _TRANSFER_SOURCE_BY_OPENDATA_VERSION,
    evaluate_calibration_transfer_policy,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


# ---------------------------------------------------------------------------
# Map-level assertions: both legacy AND new keys must be present and point at
# the same tigge_* target.
# ---------------------------------------------------------------------------


def test_legacy_high_maps_to_same_tigge_target_as_new_high() -> None:
    """_TRANSFER_SOURCE_BY_OPENDATA_VERSION: legacy HIGH key → same target as new HIGH key."""
    new_target = _TRANSFER_SOURCE_BY_OPENDATA_VERSION[ECMWF_OPENDATA_HIGH_DATA_VERSION]
    legacy_target = _TRANSFER_SOURCE_BY_OPENDATA_VERSION[_ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY]
    assert legacy_target == new_target, (
        f"Legacy HIGH key maps to {legacy_target!r}, "
        f"expected {new_target!r} (same as new mx2t3 key)"
    )
    assert legacy_target == HIGH_LOCALDAY_MAX.data_version


def test_legacy_low_maps_to_same_tigge_target_as_new_low() -> None:
    """_TRANSFER_SOURCE_BY_OPENDATA_VERSION: legacy LOW key → same target as new LOW key."""
    new_target = _TRANSFER_SOURCE_BY_OPENDATA_VERSION[ECMWF_OPENDATA_LOW_DATA_VERSION]
    legacy_target = _TRANSFER_SOURCE_BY_OPENDATA_VERSION[_ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY]
    assert legacy_target == new_target, (
        f"Legacy LOW key maps to {legacy_target!r}, "
        f"expected {new_target!r} (same as new mn2t3 key)"
    )
    assert legacy_target == LOW_LOCALDAY_MIN.data_version


def test_map_contains_all_four_keys() -> None:
    """All four opendata keys (2 new + 2 legacy) are present in the bridge map."""
    assert ECMWF_OPENDATA_HIGH_DATA_VERSION in _TRANSFER_SOURCE_BY_OPENDATA_VERSION
    assert ECMWF_OPENDATA_LOW_DATA_VERSION in _TRANSFER_SOURCE_BY_OPENDATA_VERSION
    assert _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY in _TRANSFER_SOURCE_BY_OPENDATA_VERSION
    assert _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY in _TRANSFER_SOURCE_BY_OPENDATA_VERSION


# ---------------------------------------------------------------------------
# Policy-level assertions: evaluate_calibration_transfer_policy resolves
# SHADOW_ONLY (not BLOCKED) for both legacy versions, with correct
# calibration_data_version populated.
# ---------------------------------------------------------------------------


def test_legacy_high_resolves_shadow_only_not_blocked() -> None:
    """Legacy HIGH data_version does not return CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED."""
    cfg = entry_forecast_config()
    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=_ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    )
    assert decision.status == "SHADOW_ONLY", (
        f"Expected SHADOW_ONLY, got {decision.status!r} "
        f"(reason_codes={decision.reason_codes})"
    )
    assert "CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED" not in decision.reason_codes
    assert decision.calibration_data_version == HIGH_LOCALDAY_MAX.data_version


def test_legacy_low_resolves_shadow_only_not_blocked() -> None:
    """Legacy LOW data_version does not return CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED."""
    cfg = entry_forecast_config()
    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=_ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
    )
    assert decision.status == "SHADOW_ONLY", (
        f"Expected SHADOW_ONLY, got {decision.status!r} "
        f"(reason_codes={decision.reason_codes})"
    )
    assert "CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED" not in decision.reason_codes
    assert decision.calibration_data_version == LOW_LOCALDAY_MIN.data_version


def test_legacy_high_live_eligible_with_promotion_approved() -> None:
    """Legacy HIGH data_version returns LIVE_ELIGIBLE when live_promotion_approved=True."""
    cfg = entry_forecast_config()
    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=_ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
        live_promotion_approved=True,
    )
    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.calibration_data_version == HIGH_LOCALDAY_MAX.data_version
    assert decision.live_eligible is True


def test_legacy_low_live_eligible_with_promotion_approved() -> None:
    """Legacy LOW data_version returns LIVE_ELIGIBLE when live_promotion_approved=True."""
    cfg = entry_forecast_config()
    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=_ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
        live_promotion_approved=True,
    )
    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.calibration_data_version == LOW_LOCALDAY_MIN.data_version
    assert decision.live_eligible is True
