# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast shadow veto artifact before order intent.
# Reuse: Run before wiring replacement forecast veto into event reactor or decision compiler.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast shadow veto tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_bundle_reader import ReplacementForecastPosteriorBundle
from src.engine.replacement_forecast_veto import ReplacementForecastVetoInput, apply_replacement_forecast_shadow_veto


def _bundle() -> ReplacementForecastPosteriorBundle:
    return ReplacementForecastPosteriorBundle(
        posterior_id=42,
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        q={"cold": 0.2, "warm": 0.8},
        q_lcb={"cold": 0.1, "warm": 0.7},
        posterior_method="openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T03:00:00+00:00",
        computed_at="2026-06-06T03:05:00+00:00",
        baseline_source_run_id="b0-run",
        dependency_json={"source_run_ids": ["b0-run", "aifs-run", "om9-run"]},
        provenance_json={"reader_test": True},
        trade_authority_status="SHADOW_VETO_ONLY",
    )


def _input(**overrides) -> ReplacementForecastVetoInput:
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
    return ReplacementForecastVetoInput(**params)


def test_replacement_veto_can_only_reduce_q_lcb_and_kelly() -> None:
    decision = apply_replacement_forecast_shadow_veto(
        replacement_bundle=_bundle(),
        veto_input=_input(),
    )

    assert decision.allowed_direction == "buy_yes:warm"
    assert decision.allowed_q_lcb == pytest.approx(0.55)
    assert decision.allowed_kelly_fraction == pytest.approx(0.02)
    assert decision.veto is True
    assert decision.reasons == ("SOFT_ANCHOR_LOWER_Q_LCB", "SOFT_ANCHOR_LOWER_KELLY")
    assert decision.trade_authority_status == "SHADOW_VETO_ONLY"
    assert decision.provenance["baseline_source_run_id"] == "b0-run"
    assert decision.provenance["training_allowed"] is False


def test_replacement_veto_never_flips_direction_or_raises_values() -> None:
    decision = apply_replacement_forecast_shadow_veto(
        replacement_bundle=_bundle(),
        veto_input=_input(
            candidate_direction="buy_yes:cold",
            candidate_q_lcb=0.90,
            candidate_kelly_fraction=0.10,
        ),
    )

    assert decision.allowed_direction == "buy_yes:warm"
    assert decision.allowed_q_lcb == pytest.approx(0.62)
    assert decision.allowed_kelly_fraction == pytest.approx(0.04)
    assert decision.veto is True
    assert decision.reasons == ("SOFT_ANCHOR_DIRECTION_DISAGREEMENT",)


def test_replacement_veto_row_payload_matches_shadow_decision_schema() -> None:
    decision = apply_replacement_forecast_shadow_veto(
        replacement_bundle=_bundle(),
        veto_input=_input(),
    )
    row = decision.as_shadow_decision_row()

    assert row["posterior_id"] == 42
    assert row["market_snapshot_id"] == "snap-1"
    assert row["allowed_direction"] == "buy_yes:warm"
    assert row["allowed_q_lcb"] == pytest.approx(0.55)
    assert row["allowed_kelly_fraction"] == pytest.approx(0.02)
    assert row["veto"] == 1
    assert row["trade_authority_status"] == "SHADOW_VETO_ONLY"
    assert row["dependency_source_run_ids_json"] == {"source_run_ids": ["b0-run", "aifs-run", "om9-run"]}
    assert row["provenance_json"]["role"] == "pre_intent_shadow_veto"


def test_replacement_veto_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="q_lcb"):
        _input(candidate_q_lcb=1.2)

    with pytest.raises(ValueError, match="kelly"):
        _input(candidate_kelly_fraction=-0.1)

    with pytest.raises(TypeError, match="replacement_bundle"):
        apply_replacement_forecast_shadow_veto(replacement_bundle=object(), veto_input=_input())
