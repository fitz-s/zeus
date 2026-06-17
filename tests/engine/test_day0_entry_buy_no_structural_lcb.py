# Created: 2026-06-17
# Last reused/audited: 2026-06-17
# Authority basis: operator Day0 LOW runtime opportunity capture fix; deterministic absorbing
#   observation facts must enter EDLI probability/proof authority without forecast expected-member gates.
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.decision_kernel.compiler import FORECAST_LIVE_ELIGIBLE_STATUS
from src.calibration.qlcb_provenance import _qlcb_float
from src.engine.event_reactor_adapter import (
    _apply_day0_mask_to_generated_probabilities,
    _day0_absorbing_authority_payload_and_clock,
    _day0_absorbing_calibration_payload_and_clock,
    _day0_absorbing_only_probability_and_fdr_proof,
)


def _candidate(condition_id: str, low: float | None, high: float | None):
    return SimpleNamespace(
        condition_id=condition_id,
        bin=SimpleNamespace(low=low, high=high),
    )


def _family(*candidates):
    return SimpleNamespace(city="Tokyo", candidates=list(candidates))


def test_low_day0_dead_yes_bin_gives_buy_no_structural_lcb() -> None:
    family = _family(
        _candidate("low20", 20.0, 20.0),
        _candidate("low21", 21.0, 21.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "low", "rounded_value": 20.0},
        family=family,
        q_by_condition={"low20": 0.4, "low21": 0.6},
        lcb_by_condition={
            ("low20", "buy_yes"): 0.3,
            ("low20", "buy_no"): 0.0,
            ("low21", "buy_yes"): 0.2,
            ("low21", "buy_no"): 0.0,
        },
    )

    assert q["low21"] == 0.0
    assert _qlcb_float(lcb[("low21", "buy_yes")]) == 0.0
    assert _qlcb_float(lcb[("low21", "buy_no")]) == pytest.approx(1.0)


def test_low_day0_current_record_bin_stays_unresolved_for_buy_no() -> None:
    family = _family(
        _candidate("low19", 19.0, 19.0),
        _candidate("low20", 20.0, 20.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "low", "rounded_value": 20.0},
        family=family,
        q_by_condition={"low19": 0.25, "low20": 0.75},
        lcb_by_condition={
            ("low19", "buy_yes"): 0.1,
            ("low19", "buy_no"): 0.0,
            ("low20", "buy_yes"): 0.5,
            ("low20", "buy_no"): 0.0,
        },
    )

    assert q["low20"] > 0.0
    assert _qlcb_float(lcb[("low20", "buy_no")]) == 0.0


def test_high_day0_dead_yes_bin_gives_buy_no_structural_lcb() -> None:
    family = _family(
        _candidate("high29", 29.0, 29.0),
        _candidate("high30", 30.0, 30.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "high", "rounded_value": 30.0},
        family=family,
        q_by_condition={"high29": 0.4, "high30": 0.6},
        lcb_by_condition={
            ("high29", "buy_yes"): 0.2,
            ("high29", "buy_no"): 0.0,
            ("high30", "buy_yes"): 0.4,
            ("high30", "buy_no"): 0.0,
        },
    )

    assert q["high29"] == 0.0
    assert _qlcb_float(lcb[("high29", "buy_yes")]) == 0.0
    assert _qlcb_float(lcb[("high29", "buy_no")]) == pytest.approx(1.0)


def test_absorbing_only_fallback_licenses_only_deterministic_direction() -> None:
    family = _family(
        _candidate("low20", 20.0, 20.0),
        _candidate("low21", 21.0, 21.0),
    )
    price = SimpleNamespace(value=0.87)

    generated = _day0_absorbing_only_probability_and_fdr_proof(
        event=SimpleNamespace(event_id="evt-1"),
        payload={"metric": "low", "rounded_value": 20.0},
        family=family,
        native_costs={
            ("low20", "buy_yes"): (None, price, 0.0, None, None),
            ("low20", "buy_no"): (None, price, 0.0, None, None),
            ("low21", "buy_yes"): (None, price, 0.0, None, None),
            ("low21", "buy_no"): (None, price, 0.0, None, None),
        },
        reason="FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:MISSING_EXPECTED_MEMBERS",
    )

    assert generated is not None
    q, lcb, p_values, prefilter, evidence = generated
    assert evidence["probability_authority"] == "day0_absorbing_hard_fact"
    assert q["low21"] == 0.0
    assert _qlcb_float(lcb[("low21", "buy_no")]) == pytest.approx(1.0)
    assert p_values[("low21", "buy_no")] == 0.0
    assert prefilter[("low21", "buy_no")] is True
    assert _qlcb_float(lcb[("low20", "buy_no")]) == 0.0
    assert prefilter[("low20", "buy_no")] is False


def test_day0_absorbing_authority_uses_observation_not_forecast_members() -> None:
    event = SimpleNamespace(
        event_id="evt-shanghai",
        event_type="DAY0_EXTREME_UPDATED",
        source="day0_extreme_updated_trigger",
        observed_at="2026-06-17T16:00:00+00:00",
        available_at="2026-06-17T16:05:12+00:00",
        received_at="2026-06-17T16:07:05+00:00",
        created_at="2026-06-17T16:07:05+00:00",
        causal_snapshot_id="metar_fast:ZSPD:2026-06-18:2026-06-17T16:05:12+00:00",
        payload_hash="payload-hash",
    )
    family = SimpleNamespace(city="Shanghai", target_date="2026-06-18", metric="low")
    payload = {
        "_edli_q_source": "day0_absorbing_hard_fact",
        "city": "Shanghai",
        "target_date": "2026-06-18",
        "metric": "low",
        "settlement_source": "aviationweather_metar",
        "station_id": "ZSPD",
        "observation_time": "2026-06-17T16:00:00+00:00",
        "observation_available_at": "2026-06-17T16:05:12+00:00",
        "rounded_value": 24,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "LIVE_AUTHORITY",
    }

    forecast_payload, forecast_clock = _day0_absorbing_authority_payload_and_clock(
        event=event,
        family=family,
        payload=payload,
        decision_time=datetime.fromisoformat("2026-06-17T16:10:00+00:00"),
    )
    calibration_payload, _ = _day0_absorbing_calibration_payload_and_clock(
        family=family,
        payload=payload,
        forecast_payload=forecast_payload,
        decision_time=datetime.fromisoformat("2026-06-17T16:10:00+00:00"),
    )

    assert forecast_payload["reader_authority"] == "day0_absorbing_hard_fact"
    assert forecast_payload["reader_status"] == FORECAST_LIVE_ELIGIBLE_STATUS
    assert forecast_payload["members_json_source"] == "day0_absorbing.observed_extreme"
    assert forecast_payload["members_extrema_transform"] == "daily_min"
    assert forecast_payload["effective_extreme"] == 24.0
    assert forecast_payload["source_match_status"] == "MATCH"
    assert forecast_payload["dst_status"] == "UNAMBIGUOUS"
    assert "day0_source_match" in forecast_payload["applied_validations"]
    assert "day0_dst_unambiguous" in forecast_payload["applied_validations"]
    assert forecast_clock.source_available_at.isoformat() == "2026-06-17T16:05:12+00:00"
    assert calibration_payload["authority"] == "DAY0_ABSORBING_HARD_FACT"
    assert calibration_payload["input_space"] == "deterministic_day0_absorbing_observation"
