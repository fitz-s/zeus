"""Combined readiness report for replacement forecast go-live decisions."""

from __future__ import annotations

import json
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from src.data.replacement_forecast_config_switch import (
    build_replacement_forecast_config_switch_plan,
    build_replacement_forecast_live_authority_config_switch_plan,
)
from src.data.replacement_forecast_before_after_report import (
    ReplacementForecastBeforeAfterReport,
    ReplacementForecastBeforeAfterRow,
    build_replacement_forecast_before_after_report,
)
from src.data.replacement_forecast_finetune_artifact import fine_tune_result_from_jsonable
from src.data.replacement_forecast_guardrail_report import (
    ReplacementForecastGuardrailReplayRow,
    build_replacement_forecast_guardrail_report,
)
from src.data.replacement_forecast_live_switch_surface import (
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    ReplacementForecastLiveSwitchReport,
    build_replacement_forecast_live_switch_input_from_current_state,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_live_dry_run import (
    ReplacementForecastLiveDryRunInput,
    build_replacement_forecast_live_dry_run_report,
)
from src.data.replacement_forecast_readiness import (
    ReplacementForecastDependency,
    ReplacementForecastReadinessDecision,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_promotion_evidence import (
    ReplacementForecastQLcbCoverageRow,
    build_replacement_forecast_promotion_evidence,
    build_replacement_forecast_q_lcb_coverage_report,
)
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitDecision,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload
from src.data.replacement_forecast_rollback_plan import (
    ReplacementForecastRollbackPlan,
    build_replacement_forecast_rollback_plan,
)
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    ReplacementForecastRuntimePolicy,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecision,
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)
from src.state.db import _connect


REPORT_SCHEMA_VERSION = "replacement_forecast_go_live_readiness_v1"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
REQUIRED_RUNTIME_FLAGS = (
    SHADOW_FLAG,
    VETO_FLAG,
    TRADE_AUTHORITY_FLAG,
    KELLY_INCREASE_FLAG,
    DIRECTION_FLIP_FLAG,
)
BEFORE_AFTER_CSV_COLUMNS = (
    "official_date",
    "city",
    "temperature_metric",
    "guardrail_bucket",
    "baseline_brier",
    "replacement_brier",
    "baseline_log_loss",
    "replacement_log_loss",
    "baseline_after_cost_pnl",
    "replacement_after_cost_pnl",
    "truth_authority",
    "replay_status",
)


def replacement_forecast_go_live_payload_template() -> dict[str, object]:
    """Return an explicit JSON template for the readiness report CLI."""

    return {
        "strategy_key": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        "runtime_flags": {
            SHADOW_FLAG: True,
            VETO_FLAG: True,
            TRADE_AUTHORITY_FLAG: False,
            KELLY_INCREASE_FLAG: False,
            DIRECTION_FLIP_FLAG: False,
        },
        "promotion_evidence": None,
        "source_fact_status": "STALE_FOR_LIVE",
        "data_fact_status": "STALE_FOR_LIVE",
        "live_switch": {
            "available_files": list(REQUIRED_LIVE_READ_FILES),
            "forecast_tables": list(REQUIRED_FORECAST_TABLES),
            "world_tables": list(REQUIRED_WORLD_TABLES),
            "trade_tables": list(REQUIRED_TRADE_TABLES),
            "enabled_evidence_gates": list(REQUIRED_EVIDENCE_GATES),
            "proposed_write_tables": [],
        },
        "refit_evidence": {
            "official_days": 0,
            "official_rows": 0,
            "temperature_metric": "high",
            "source_family": "derived_posterior",
            "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            "calibration_method": "soft_anchor_product_specific_nested_refit",
            "enabled_evidence": list(REQUIRED_REFIT_EVIDENCE),
            "min_guardrail_bucket_rows": 0,
            "high_low_mixed": False,
            "baseline_calibration_reused": False,
            "emos_key_includes_product": False,
            "emos_key_schema": "missing",
            "emos_identity_evidence_status": "MISSING",
            "data_refit_requested": False,
            "live_promotion_requested": False,
        },
        "readiness": {
            "city": "Example City",
            "target_date": "2026-06-07",
            "temperature_metric": "high",
            "decision_time": "2026-06-06T04:00:00+00:00",
            "computed_at": "2026-06-06T04:01:00+00:00",
            "expires_at": "2026-06-06T06:00:00+00:00",
            "dependencies": [
                {
                    "role": "baseline_b0",
                    "source_id": "ecmwf_open_data",
                    "product_id": "ecmwf_opendata_ifs_ens_0p25",
                    "data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
                    "source_run_id": "baseline-run",
                    "source_available_at": "2026-06-06T02:00:00+00:00",
                    "status": "SHADOW_ONLY",
                },
                {
                    "role": "aifs_sampled_2t",
                    "source_id": "ecmwf_aifs_ens",
                    "product_id": "ecmwf_aifs_ens_sampled_2t_6h_v1",
                    "data_version": "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                    "source_run_id": "aifs-run",
                    "source_available_at": "2026-06-06T02:30:00+00:00",
                    "status": "SHADOW_ONLY",
                },
                {
                    "role": "openmeteo_ifs9_anchor",
                    "source_id": "openmeteo_ecmwf_ifs_9km",
                    "product_id": "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                    "data_version": "openmeteo_ecmwf_ifs9_anchor_localday_high",
                    "source_run_id": "openmeteo-anchor-run",
                    "source_available_at": "2026-06-06T03:00:00+00:00",
                    "status": "SHADOW_ONLY",
                },
                {
                    "role": "soft_anchor_posterior",
                    "source_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                    "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
                    "data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
                    "source_run_id": "posterior-run",
                    "source_available_at": "2026-06-06T03:05:00+00:00",
                    "status": "SHADOW_ONLY",
                    "posterior_id": 1,
                },
            ],
        },
        "before_after_rows": [
            {
                "official_date": "2026-06-01",
                "city": "Example City",
                "temperature_metric": "high",
                "guardrail_bucket": "standard",
                "baseline_brier": 0.0,
                "replacement_brier": 0.0,
                "baseline_log_loss": 0.0,
                "replacement_log_loss": 0.0,
                "baseline_after_cost_pnl": 0.0,
                "replacement_after_cost_pnl": 0.0,
                "truth_authority": "VERIFIED",
                "replay_status": "SCORED",
            }
        ],
        "rollback": {
            "reason": "operator rollback path for replacement forecast readiness report",
            "generated_at": "2026-06-06T00:00:00+00:00",
            "additional_source_ids_to_pause": [],
        },
        "operator_approval_id": None,
        "min_before_after_official_days": 5,
        "min_before_after_official_rows": 250,
        "capital_replay": None,
    }


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastGoLiveReadinessInput:
    strategy_key: str
    runtime_policy: ReplacementForecastRuntimePolicy
    live_switch_report: ReplacementForecastLiveSwitchReport
    switch_decision: ReplacementForecastSwitchDecision
    refit_decision: ReplacementForecastRefitDecision
    before_after_report: ReplacementForecastBeforeAfterReport
    rollback_plan: ReplacementForecastRollbackPlan
    source_fact_status: str
    data_fact_status: str
    config_switch_report: Mapping[str, object] | None = None
    operator_approval_id: str | None = None
    capital_replay: Mapping[str, object] | None = None
    live_dry_run: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _reject_alias(str(self.strategy_key or ""), field_name="strategy_key")
        if not self.strategy_key:
            raise ValueError("strategy_key is required")
        if not isinstance(self.runtime_policy, ReplacementForecastRuntimePolicy):
            raise TypeError("runtime_policy must be ReplacementForecastRuntimePolicy")
        if not isinstance(self.live_switch_report, ReplacementForecastLiveSwitchReport):
            raise TypeError("live_switch_report must be ReplacementForecastLiveSwitchReport")
        if not isinstance(self.switch_decision, ReplacementForecastSwitchDecision):
            raise TypeError("switch_decision must be ReplacementForecastSwitchDecision")
        if not isinstance(self.refit_decision, ReplacementForecastRefitDecision):
            raise TypeError("refit_decision must be ReplacementForecastRefitDecision")
        if not isinstance(self.before_after_report, ReplacementForecastBeforeAfterReport):
            raise TypeError("before_after_report must be ReplacementForecastBeforeAfterReport")
        if not isinstance(self.rollback_plan, ReplacementForecastRollbackPlan):
            raise TypeError("rollback_plan must be ReplacementForecastRollbackPlan")
        if self.source_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
            raise ValueError("source_fact_status must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")
        if self.data_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
            raise ValueError("data_fact_status must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")
        if self.operator_approval_id is not None:
            _reject_alias(self.operator_approval_id, field_name="operator_approval_id")
        if self.capital_replay is not None:
            for field_name in (
                "selected_label",
                "selected_capital_gain_variant",
                "selected_roi_variant",
                "objective",
            ):
                value = self.capital_replay.get(field_name)
                if value is not None:
                    _reject_alias(str(value), field_name=f"capital_replay.{field_name}")


@dataclass(frozen=True)
class ReplacementForecastGoLiveReadinessReport:
    schema_version: str
    status: str
    reason_codes: tuple[str, ...]
    strategy_key: str
    simple_switch_ready: bool
    fine_tune_ready: bool
    live_promotion_ready: bool
    source_fact_status: str
    data_fact_status: str
    runtime_policy_status: str
    switch_decision_status: str
    switch_decision_readiness_id: str | None
    switch_can_read_shadow_posterior: bool
    switch_can_apply_reactor_hook: bool
    switch_can_apply_veto: bool
    switch_can_initiate_trade: bool
    switch_can_increase_kelly: bool
    switch_can_flip_direction: bool
    rollback_reversible: bool
    before_after_official_days: int
    before_after_official_rows: int
    before_after_brier_delta: float | None
    before_after_log_loss_delta: float | None
    before_after_after_cost_delta: float | None
    config_switch_status: str | None
    config_switch_reason_codes: tuple[str, ...]
    config_switch_json_patch: tuple[Mapping[str, object], ...]
    capital_replay: Mapping[str, object] | None
    live_dry_run: Mapping[str, object] | None
    blockers: dict[str, list[str]]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "strategy_key": self.strategy_key,
            "simple_switch_ready": self.simple_switch_ready,
            "fine_tune_ready": self.fine_tune_ready,
            "live_promotion_ready": self.live_promotion_ready,
            "source_fact_status": self.source_fact_status,
            "data_fact_status": self.data_fact_status,
            "runtime_policy_status": self.runtime_policy_status,
            "switch_decision_status": self.switch_decision_status,
            "switch_decision_readiness_id": self.switch_decision_readiness_id,
            "switch_can_read_shadow_posterior": self.switch_can_read_shadow_posterior,
            "switch_can_apply_reactor_hook": self.switch_can_apply_reactor_hook,
            "switch_can_apply_veto": self.switch_can_apply_veto,
            "switch_can_initiate_trade": self.switch_can_initiate_trade,
            "switch_can_increase_kelly": self.switch_can_increase_kelly,
            "switch_can_flip_direction": self.switch_can_flip_direction,
            "rollback_reversible": self.rollback_reversible,
            "before_after_official_days": self.before_after_official_days,
            "before_after_official_rows": self.before_after_official_rows,
            "before_after_brier_delta": self.before_after_brier_delta,
            "before_after_log_loss_delta": self.before_after_log_loss_delta,
            "before_after_after_cost_delta": self.before_after_after_cost_delta,
            "config_switch_status": self.config_switch_status,
            "config_switch_reason_codes": list(self.config_switch_reason_codes),
            "config_switch_json_patch": [dict(item) for item in self.config_switch_json_patch],
            "capital_replay": None if self.capital_replay is None else dict(self.capital_replay),
            "live_dry_run": None if self.live_dry_run is None else dict(self.live_dry_run),
            "blockers": {key: list(value) for key, value in self.blockers.items()},
        }


def build_replacement_forecast_go_live_readiness_report(
    request: ReplacementForecastGoLiveReadinessInput,
) -> ReplacementForecastGoLiveReadinessReport:
    """Combine switch, refit, report, rollback, and runtime evidence."""

    if not isinstance(request, ReplacementForecastGoLiveReadinessInput):
        raise TypeError("request must be ReplacementForecastGoLiveReadinessInput")
    blockers: dict[str, list[str]] = {}
    live_switch_reasons = [
        reason
        for reason in request.live_switch_report.reason_codes
        if reason != "REPLACEMENT_SWITCH_TRADE_AUTHORITY_NOT_SIMPLE_SWITCH"
    ]
    switch_surface_ready = request.live_switch_report.simple_switch_ready or request.live_switch_report.live_authority_ready
    if live_switch_reasons and not switch_surface_ready:
        blockers["live_switch"] = live_switch_reasons
    if request.source_fact_status != "CURRENT_FOR_LIVE":
        blockers.setdefault("current_facts", []).append("REPLACEMENT_GO_LIVE_SOURCE_FACTS_STALE")
    if request.data_fact_status != "CURRENT_FOR_LIVE":
        blockers.setdefault("current_facts", []).append("REPLACEMENT_GO_LIVE_DATA_FACTS_STALE")
    if not request.rollback_plan.feature_flag_updates:
        blockers.setdefault("rollback", []).append("REPLACEMENT_GO_LIVE_ROLLBACK_PLAN_MISSING_FLAG_UPDATES")
    if "delete_shadow_rows" not in request.rollback_plan.prohibited_actions:
        blockers.setdefault("rollback", []).append("REPLACEMENT_GO_LIVE_ROLLBACK_MAY_DELETE_SHADOW_ROWS")
    if not request.refit_decision.product_specific_training_allowed:
        blockers["refit"] = list(request.refit_decision.reason_codes)
    if request.before_after_report.status != "REPORT_READY":
        blockers["before_after"] = list(request.before_after_report.reason_codes)
    if request.before_after_report.after_cost_delta is None or request.before_after_report.after_cost_delta <= 0.0:
        blockers.setdefault("before_after", []).append("REPLACEMENT_GO_LIVE_AFTER_COST_DELTA_NOT_POSITIVE")
    if request.before_after_report.bucket_regressions:
        blockers.setdefault("before_after", []).append("REPLACEMENT_GO_LIVE_BUCKET_REGRESSIONS_PRESENT")
    capital_replay_blockers = _capital_replay_promotion_blockers(request.capital_replay)
    if capital_replay_blockers:
        blockers.setdefault("capital_replay", []).extend(capital_replay_blockers)
    live_dry_run_blockers = _live_dry_run_blockers(request.live_dry_run)
    if live_dry_run_blockers:
        blockers.setdefault("live_dry_run", []).extend(live_dry_run_blockers)
    if request.runtime_policy.can_initiate_trade and not request.operator_approval_id:
        blockers.setdefault("operator_approval", []).append("REPLACEMENT_GO_LIVE_OPERATOR_APPROVAL_REQUIRED")
    if request.runtime_policy.status == "BLOCKED":
        blockers.setdefault("runtime_policy", []).extend(request.runtime_policy.reason_codes)
    if request.switch_decision.status in {"BLOCKED", "DISABLED"}:
        blockers.setdefault("switch_decision", []).extend(request.switch_decision.reason_codes)
    config_switch_status: str | None = None
    config_switch_reason_codes: tuple[str, ...] = ()
    config_switch_json_patch: tuple[Mapping[str, object], ...] = ()
    if request.config_switch_report is not None:
        config_switch_status = str(request.config_switch_report.get("status") or "INVALID_CONFIG")
        raw_reasons = request.config_switch_report.get("reason_codes", ())
        config_switch_reason_codes = tuple(str(item) for item in _sequence(raw_reasons, field_name="config_switch.reason_codes"))
        raw_patch = request.config_switch_report.get("json_patch", ())
        config_switch_json_patch = tuple(
            _mapping(item, field_name="config_switch.json_patch[]") for item in _sequence(raw_patch, field_name="config_switch.json_patch")
        )
        if config_switch_status != "READY":
            blockers.setdefault("config_switch", []).extend(config_switch_reason_codes or ("REPLACEMENT_GO_LIVE_CONFIG_SWITCH_INVALID",))
        if config_switch_json_patch:
            blockers.setdefault("config_switch", []).append("REPLACEMENT_GO_LIVE_CONFIG_PATCH_NOT_APPLIED")

    simple_switch_ready = not any(key in blockers for key in ("config_switch", "live_switch", "live_dry_run", "current_facts", "rollback", "switch_decision"))
    fine_tune_ready = simple_switch_ready and "refit" not in blockers
    live_ready = (
        fine_tune_ready
        and "before_after" not in blockers
        and "capital_replay" not in blockers
        and request.runtime_policy.can_initiate_trade
        and bool(request.operator_approval_id)
    )
    if live_ready:
        status = "LIVE_PROMOTION_READY"
        reasons = ("REPLACEMENT_GO_LIVE_PROMOTION_READY",)
    elif fine_tune_ready:
        if request.runtime_policy.can_initiate_trade:
            status = "BLOCKED"
            reasons = tuple(reason for values in blockers.values() for reason in values) or (
                "REPLACEMENT_GO_LIVE_BLOCKED",
            )
        else:
            status = "FINE_TUNE_READY"
            reasons = ("REPLACEMENT_GO_LIVE_FINE_TUNE_READY_PROMOTION_BLOCKED",)
    elif simple_switch_ready:
        status = "SIMPLE_SWITCH_READY"
        reasons = ("REPLACEMENT_GO_LIVE_SIMPLE_SWITCH_READY_REFIT_BLOCKED",)
    else:
        status = "BLOCKED"
        reasons = tuple(reason for values in blockers.values() for reason in values) or (
            "REPLACEMENT_GO_LIVE_BLOCKED",
        )
    return ReplacementForecastGoLiveReadinessReport(
        schema_version=REPORT_SCHEMA_VERSION,
        status=status,
        reason_codes=reasons,
        strategy_key=request.strategy_key,
        simple_switch_ready=simple_switch_ready,
        fine_tune_ready=fine_tune_ready,
        live_promotion_ready=live_ready,
        source_fact_status=request.source_fact_status,
        data_fact_status=request.data_fact_status,
        runtime_policy_status=request.runtime_policy.status,
        switch_decision_status=request.switch_decision.status,
        switch_decision_readiness_id=request.switch_decision.readiness_id,
        switch_can_read_shadow_posterior=request.switch_decision.can_read_shadow_posterior,
        switch_can_apply_reactor_hook=request.switch_decision.can_apply_reactor_hook,
        switch_can_apply_veto=request.switch_decision.can_apply_veto,
        switch_can_initiate_trade=request.switch_decision.can_initiate_trade,
        switch_can_increase_kelly=request.switch_decision.can_increase_kelly,
        switch_can_flip_direction=request.switch_decision.can_flip_direction,
        rollback_reversible="delete_shadow_rows" in request.rollback_plan.prohibited_actions,
        before_after_official_days=request.before_after_report.official_days,
        before_after_official_rows=request.before_after_report.official_rows,
        before_after_brier_delta=request.before_after_report.brier_delta,
        before_after_log_loss_delta=request.before_after_report.log_loss_delta,
        before_after_after_cost_delta=request.before_after_report.after_cost_delta,
        config_switch_status=config_switch_status,
        config_switch_reason_codes=config_switch_reason_codes,
        config_switch_json_patch=config_switch_json_patch,
        capital_replay=request.capital_replay,
        live_dry_run=request.live_dry_run,
        blockers=blockers,
    )


def _live_dry_run_blockers(live_dry_run: Mapping[str, object] | None) -> tuple[str, ...]:
    if live_dry_run is None:
        return ()
    reasons: list[str] = []
    if str(live_dry_run.get("status") or "") != "DRY_RUN_READY":
        reasons.append("REPLACEMENT_GO_LIVE_DRY_RUN_NOT_READY")
    if str(live_dry_run.get("raw_artifact_lineage_status") or "") != "READY":
        reasons.append("REPLACEMENT_GO_LIVE_RAW_ARTIFACT_LINEAGE_NOT_READY")
    latest_status = str(live_dry_run.get("latest_readiness_artifact_status") or "")
    if latest_status not in {"READY", "NOT_APPLICABLE_NO_POSTERIOR"}:
        reasons.append("REPLACEMENT_GO_LIVE_LATEST_READINESS_ARTIFACTS_NOT_READY")
    configured_refit_status = str(live_dry_run.get("configured_refit_handoff_status") or "")
    if configured_refit_status != "READY":
        reasons.append("REPLACEMENT_GO_LIVE_CONFIGURED_REFIT_HANDOFF_NOT_READY")
    return tuple(dict.fromkeys(reasons))


def _capital_replay_promotion_blockers(capital_replay: Mapping[str, object] | None) -> tuple[str, ...]:
    if capital_replay is None:
        return ()
    reasons: list[str] = []
    coverage = capital_replay.get("coverage")
    coverage_map = coverage if isinstance(coverage, Mapping) else {}
    if coverage_map.get("promotion_grade") is False:
        reasons.append("REPLACEMENT_GO_LIVE_CAPITAL_REPLAY_NOT_PROMOTION_GRADE")
    source_availability_mode = str(coverage_map.get("source_availability_mode") or "")
    source_availability_observed = coverage_map.get("source_availability_observed")
    if source_availability_mode and source_availability_mode != "observed":
        reasons.append("REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_ASSUMED")
    if source_availability_observed is False:
        reasons.append("REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_ASSUMED")
    violations = coverage_map.get("source_availability_violations")
    if violations not in (None, "") and int(violations) > 0:
        reasons.append("REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_VIOLATIONS")
    evidence_grade = str(coverage_map.get("evidence_grade") or "")
    promotion_blocker = str(coverage_map.get("promotion_blocker") or "")
    if "assumed_source_time" in evidence_grade or "source_available_at is assumed" in promotion_blocker:
        reasons.append("REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_ASSUMED")
    return tuple(dict.fromkeys(reasons))


def _mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _sequence(value: object, *, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a JSON array")
    return value


def _tuple_from_payload(payload: Mapping[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = payload.get(key, default)
    return tuple(str(item) for item in _sequence(value, field_name=key))


def _bool_flags(raw: Mapping[str, object]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for key in REQUIRED_RUNTIME_FLAGS:
        if key not in raw:
            raise ValueError(f"runtime_flags missing {key}")
        value = raw[key]
        if not isinstance(value, bool):
            raise ValueError(f"runtime_flags.{key} must be bool")
        flags[key] = value
    return flags


def _promotion_evidence_from_payload(raw: Mapping[str, object] | None) -> ReplacementForecastPromotionEvidence | None:
    if raw is None:
        return None
    return ReplacementForecastPromotionEvidence(
        official_days=int(raw.get("official_days", 0)),
        official_rows=int(raw.get("official_rows", 0)),
        after_cost_pnl=float(raw.get("after_cost_pnl", 0.0)),
        q_lcb_coverage=float(raw.get("q_lcb_coverage", 0.0)),
        anti_lookahead_violations=int(raw.get("anti_lookahead_violations", 0)),
        source_availability_violations=int(raw.get("source_availability_violations", 0)),
        unresolved_regression_clusters=int(raw.get("unresolved_regression_clusters", 0)),
        same_clob_replay_passed=bool(raw.get("same_clob_replay_passed", False)),
        nested_walk_forward_passed=bool(raw.get("nested_walk_forward_passed", False)),
        same_clob_replay_scored_rows=int(raw.get("same_clob_replay_scored_rows", 0)),
        same_clob_replay_blocked_rows=int(raw.get("same_clob_replay_blocked_rows", 0)),
        fee_depth_fill_evidence_passed=bool(raw.get("fee_depth_fill_evidence_passed", False)),
        unit_pnl_only=bool(raw.get("unit_pnl_only", True)),
        nested_holdout_brier=None if raw.get("nested_holdout_brier") is None else float(raw.get("nested_holdout_brier")),
        nested_holdout_log_loss=None if raw.get("nested_holdout_log_loss") is None else float(raw.get("nested_holdout_log_loss")),
        nested_selected_anchor_weight=None if raw.get("nested_selected_anchor_weight") is None else float(raw.get("nested_selected_anchor_weight")),
        nested_selected_anchor_sigma_c=None if raw.get("nested_selected_anchor_sigma_c") is None else float(raw.get("nested_selected_anchor_sigma_c")),
        nested_guardrail_bucket_count=int(raw.get("nested_guardrail_bucket_count", 0)),
        nested_guardrail_bucket_min_rows=int(raw.get("nested_guardrail_bucket_min_rows", 0)),
        product_specific_refit_passed=bool(raw.get("product_specific_refit_passed", False)),
    )


def _guardrail_report_from_payload(raw: Mapping[str, object]) -> object:
    rows = tuple(
        ReplacementForecastGuardrailReplayRow(
            city=str(_mapping(item, field_name="guardrail_rows[]").get("city") or ""),
            temperature_metric=str(_mapping(item, field_name="guardrail_rows[]").get("temperature_metric") or ""),
            guardrail_bucket=str(_mapping(item, field_name="guardrail_rows[]").get("guardrail_bucket") or ""),
            replay_status=str(_mapping(item, field_name="guardrail_rows[]").get("replay_status") or ""),
            replacement_delta_after_cost_pnl=float(_mapping(item, field_name="guardrail_rows[]").get("replacement_delta_after_cost_pnl", 0.0)),
            veto_applied=bool(_mapping(item, field_name="guardrail_rows[]").get("veto_applied", False)),
            baseline_after_cost_pnl=float(_mapping(item, field_name="guardrail_rows[]").get("baseline_after_cost_pnl", 0.0)),
            replacement_after_cost_pnl=float(_mapping(item, field_name="guardrail_rows[]").get("replacement_after_cost_pnl", 0.0)),
            reason_codes=tuple(
                str(reason)
                for reason in _sequence(
                    _mapping(item, field_name="guardrail_rows[]").get("reason_codes", ()),
                    field_name="guardrail_rows[].reason_codes",
                )
            ),
        )
        for item in _sequence(raw.get("guardrail_rows", ()), field_name="derived_promotion_evidence_inputs.guardrail_rows")
    )
    return build_replacement_forecast_guardrail_report(
        rows,
        axes=("guardrail_bucket",),
        min_scored_rows_per_bucket=int(raw.get("min_guardrail_scored_rows_per_bucket", 20)),
    )


def _q_lcb_report_from_payload(raw: Mapping[str, object]):
    rows = tuple(
        ReplacementForecastQLcbCoverageRow(
            official_date=str(_mapping(item, field_name="q_lcb_coverage_rows[]").get("official_date") or ""),
            city=str(_mapping(item, field_name="q_lcb_coverage_rows[]").get("city") or ""),
            temperature_metric=str(_mapping(item, field_name="q_lcb_coverage_rows[]").get("temperature_metric") or ""),
            guardrail_bucket=str(_mapping(item, field_name="q_lcb_coverage_rows[]").get("guardrail_bucket") or ""),
            truth_authority=str(_mapping(item, field_name="q_lcb_coverage_rows[]").get("truth_authority") or ""),
            scored=bool(_mapping(item, field_name="q_lcb_coverage_rows[]").get("scored", False)),
            covered_by_q_lcb=bool(_mapping(item, field_name="q_lcb_coverage_rows[]").get("covered_by_q_lcb", False)),
        )
        for item in _sequence(raw.get("q_lcb_coverage_rows", ()), field_name="derived_promotion_evidence_inputs.q_lcb_coverage_rows")
    )
    return build_replacement_forecast_q_lcb_coverage_report(
        rows,
        min_official_rows=int(raw.get("min_q_lcb_official_rows", 250)),
        min_coverage=float(raw.get("min_q_lcb_coverage", 0.95)),
    )


def _derived_promotion_evidence_from_payload(
    raw: Mapping[str, object] | None,
    *,
    before_after_report: ReplacementForecastBeforeAfterReport,
    refit_decision: ReplacementForecastRefitDecision,
) -> ReplacementForecastPromotionEvidence | None:
    if raw is None:
        return None
    fine_tune_artifact = _mapping(raw.get("fine_tune_artifact"), field_name="derived_promotion_evidence_inputs.fine_tune_artifact")
    fine_tune_result_payload = _mapping(fine_tune_artifact.get("result"), field_name="derived_promotion_evidence_inputs.fine_tune_artifact.result")
    build_report = build_replacement_forecast_promotion_evidence(
        before_after_report=before_after_report,
        guardrail_report=_guardrail_report_from_payload(raw),
        q_lcb_coverage_report=_q_lcb_report_from_payload(raw),
        fine_tune_result=fine_tune_result_from_jsonable(fine_tune_result_payload),
        refit_decision=refit_decision,
    )
    return build_report.promotion_evidence


def _refit_decision_from_payload(data: Mapping[str, object]) -> ReplacementForecastRefitDecision:
    refit_evidence_payload = data.get("refit_evidence")
    refit_handoff_payload = data.get("refit_handoff")
    if refit_handoff_payload is None:
        refit_handoff_payload = data.get("derived_refit_handoff")
    evidence_decision = (
        None
        if refit_evidence_payload is None
        else _refit_decision_from_evidence_payload(
            _mapping(refit_evidence_payload, field_name="refit_evidence")
        )
    )
    handoff_decision = (
        None
        if refit_handoff_payload is None
        else refit_decision_from_handoff_payload(
            _mapping(refit_handoff_payload, field_name="refit_handoff")
        )
    )
    if evidence_decision is None and handoff_decision is None:
        raise ValueError("refit_evidence or refit_handoff is required")
    if evidence_decision is not None and handoff_decision is not None and evidence_decision.as_dict() != handoff_decision.as_dict():
        raise ValueError("refit_evidence conflicts with refit_handoff")
    decision = handoff_decision or evidence_decision
    if decision is None:
        raise ValueError("refit_evidence or refit_handoff is required")
    return decision


def _refit_decision_from_evidence_payload(refit_payload: Mapping[str, object]) -> ReplacementForecastRefitDecision:
    return evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=int(refit_payload.get("official_days", 0)),
            official_rows=int(refit_payload.get("official_rows", 0)),
            temperature_metric=str(refit_payload.get("temperature_metric") or ""),
            source_family=str(refit_payload.get("source_family") or ""),
            product_id=str(refit_payload.get("product_id") or ""),
            calibration_method=str(refit_payload.get("calibration_method") or ""),
            enabled_evidence=_tuple_from_payload(refit_payload, "enabled_evidence", tuple(REQUIRED_REFIT_EVIDENCE)),
            min_guardrail_bucket_rows=int(refit_payload.get("min_guardrail_bucket_rows", 0)),
            high_low_mixed=bool(refit_payload.get("high_low_mixed", False)),
            baseline_calibration_reused=bool(refit_payload.get("baseline_calibration_reused", False)),
            emos_key_includes_product=bool(refit_payload.get("emos_key_includes_product", False)),
            emos_key_schema=str(refit_payload.get("emos_key_schema") or "missing"),
            emos_identity_evidence_status=str(refit_payload.get("emos_identity_evidence_status") or "MISSING"),
            data_refit_requested=bool(refit_payload.get("data_refit_requested", False)),
            live_promotion_requested=bool(refit_payload.get("live_promotion_requested", False)),
        )
    )


def _before_after_report_from_payload(data: Mapping[str, object]) -> ReplacementForecastBeforeAfterReport:
    before_after_rows = tuple(
        ReplacementForecastBeforeAfterRow(**_mapping(row, field_name="before_after_rows[]"))
        for row in _sequence(data.get("before_after_rows", ()), field_name="before_after_rows")
    )
    return build_replacement_forecast_before_after_report(
        before_after_rows,
        min_official_days=int(data.get("min_before_after_official_days", 5)),
        min_official_rows=int(data.get("min_before_after_official_rows", 250)),
    )


def _promotion_evidence_for_payload_data(
    data: Mapping[str, object],
    *,
    before_after_report: ReplacementForecastBeforeAfterReport,
    refit_decision: ReplacementForecastRefitDecision,
) -> ReplacementForecastPromotionEvidence | None:
    explicit_promotion_evidence = _promotion_evidence_from_payload(
        None
        if data.get("promotion_evidence") is None
        else _mapping(data.get("promotion_evidence"), field_name="promotion_evidence")
    )
    derived_promotion_evidence = _derived_promotion_evidence_from_payload(
        None
        if data.get("derived_promotion_evidence_inputs") is None
        else _mapping(data.get("derived_promotion_evidence_inputs"), field_name="derived_promotion_evidence_inputs"),
        before_after_report=before_after_report,
        refit_decision=refit_decision,
    )
    evidence = explicit_promotion_evidence or derived_promotion_evidence
    if evidence is None:
        return None
    if evidence.product_specific_refit_passed == refit_decision.product_specific_training_allowed:
        return evidence
    return ReplacementForecastPromotionEvidence(
        official_days=evidence.official_days,
        official_rows=evidence.official_rows,
        after_cost_pnl=evidence.after_cost_pnl,
        q_lcb_coverage=evidence.q_lcb_coverage,
        anti_lookahead_violations=evidence.anti_lookahead_violations,
        source_availability_violations=evidence.source_availability_violations,
        unresolved_regression_clusters=evidence.unresolved_regression_clusters,
        same_clob_replay_passed=evidence.same_clob_replay_passed,
        nested_walk_forward_passed=evidence.nested_walk_forward_passed,
        same_clob_replay_scored_rows=evidence.same_clob_replay_scored_rows,
        same_clob_replay_blocked_rows=evidence.same_clob_replay_blocked_rows,
        fee_depth_fill_evidence_passed=evidence.fee_depth_fill_evidence_passed,
        unit_pnl_only=evidence.unit_pnl_only,
        nested_holdout_brier=evidence.nested_holdout_brier,
        nested_holdout_log_loss=evidence.nested_holdout_log_loss,
        nested_selected_anchor_weight=evidence.nested_selected_anchor_weight,
        nested_selected_anchor_sigma_c=evidence.nested_selected_anchor_sigma_c,
        nested_guardrail_bucket_count=evidence.nested_guardrail_bucket_count,
        nested_guardrail_bucket_min_rows=evidence.nested_guardrail_bucket_min_rows,
        product_specific_refit_passed=refit_decision.product_specific_training_allowed,
    )


def replacement_forecast_promotion_evidence_from_payload(
    payload: Mapping[str, object],
) -> ReplacementForecastPromotionEvidence | None:
    """Return the explicit or derived promotion evidence used by go-live gates."""

    data = _mapping(payload, field_name="payload")
    refit_decision = _refit_decision_from_payload(data)
    before_after_report = _before_after_report_from_payload(data)
    return _promotion_evidence_for_payload_data(
        data,
        before_after_report=before_after_report,
        refit_decision=refit_decision,
    )


def replacement_forecast_capital_objective_evidence_from_payload(
    payload: Mapping[str, object],
) -> ReplacementForecastCapitalObjectiveEvidence | None:
    """Return capital-objective evidence from a go-live payload."""

    data = _mapping(payload, field_name="payload")
    capital_replay = data.get("capital_replay")
    if not isinstance(capital_replay, Mapping):
        return None
    coverage = capital_replay.get("coverage")
    coverage_map = coverage if isinstance(coverage, Mapping) else {}
    promotion = replacement_forecast_promotion_evidence_from_payload(data)
    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label=str(capital_replay.get("selected_label") or ""),
        replay_status=str(capital_replay.get("status") or ""),
        after_cost_pnl=0.0 if promotion is None else float(promotion.after_cost_pnl),
        source_availability_observed=coverage_map.get("source_availability_observed") is True,
        source_availability_violations=int(coverage_map.get("source_availability_violations") or 0),
        anti_lookahead_violations=0 if promotion is None else int(promotion.anti_lookahead_violations),
        same_clob_replay_passed=False if promotion is None else bool(promotion.same_clob_replay_passed),
        fee_depth_fill_evidence_passed=False if promotion is None else bool(promotion.fee_depth_fill_evidence_passed),
        unit_pnl_only=True if promotion is None else bool(promotion.unit_pnl_only),
        product_specific_refit_passed=False if promotion is None else bool(promotion.product_specific_refit_passed),
    )


def _readiness_from_payload(raw: Mapping[str, object]) -> ReplacementForecastReadinessDecision:
    dependencies_payload = _sequence(raw.get("dependencies", ()), field_name="readiness.dependencies")
    dependencies: list[ReplacementForecastDependency] = []
    for item in dependencies_payload:
        dependency = _mapping(item, field_name="readiness.dependencies[]")
        source_run_id = dependency.get("source_run_id")
        artifact_id = dependency.get("artifact_id")
        anchor_id = dependency.get("anchor_id")
        posterior_id = dependency.get("posterior_id")
        dependencies.append(
            ReplacementForecastDependency(
                role=str(dependency.get("role") or ""),
                source_id=str(dependency.get("source_id") or ""),
                product_id=str(dependency.get("product_id") or ""),
                data_version=str(dependency.get("data_version") or ""),
                source_run_id=None if source_run_id in (None, "") else str(source_run_id),
                source_available_at=str(dependency.get("source_available_at") or ""),
                status=str(dependency.get("status") or "SHADOW_ONLY"),
                artifact_id=None if artifact_id in (None, "") else int(artifact_id),
                anchor_id=None if anchor_id in (None, "") else int(anchor_id),
                posterior_id=None if posterior_id in (None, "") else int(posterior_id),
            )
        )
    return build_replacement_forecast_readiness(
        city=str(raw.get("city") or ""),
        target_date=str(raw.get("target_date") or ""),
        temperature_metric=str(raw.get("temperature_metric") or ""),
        decision_time=str(raw.get("decision_time") or ""),
        computed_at=str(raw.get("computed_at") or ""),
        expires_at=None if raw.get("expires_at") in (None, "") else str(raw.get("expires_at")),
        dependencies=tuple(dependencies),
    )


def build_replacement_forecast_go_live_readiness_from_payload(
    payload: Mapping[str, object],
) -> ReplacementForecastGoLiveReadinessReport:
    """Build the final readiness report from an explicit JSON payload."""

    data = _mapping(payload, field_name="payload")
    strategy_key = str(data.get("strategy_key") or "")
    if not strategy_key:
        raise ValueError("strategy_key is required")
    runtime_flags = _bool_flags(_mapping(data.get("runtime_flags"), field_name="runtime_flags"))

    refit_decision = _refit_decision_from_payload(data)
    before_after_report = _before_after_report_from_payload(data)
    promotion_evidence = _promotion_evidence_for_payload_data(
        data,
        before_after_report=before_after_report,
        refit_decision=refit_decision,
    )
    capital_objective_evidence = replacement_forecast_capital_objective_evidence_from_payload(data)
    runtime_policy = resolve_replacement_forecast_runtime_policy(
        runtime_flags,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )

    live_switch_payload = _mapping(data.get("live_switch", {}), field_name="live_switch")
    source_fact_status = str(data.get("source_fact_status") or live_switch_payload.get("source_fact_status") or "STALE_FOR_LIVE")
    data_fact_status = str(data.get("data_fact_status") or live_switch_payload.get("data_fact_status") or "STALE_FOR_LIVE")
    live_switch_report = build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=runtime_policy,
            available_files=_tuple_from_payload(live_switch_payload, "available_files", tuple(REQUIRED_LIVE_READ_FILES)),
            forecast_tables=_tuple_from_payload(live_switch_payload, "forecast_tables", tuple(REQUIRED_FORECAST_TABLES)),
            world_tables=_tuple_from_payload(live_switch_payload, "world_tables", tuple(REQUIRED_WORLD_TABLES)),
            trade_tables=_tuple_from_payload(live_switch_payload, "trade_tables", tuple(REQUIRED_TRADE_TABLES)),
            enabled_evidence_gates=_tuple_from_payload(live_switch_payload, "enabled_evidence_gates", tuple(REQUIRED_EVIDENCE_GATES)),
            proposed_write_tables=_tuple_from_payload(live_switch_payload, "proposed_write_tables", ()),
            source_fact_status=source_fact_status,
            data_fact_status=data_fact_status,
        )
    )

    if data.get("live_readiness_decision") not in (None, ""):
        readiness = _readiness_decision_from_jsonable(
            _mapping(data.get("live_readiness_decision"), field_name="live_readiness_decision")
        )
    else:
        readiness = _readiness_from_payload(_mapping(data.get("readiness"), field_name="readiness"))
    switch_decision = evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=runtime_policy,
            live_switch_report=live_switch_report,
            readiness=readiness,
            refit_decision=refit_decision,
            capital_objective_evidence=capital_objective_evidence,
        )
    )

    rollback_payload = _mapping(data.get("rollback", {}), field_name="rollback")
    rollback_plan = build_replacement_forecast_rollback_plan(
        current_policy=runtime_policy,
        reason=str(rollback_payload.get("reason") or "replacement readiness report rollback path"),
        generated_at=str(rollback_payload.get("generated_at") or "2026-06-06T00:00:00+00:00"),
        additional_source_ids_to_pause=tuple(str(item) for item in _sequence(rollback_payload.get("additional_source_ids_to_pause", ()), field_name="additional_source_ids_to_pause")),
    )
    approval = data.get("operator_approval_id")
    capital_replay_payload = data.get("capital_replay")
    live_dry_run_payload = data.get("live_dry_run")
    return build_replacement_forecast_go_live_readiness_report(
        ReplacementForecastGoLiveReadinessInput(
            strategy_key=strategy_key,
            runtime_policy=runtime_policy,
            live_switch_report=live_switch_report,
            switch_decision=switch_decision,
            refit_decision=refit_decision,
            before_after_report=before_after_report,
            rollback_plan=rollback_plan,
            source_fact_status=source_fact_status,
            data_fact_status=data_fact_status,
            config_switch_report=None if data.get("config_switch") is None else _mapping(data.get("config_switch"), field_name="config_switch"),
            operator_approval_id=None if approval in (None, "") else str(approval),
            capital_replay=None if capital_replay_payload in (None, "") else _mapping(capital_replay_payload, field_name="capital_replay"),
            live_dry_run=None if live_dry_run_payload in (None, "") else _mapping(live_dry_run_payload, field_name="live_dry_run"),
        )
    )


def replacement_forecast_before_after_rows_from_csv(path: Path) -> tuple[ReplacementForecastBeforeAfterRow, ...]:
    """Load explicit before/after report rows from a CSV file."""

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = tuple(reader.fieldnames or ())
        missing = tuple(column for column in BEFORE_AFTER_CSV_COLUMNS if column not in fieldnames)
        if missing:
            raise ValueError(f"before-after CSV missing required columns: {', '.join(missing)}")
        rows: list[ReplacementForecastBeforeAfterRow] = []
        for index, row in enumerate(reader, start=2):
            try:
                rows.append(
                    ReplacementForecastBeforeAfterRow(
                        official_date=str(row["official_date"]),
                        city=str(row["city"]),
                        temperature_metric=str(row["temperature_metric"]),
                        guardrail_bucket=str(row["guardrail_bucket"]),
                        baseline_brier=float(row["baseline_brier"]),
                        replacement_brier=float(row["replacement_brier"]),
                        baseline_log_loss=float(row["baseline_log_loss"]),
                        replacement_log_loss=float(row["replacement_log_loss"]),
                        baseline_after_cost_pnl=float(row["baseline_after_cost_pnl"]),
                        replacement_after_cost_pnl=float(row["replacement_after_cost_pnl"]),
                        truth_authority=str(row["truth_authority"]),
                        replay_status=str(row["replay_status"]),
                    )
                )
            except Exception as exc:
                raise ValueError(f"invalid before-after CSV row {index}: {exc}") from exc
    if not rows:
        raise ValueError("before-after CSV must contain at least one row")
    return tuple(rows)


def _latest_live_replacement_readiness(root: Path) -> ReplacementForecastReadinessDecision | None:
    db_path = root / "state" / "zeus-forecasts.db"
    if not db_path.exists():
        return None
    conn = _connect(db_path, write_class="live")
    try:
        row = conn.execute(
            """
            SELECT readiness_id, status, reason_codes_json, dependency_json,
                   provenance_json, expires_at, source_id, data_version, strategy_key
            FROM readiness_state
            WHERE strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
              AND source_id = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
              AND scope_type = 'strategy'
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    reason_codes = tuple(str(item) for item in _sequence(json.loads(str(row["reason_codes_json"] or "[]")), field_name="readiness_state.reason_codes_json"))
    dependency_json = _mapping(json.loads(str(row["dependency_json"] or "{}")), field_name="readiness_state.dependency_json")
    provenance_json = _mapping(json.loads(str(row["provenance_json"] or "{}")), field_name="readiness_state.provenance_json")
    return ReplacementForecastReadinessDecision(
        readiness_id=str(row["readiness_id"]),
        status=str(row["status"]),
        reason_codes=reason_codes,
        dependency_json=dependency_json,
        provenance_json=provenance_json,
        expires_at=None if row["expires_at"] in (None, "") else str(row["expires_at"]),
        source_id=str(row["source_id"]),
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        strategy_key=str(row["strategy_key"]),
    )


def _latest_live_refit_handoff_payload(root: Path) -> Mapping[str, object] | None:
    handoff_path = root / "state" / "replacement_forecast_shadow" / "refit_handoff.json"
    if not handoff_path.exists():
        return None
    try:
        payload = _mapping(json.loads(handoff_path.read_text(encoding="utf-8")), field_name="refit_handoff")
        refit_decision_from_handoff_payload(payload)
    except Exception:
        return None
    return payload


def _payload_readiness_from_decision(readiness: ReplacementForecastReadinessDecision) -> dict[str, object]:
    provenance = dict(readiness.provenance_json)
    return {
        "city": provenance.get("city") or "live_replacement_readiness",
        "target_date": provenance.get("target_date") or "1970-01-01",
        "temperature_metric": provenance.get("temperature_metric") or "high",
        "decision_time": provenance.get("decision_time") or provenance.get("computed_at"),
        "computed_at": provenance.get("computed_at") or provenance.get("decision_time"),
        "expires_at": None if readiness.expires_at is None else readiness.expires_at.isoformat(),
        "dependencies": list(_sequence(readiness.dependency_json.get("dependencies", ()), field_name="readiness.dependencies")),
        "live_readiness_id": readiness.readiness_id,
        "live_readiness_status": readiness.status,
        "live_readiness_reason_codes": list(readiness.reason_codes),
    }


def _readiness_decision_from_jsonable(raw: Mapping[str, object]) -> ReplacementForecastReadinessDecision:
    return ReplacementForecastReadinessDecision(
        readiness_id=str(raw.get("readiness_id") or ""),
        status=str(raw.get("status") or ""),
        reason_codes=tuple(str(item) for item in _sequence(raw.get("reason_codes", ()), field_name="live_readiness_decision.reason_codes")),
        dependency_json=_mapping(raw.get("dependency_json", {}), field_name="live_readiness_decision.dependency_json"),
        provenance_json=_mapping(raw.get("provenance_json", {}), field_name="live_readiness_decision.provenance_json"),
        expires_at=None if raw.get("expires_at") in (None, "") else str(raw.get("expires_at")),
        source_id=str(raw.get("source_id") or "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"),
        product_id=str(raw.get("product_id") or "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"),
        strategy_key=str(raw.get("strategy_key") or "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"),
    )


def _readiness_decision_to_jsonable(readiness: ReplacementForecastReadinessDecision) -> dict[str, object]:
    return {
        "readiness_id": readiness.readiness_id,
        "status": readiness.status,
        "reason_codes": list(readiness.reason_codes),
        "dependency_json": dict(readiness.dependency_json),
        "provenance_json": dict(readiness.provenance_json),
        "expires_at": None if readiness.expires_at is None else readiness.expires_at.isoformat(),
        "source_id": readiness.source_id,
        "product_id": readiness.product_id,
        "strategy_key": readiness.strategy_key,
    }


def replacement_forecast_payload_with_current_live_switch_inventory(
    payload: Mapping[str, object],
    root: Path,
) -> dict[str, object]:
    """Return payload with live-switch fields replaced by real current-state inventory."""

    data = dict(_mapping(payload, field_name="payload"))
    runtime_flags = _bool_flags(_mapping(data.get("runtime_flags"), field_name="runtime_flags"))
    settings_payload: Mapping[str, object] | None = None
    settings_path = root / "config" / "settings.json"
    try:
        loaded_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings_payload = _mapping(loaded_settings, field_name="config/settings.json")
        settings_flags = _bool_flags(
            _mapping(settings_payload.get("feature_flags", {}), field_name="config/settings.json.feature_flags")
        )
        if bool(settings_flags.get(TRADE_AUTHORITY_FLAG, False)) and not bool(runtime_flags.get(TRADE_AUTHORITY_FLAG, False)):
            shadow_config = _mapping(
                settings_payload.get("replacement_forecast_shadow", {}),
                field_name="config/settings.json.replacement_forecast_shadow",
            )
            evidence_path = Path(str(shadow_config.get("promotion_evidence_path") or "state/replacement_forecast_shadow/promotion_evidence.json"))
            if not evidence_path.is_absolute():
                evidence_path = root / evidence_path
            if evidence_path.exists():
                evidence_payload = _mapping(
                    json.loads(evidence_path.read_text(encoding="utf-8")),
                    field_name="promotion_evidence_path",
                )
                data = dict(evidence_payload)
            runtime_flags = settings_flags
            data["runtime_flags"] = dict(runtime_flags)
    except Exception:
        settings_payload = None
    refit_decision = _refit_decision_from_payload(data)
    before_after_report = _before_after_report_from_payload(data)
    promotion_evidence = _promotion_evidence_for_payload_data(
        data,
        before_after_report=before_after_report,
        refit_decision=refit_decision,
    )
    capital_objective_evidence = replacement_forecast_capital_objective_evidence_from_payload(data)
    runtime_policy = resolve_replacement_forecast_runtime_policy(
        runtime_flags,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )
    live_switch_payload = _mapping(data.get("live_switch", {}), field_name="live_switch")
    enabled_evidence_gates = _tuple_from_payload(
        live_switch_payload,
        "enabled_evidence_gates",
        (),
    )
    proposed_write_tables = _tuple_from_payload(
        live_switch_payload,
        "proposed_write_tables",
        (),
    )
    current = build_replacement_forecast_live_switch_input_from_current_state(
        root,
        runtime_policy=runtime_policy,
        enabled_evidence_gates=enabled_evidence_gates,
        proposed_write_tables=proposed_write_tables,
    )
    data["source_fact_status"] = current.source_fact_status
    data["data_fact_status"] = current.data_fact_status
    data["live_switch"] = {
        "available_files": list(current.available_files),
        "forecast_tables": list(current.forecast_tables),
        "world_tables": list(current.world_tables),
        "trade_tables": list(current.trade_tables),
        "enabled_evidence_gates": list(current.enabled_evidence_gates),
        "proposed_write_tables": list(current.proposed_write_tables),
        "source_fact_status": current.source_fact_status,
        "data_fact_status": current.data_fact_status,
    }
    live_dry_run = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=root,
            runtime_flags={
                **runtime_flags,
                SHADOW_FLAG: True,
                VETO_FLAG: True,
                TRADE_AUTHORITY_FLAG: False,
                KELLY_INCREASE_FLAG: False,
                DIRECTION_FLIP_FLAG: False,
            },
            enabled_evidence_gates=enabled_evidence_gates,
            optional_dependencies=("requests",),
        )
    )
    data["live_dry_run"] = live_dry_run.as_dict()
    live_refit_handoff = _latest_live_refit_handoff_payload(root)
    if live_refit_handoff is not None and not bool(runtime_flags.get(TRADE_AUTHORITY_FLAG, False)):
        data.pop("refit_evidence", None)
        data.pop("derived_refit_handoff", None)
        data["refit_handoff"] = dict(live_refit_handoff)
    live_readiness = _latest_live_replacement_readiness(root)
    if live_readiness is not None:
        data["readiness"] = _payload_readiness_from_decision(live_readiness)
        data["live_readiness_decision"] = _readiness_decision_to_jsonable(live_readiness)
    try:
        if settings_payload is None:
            loaded_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings_payload = _mapping(loaded_settings, field_name="config/settings.json")
        settings_map = _mapping(settings_payload, field_name="config/settings.json")
        settings_flags = _mapping(settings_map.get("feature_flags", {}), field_name="config/settings.json.feature_flags")
        current_trade_authority_enabled = bool(settings_flags.get(TRADE_AUTHORITY_FLAG, False))
        if current_trade_authority_enabled and promotion_evidence is not None:
            config_plan = build_replacement_forecast_live_authority_config_switch_plan(
                settings_map,
                promotion_evidence=promotion_evidence,
                capital_objective_evidence=capital_objective_evidence,
            )
        else:
            config_plan = build_replacement_forecast_config_switch_plan(settings_map)
        data["config_switch"] = config_plan.as_dict()
    except Exception as exc:  # noqa: BLE001 - operator-facing readiness payload
        data["config_switch"] = {
            "status": "INVALID_CONFIG",
            "reason_codes": ["REPLACEMENT_GO_LIVE_CONFIG_SWITCH_UNREADABLE"],
            "json_patch": [],
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
    return data


def replacement_forecast_go_live_report_to_jsonable(
    report: ReplacementForecastGoLiveReadinessReport,
) -> dict[str, object]:
    """Return a stable JSON payload for final readiness artifacts."""

    if not isinstance(report, ReplacementForecastGoLiveReadinessReport):
        raise TypeError("report must be ReplacementForecastGoLiveReadinessReport")
    return report.as_dict()


def _fmt(value: float | None, *, digits: int = 6) -> str:
    if value is None:
        return "None"
    return f"{value:.{digits}f}"


def render_replacement_forecast_go_live_markdown(
    report: ReplacementForecastGoLiveReadinessReport,
) -> str:
    """Render the final operator-facing readiness report."""

    if not isinstance(report, ReplacementForecastGoLiveReadinessReport):
        raise TypeError("report must be ReplacementForecastGoLiveReadinessReport")
    lines = [
        "# Replacement Forecast Go-Live Readiness",
        "",
        f"Schema version: {report.schema_version}",
        f"Status: {report.status}",
        f"Strategy key: {report.strategy_key}",
        f"Reason codes: {', '.join(report.reason_codes)}",
        "",
        "## Gate Summary",
        "",
        "| Gate | Ready |",
        "|---|---:|",
        f"| Simple switch | {str(report.simple_switch_ready)} |",
        f"| Fine tune / product-specific refit | {str(report.fine_tune_ready)} |",
        f"| Live promotion | {str(report.live_promotion_ready)} |",
        f"| Rollback reversible | {str(report.rollback_reversible)} |",
        "",
        "## Current Fact Surfaces",
        "",
        f"- Source facts: {report.source_fact_status}",
        f"- Data facts: {report.data_fact_status}",
        f"- Runtime policy: {report.runtime_policy_status}",
        f"- Switch decision: {report.switch_decision_status}",
        f"- Switch readiness id: {report.switch_decision_readiness_id}",
        f"- Switch can read shadow posterior: {str(report.switch_can_read_shadow_posterior)}",
        f"- Switch can apply reactor hook: {str(report.switch_can_apply_reactor_hook)}",
        f"- Switch can apply veto: {str(report.switch_can_apply_veto)}",
        f"- Switch can initiate trade: {str(report.switch_can_initiate_trade)}",
        f"- Switch can increase Kelly: {str(report.switch_can_increase_kelly)}",
        f"- Switch can flip direction: {str(report.switch_can_flip_direction)}",
        f"- Config switch status: {report.config_switch_status}",
        "",
    ]
    if report.live_dry_run is not None:
        raw_counts = report.live_dry_run.get("raw_artifact_lineage_counts")
        raw_counts_map = raw_counts if isinstance(raw_counts, Mapping) else {}
        readiness_counts = report.live_dry_run.get("latest_readiness_artifact_counts")
        readiness_counts_map = readiness_counts if isinstance(readiness_counts, Mapping) else {}
        latest_posterior = report.live_dry_run.get("latest_materialized_posterior")
        latest_posterior_map = latest_posterior if isinstance(latest_posterior, Mapping) else {}
        lines.extend(
            [
                "## Live Dry Run Lineage",
                "",
                f"- Dry-run status: {report.live_dry_run.get('status')}",
                f"- Raw artifact lineage: {report.live_dry_run.get('raw_artifact_lineage_status')}",
                f"- Raw Open-Meteo ECMWF IFS 9km artifacts: {raw_counts_map.get('openmeteo_ecmwf_ifs_9km')}",
                f"- Raw AIFS ENS artifacts: {raw_counts_map.get('ecmwf_aifs_ens')}",
                f"- Latest readiness artifacts: {report.live_dry_run.get('latest_readiness_artifact_status')}",
                f"- Latest readiness AIFS artifact links: {readiness_counts_map.get('aifs_sampled_2t')}",
                f"- Latest readiness Open-Meteo artifact links: {readiness_counts_map.get('openmeteo_ifs9_anchor')}",
                f"- Latest posterior: {latest_posterior_map.get('city')} {latest_posterior_map.get('target_date')} {latest_posterior_map.get('temperature_metric')}",
                "",
            ]
        )
    lines.extend([
        "## Before / After Evidence",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Official days | {report.before_after_official_days} |",
        f"| Official rows | {report.before_after_official_rows} |",
        f"| Brier delta | {_fmt(report.before_after_brier_delta)} |",
        f"| Log loss delta | {_fmt(report.before_after_log_loss_delta)} |",
        f"| After-cost delta | {_fmt(report.before_after_after_cost_delta, digits=2)} |",
        "",
    ])
    if report.capital_replay is not None:
        coverage = report.capital_replay.get("coverage")
        coverage_map = coverage if isinstance(coverage, Mapping) else {}
        lines.extend(
            [
                "## Capital Replay Objective",
                "",
                f"- Objective: {report.capital_replay.get('objective')}",
                f"- Status: {report.capital_replay.get('status')}",
                f"- Variant status: {report.capital_replay.get('variant_status')}",
                f"- Selected product: {report.capital_replay.get('selected_label')}",
                f"- Selected capital-gain variant: {report.capital_replay.get('selected_capital_gain_variant')}",
                f"- Selected ROI variant: {report.capital_replay.get('selected_roi_variant')}",
                f"- Evidence grade: {coverage_map.get('evidence_grade')}",
                f"- Replay rows: {coverage_map.get('rows')}",
                f"- Skipped rows: {coverage_map.get('skipped')}",
                f"- Promotion grade: {coverage_map.get('promotion_grade')}",
                f"- Promotion blocker: {coverage_map.get('promotion_blocker')}",
                "",
            ]
        )
    lines.extend([
        "## Blockers",
        "",
    ])
    if report.blockers:
        for group, reasons in sorted(report.blockers.items()):
            lines.append(f"### {group}")
            lines.append("")
            for reason in reasons:
                lines.append(f"- {reason}")
            lines.append("")
    else:
        lines.append("None")
        lines.append("")
    if report.config_switch_json_patch:
        lines.extend(["## Config Patch", ""])
        for item in report.config_switch_json_patch:
            lines.append(f"- {item.get('op')} {item.get('path')} = {item.get('value')}")
        lines.append("")
    lines.extend(
        [
            "Promotion note: this readiness report is evidence composition only. It cannot place orders, mutate production DBs, refit calibration, or authorize live promotion without the configured runtime and operator gates.",
            "",
        ]
    )
    return "\n".join(lines)


def write_replacement_forecast_go_live_artifacts(
    report: ReplacementForecastGoLiveReadinessReport,
    *,
    markdown_path: Path | None = None,
    json_path: Path | None = None,
) -> dict[str, str]:
    """Write Markdown/JSON readiness artifacts and return written paths."""

    if markdown_path is None and json_path is None:
        raise ValueError("at least one artifact path is required")
    written: dict[str, str] = {}
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_replacement_forecast_go_live_markdown(report), encoding="utf-8")
        written["markdown"] = str(markdown_path)
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = replacement_forecast_go_live_report_to_jsonable(report)
        json_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        written["json"] = str(json_path)
    return written
