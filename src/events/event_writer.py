"""Single-writer facade for EDLI opportunity events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent


@dataclass(frozen=True)
class EventWriteResult:
    event_id: str
    inserted: bool
    duplicate: bool


class EventWriter:
    """Owns event-row writes for EDLI world event tables."""

    def __init__(self, conn: sqlite3.Connection, *, consumer_name: str = "edli_reactor_v1") -> None:
        self._store = EventStore(conn, consumer_name=consumer_name)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._store.conn

    def write(self, event: OpportunityEvent) -> EventWriteResult:
        inserted = self._store.insert_or_ignore(event)
        if inserted and event.event_type == "DAY0_EXTREME_UPDATED":
            self._store.archive_superseded_day0_family(event)
        return EventWriteResult(
            event_id=event.event_id,
            inserted=inserted,
            duplicate=not inserted,
        )

    def write_many(self, events: list[OpportunityEvent]) -> list[EventWriteResult]:
        return [self.write(event) for event in events]
