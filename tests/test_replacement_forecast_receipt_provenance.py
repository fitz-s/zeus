# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast receipt provenance as forecast-only attribution with no settlement authority.
# Reuse: Run before attaching replacement shadow/veto provenance to no-submit receipts or attribution reports.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast receipt provenance tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID, ReplacementForecastPosteriorBundle
from src.data.replacement_forecast_guardrail_report import ReplacementForecastGuardrailReplayRow, build_replacement_forecast_guardrail_report
from src.data.replacement_forecast_readiness import ReplacementForecastDependency, build_replacement_forecast_readiness
from src.data.replacement_forecast_receipt_provenance import (
    RECEIPT_ROLE,
    SETTLEMENT_AUTHORITY_STATUS,
    build_replacement_forecast_receipt_provenance,
)
from src.engine.replacement_forecast_veto import ReplacementForecastVetoInput, apply_replacement_forecast_shadow_veto


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _bundle() -> ReplacementForecastPosteriorBundle:
    return ReplacementForecastPosteriorBundle(
        posterior_id=77,
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        q={"cool": 0.25, "warm": 0.75},
        q_lcb={"cool": 0.20, "warm": 0.65},
        q_ucb=None,
        bin_topology_hash="test-topology",
        family_id="test-family",
        posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T03:00:00+00:00",
        computed_at="2026-06-06T03:05:00+00:00",
        baseline_source_run_id="b0-run",
        dependency_json={"source_run_ids": ["b0-run", "aifs-run", "om9-run"]},
        provenance_json={"test": True, "bin_topology_hash": "test-topology"},
        trade_authority_status="SHADOW_VETO_ONLY",
    )


def _readiness():
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2, 30),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(3),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(3, 5),
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


def _veto_decision(**overrides):
    params = {
        "baseline_direction": "buy_yes:warm",
        "baseline_q_posterior": 0.70,
        "baseline_q_lcb": 0.62,
        "baseline_kelly_fraction": 0.04,
        "candidate_direction": "buy_yes:warm",
        "candidate_q_posterior": 0.75,
        "candidate_q_lcb": 0.55,
        "candidate_kelly_fraction": 0.02,
        "market_snapshot_id": "snap-1",
        "condition_id": "cond-1",
        "token_id": "token-yes",
        "decision_time": "2026-06-06T04:00:00+00:00",
    }
    params.update(overrides)
    return apply_replacement_forecast_shadow_veto(
        replacement_bundle=_bundle(),
        veto_input=ReplacementForecastVetoInput(**params),
    )


def test_receipt_provenance_is_forecast_attribution_only() -> None:
    provenance = build_replacement_forecast_receipt_provenance(
        veto_decision=_veto_decision(),
        readiness=_readiness(),
    ).as_dict()

    assert provenance["receipt_role"] == RECEIPT_ROLE
    assert provenance["settlement_authority_status"] == SETTLEMENT_AUTHORITY_STATUS
    assert provenance["source_id"] == SOURCE_ID
    assert provenance["product_id"] == PRODUCT_ID
    assert provenance["posterior_id"] == 77
    assert provenance["readiness_id"].startswith("replacement_readiness:")
    assert provenance["training_allowed"] is False
    assert provenance["promotion_allowed"] is False
    assert provenance["trade_authority_status"] == "SHADOW_VETO_ONLY"
    assert provenance["authority_limits"] == {
        "can_flip_direction": False,
        "can_increase_kelly": False,
        "can_increase_q_lcb": False,
        "can_initiate_trade": False,
        "can_settle_market": False,
        "can_train_model": False,
    }


def test_receipt_provenance_carries_dependency_and_veto_identity() -> None:
    provenance = build_replacement_forecast_receipt_provenance(
        veto_decision=_veto_decision(),
        readiness=_readiness(),
    ).as_dict()

    assert provenance["baseline_source_run_id"] == "b0-run"
    assert provenance["dependency_source_run_ids"] == {
        "baseline_b0": "b0-run",
        "aifs_sampled_2t": "aifs-run",
        "openmeteo_ifs9_anchor": "om9-run",
        "soft_anchor_posterior": "posterior-run",
    }
    assert provenance["source_available_at_max"] == "2026-06-06T03:05:00+00:00"
    assert provenance["market_snapshot_id"] == "snap-1"
    assert provenance["condition_id"] == "cond-1"
    assert provenance["token_id"] == "token-yes"
    assert provenance["decision_time"] == "2026-06-06T04:00:00+00:00"
    assert provenance["veto_applied"] is True
    assert provenance["veto_reasons"] == ["SOFT_ANCHOR_LOWER_Q_LCB", "SOFT_ANCHOR_LOWER_KELLY"]
    assert provenance["allowed_direction"] == "buy_yes:warm"
    assert provenance["allowed_q_lcb"] == pytest.approx(0.55)
    assert provenance["allowed_kelly_fraction"] == pytest.approx(0.02)


def test_receipt_provenance_preserves_guardrail_regression_clusters() -> None:
    report = build_replacement_forecast_guardrail_report(
        (
            ReplacementForecastGuardrailReplayRow(
                city="Shanghai",
                temperature_metric="high",
                guardrail_bucket="coastal_land_sea_flip",
                replay_status="SCORED",
                replacement_delta_after_cost_pnl=-0.40,
                veto_applied=True,
                baseline_after_cost_pnl=0.20,
                replacement_after_cost_pnl=-0.20,
            ),
            ReplacementForecastGuardrailReplayRow(
                city="Shanghai",
                temperature_metric="high",
                guardrail_bucket="coastal_land_sea_flip",
                replay_status="SCORED",
                replacement_delta_after_cost_pnl=-0.20,
                veto_applied=True,
                baseline_after_cost_pnl=0.10,
                replacement_after_cost_pnl=-0.10,
            ),
        ),
        min_scored_rows_per_bucket=1,
    )

    provenance = build_replacement_forecast_receipt_provenance(
        veto_decision=_veto_decision(),
        readiness=_readiness(),
        guardrail_report=report,
    ).as_dict()

    assert provenance["guardrail_report_status"] == "SHADOW_ONLY"
    assert provenance["guardrail_promotion_allowed"] is False
    assert provenance["unresolved_regression_clusters"]
    assert provenance["net_delta_after_cost_pnl"] == pytest.approx(-0.60)


def test_receipt_provenance_rejects_settlement_truth_and_records_live_trade_authority_only() -> None:
    with pytest.raises(ValueError, match="settlement truth field"):
        build_replacement_forecast_receipt_provenance(
            veto_decision=_veto_decision(),
            readiness=_readiness(),
            extra_provenance={"settlement_value": 78},
        )

    live_authority = {**_veto_decision().__dict__, "trade_authority_status": "LIVE_AUTHORITY"}
    provenance = build_replacement_forecast_receipt_provenance(
        veto_decision=live_authority,
        readiness=_readiness(),
    ).as_dict()
    assert provenance["trade_authority_status"] == "LIVE_AUTHORITY"
    assert provenance["authority_limits"]["can_initiate_trade"] is True
    assert provenance["authority_limits"]["can_settle_market"] is False
    assert provenance["authority_limits"]["can_train_model"] is False
    assert provenance["promotion_allowed"] is False

    bad_product = {**_veto_decision().__dict__, "product_id": "short_" + "h" + "3_alias"}
    with pytest.raises(ValueError, match="full replacement product identity"):
        build_replacement_forecast_receipt_provenance(
            veto_decision=bad_product,
            readiness=_readiness(),
        )
