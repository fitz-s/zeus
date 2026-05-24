"""Typed EDLI opportunity event model for the R1 proof kernel."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from src.events.idempotency import canonical_json, payload_hash, stable_event_id, stable_idempotency_key

EventType = Literal[
    "FORECAST_SNAPSHOT_READY",
    "DAY0_EXTREME_UPDATED",
    "BOOK_SNAPSHOT",
    "BEST_BID_ASK_CHANGED",
    "NEW_MARKET_DISCOVERED",
]

SCHEMA_VERSION = 1


class OpportunityEventValidationError(ValueError):
    """Raised when an EDLI event would violate causality or model contract."""


@dataclass(frozen=True)
class ForecastSnapshotReadyPayload:
    city: str
    target_date: str
    metric: str
    source_id: str
    source_run_id: str
    cycle: str
    track: str
    snapshot_id: str
    snapshot_hash: str
    captured_at: str
    available_at: str
    required_fields_present: bool
    required_steps_present: bool
    member_count: int
    min_members_floor: int
    completeness_status: Literal["COMPLETE"]
    required_steps: list[int]
    observed_steps: list[int]
    expected_members: int
    source_run_status: str
    source_run_completeness_status: str
    coverage_completeness_status: str
    coverage_readiness_status: str


@dataclass(frozen=True)
class Day0ExtremeUpdatedPayload:
    city: str
    target_date: str
    metric: Literal["high", "low"]
    settlement_source: str
    station_id: str
    observation_time: str
    observation_available_at: str
    raw_value: float
    rounded_value: int
    high_so_far: float | None = None
    low_so_far: float | None = None
    source_match_status: str = "UNKNOWN"
    local_date_status: str = "UNKNOWN"
    station_match_status: str = "UNKNOWN"
    dst_status: str = "UNKNOWN"
    metric_match_status: str = "UNKNOWN"
    rounding_status: str = "UNKNOWN"
    source_authorized_status: str = "UNKNOWN"
    live_authority_status: str = "UNKNOWN"


@dataclass(frozen=True)
class MarketBookEventPayload:
    condition_id: str
    token_id: str
    outcome_label: Literal["YES", "NO"]
    event_type: Literal["BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED", "NEW_MARKET_DISCOVERED"]
    quote_seen_at: str
    book_hash: str | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    depth_json: str | None = None
    tick_size: str | None = None
    min_order_size: str | None = None
    neg_risk: bool | None = None
    executable_snapshot_id: str | None = None
    gap_start: str | None = None
    gap_recovered_at: str | None = None


@dataclass(frozen=True)
class OpportunityEvent:
    event_id: str
    event_type: EventType
    entity_key: str
    source: str
    observed_at: str
    available_at: str
    received_at: str
    causal_snapshot_id: str | None
    payload_hash: str
    idempotency_key: str
    priority: int
    expires_at: str | None
    payload_json: str
    schema_version: int
    created_at: str


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OpportunityEventValidationError(f"{field_name} must be ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise OpportunityEventValidationError(f"{field_name} must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def assert_available_for_decision(event: OpportunityEvent, decision_time: str | datetime) -> None:
    """Fail closed if an event is not available by the decision time."""

    event_available_at = _parse_utc(event.available_at, "available_at")
    event_received_at = _parse_utc(event.received_at, "received_at")
    if isinstance(decision_time, str):
        parsed_decision_time = _parse_utc(decision_time, "decision_time")
    elif decision_time.tzinfo is None:
        raise OpportunityEventValidationError("decision_time must include timezone")
    else:
        parsed_decision_time = decision_time.astimezone(timezone.utc)
    if event_available_at > parsed_decision_time:
        raise OpportunityEventValidationError(
            f"available_at {event.available_at} is after decision_time {parsed_decision_time.isoformat()}"
        )
    if event_received_at > parsed_decision_time:
        raise OpportunityEventValidationError(
            f"received_at {event.received_at} is after decision_time {parsed_decision_time.isoformat()}"
        )


def make_opportunity_event(
    *,
    event_type: EventType,
    entity_key: str,
    source: str,
    observed_at: str,
    available_at: str,
    received_at: str,
    payload: Any,
    causal_snapshot_id: str | None = None,
    priority: int = 0,
    expires_at: str | None = None,
    created_at: str | None = None,
) -> OpportunityEvent:
    """Build an immutable event with deterministic hash, id, and idempotency key."""

    _parse_utc(observed_at, "observed_at")
    _parse_utc(available_at, "available_at")
    _parse_utc(received_at, "received_at")
    if created_at is not None:
        _parse_utc(created_at, "created_at")
    if expires_at is not None:
        _parse_utc(expires_at, "expires_at")

    payload_obj = dataclasses.asdict(payload) if dataclasses.is_dataclass(payload) else payload
    payload_json = canonical_json(payload_obj)
    digest = payload_hash(payload_obj)
    idem = stable_idempotency_key(event_type, entity_key, source, available_at, digest)
    event_id = stable_event_id(idem)
    return OpportunityEvent(
        event_id=event_id,
        event_type=event_type,
        entity_key=entity_key,
        source=source,
        observed_at=observed_at,
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=causal_snapshot_id,
        payload_hash=digest,
        idempotency_key=idem,
        priority=priority,
        expires_at=expires_at,
        payload_json=payload_json,
        schema_version=SCHEMA_VERSION,
        created_at=created_at or received_at,
    )


def make_day0_extreme_updated_event(
    *,
    entity_key: str,
    source: str,
    observed_at: str,
    received_at: str,
    payload: Day0ExtremeUpdatedPayload,
    causal_snapshot_id: str | None = None,
    priority: int = 0,
) -> OpportunityEvent:
    """Create a Day0 event using observation availability, not observation time."""

    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=entity_key,
        source=source,
        observed_at=observed_at,
        available_at=payload.observation_available_at,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=causal_snapshot_id,
        priority=priority,
    )
