"""EDLI redemption reactor shell with no live submit side effects."""

from __future__ import annotations

from dataclasses import dataclass

from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest, EventBoundDecisionResult


@dataclass(frozen=True)
class ReactorConfig:
    live_submit_enabled: bool = False


class OpportunityEventReactor:
    def __init__(self, *, decision_engine: EventBoundDecisionEngine, config: ReactorConfig | None = None) -> None:
        self._decision_engine = decision_engine
        self._config = config or ReactorConfig()

    def process(self, request: EventBoundDecisionRequest) -> EventBoundDecisionResult:
        result = self._decision_engine.evaluate(request)
        if result.status == "FINAL_INTENT_READY" and not self._config.live_submit_enabled:
            return EventBoundDecisionResult(
                status="NO_TRADE",
                event_id=result.event_id,
                candidate_family=result.candidate_family,
                final_intent_ready=False,
                rejection_reason="LIVE_SUBMIT_DISABLED",
            )
        return result
