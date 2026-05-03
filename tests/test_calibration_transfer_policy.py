# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 9 calibration transfer policy.
"""Calibration transfer policy contract tests."""

from __future__ import annotations

from dataclasses import replace

from src.config import entry_forecast_config
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.calibration_transfer_policy import evaluate_calibration_transfer_policy
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


def test_open_data_high_uses_tigge_high_calibration_shadow_only_by_default() -> None:
    cfg = entry_forecast_config()

    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_SHADOW_ONLY",)
    assert decision.calibration_data_version == HIGH_LOCALDAY_MAX.data_version
    assert decision.live_eligible is False


def test_open_data_low_uses_tigge_low_calibration_shadow_only_by_default() -> None:
    cfg = entry_forecast_config()

    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.calibration_data_version == LOW_LOCALDAY_MIN.data_version


def test_calibration_transfer_needs_explicit_live_promotion() -> None:
    cfg = entry_forecast_config()

    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        live_promotion_approved=True,
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_APPROVED",)
    assert decision.live_promotion_approved is True


def test_unmapped_data_version_blocks_transfer() -> None:
    cfg = entry_forecast_config()

    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="ecmwf_open_data",
        forecast_data_version="tigge_mx2t6_local_calendar_day_max_v1",
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED",)
    assert decision.calibration_data_version is None


def test_source_mismatch_blocks_transfer() -> None:
    cfg = replace(entry_forecast_config(), source_id="ecmwf_open_data")

    decision = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id="tigge",
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_SOURCE_MISMATCH",)
