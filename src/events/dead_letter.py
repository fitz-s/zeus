"""Dead-letter helpers for EDLI event processing."""

from __future__ import annotations

from dataclasses import dataclass

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent


@dataclass(frozen=True)
class DeadLetterRecord:
    dead_letter_id: str
    event_id: str
    failure_stage: str
    error_message: str


def dead_letter_event(
    store: EventStore,
    event: OpportunityEvent,
    *,
    failure_stage: str,
    error_message: str,
    created_at: str | None = None,
) -> DeadLetterRecord:
    dead_letter_id = store.mark_dead_letter(
        event,
        failure_stage=failure_stage,
        error_message=error_message,
        created_at=created_at,
    )
    return DeadLetterRecord(
        dead_letter_id=dead_letter_id,
        event_id=event.event_id,
        failure_stage=failure_stage,
        error_message=error_message,
    )
