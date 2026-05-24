"""R1 EDLI event-bound decision skeleton with no runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.events.candidate_binding import (
    CandidateBindingError,
    EventBoundCandidateFamily,
    MarketTopologyCandidate,
    bind_event_to_candidate_family,
)
from src.events.opportunity_event import OpportunityEvent, OpportunityEventValidationError


R1DecisionStatus = Literal["CANDIDATE_FAMILY_READY", "NO_TRADE"]


@dataclass(frozen=True)
class EventBoundDecisionRequest:
    event: OpportunityEvent
    market_topology: tuple[MarketTopologyCandidate, ...]
    decision_time: str | datetime
    market_topology_source: str = "in_memory_market_topology"


@dataclass(frozen=True)
class EventBoundDecisionResult:
    status: R1DecisionStatus
    event_id: str
    candidate_family: EventBoundCandidateFamily | None
    rejection_reason: str | None = None


class EventBoundDecisionEngine:
    """Bind an event to candidates or reject it; never route orders."""

    def evaluate(self, request: EventBoundDecisionRequest) -> EventBoundDecisionResult:
        try:
            candidate_family = bind_event_to_candidate_family(
                request.event,
                request.market_topology,
                decision_time=request.decision_time,
                market_topology_source=request.market_topology_source,
            )
        except (CandidateBindingError, OpportunityEventValidationError) as exc:
            return EventBoundDecisionResult(
                status="NO_TRADE",
                event_id=request.event.event_id,
                candidate_family=None,
                rejection_reason=str(exc),
            )
        return EventBoundDecisionResult(
            status="CANDIDATE_FAMILY_READY",
            event_id=request.event.event_id,
            candidate_family=candidate_family,
        )
