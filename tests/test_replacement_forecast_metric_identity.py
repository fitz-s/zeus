# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast metric identity from B0/TIGGE calibration lineage contamination.
# Reuse: Run before wiring replacement products into forecast skill, replay, calibration, or receipt attribution.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast metric identity tests."""

from __future__ import annotations

import pytest

from src.data.forecast_source_registry import (
    ForecastReplacementEvidence,
    replacement_forecast_product,
    select_empirical_replacement_strategy,
)
from src.data.replacement_forecast_metric_identity import (
    replacement_forecast_metric_identity,
    replacement_physical_quantity_for_data_version,
    replacement_source_family_from_data_version,
)


OPENMETEO_ECMWF_IFS9_ANCHOR_LABEL = (
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
    "soft spatial anchor"
)
OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL = (
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
    "soft spatial anchor plus AIFS ENS sampled-2t posterior"
)


def test_aifs_sampled_2t_metric_identity_is_not_period_extrema_or_baseline() -> None:
    high = replacement_forecast_metric_identity("A1", "high")
    low = replacement_forecast_metric_identity("A1", "low")

    assert high.source_id == "ecmwf_aifs_ens"
    assert high.source_family == "ecmwf_aifs"
    assert high.product_id == "ecmwf_aifs_ens_sampled_2t_6h_v1"
    assert high.product_class == "ai_ensemble"
    assert high.data_version == "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max"
    assert high.physical_quantity == "sampled_2t_6h_local_calendar_day_max"
    assert high.measurement_object == "aifs_ens_member_sampled_2t_6h"
    assert high.raw_ensemble_eligible is True
    assert high.training_allowed is False
    assert high.trade_authority_status == "SHADOW_ONLY"
    assert low.data_version == "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_min"
    assert low.physical_quantity == "sampled_2t_6h_local_calendar_day_min"

    for identity in (high, low):
        assert not identity.data_version.startswith("ecmwf_opendata_")
        assert not identity.data_version.startswith("tigge_")
        assert "mx2t" not in identity.physical_quantity
        assert "mn2t" not in identity.physical_quantity


def test_openmeteo_anchor_and_derived_posterior_are_not_raw_ensemble_identities() -> None:
    anchor = replacement_forecast_metric_identity(OPENMETEO_ECMWF_IFS9_ANCHOR_LABEL, "high")
    posterior = replacement_forecast_metric_identity(
        OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL,
        "high",
    )

    assert anchor.source_id == "openmeteo_ecmwf_ifs_9km"
    assert anchor.product_class == "deterministic_spatial_anchor"
    assert anchor.physical_quantity == "deterministic_2t_anchor_local_calendar_day_max"
    assert anchor.measurement_object == "openmeteo_ecmwf_ifs9_deterministic_anchor"
    assert anchor.raw_ensemble_eligible is False

    assert posterior.source_family == "derived_posterior"
    assert posterior.product_class == "derived_shadow_posterior"
    assert posterior.physical_quantity == (
        "aifs_sampled_2t_plus_openmeteo_ecmwf_ifs9_anchor_local_calendar_day_max"
    )
    assert posterior.measurement_object == (
        "derived_aifs_sampled_2t_openmeteo_ecmwf_ifs9_soft_anchor_posterior"
    )
    assert posterior.raw_ensemble_eligible is False


def test_ifs_ens_0p1_metric_identity_stays_separate_from_aifs_sampled_2t() -> None:
    period = replacement_forecast_metric_identity("R1", "high")
    since_prev = replacement_forecast_metric_identity("R2", "low")

    assert period.source_id == "ecmwf_ifs_ens_0p1"
    assert period.product_class == "ifs_ens_direct_model_output"
    assert period.physical_quantity == "mx2t3_local_calendar_day_max"
    assert period.measurement_object == "ifs_ens_member_period_extrema"
    assert period.raw_ensemble_eligible is True

    assert since_prev.data_version == (
        "ecmwf_ifs_ens_0p1_mn2t_since_prev_postproc_local_calendar_day_min"
    )
    assert since_prev.physical_quantity == "mn2t_since_prev_postproc_local_calendar_day_min"
    assert since_prev.raw_ensemble_eligible is True


def test_replacement_metric_identity_reverse_lookup_blocks_baseline_versions() -> None:
    aifs = replacement_forecast_product("A1")
    anchor = replacement_forecast_product(OPENMETEO_ECMWF_IFS9_ANCHOR_LABEL)
    posterior = replacement_forecast_product(OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL)

    assert replacement_source_family_from_data_version(aifs.high_data_version) == "ecmwf_aifs"
    assert replacement_source_family_from_data_version(anchor.high_data_version) == "openmeteo_ecmwf"
    assert replacement_source_family_from_data_version(posterior.high_data_version) == "derived_posterior"
    assert replacement_source_family_from_data_version("ecmwf_opendata_mx2t3_local_calendar_day_max") is None
    assert replacement_source_family_from_data_version("tigge_mx2t6_local_calendar_day_max") is None
    assert replacement_source_family_from_data_version(None) is None

    assert replacement_physical_quantity_for_data_version("high", aifs.high_data_version) == (
        "sampled_2t_6h_local_calendar_day_max"
    )
    assert replacement_physical_quantity_for_data_version("low", aifs.low_data_version) == (
        "sampled_2t_6h_local_calendar_day_min"
    )
    assert replacement_physical_quantity_for_data_version("high", anchor.high_data_version) == (
        "deterministic_2t_anchor_local_calendar_day_max"
    )
    assert replacement_physical_quantity_for_data_version("mean", aifs.high_data_version) is None
    assert replacement_physical_quantity_for_data_version("high", "ecmwf_opendata_mx2t3_local_calendar_day_max") is None


def test_replacement_metric_identity_rejects_baseline_control_and_transcript_shorthand() -> None:
    with pytest.raises(ValueError, match="baseline products"):
        replacement_forecast_metric_identity("B0", "high")

    with pytest.raises(ValueError, match="no high data_version"):
        replacement_forecast_metric_identity("C1", "high")

    with pytest.raises(ValueError, match="temperature_metric"):
        replacement_forecast_metric_identity("A1", "mean")

    with pytest.raises(Exception):
        replacement_forecast_metric_identity("H" + "3", "high")


def test_replacement_empirical_selector_default_requires_250_settled_decisions() -> None:
    evidence = ForecastReplacementEvidence(
        label=OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL,
        settled_decisions=249,
        anti_lookahead_violations=0,
        availability_violations=0,
        q_lcb_coverage=0.99,
        after_cost_pnl=1.0,
        max_drawdown=0.1,
        brier=0.1,
        log_loss=0.2,
    )

    blocked = select_empirical_replacement_strategy([evidence])
    selected = select_empirical_replacement_strategy([
        ForecastReplacementEvidence(**{**evidence.__dict__, "settled_decisions": 250})
    ])

    assert blocked.status == "NO_PROMOTION_CANDIDATE"
    assert "INSUFFICIENT_SETTLED_DECISIONS" in blocked.reason_codes
    assert selected.status == "PROMOTION_CANDIDATE"
    assert selected.selected_label == OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL
