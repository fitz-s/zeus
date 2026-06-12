# Created: 2026-05-24
# Last reused/audited: 2026-06-04
# Authority basis: Operator GOAL 2026-06-04 — full-family q/FDR + executable-mask for illiquid bins; never trade an assumed/renormalized subset

import pytest

from src.events.candidate_binding import (
    CandidateBindingError,
    MarketTopologyCandidate,
    bind_event_to_candidate_family,
)
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.types.market import Bin


DECISION_TIME = "2026-05-24T12:00:00+00:00"


def _forecast_payload(
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-25",
    metric: str = "high",
    snapshot_id: str = "snapshot-1",
) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        source_id="ecmwf_open_data",
        source_run_id="source-run-1",
        cycle="00",
        track="mx2t6_high_full_horizon",
        snapshot_id=snapshot_id,
        snapshot_hash="hash-1",
        captured_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="READY",
    )


def _forecast_event(
    *,
    payload: ForecastSnapshotReadyPayload | None = None,
    causal_snapshot_id: str | None = "snapshot-1",
):
    payload = payload or _forecast_payload(snapshot_id=causal_snapshot_id or "snapshot-1")
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{payload.city}|{payload.target_date}|{payload.metric}",
        source="forecast",
        observed_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at="2026-05-24T10:01:00+00:00",
        payload=payload,
        causal_snapshot_id=causal_snapshot_id,
    )


def _day0_event(*, live_authority_status: str = "LIVE_AUTHORITY"):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="wu_icao",
        station_id="KMDW",
        observation_time="2026-05-24T08:00:00+00:00",
        observation_available_at="2026-05-24T08:05:00+00:00",
        raw_value=80.2,
        rounded_value=80,
        high_so_far=80.2,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status=live_authority_status,
    )
    return make_day0_extreme_updated_event(
        entity_key="Chicago|2026-05-24|high",
        source="day0",
        observed_at="2026-05-24T08:00:00+00:00",
        received_at="2026-05-24T08:06:00+00:00",
        payload=payload,
        causal_snapshot_id="day0-observation-1",
    )


def _candidate(
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-25",
    metric: str = "high",
    condition_id: str | None = "condition-1",
    yes_token_id: str | None = "yes-1",
    no_token_id: str | None = "no-1",
    label: str = "70-71°F",
    bin: Bin | None = None,
) -> MarketTopologyCandidate:
    return MarketTopologyCandidate(
        city=city,
        target_date=target_date,
        metric=metric,
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        bin=bin if bin is not None else Bin(low=70, high=71, unit="F", label=label),
        market_slug="chicago-high-2026-05-25",
    )


def test_forecast_event_requires_causal_snapshot_id():
    event = _forecast_event(causal_snapshot_id=None)

    with pytest.raises(CandidateBindingError, match="causal_snapshot_id"):
        bind_event_to_candidate_family(event, [_candidate()], decision_time=DECISION_TIME)


def test_forecast_binding_completeness_label_is_advisory_structure_binds():
    """Serving-authority ruling (incident 2026-06-11T16:33:51Z; THIRD site found
    live 19:53Z, minutes after the gate fix let PARTIAL events through): the
    trigger event's completeness label is ADVISORY at candidate binding — the
    money path serves the freshest ELIGIBLE bundle and proves coverage at proof
    time. ANTIBODY relationship: binding succeeds across the entire known
    completeness vocabulary; only structural junk (unknown label / missing
    identity fields / snapshot mismatch) raises."""
    import dataclasses
    import json as _json
    from typing import get_args

    from src.events.forecast_completeness import ForecastCompletenessStatus

    # Full -inf..+inf MECE family (mirrors test_family_binding_hash_deterministic)
    # so the bind reaches the forecast validator and topology guard both pass.
    full_family = [
        _candidate(
            condition_id="condition-1", yes_token_id="yes-1", no_token_id="no-1",
            bin=Bin(low=70, high=71, unit="F", label="70-71°F"),
        ),
        _candidate(
            condition_id="condition-2", yes_token_id="yes-2", no_token_id="no-2",
            bin=Bin(low=72, high=73, unit="F", label="72-73°F"),
        ),
        _candidate(
            condition_id="condition-low", yes_token_id="yes-low", no_token_id="no-low",
            bin=Bin(low=None, high=69, unit="F", label="69°F or below"),
        ),
        _candidate(
            condition_id="condition-high", yes_token_id="yes-high", no_token_id="no-high",
            bin=Bin(low=74, high=None, unit="F", label="74°F or above"),
        ),
    ]

    base = _forecast_event()

    def _with_payload(**updates):
        payload = _json.loads(base.payload_json)
        payload.update(updates)
        return dataclasses.replace(
            base,
            payload_json=_json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )

    for label in get_args(ForecastCompletenessStatus):
        event = _with_payload(completeness_status=label)
        family = bind_event_to_candidate_family(
            event, full_family, decision_time=DECISION_TIME
        )
        assert family is not None, label

    # Structural junk still raises (fail-closed unchanged):
    with pytest.raises(CandidateBindingError, match="known completeness label"):
        bind_event_to_candidate_family(
            _with_payload(completeness_status="GARBAGE"),
            full_family,
            decision_time=DECISION_TIME,
        )
    with pytest.raises(CandidateBindingError, match="required fields"):
        bind_event_to_candidate_family(
            _with_payload(required_fields_present=False),
            full_family,
            decision_time=DECISION_TIME,
        )


def test_day0_event_requires_live_authority_status():
    event = _day0_event(live_authority_status="UNKNOWN")

    with pytest.raises(CandidateBindingError, match="live_authority_status"):
        bind_event_to_candidate_family(
            event,
            [_candidate(target_date="2026-05-24")],
            decision_time=DECISION_TIME,
        )


def test_market_event_never_creates_live_trade_candidate():
    payload = MarketBookEventPayload(
        condition_id="condition-1",
        token_id="yes-1",
        outcome_label="YES",
        event_type="BOOK_SNAPSHOT",
        quote_seen_at="2026-05-24T10:00:00+00:00",
        book_hash="book-hash-1",
    )
    event = make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key="condition-1|yes-1",
        source="market_channel",
        observed_at="2026-05-24T10:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at="2026-05-24T10:00:01+00:00",
        payload=payload,
        causal_snapshot_id="book-hash-1",
    )

    with pytest.raises(CandidateBindingError, match="market-data events"):
        bind_event_to_candidate_family(event, [_candidate()], decision_time=DECISION_TIME)


def test_candidate_family_requires_yes_token_id_for_every_bin():
    """condition_id and yes_token_id are required for every bin (identity).
    no_token_id is optional — None marks a non-tradeable bin (illiquid tail
    bin absent from executable_market_snapshots).  Such a bin is still part of
    the full MECE family for q/FDR; it just cannot generate executable orders.
    A missing YES token is still rejected (no identity for the hypothesis).
    """
    event = _forecast_event()

    with pytest.raises(CandidateBindingError, match="YES token"):
        bind_event_to_candidate_family(
            event,
            [_candidate(yes_token_id=None)],
            decision_time=DECISION_TIME,
        )


def test_wrong_city_market_rejected():
    event = _forecast_event()

    with pytest.raises(CandidateBindingError, match="no market topology candidates"):
        bind_event_to_candidate_family(
            event,
            [_candidate(city="New York")],
            decision_time=DECISION_TIME,
        )


def test_wrong_date_market_rejected():
    event = _forecast_event()

    with pytest.raises(CandidateBindingError, match="no market topology candidates"):
        bind_event_to_candidate_family(
            event,
            [_candidate(target_date="2026-05-26")],
            decision_time=DECISION_TIME,
        )


def test_wrong_metric_market_rejected():
    event = _forecast_event()

    with pytest.raises(CandidateBindingError, match="no market topology candidates"):
        bind_event_to_candidate_family(
            event,
            [_candidate(metric="low")],
            decision_time=DECISION_TIME,
        )


def test_family_binding_hash_deterministic():
    event = _forecast_event()
    # COMPLETE-support family: two closed interior bins (70-71, 72-73) bracketed
    # by '...or below' (low=None) and '...or above' (high=None) shoulders so the
    # family forms a full -inf..+inf integer partition and passes the
    # FDR_FAMILY_TOPOLOGY_INCOMPLETE guard at bind time (mirrors the M2 pattern
    # in test_family_topology_completeness.py). A bare closed bin with no
    # shoulders is (correctly) rejected by that guard.
    candidates = [
        _candidate(
            condition_id="condition-2", yes_token_id="yes-2", no_token_id="no-2",
            bin=Bin(low=72, high=73, unit="F", label="72-73°F"),
        ),
        _candidate(
            condition_id="condition-1", yes_token_id="yes-1", no_token_id="no-1",
            bin=Bin(low=70, high=71, unit="F", label="70-71°F"),
        ),
        _candidate(
            condition_id="condition-low", yes_token_id="yes-low", no_token_id="no-low",
            bin=Bin(low=None, high=69, unit="F", label="69°F or below"),
        ),
        _candidate(
            condition_id="condition-high", yes_token_id="yes-high", no_token_id="no-high",
            bin=Bin(low=74, high=None, unit="F", label="74°F or above"),
        ),
    ]

    first = bind_event_to_candidate_family(event, candidates, decision_time=DECISION_TIME)
    second = bind_event_to_candidate_family(event, reversed(candidates), decision_time=DECISION_TIME)

    assert first.binding_hash == second.binding_hash
    assert first.family_id == second.family_id
    assert first.condition_ids == ("condition-1", "condition-2", "condition-high", "condition-low")
