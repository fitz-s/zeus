"""Deterministic EDLI event replay surface."""

from __future__ import annotations

from dataclasses import dataclass

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent


@dataclass(frozen=True)
class ReplayBatch:
    events: tuple[OpportunityEvent, ...]

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(event.event_id for event in self.events)


def replay_all_events(store: EventStore) -> ReplayBatch:
    """Return immutable events in the same order the reactor consumes them."""

    return ReplayBatch(tuple(store.replay_events()))
