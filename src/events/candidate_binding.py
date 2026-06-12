"""Pure EDLI event-to-candidate-family binding.

This module deliberately does not know how to run a cycle, submit an order, or
perform runtime I/O. Its only job is to prove that an immutable event can be
bound to the exact market-family topology it is allowed to evaluate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from src.events.idempotency import canonical_json, sha256_text
from src.events.opportunity_event import OpportunityEvent, assert_available_for_decision
from src.types.market import Bin, BinTopologyError, to_json_safe, validate_bin_topology


class CandidateBindingError(ValueError):
    """Raised when an event cannot be bound to an exact candidate family."""


MARKET_DATA_EVENT_TYPES = {
    "BOOK_SNAPSHOT",
    "BEST_BID_ASK_CHANGED",
    "NEW_MARKET_DISCOVERED",
}


@dataclass(frozen=True)
class MarketTopologyCandidate:
    city: str
    target_date: str
    metric: str
    condition_id: str | None
    yes_token_id: str | None
    no_token_id: str | None
    bin: Bin
    market_slug: str | None = None

    def binding_payload(self) -> dict:
        return {
            "city": self.city,
            "target_date": self.target_date,
            "metric": self.metric,
            "condition_id": self.condition_id,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "bin": to_json_safe(self.bin),
            "market_slug": self.market_slug,
        }


@dataclass(frozen=True)
class EventBoundCandidateFamily:
    family_id: str
    event_id: str
    event_type: str
    city: str
    target_date: str
    metric: str
    condition_ids: tuple[str, ...]
    yes_token_ids: tuple[str, ...]
    no_token_ids: tuple[str, ...]
    bins: tuple[Bin, ...]
    candidates: tuple[MarketTopologyCandidate, ...]
    causal_snapshot_id: str
    market_topology_source: str
    binding_hash: str


def bind_event_to_candidate_family(
    event: OpportunityEvent,
    market_topology: Iterable[MarketTopologyCandidate],
    *,
    decision_time: str | datetime,
    market_topology_source: str = "in_memory_market_topology",
) -> EventBoundCandidateFamily:
    """Bind a forecast/Day0 event to one exact city/date/metric candidate set."""

    assert_available_for_decision(event, decision_time)
    if event.event_type in MARKET_DATA_EVENT_TYPES:
        raise CandidateBindingError("market-data events cannot create live trade candidates")

    payload = _payload_dict(event)
    city = _required_payload_text(payload, "city")
    target_date = _required_payload_text(payload, "target_date")
    metric = _required_payload_text(payload, "metric")
    causal_snapshot_id = _validate_event_causality(event, payload)

    if event.event_type == "FORECAST_SNAPSHOT_READY":
        _validate_forecast_event(event, payload, causal_snapshot_id)
    elif event.event_type == "DAY0_EXTREME_UPDATED":
        _validate_day0_event(payload)
    else:
        raise CandidateBindingError(f"unsupported live candidate event type: {event.event_type}")

    candidates = tuple(
        sorted(
            (
                candidate
                for candidate in market_topology
                if candidate.city == city
                and candidate.target_date == target_date
                and candidate.metric == metric
            ),
            key=lambda candidate: (
                candidate.condition_id or "",
                candidate.yes_token_id or "",
                candidate.no_token_id or "",
                candidate.bin.label,
            ),
        )
    )
    if not candidates:
        raise CandidateBindingError(
            f"no market topology candidates match event city={city!r} "
            f"target_date={target_date!r} metric={metric!r}"
        )
    _validate_complete_token_map(candidates)

    condition_ids = tuple(_unique(candidate.condition_id for candidate in candidates))
    yes_token_ids = tuple(_unique(candidate.yes_token_id for candidate in candidates))
    no_token_ids = tuple(_unique(candidate.no_token_id for candidate in candidates))
    bins = tuple(candidate.bin for candidate in candidates)
    # FDR family-completeness antibody (Task #114, gates_on_q=FALSE): the bound
    # family must form a complete integer partition (full -inf..+inf support).
    # Validate the FULL family bins, never an executable-filtered subset — a
    # legitimately delisted/subset family that still spans full support (with
    # open-ended shoulders) MUST still construct (M2 carve-out is load-bearing,
    # no trade-halt). An incomplete topology (e.g. NYC-type dup + no-shoulder,
    # where settlement can land outside modeled bins) is rejected HERE rather
    # than masked downstream by MC re-normalization (ensemble_signal.py:264).
    try:
        validate_bin_topology(list(bins))
    except BinTopologyError as exc:
        raise CandidateBindingError(
            f"FDR_FAMILY_TOPOLOGY_INCOMPLETE:{exc}"
        ) from exc
    binding_payload = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "causal_snapshot_id": causal_snapshot_id,
        "market_topology_source": market_topology_source,
        "candidates": [candidate.binding_payload() for candidate in candidates],
    }
    binding_hash = sha256_text(canonical_json(binding_payload))
    family_id = "edli_family_" + binding_hash[:24]
    return EventBoundCandidateFamily(
        family_id=family_id,
        event_id=event.event_id,
        event_type=event.event_type,
        city=city,
        target_date=target_date,
        metric=metric,
        condition_ids=condition_ids,
        yes_token_ids=yes_token_ids,
        no_token_ids=no_token_ids,
        bins=bins,
        candidates=candidates,
        causal_snapshot_id=causal_snapshot_id,
        market_topology_source=market_topology_source,
        binding_hash=binding_hash,
    )


def _payload_dict(event: OpportunityEvent) -> dict:
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError as exc:
        raise CandidateBindingError("event payload_json is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CandidateBindingError("event payload_json must decode to an object")
    return payload


def _required_payload_text(payload: dict, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise CandidateBindingError(f"event payload missing required {field_name}")
    return value


def _validate_event_causality(event: OpportunityEvent, payload: dict) -> str:
    if not event.causal_snapshot_id:
        raise CandidateBindingError("live EDLI candidate binding requires causal_snapshot_id")
    payload_snapshot_id = payload.get("snapshot_id")
    if isinstance(payload_snapshot_id, str) and payload_snapshot_id and payload_snapshot_id != event.causal_snapshot_id:
        raise CandidateBindingError("event causal_snapshot_id does not match payload snapshot_id")
    return event.causal_snapshot_id


def _validate_forecast_event(event: OpportunityEvent, payload: dict, causal_snapshot_id: str) -> None:
    if event.event_type != "FORECAST_SNAPSHOT_READY":
        raise CandidateBindingError("forecast validator received non-forecast event")
    # Coverage labels are ADVISORY here (serving-authority ruling, incident
    # 2026-06-11T16:33:51Z; THIRD site found live 2026-06-11T19:53Z minutes
    # after the gate fix e7e6749054 let PARTIAL events through): the money path
    # serves the freshest ELIGIBLE bundle keyed by (city, target_date, metric)
    # — never this trigger event's run — and the adapter rejects honestly at
    # proof time when nothing servable exists. Binding binds STRUCTURE only:
    # a label from the known vocabulary (junk guard), required identity fields,
    # and causal snapshot equality. required_steps_present reflects THIS run's
    # download state — advisory, the served bundle has its own coverage proof.
    from typing import get_args

    from src.events.forecast_completeness import ForecastCompletenessStatus

    if payload.get("completeness_status") not in set(get_args(ForecastCompletenessStatus)):
        raise CandidateBindingError("forecast candidate binding requires a known completeness label")
    if payload.get("required_fields_present") is not True:
        raise CandidateBindingError("forecast candidate binding requires required fields")
    if payload.get("snapshot_id") != causal_snapshot_id:
        raise CandidateBindingError("forecast candidate binding requires causal snapshot equality")


def _validate_day0_event(payload: dict) -> None:
    required_statuses = {
        "live_authority_status": {"LIVE_AUTHORITY"},
        "source_match_status": {"MATCH"},
        "station_match_status": {"MATCH"},
        "local_date_status": {"MATCH"},
        "dst_status": {"UNAMBIGUOUS", "MATCH"},
        "metric_match_status": {"MATCH"},
        "rounding_status": {"MATCH"},
        "source_authorized_status": {"AUTHORIZED"},
    }
    for field_name, accepted in required_statuses.items():
        if payload.get(field_name) not in accepted:
            raise CandidateBindingError(
                f"Day0 candidate binding requires {field_name} in {sorted(accepted)!r}"
            )


def _validate_complete_token_map(candidates: tuple[MarketTopologyCandidate, ...]) -> None:
    # Every bin requires a condition_id (used as FDR hypothesis key) and at minimum a
    # YES token id (used for proof identity).  no_token_id may be None for non-tradeable
    # bins (illiquid tail bins absent from executable_market_snapshots) — those bins
    # carry the full MECE family so q/FDR are computed over the complete partition, but
    # they never generate executable orders (executable_mask=False downstream).
    for candidate in candidates:
        if not candidate.condition_id:
            raise CandidateBindingError("candidate family requires condition_id for every bin")
        if not candidate.yes_token_id:
            raise CandidateBindingError("candidate family requires YES token id for every bin")


def _unique(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
