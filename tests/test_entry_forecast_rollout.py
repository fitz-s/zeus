# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 12 canary/live rollout gate.
"""Entry forecast canary/live rollout gate tests."""

from __future__ import annotations

from dataclasses import replace

from src.config import EntryForecastRolloutMode, entry_forecast_config
from src.control.entry_forecast_rollout import (
    EntryForecastPromotionEvidence,
    evaluate_entry_forecast_rollout_gate,
)
from src.data.live_entry_status import LiveEntryForecastStatus


def _status(*, ready: bool = True) -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE" if ready else "BLOCKED",
        blockers=() if ready else ("NO_FUTURE_TARGET_DATE_COVERAGE",),
        executable_row_count=2 if ready else 0,
        producer_readiness_count=2 if ready else 0,
        producer_live_eligible_count=2 if ready else 0,
    )


def _evidence(**overrides):
    base = {
        "operator_approval_id": "operator-approval-1",
        "g1_evidence_id": "g1-report-1",
        "status_snapshot": _status(),
        "calibration_promotion_approved": True,
        "canary_success_evidence_id": None,
    }
    base.update(overrides)
    return EntryForecastPromotionEvidence(**base)


def test_blocked_rollout_mode_never_promotes() -> None:
    decision = evaluate_entry_forecast_rollout_gate(
        config=entry_forecast_config(),
        evidence=_evidence(),
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("ENTRY_FORECAST_ROLLOUT_BLOCKED",)
    assert decision.may_run_canary is False
    assert decision.may_submit_live_orders is False


def test_canary_requires_operator_approval_g1_and_calibration_promotion() -> None:
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.CANARY)

    decision = evaluate_entry_forecast_rollout_gate(
        config=cfg,
        evidence=_evidence(
            operator_approval_id=None,
            g1_evidence_id=None,
            calibration_promotion_approved=False,
        ),
    )

    assert decision.status == "BLOCKED"
    assert "ENTRY_FORECAST_OPERATOR_APPROVAL_MISSING" in decision.reason_codes
    assert "ENTRY_FORECAST_G1_EVIDENCE_MISSING" in decision.reason_codes
    assert "CALIBRATION_TRANSFER_APPROVAL_MISSING" in decision.reason_codes


def test_canary_requires_live_entry_status_ready() -> None:
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.CANARY)

    decision = evaluate_entry_forecast_rollout_gate(
        config=cfg,
        evidence=_evidence(status_snapshot=_status(ready=False)),
    )

    assert decision.status == "BLOCKED"
    assert "NO_FUTURE_TARGET_DATE_COVERAGE" in decision.reason_codes


def test_canary_with_required_evidence_is_canary_eligible_only() -> None:
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.CANARY)

    decision = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=_evidence())

    assert decision.status == "CANARY_ELIGIBLE"
    assert decision.reason_codes == ("ENTRY_FORECAST_CANARY_APPROVED",)
    assert decision.may_run_canary is True
    assert decision.may_submit_live_orders is False


def test_live_rollout_requires_canary_success_evidence() -> None:
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    decision = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=_evidence())

    assert decision.status == "BLOCKED"
    assert "ENTRY_FORECAST_CANARY_SUCCESS_MISSING" in decision.reason_codes


def test_live_rollout_with_canary_success_can_submit_live_orders() -> None:
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    decision = evaluate_entry_forecast_rollout_gate(
        config=cfg,
        evidence=_evidence(canary_success_evidence_id="canary-success-1"),
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.reason_codes == ("ENTRY_FORECAST_LIVE_APPROVED",)
    assert decision.may_submit_live_orders is True
