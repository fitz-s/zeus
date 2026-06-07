# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement promotion evidence from hand-authored field claims.
# Reuse: Run before using replacement evidence to resolve runtime trade authority.
# Authority basis: Promotion must be composed from before/after, same-CLOB, q_lcb, fine-tune, and refit reports.
"""Replacement forecast promotion evidence composer tests."""

from __future__ import annotations

from datetime import date, timedelta

from src.data.replacement_forecast_before_after_report import (
    ReplacementForecastBeforeAfterRow,
    build_replacement_forecast_before_after_report,
)
from src.data.replacement_forecast_emos_identity import READY_STATUS, REPLACEMENT_EMOS_KEY_SCHEMA
from src.data.replacement_forecast_guardrail_report import (
    ReplacementForecastGuardrailReplayRow,
    build_replacement_forecast_guardrail_report,
)
from src.data.replacement_forecast_promotion_evidence import (
    ReplacementForecastQLcbCoverageRow,
    build_replacement_forecast_promotion_evidence,
    build_replacement_forecast_q_lcb_coverage_report,
)
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import (
    SoftAnchorFineTuneRow,
    SoftAnchorParameter,
    evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune,
)


PARAM_SELECTED = SoftAnchorParameter(anchor_weight=0.80, anchor_sigma_c=3.00)
PARAM_OTHER = SoftAnchorParameter(anchor_weight=0.60, anchor_sigma_c=4.00)
GRID = (PARAM_SELECTED, PARAM_OTHER)


def _before_after():
    rows = []
    start = date(2026, 6, 1)
    for offset in range(5):
        for idx in range(50):
            rows.append(
                ReplacementForecastBeforeAfterRow(
                    official_date=(start + timedelta(days=offset)).isoformat(),
                    city=f"City{idx}",
                    temperature_metric="high",
                    guardrail_bucket="standard",
                    baseline_brier=0.30,
                    replacement_brier=0.20,
                    baseline_log_loss=0.70,
                    replacement_log_loss=0.50,
                    baseline_after_cost_pnl=0.00,
                    replacement_after_cost_pnl=1.00,
                )
            )
    return build_replacement_forecast_before_after_report(rows)


def _guardrail(*, blocked: bool = False):
    rows = []
    for idx in range(250):
        rows.append(
            ReplacementForecastGuardrailReplayRow(
                city=f"City{idx % 50}",
                temperature_metric="high",
                guardrail_bucket="standard",
                replay_status="BLOCKED" if blocked and idx == 0 else "SCORED",
                replacement_delta_after_cost_pnl=1.0,
                veto_applied=True,
                baseline_after_cost_pnl=-1.0,
                replacement_after_cost_pnl=0.0,
                reason_codes=("REPLACEMENT_REPLAY_SOURCE_AFTER_DECISION_TIME",) if blocked and idx == 0 else (),
            )
        )
    return build_replacement_forecast_guardrail_report(rows, axes=("guardrail_bucket",), min_scored_rows_per_bucket=20)


def _q_lcb(*, covered_rows: int = 250):
    rows = []
    start = date(2026, 6, 1)
    for idx in range(250):
        rows.append(
            ReplacementForecastQLcbCoverageRow(
                official_date=(start + timedelta(days=idx // 50)).isoformat(),
                city=f"City{idx % 50}",
                temperature_metric="high",
                guardrail_bucket="standard",
                truth_authority="VERIFIED",
                scored=True,
                covered_by_q_lcb=idx < covered_rows,
            )
        )
    return build_replacement_forecast_q_lcb_coverage_report(rows)


def _fine_tune(*, selected: SoftAnchorParameter = PARAM_SELECTED):
    rows = []
    start = date(2026, 6, 1)
    for offset in range(5):
        for idx in range(50):
            rows.append(
                SoftAnchorFineTuneRow(
                    official_date=start + timedelta(days=offset),
                    city=f"City{idx}",
                    temperature_metric="high",
                    bin_id="warm",
                    truth_authority="VERIFIED",
                    probabilities_by_parameter={
                        selected: {"warm": 0.80, "miss": 0.20},
                        (PARAM_OTHER if selected == PARAM_SELECTED else PARAM_SELECTED): {"warm": 0.40, "miss": 0.60},
                    },
                    settled_bin_id="warm",
                    guardrail_bucket="standard",
                )
            )
    return evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(rows, candidate_grid=GRID)


def _refit():
    return evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=5,
            official_rows=250,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="soft_anchor_product_specific_nested_refit",
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=20,
            emos_key_includes_product=True,
            emos_key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
            emos_identity_evidence_status=READY_STATUS,
            data_refit_requested=True,
        )
    )


def test_promotion_evidence_composes_runtime_admissible_evidence_from_reports() -> None:
    report = build_replacement_forecast_promotion_evidence(
        before_after_report=_before_after(),
        guardrail_report=_guardrail(),
        q_lcb_coverage_report=_q_lcb(),
        fine_tune_result=_fine_tune(),
        refit_decision=_refit(),
    )

    assert report.status == "PROMOTION_EVIDENCE_READY"
    assert report.promotion_evidence.promotion_allowed() is True
    policy = resolve_replacement_forecast_runtime_policy(
        {SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True, "openmeteo_ecmwf_ifs9_aifs_soft_anchor_kelly_increase_enabled": False, "openmeteo_ecmwf_ifs9_aifs_soft_anchor_direction_flip_enabled": False},
        promotion_evidence=report.promotion_evidence,
    )
    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PR399_LIVE_AUTHORITY_DISABLED" in policy.reason_codes


def test_promotion_evidence_blocks_low_q_lcb_coverage() -> None:
    report = build_replacement_forecast_promotion_evidence(
        before_after_report=_before_after(),
        guardrail_report=_guardrail(),
        q_lcb_coverage_report=_q_lcb(covered_rows=230),
        fine_tune_result=_fine_tune(),
        refit_decision=_refit(),
    )

    assert report.status == "SHADOW_PROMOTION_BLOCKED"
    assert "REPLACEMENT_Q_LCB_COVERAGE_BELOW_THRESHOLD" in report.reason_codes
    assert "REPLACEMENT_PROMOTION_Q_LCB_COVERAGE_TOO_LOW" in report.reason_codes


def test_promotion_evidence_blocks_wrong_finetune_parameter() -> None:
    report = build_replacement_forecast_promotion_evidence(
        before_after_report=_before_after(),
        guardrail_report=_guardrail(),
        q_lcb_coverage_report=_q_lcb(),
        fine_tune_result=_fine_tune(selected=PARAM_OTHER),
        refit_decision=_refit(),
    )

    assert report.status == "SHADOW_PROMOTION_BLOCKED"
    assert "REPLACEMENT_PROMOTION_FINE_TUNE_PARAMETER_MISMATCH" in report.reason_codes
    assert "REPLACEMENT_PROMOTION_ANCHOR_WEIGHT_MISMATCH" in report.reason_codes


def test_promotion_evidence_blocks_same_clob_guardrail_failures() -> None:
    report = build_replacement_forecast_promotion_evidence(
        before_after_report=_before_after(),
        guardrail_report=_guardrail(blocked=True),
        q_lcb_coverage_report=_q_lcb(),
        fine_tune_result=_fine_tune(),
        refit_decision=_refit(),
    )

    assert report.status == "SHADOW_PROMOTION_BLOCKED"
    assert "REPLACEMENT_GUARDRAIL_REPORT_HAS_BLOCKED_ROWS" in report.reason_codes
    assert "REPLACEMENT_PROMOTION_SAME_CLOB_REPLAY_INCOMPLETE" in report.reason_codes
