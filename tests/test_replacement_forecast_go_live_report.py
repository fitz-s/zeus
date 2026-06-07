# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Compose replacement switch, refit, rollback, and before/after evidence into one readiness verdict.
# Reuse: Run before claiming Open-Meteo ECMWF IFS 9km + AIFS sampled-2t is simple-switch, fine-tune, or live-promotion ready.
# Authority basis: Operator-directed replacement forecast worktree integration; final readiness must be evidence-composed.
"""Replacement forecast go-live readiness report tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import sys

import pytest

from src.data.replacement_forecast_before_after_report import (
    ReplacementForecastBeforeAfterRow,
    build_replacement_forecast_before_after_report,
)
from src.data.replacement_forecast_config_switch import TARGET_SHADOW_MATERIALIZATION_CONFIG
from src.data.replacement_forecast_go_live_report import (
    BEFORE_AFTER_CSV_COLUMNS,
    REPORT_SCHEMA_VERSION,
    ReplacementForecastGoLiveReadinessInput,
    build_replacement_forecast_go_live_readiness_from_payload,
    build_replacement_forecast_go_live_readiness_report,
    replacement_forecast_before_after_rows_from_csv,
    replacement_forecast_go_live_payload_template,
    render_replacement_forecast_go_live_markdown,
    replacement_forecast_go_live_report_to_jsonable,
    write_replacement_forecast_go_live_artifacts,
)
from src.data.replacement_forecast_live_switch_surface import (
    CURRENT_DATA_FACT_FILE,
    CURRENT_SOURCE_FACT_FILE,
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    SOURCE_ID,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_emos_identity import READY_STATUS, REPLACEMENT_EMOS_KEY_SCHEMA
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_refit_handoff import build_replacement_forecast_refit_handoff
from src.data.replacement_forecast_rollback_plan import build_replacement_forecast_rollback_plan
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastPromotionEvidence,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)
from src.data.replacement_forecast_finetune_artifact import parameter_key
from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import SoftAnchorParameter


STRATEGY_KEY = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
PARAM_SELECTED = SoftAnchorParameter(anchor_weight=0.80, anchor_sigma_c=3.00)
PARAM_OTHER = SoftAnchorParameter(anchor_weight=0.60, anchor_sigma_c=4.00)


def _flags(**overrides: bool) -> dict[str, bool]:
    flags = {
        SHADOW_FLAG: True,
        VETO_FLAG: True,
        TRADE_AUTHORITY_FLAG: False,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    flags.update(overrides)
    return flags


def _promotion_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _policy(*, live: bool = False):
    flags = _flags(**({TRADE_AUTHORITY_FLAG: True} if live else {}))
    return resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_promotion_evidence() if live else None,
    )


def _switch(policy=None, *, source_status: str = "CURRENT_FOR_LIVE", data_status: str = "CURRENT_FOR_LIVE"):
    policy = policy or _policy()
    return build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=tuple(REQUIRED_LIVE_READ_FILES),
            forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            trade_tables=tuple(REQUIRED_TRADE_TABLES),
            enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
            source_fact_status=source_status,
            data_fact_status=data_status,
        )
    )


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=timezone.utc)


def _readiness(*, ready: bool = True):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="baseline-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2, 30),
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="openmeteo-run",
            source_available_at=_dt(3),
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
            source_run_id="posterior-run",
            source_available_at=_dt(3, 5) if ready else _dt(5),
            posterior_id=77,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def _refit(*, ready: bool = True, live_promotion: bool = False):
    return evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=5 if ready else 1,
            official_rows=250 if ready else 57,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="soft_anchor_product_specific_nested_refit",
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=20 if ready else 5,
            emos_key_includes_product=True,
            emos_key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
            emos_identity_evidence_status=READY_STATUS,
            data_refit_requested=ready,
            live_promotion_requested=live_promotion,
        )
    )


def _switch_decision(policy=None, live_switch=None, readiness=None, refit=None):
    policy = policy or _policy()
    live_switch = live_switch or _switch(policy)
    refit = refit or _refit()
    readiness = _readiness() if readiness is None else readiness
    return evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=live_switch,
            readiness=readiness,
            refit_decision=refit,
        )
    )


def _before_after(*, ready: bool = True, positive: bool = True):
    if not ready:
        return build_replacement_forecast_before_after_report(
            [
                ReplacementForecastBeforeAfterRow(
                    official_date="2026-06-04",
                    city="Shanghai",
                    temperature_metric="high",
                    guardrail_bucket="standard",
                    baseline_brier=0.3,
                    replacement_brier=0.2,
                    baseline_log_loss=0.7,
                    replacement_log_loss=0.5,
                    baseline_after_cost_pnl=0.0,
                    replacement_after_cost_pnl=1.0,
                )
            ]
        )
    start = date(2026, 6, 1)
    rows = []
    for offset in range(5):
        for _ in range(50):
            rows.append(
                ReplacementForecastBeforeAfterRow(
                    official_date=(start + timedelta(days=offset)).isoformat(),
                    city="Shanghai",
                    temperature_metric="high",
                    guardrail_bucket="standard",
                    baseline_brier=0.3,
                    replacement_brier=0.2,
                    baseline_log_loss=0.7,
                    replacement_log_loss=0.5,
                    baseline_after_cost_pnl=0.0,
                    replacement_after_cost_pnl=1.0 if positive else -1.0,
                )
            )
    return build_replacement_forecast_before_after_report(rows)


def _rollback(policy=None):
    return build_replacement_forecast_rollback_plan(
        current_policy=policy or _policy(),
        reason="test rollback",
        generated_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
    )


def _input(**overrides):
    policy = overrides.pop("runtime_policy", _policy())
    live_switch = overrides.pop("live_switch_report", _switch(policy))
    refit = overrides.pop("refit_decision", _refit())
    switch_decision = overrides.pop(
        "switch_decision",
        _switch_decision(policy=policy, live_switch=live_switch, refit=refit),
    )
    params = {
        "strategy_key": STRATEGY_KEY,
        "runtime_policy": policy,
        "live_switch_report": live_switch,
        "switch_decision": switch_decision,
        "refit_decision": refit,
        "before_after_report": _before_after(),
        "rollback_plan": _rollback(policy),
        "source_fact_status": "CURRENT_FOR_LIVE",
        "data_fact_status": "CURRENT_FOR_LIVE",
        "operator_approval_id": None,
    }
    params.update(overrides)
    return ReplacementForecastGoLiveReadinessInput(**params)


def _capital_replay() -> dict[str, object]:
    return {
        "objective": "maximize_after_cost_capital_gain_with_roi_drawdown_diagnostics",
        "status": "EMPIRICAL_WINNER",
        "variant_status": "STRATEGY_VARIANT_WINNER",
        "selected_label": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_brier",
        "selected_capital_gain_variant": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_brier:all_top1",
        "selected_roi_variant": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00:after_cost_edge",
        "coverage": {
            "evidence_grade": "shadow_economic_with_assumed_source_time",
            "rows": 287,
            "skipped": 475,
            "promotion_grade": False,
            "promotion_blocker": "source_available_at is assumed from decision cutoff",
        },
    }


def _live_dry_run() -> dict[str, object]:
    return {
        "status": "DRY_RUN_READY",
        "raw_artifact_lineage_status": "READY",
        "raw_artifact_lineage_counts": {
            "openmeteo_ecmwf_ifs_9km": 100,
            "ecmwf_aifs_ens": 7,
        },
        "latest_readiness_artifact_status": "READY",
        "latest_readiness_artifact_counts": {
            "aifs_sampled_2t": 1,
            "openmeteo_ifs9_anchor": 1,
        },
        "configured_refit_handoff_status": "READY",
        "latest_materialized_posterior": {
            "city": "Chicago",
            "target_date": "2026-06-05",
            "temperature_metric": "high",
        },
    }


def test_go_live_report_reaches_fine_tune_ready_without_trade_authority() -> None:
    report = build_replacement_forecast_go_live_readiness_report(_input())

    assert report.schema_version == REPORT_SCHEMA_VERSION
    assert report.status == "FINE_TUNE_READY"
    assert report.simple_switch_ready is True
    assert report.fine_tune_ready is True
    assert report.live_promotion_ready is False
    assert report.before_after_brier_delta == pytest.approx(-0.1)
    assert report.before_after_log_loss_delta == pytest.approx(-0.2)
    assert report.before_after_after_cost_delta == pytest.approx(250.0)
    assert report.switch_decision_status == "SHADOW_VETO_ONLY"
    assert report.switch_can_read_shadow_posterior is True
    assert report.switch_can_apply_reactor_hook is True
    assert report.switch_can_apply_veto is True
    assert report.switch_can_initiate_trade is False
    assert report.switch_can_increase_kelly is False
    assert report.switch_can_flip_direction is False
    assert report.switch_decision_readiness_id is not None
    assert report.blockers == {}


def test_go_live_report_blocks_on_stale_fact_surfaces_even_if_components_exist() -> None:
    policy = _policy()
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy, source_status="STALE_FOR_LIVE"),
            source_fact_status="STALE_FOR_LIVE",
        )
    )

    assert report.status == "BLOCKED"
    assert report.simple_switch_ready is False
    assert "current_facts" in report.blockers
    assert "REPLACEMENT_GO_LIVE_SOURCE_FACTS_STALE" in report.reason_codes


def test_go_live_report_distinguishes_simple_switch_ready_from_refit_ready() -> None:
    report = build_replacement_forecast_go_live_readiness_report(
        _input(refit_decision=_refit(ready=False))
    )

    assert report.status == "SIMPLE_SWITCH_READY"
    assert report.simple_switch_ready is True
    assert report.fine_tune_ready is False
    assert "refit" in report.blockers
    assert "switch_decision" not in report.blockers


def test_go_live_report_blocks_live_promotion_without_positive_before_after_evidence() -> None:
    policy = _policy(live=True)
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            before_after_report=_before_after(positive=False),
            rollback_plan=_rollback(policy),
            operator_approval_id="operator-approved",
        )
    )

    assert report.live_promotion_ready is False
    assert "before_after" in report.blockers
    assert "REPLACEMENT_GO_LIVE_AFTER_COST_DELTA_NOT_POSITIVE" in report.blockers["before_after"]


def test_go_live_report_blocks_live_promotion_on_non_promotion_grade_capital_replay() -> None:
    policy = _policy(live=True)
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            refit_decision=_refit(live_promotion=True),
            rollback_plan=_rollback(policy),
            operator_approval_id="operator-approved",
            capital_replay=_capital_replay(),
        )
    )

    assert report.live_promotion_ready is False
    assert report.status == "BLOCKED"
    assert "capital_replay" in report.blockers
    assert "REPLACEMENT_GO_LIVE_CAPITAL_REPLAY_NOT_PROMOTION_GRADE" in report.blockers["capital_replay"]
    assert "REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_ASSUMED" in report.blockers["capital_replay"]


def test_go_live_report_accepts_observed_promotion_grade_capital_replay() -> None:
    policy = _policy(live=True)
    capital_replay = _capital_replay()
    capital_replay["coverage"] = {
        **dict(capital_replay["coverage"]),
        "promotion_grade": True,
        "source_availability_mode": "observed",
        "source_availability_observed": True,
        "source_availability_violations": 0,
        "evidence_grade": "shadow_economic_with_observed_source_time",
        "promotion_blocker": "",
    }
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            refit_decision=_refit(live_promotion=True),
            rollback_plan=_rollback(policy),
            operator_approval_id="operator-approved",
            capital_replay=capital_replay,
        )
    )

    assert report.live_promotion_ready is True
    assert report.status == "LIVE_PROMOTION_READY"
    assert "capital_replay" not in report.blockers
    assert "runtime_policy" not in report.blockers


def test_go_live_report_blocks_observed_capital_replay_with_source_availability_violations() -> None:
    policy = _policy(live=True)
    capital_replay = _capital_replay()
    capital_replay["coverage"] = {
        **dict(capital_replay["coverage"]),
        "promotion_grade": True,
        "source_availability_mode": "observed",
        "source_availability_observed": True,
        "source_availability_violations": 1,
        "evidence_grade": "shadow_economic_with_observed_source_time",
        "promotion_blocker": "",
    }
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            refit_decision=_refit(live_promotion=True),
            rollback_plan=_rollback(policy),
            operator_approval_id="operator-approved",
            capital_replay=capital_replay,
        )
    )

    assert report.live_promotion_ready is False
    assert "REPLACEMENT_GO_LIVE_SOURCE_AVAILABILITY_VIOLATIONS" in report.blockers["capital_replay"]


def test_go_live_report_does_not_require_operator_approval_string_for_live_trade_authority() -> None:
    policy = _policy(live=True)
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            refit_decision=_refit(live_promotion=True),
            rollback_plan=_rollback(policy),
            operator_approval_id=None,
        )
    )

    assert report.status == "LIVE_PROMOTION_READY"
    assert report.live_promotion_ready is True
    assert "operator_approval" not in report.blockers
    assert "runtime_policy" not in report.blockers
    assert "switch_decision" not in report.blockers
    assert report.switch_can_initiate_trade is True


def test_go_live_report_reaches_live_promotion_ready_with_all_evidence_and_operator_approval() -> None:
    policy = _policy(live=True)
    report = build_replacement_forecast_go_live_readiness_report(
        _input(
            runtime_policy=policy,
            live_switch_report=_switch(policy),
            refit_decision=_refit(live_promotion=True),
            rollback_plan=_rollback(policy),
            operator_approval_id="operator-approved",
        )
    )

    assert report.status == "LIVE_PROMOTION_READY"
    assert report.live_promotion_ready is True
    assert report.switch_can_initiate_trade is True
    assert report.switch_decision_status == "LIVE_AUTHORITY"
    assert "runtime_policy" not in report.blockers
    assert "switch_decision" not in report.blockers


def test_go_live_report_rejects_short_alias_in_system_identity_fields() -> None:
    with pytest.raises(ValueError, match="full replacement identity"):
        _input(strategy_key="short_" + "h" + "3_alias")


def test_go_live_report_renders_markdown_and_json_artifacts(tmp_path) -> None:
    report = build_replacement_forecast_go_live_readiness_report(
        _input(capital_replay=_capital_replay(), live_dry_run=_live_dry_run())
    )

    markdown = render_replacement_forecast_go_live_markdown(report)
    payload = replacement_forecast_go_live_report_to_jsonable(report)

    assert "# Replacement Forecast Go-Live Readiness" in markdown
    assert "## Gate Summary" in markdown
    assert "Brier delta" in markdown
    assert "Log loss delta" in markdown
    assert "After-cost delta" in markdown
    assert "## Capital Replay Objective" in markdown
    assert "## Live Dry Run Lineage" in markdown
    assert "Raw Open-Meteo ECMWF IFS 9km artifacts: 100" in markdown
    assert "Raw AIFS ENS artifacts: 7" in markdown
    assert "Latest readiness AIFS artifact links: 1" in markdown
    assert "Latest posterior: Chicago 2026-06-05 high" in markdown
    assert "Selected capital-gain variant" in markdown
    assert "Selected ROI variant" in markdown
    assert "shadow_economic_with_assumed_source_time" in markdown
    assert "Switch decision: SHADOW_VETO_ONLY" in markdown
    assert "Switch can initiate trade: False" in markdown
    assert "Promotion note:" in markdown
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["status"] == "FINE_TUNE_READY"
    assert payload["capital_replay"]["selected_capital_gain_variant"].endswith(":all_top1")
    assert payload["capital_replay"]["selected_roi_variant"].endswith(":after_cost_edge")
    assert payload["capital_replay"]["coverage"]["promotion_grade"] is False
    assert payload["live_dry_run"]["raw_artifact_lineage_status"] == "READY"
    assert payload["live_dry_run"]["latest_readiness_artifact_status"] == "READY"
    assert payload["switch_decision_status"] == "SHADOW_VETO_ONLY"
    assert payload["switch_can_initiate_trade"] is False

    markdown_path = tmp_path / "readiness.md"
    json_path = tmp_path / "readiness.json"
    written = write_replacement_forecast_go_live_artifacts(
        report,
        markdown_path=markdown_path,
        json_path=json_path,
    )

    assert written == {"markdown": str(markdown_path), "json": str(json_path)}
    assert markdown_path.read_text(encoding="utf-8") == markdown
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["before_after_official_days"] == 5
    assert parsed["before_after_official_rows"] == 250
    assert parsed["capital_replay"]["objective"] == "maximize_after_cost_capital_gain_with_roi_drawdown_diagnostics"
    assert parsed["live_promotion_ready"] is False


def test_go_live_artifact_writer_requires_at_least_one_path() -> None:
    report = build_replacement_forecast_go_live_readiness_report(_input())

    with pytest.raises(ValueError, match="at least one artifact path"):
        write_replacement_forecast_go_live_artifacts(report)


def _payload() -> dict[str, object]:
    rows = []
    start = date(2026, 6, 1)
    for offset in range(5):
        for _ in range(50):
            rows.append(
                {
                    "official_date": (start + timedelta(days=offset)).isoformat(),
                    "city": "Shanghai",
                    "temperature_metric": "high",
                    "guardrail_bucket": "standard",
                    "baseline_brier": 0.3,
                    "replacement_brier": 0.2,
                    "baseline_log_loss": 0.7,
                    "replacement_log_loss": 0.5,
                    "baseline_after_cost_pnl": 0.0,
                    "replacement_after_cost_pnl": 1.0,
                }
            )
    return {
        "strategy_key": STRATEGY_KEY,
        "runtime_flags": _flags(),
        "source_fact_status": "CURRENT_FOR_LIVE",
        "data_fact_status": "CURRENT_FOR_LIVE",
        "live_switch": {
            "available_files": list(REQUIRED_LIVE_READ_FILES),
            "forecast_tables": list(REQUIRED_FORECAST_TABLES),
            "world_tables": list(REQUIRED_WORLD_TABLES),
            "trade_tables": list(REQUIRED_TRADE_TABLES),
            "enabled_evidence_gates": list(REQUIRED_EVIDENCE_GATES),
        },
        "refit_evidence": {
            "official_days": 5,
            "official_rows": 250,
            "temperature_metric": "high",
            "source_family": "derived_posterior",
            "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            "calibration_method": "soft_anchor_product_specific_nested_refit",
            "enabled_evidence": list(REQUIRED_REFIT_EVIDENCE),
            "min_guardrail_bucket_rows": 20,
            "emos_key_includes_product": True,
            "emos_key_schema": REPLACEMENT_EMOS_KEY_SCHEMA,
            "emos_identity_evidence_status": READY_STATUS,
            "data_refit_requested": True,
        },
        "readiness": {
            "city": "Shanghai",
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
                    "source_run_id": "openmeteo-run",
                    "source_available_at": "2026-06-06T03:00:00+00:00",
                    "status": "SHADOW_ONLY",
                },
                {
                    "role": "soft_anchor_posterior",
                    "source_id": SOURCE_ID,
                    "product_id": PRODUCT_ID,
                    "data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
                    "source_run_id": "posterior-run",
                    "source_available_at": "2026-06-06T03:05:00+00:00",
                    "status": "SHADOW_ONLY",
                    "posterior_id": 77,
                },
            ],
        },
        "before_after_rows": rows,
        "rollback": {
            "reason": "test rollback",
            "generated_at": "2026-06-06T00:00:00+00:00",
        },
    }


def _derived_promotion_inputs(*, covered_rows: int = 250) -> dict[str, object]:
    start = date(2026, 6, 1)
    guardrail_rows = []
    q_lcb_rows = []
    for idx in range(250):
        guardrail_rows.append(
            {
                "city": f"City{idx % 50}",
                "temperature_metric": "high",
                "guardrail_bucket": "standard",
                "replay_status": "SCORED",
                "replacement_delta_after_cost_pnl": 1.0,
                "veto_applied": True,
                "baseline_after_cost_pnl": -1.0,
                "replacement_after_cost_pnl": 0.0,
                "reason_codes": [],
            }
        )
        q_lcb_rows.append(
            {
                "official_date": (start + timedelta(days=idx // 50)).isoformat(),
                "city": f"City{idx % 50}",
                "temperature_metric": "high",
                "guardrail_bucket": "standard",
                "truth_authority": "VERIFIED",
                "scored": True,
                "covered_by_q_lcb": idx < covered_rows,
            }
        )
    return {
        "guardrail_rows": guardrail_rows,
        "q_lcb_coverage_rows": q_lcb_rows,
        "fine_tune_artifact": {
            "schema_version": "replacement_soft_anchor_finetune_artifact_v1",
            "status": "FINE_TUNE_ARTIFACT_READY",
            "result": {
                "status": "PROMOTION_EVIDENCE_READY",
                "reason_codes": ["REPLACEMENT_FINETUNE_NESTED_WALK_FORWARD_READY"],
                "official_days": 5,
                "official_rows": 250,
                "candidate_grid": [parameter_key(PARAM_SELECTED), parameter_key(PARAM_OTHER)],
                "selected_parameter": parameter_key(PARAM_SELECTED),
                "mean_holdout_brier": 0.20,
                "mean_holdout_log_loss": 0.50,
                "promotion_ready": True,
                "folds": [
                    {
                        "holdout_day": (start + timedelta(days=offset)).isoformat(),
                        "selected_parameter": parameter_key(PARAM_SELECTED),
                        "train_row_count": 200,
                        "holdout_row_count": 50,
                        "holdout_brier": 0.20,
                        "holdout_log_loss": 0.50,
                        "status": "SCORED",
                        "reason_codes": ["REPLACEMENT_FINETUNE_HOLDOUT_SCORED"],
                    }
                    for offset in range(5)
                ],
                "guardrail_bucket_coverage": [
                    {
                        "guardrail_bucket": "standard",
                        "row_count": 250,
                        "status": "PASS",
                        "reason_codes": ["REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_ROW_COVERAGE_PASS"],
                    }
                ],
            },
        },
    }


def _refit_handoff_payload(*, ready: bool = True) -> dict[str, object]:
    artifact = _derived_promotion_inputs()["fine_tune_artifact"]
    if not ready:
        artifact = dict(artifact)
        result = dict(artifact["result"])
        result["official_days"] = 1
        result["official_rows"] = 50
        result["promotion_ready"] = False
        result["reason_codes"] = ["REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_DAYS"]
        artifact["result"] = result
        artifact["ready_for_refit"] = False
    else:
        artifact = dict(artifact)
        artifact["ready_for_refit"] = True
    return build_replacement_forecast_refit_handoff(
        fine_tune_artifact=artifact,
        city="Shanghai",
        season="JJA",
        metric="high",
        generated_at="2026-06-06T09:00:00+00:00",
    ).as_dict()


def test_go_live_report_can_be_built_from_explicit_json_payload() -> None:
    report = build_replacement_forecast_go_live_readiness_from_payload(_payload())

    assert report.status == "FINE_TUNE_READY"
    assert report.before_after_official_days == 5
    assert report.before_after_official_rows == 250
    assert report.before_after_after_cost_delta == pytest.approx(250.0)


def test_go_live_payload_can_use_refit_handoff_without_manual_refit_evidence() -> None:
    payload = _payload()
    payload.pop("refit_evidence")
    payload["refit_handoff"] = _refit_handoff_payload()

    report = build_replacement_forecast_go_live_readiness_from_payload(payload)

    assert report.status == "FINE_TUNE_READY"
    assert report.fine_tune_ready is True
    assert report.switch_decision_status == "SHADOW_VETO_ONLY"


def test_go_live_payload_aligns_explicit_promotion_refit_bit_with_ready_handoff() -> None:
    payload = _payload()
    payload["runtime_flags"] = _flags(**{TRADE_AUTHORITY_FLAG: True})
    payload["operator_approval_id"] = "operator-approved"
    payload.pop("refit_evidence")
    payload["refit_handoff"] = _refit_handoff_payload()
    stale_evidence = _promotion_evidence()
    payload["promotion_evidence"] = {
        "official_days": stale_evidence.official_days,
        "official_rows": stale_evidence.official_rows,
        "after_cost_pnl": stale_evidence.after_cost_pnl,
        "q_lcb_coverage": stale_evidence.q_lcb_coverage,
        "anti_lookahead_violations": stale_evidence.anti_lookahead_violations,
        "source_availability_violations": stale_evidence.source_availability_violations,
        "unresolved_regression_clusters": stale_evidence.unresolved_regression_clusters,
        "same_clob_replay_passed": stale_evidence.same_clob_replay_passed,
        "nested_walk_forward_passed": stale_evidence.nested_walk_forward_passed,
        "same_clob_replay_scored_rows": stale_evidence.same_clob_replay_scored_rows,
        "same_clob_replay_blocked_rows": stale_evidence.same_clob_replay_blocked_rows,
        "fee_depth_fill_evidence_passed": stale_evidence.fee_depth_fill_evidence_passed,
        "unit_pnl_only": stale_evidence.unit_pnl_only,
        "nested_holdout_brier": stale_evidence.nested_holdout_brier,
        "nested_holdout_log_loss": stale_evidence.nested_holdout_log_loss,
        "nested_selected_anchor_weight": stale_evidence.nested_selected_anchor_weight,
        "nested_selected_anchor_sigma_c": stale_evidence.nested_selected_anchor_sigma_c,
        "nested_guardrail_bucket_count": stale_evidence.nested_guardrail_bucket_count,
        "nested_guardrail_bucket_min_rows": stale_evidence.nested_guardrail_bucket_min_rows,
        "product_specific_refit_passed": False,
    }

    report = build_replacement_forecast_go_live_readiness_from_payload(payload)

    assert report.runtime_policy_status == "LIVE_AUTHORITY"
    assert "runtime_policy" not in report.blockers
    assert report.switch_decision_status == "LIVE_AUTHORITY"
    assert "switch_decision" not in report.blockers


def test_go_live_payload_fails_closed_when_refit_evidence_conflicts_with_handoff() -> None:
    payload = _payload()
    payload["refit_evidence"] = dict(payload["refit_evidence"])
    payload["refit_evidence"]["official_days"] = 1
    payload["refit_handoff"] = _refit_handoff_payload()

    with pytest.raises(ValueError, match="refit_evidence conflicts with refit_handoff"):
        build_replacement_forecast_go_live_readiness_from_payload(payload)


def test_go_live_payload_rejects_blocked_refit_handoff() -> None:
    payload = _payload()
    payload.pop("refit_evidence")
    payload["refit_handoff"] = _refit_handoff_payload(ready=False)

    with pytest.raises(ValueError, match="status must be REFIT_HANDOFF_READY"):
        build_replacement_forecast_go_live_readiness_from_payload(payload)


def test_go_live_payload_derives_promotion_evidence_from_report_inputs() -> None:
    payload = _payload()
    payload["runtime_flags"] = _flags(**{TRADE_AUTHORITY_FLAG: True})
    payload["refit_evidence"] = dict(payload["refit_evidence"])
    payload["refit_evidence"]["live_promotion_requested"] = True
    payload["operator_approval_id"] = "operator-approved"
    payload["promotion_evidence"] = None
    payload["derived_promotion_evidence_inputs"] = _derived_promotion_inputs()

    report = build_replacement_forecast_go_live_readiness_from_payload(payload)

    assert report.runtime_policy_status == "LIVE_AUTHORITY"
    assert report.status == "LIVE_PROMOTION_READY"
    assert report.switch_decision_status == "LIVE_AUTHORITY"
    assert report.switch_can_initiate_trade is True
    assert "runtime_policy" not in report.blockers
    assert "switch_decision" not in report.blockers


def test_go_live_payload_ignores_derived_promotion_evidence_q_lcb_for_direct_authority() -> None:
    payload = _payload()
    payload["runtime_flags"] = _flags(**{TRADE_AUTHORITY_FLAG: True})
    payload["promotion_evidence"] = None
    payload["derived_promotion_evidence_inputs"] = _derived_promotion_inputs(covered_rows=230)

    report = build_replacement_forecast_go_live_readiness_from_payload(payload)

    assert report.runtime_policy_status == "LIVE_AUTHORITY"
    assert "runtime_policy" not in report.blockers


def _before_after_csv_text(*, rows_per_day: int = 50, replacement_after_cost_pnl: float = 1.0) -> str:
    lines = [",".join(BEFORE_AFTER_CSV_COLUMNS)]
    start = date(2026, 6, 1)
    for offset in range(5):
        for _ in range(rows_per_day):
            values = {
                "official_date": (start + timedelta(days=offset)).isoformat(),
                "city": "Shanghai",
                "temperature_metric": "high",
                "guardrail_bucket": "standard",
                "baseline_brier": "0.3",
                "replacement_brier": "0.2",
                "baseline_log_loss": "0.7",
                "replacement_log_loss": "0.5",
                "baseline_after_cost_pnl": "0.0",
                "replacement_after_cost_pnl": str(replacement_after_cost_pnl),
                "truth_authority": "VERIFIED",
                "replay_status": "SCORED",
            }
            lines.append(",".join(values[column] for column in BEFORE_AFTER_CSV_COLUMNS))
    return "\n".join(lines) + "\n"


def test_go_live_before_after_rows_can_be_loaded_from_csv(tmp_path) -> None:
    csv_path = tmp_path / "before_after.csv"
    csv_path.write_text(_before_after_csv_text(), encoding="utf-8")

    rows = replacement_forecast_before_after_rows_from_csv(csv_path)

    assert len(rows) == 250
    assert rows[0].official_date == "2026-06-01"
    assert rows[0].truth_authority == "VERIFIED"
    assert rows[0].replay_status == "SCORED"
    assert rows[-1].replacement_after_cost_pnl == pytest.approx(1.0)


def test_go_live_before_after_csv_requires_declared_columns(tmp_path) -> None:
    csv_path = tmp_path / "before_after_missing.csv"
    csv_path.write_text("official_date,city\n2026-06-01,Shanghai\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        replacement_forecast_before_after_rows_from_csv(csv_path)


def test_go_live_payload_template_is_explicit_and_safe_default() -> None:
    template = replacement_forecast_go_live_payload_template()

    assert template["strategy_key"] == STRATEGY_KEY
    assert template["source_fact_status"] == "STALE_FOR_LIVE"
    assert template["data_fact_status"] == "STALE_FOR_LIVE"
    assert template["operator_approval_id"] is None
    flags = template["runtime_flags"]
    assert isinstance(flags, dict)
    assert flags[TRADE_AUTHORITY_FLAG] is False
    assert "before_after_rows" in template
    assert "refit_evidence" in template
    assert "readiness" in template
    assert template["readiness"]["dependencies"][3]["source_id"] == SOURCE_ID


def test_go_live_report_cli_writes_markdown_and_json(tmp_path) -> None:
    input_path = tmp_path / "readiness_payload.json"
    markdown_path = tmp_path / "readiness.md"
    json_path = tmp_path / "readiness.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--report-md",
            str(markdown_path),
            "--report-json",
            str(json_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["switch_decision_status"] == "SHADOW_VETO_ONLY"
    assert stdout_payload["switch_can_initiate_trade"] is False
    assert stdout_payload["written_artifacts"] == {"json": str(json_path), "markdown": str(markdown_path)}
    assert "# Replacement Forecast Go-Live Readiness" in markdown_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["before_after_official_rows"] == 250


def test_go_live_report_cli_csv_rows_override_payload_before_after_rows(tmp_path) -> None:
    payload = _payload()
    payload["before_after_rows"] = [
        {
            "official_date": "2026-06-01",
            "city": "Shanghai",
            "temperature_metric": "high",
            "guardrail_bucket": "standard",
            "baseline_brier": 0.3,
            "replacement_brier": 0.2,
            "baseline_log_loss": 0.7,
            "replacement_log_loss": 0.5,
            "baseline_after_cost_pnl": 0.0,
            "replacement_after_cost_pnl": -10.0,
        }
    ]
    input_path = tmp_path / "readiness_payload.json"
    csv_path = tmp_path / "before_after.csv"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    csv_path.write_text(_before_after_csv_text(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--before-after-rows-csv",
            str(csv_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["before_after_official_rows"] == 250
    assert stdout_payload["before_after_after_cost_delta"] == pytest.approx(250.0)


def test_go_live_report_cli_loads_payload_declared_before_after_csv(tmp_path) -> None:
    payload = _payload()
    payload["before_after_rows"] = []
    payload["capital_replay"] = {
        "before_after_rows_csv": "reports/before_after.csv",
        "status": "EMPIRICAL_WINNER",
    }
    input_path = tmp_path / "readiness_payload.json"
    csv_path = tmp_path / "reports" / "before_after.csv"
    csv_path.parent.mkdir(parents=True)
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    csv_path.write_text(_before_after_csv_text(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["before_after_official_rows"] == 250
    assert stdout_payload["before_after_after_cost_delta"] == pytest.approx(250.0)


def test_go_live_report_cli_loads_payload_csv_relative_to_input_ancestor(tmp_path) -> None:
    payload = _payload()
    payload["before_after_rows"] = []
    payload["capital_replay"] = {
        "before_after_rows_csv": ".local/replacement_reports/economic/before_after.csv",
        "status": "EMPIRICAL_WINNER",
    }
    input_dir = tmp_path / ".local" / "replacement_reports" / "economic"
    input_path = input_dir / "readiness_payload.json"
    csv_path = input_dir / "before_after.csv"
    input_dir.mkdir(parents=True)
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    csv_path.write_text(_before_after_csv_text(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["before_after_official_rows"] == 250


def _create_go_live_tables(db_path, tables: tuple[str, ...]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for table in tables:
            if table == "raw_forecast_artifacts":
                conn.execute(
                    """
                    CREATE TABLE raw_forecast_artifacts (
                        artifact_id INTEGER PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        product_id TEXT NOT NULL,
                        data_version TEXT NOT NULL,
                        source_cycle_time TEXT NOT NULL,
                        source_available_at TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO raw_forecast_artifacts (
                        source_id, product_id, data_version, source_cycle_time,
                        source_available_at, captured_at, artifact_path, sha256, byte_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "openmeteo_ecmwf_ifs_9km",
                            "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                            "openmeteo_ecmwf_ifs9_anchor_localday_high",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/openmeteo.json",
                            "a" * 64,
                            1,
                        ),
                        (
                            "ecmwf_aifs_ens",
                            "ecmwf_aifs_ens_sampled_2t_6h_v1",
                            "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/aifs.grib",
                            "b" * 64,
                            1,
                        ),
                    ),
                )
            else:
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")


def _write_go_live_current_docs(root, *, current: bool) -> None:
    status = "CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE"
    for relative in (CURRENT_SOURCE_FACT_FILE, CURRENT_DATA_FACT_FILE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Status: {status}\n", encoding="utf-8")


def _write_go_live_required_non_db_files(root) -> None:
    for relative in REQUIRED_LIVE_READ_FILES:
        if relative.endswith(".db"):
            continue
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "state/replacement_forecast_shadow/refit_handoff.json":
            path.write_text(json.dumps(_refit_handoff_payload()) + "\n", encoding="utf-8")
        else:
            path.write_text("{}\n", encoding="utf-8")


def _write_go_live_replacement_flags(root, *, present: bool = True) -> None:
    path = root / "config" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not present:
        path.write_text('{"feature_flags": {}}\n', encoding="utf-8")
        return
    path.write_text(
        json.dumps(
            {
                "feature_flags": {
                    SHADOW_FLAG: True,
                    VETO_FLAG: True,
                    TRADE_AUTHORITY_FLAG: False,
                    KELLY_INCREASE_FLAG: False,
                    DIRECTION_FLIP_FLAG: False,
                },
                "replacement_forecast_shadow": dict(TARGET_SHADOW_MATERIALIZATION_CONFIG),
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_go_live_report_cli_live_state_root_overrides_manual_ready_payload_with_real_blockers(tmp_path) -> None:
    payload = _payload()
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_current_docs(state_root, current=False)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "BLOCKED"
    assert stdout_payload["source_fact_status"] == "STALE_FOR_LIVE"
    assert stdout_payload["data_fact_status"] == "STALE_FOR_LIVE"
    assert "REPLACEMENT_GO_LIVE_SOURCE_FACTS_STALE" in stdout_payload["reason_codes"]
    assert "REPLACEMENT_SWITCH_MISSING_READ_TABLES" in stdout_payload["blockers"]["live_switch"]
    assert stdout_payload["switch_decision_status"] == "BLOCKED"
    assert "switch_decision" in stdout_payload["blockers"]


def test_go_live_report_cli_live_state_root_can_prove_simple_switch_inventory(tmp_path) -> None:
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root)
    _write_go_live_current_docs(state_root, current=True)
    _create_go_live_tables(state_root / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["simple_switch_ready"] is True
    assert stdout_payload["switch_decision_status"] == "SHADOW_VETO_ONLY"
    assert stdout_payload["source_fact_status"] == "CURRENT_FOR_LIVE"
    assert stdout_payload["data_fact_status"] == "CURRENT_FOR_LIVE"
    assert stdout_payload["config_switch_json_patch"] == []
    assert "config_switch" not in stdout_payload["blockers"]


def test_go_live_report_cli_live_state_root_blocks_missing_raw_lineage(tmp_path) -> None:
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root)
    _write_go_live_current_docs(state_root, current=True)
    forecast_db = state_root / "state/zeus-forecasts.db"
    _create_go_live_tables(forecast_db, tuple(REQUIRED_FORECAST_TABLES))
    with sqlite3.connect(forecast_db) as conn:
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE source_id = 'ecmwf_aifs_ens'")
        conn.commit()
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "BLOCKED"
    assert stdout_payload["simple_switch_ready"] is False
    assert stdout_payload["live_dry_run"]["raw_artifact_lineage_status"] == "MISSING_INPUT_FAMILY"
    assert stdout_payload["live_dry_run"]["raw_artifact_lineage_counts"]["ecmwf_aifs_ens"] == 0
    assert "REPLACEMENT_GO_LIVE_DRY_RUN_NOT_READY" in stdout_payload["blockers"]["live_dry_run"]
    assert "REPLACEMENT_GO_LIVE_RAW_ARTIFACT_LINEAGE_NOT_READY" in stdout_payload["blockers"]["live_dry_run"]


def test_go_live_report_cli_live_state_root_uses_derived_promotion_evidence(tmp_path) -> None:
    payload = _payload()
    payload["runtime_flags"] = _flags(**{TRADE_AUTHORITY_FLAG: True})
    payload["refit_evidence"] = dict(payload["refit_evidence"])
    payload["refit_evidence"]["live_promotion_requested"] = True
    payload["operator_approval_id"] = "operator-approved"
    payload["promotion_evidence"] = None
    payload["derived_promotion_evidence_inputs"] = _derived_promotion_inputs()
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root)
    _write_go_live_current_docs(state_root, current=True)
    _create_go_live_tables(state_root / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["runtime_policy_status"] == "LIVE_AUTHORITY"
    assert stdout_payload["source_fact_status"] == "CURRENT_FOR_LIVE"
    assert stdout_payload["data_fact_status"] == "CURRENT_FOR_LIVE"
    assert stdout_payload["status"] == "LIVE_PROMOTION_READY"
    assert stdout_payload["switch_decision_status"] == "LIVE_AUTHORITY"
    assert stdout_payload["switch_can_initiate_trade"] is True
    assert "runtime_policy" not in stdout_payload["blockers"]
    assert "switch_decision" not in stdout_payload["blockers"]


def test_go_live_report_cli_live_state_root_uses_refit_handoff(tmp_path) -> None:
    payload = _payload()
    payload.pop("refit_evidence")
    payload["refit_handoff"] = _refit_handoff_payload()
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root)
    _write_go_live_current_docs(state_root, current=True)
    _create_go_live_tables(state_root / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["fine_tune_ready"] is True
    assert stdout_payload["source_fact_status"] == "CURRENT_FOR_LIVE"
    assert stdout_payload["data_fact_status"] == "CURRENT_FOR_LIVE"


def test_go_live_report_cli_live_state_root_overlays_current_refit_handoff(tmp_path) -> None:
    payload = _payload()
    payload["refit_evidence"] = dict(payload["refit_evidence"])
    payload["refit_evidence"]["official_days"] = 1
    payload["refit_evidence"]["official_rows"] = 10
    payload["refit_evidence"]["data_refit_requested"] = False
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root)
    _write_go_live_current_docs(state_root, current=True)
    _create_go_live_tables(state_root / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))
    handoff_path = state_root / "state" / "replacement_forecast_shadow" / "refit_handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(_refit_handoff_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "FINE_TUNE_READY"
    assert stdout_payload["fine_tune_ready"] is True
    assert "refit" not in stdout_payload["blockers"]


def test_go_live_report_cli_live_state_root_reports_config_patch_before_switch(tmp_path) -> None:
    input_path = tmp_path / "readiness_payload.json"
    state_root = tmp_path / "state_root"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    _write_go_live_required_non_db_files(state_root)
    _write_go_live_replacement_flags(state_root, present=False)
    _write_go_live_current_docs(state_root, current=True)
    _create_go_live_tables(state_root / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_go_live_tables(state_root / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_go_live_tables(state_root / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--live-state-root",
            str(state_root),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1, result.stderr
    stdout_payload = json.loads(result.stdout)
    assert stdout_payload["status"] == "BLOCKED"
    assert stdout_payload["simple_switch_ready"] is False
    assert stdout_payload["config_switch_status"] == "READY"
    assert "REPLACEMENT_GO_LIVE_CONFIG_PATCH_NOT_APPLIED" in stdout_payload["blockers"]["config_switch"]
    patch_paths = {item["path"] for item in stdout_payload["config_switch_json_patch"]}
    assert {
        f"/feature_flags/{SHADOW_FLAG}",
        f"/feature_flags/{VETO_FLAG}",
        f"/feature_flags/{TRADE_AUTHORITY_FLAG}",
        f"/feature_flags/{KELLY_INCREASE_FLAG}",
        f"/feature_flags/{DIRECTION_FLIP_FLAG}",
    }.issubset(patch_paths)
    assert {
        f"/replacement_forecast_shadow/{key}" for key in TARGET_SHADOW_MATERIALIZATION_CONFIG
    }.issubset(patch_paths)


def test_go_live_report_cli_prints_template(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--print-template",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    template = json.loads(result.stdout)
    assert template["source_fact_status"] == "STALE_FOR_LIVE"
    assert template["runtime_flags"][TRADE_AUTHORITY_FLAG] is False


def test_go_live_report_cli_invalid_payload_fails_closed_without_traceback(tmp_path) -> None:
    input_path = tmp_path / "bad_payload.json"
    input_path.write_text(json.dumps({"strategy_key": STRATEGY_KEY}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_replacement_forecast_go_live.py",
            "--input-json",
            str(input_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error["status"] == "INVALID_PAYLOAD"
    assert "runtime_flags" in error["error"]
    assert "Traceback" not in result.stderr
