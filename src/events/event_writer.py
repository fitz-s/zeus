"""Single-writer facade for EDLI opportunity events."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent


_write_phase_hook: ContextVar[Callable[[], None] | None] = ContextVar(
    "event_writer_write_phase_hook",
    default=None,
)
_preflight_lock = threading.Lock()
_preflight_stores: dict[tuple[int, str], EventStore] = {}


@dataclass(frozen=True)
class EventWriteResult:
    event_id: str
    inserted: bool
    duplicate: bool


class EventWriter:
    """Owns event-row writes for EDLI world event tables."""

    def __init__(self, conn: sqlite3.Connection, *, consumer_name: str = "edli_reactor_v1") -> None:
        key = (id(conn), consumer_name)
        with _preflight_lock:
            store = _preflight_stores.get(key)
        self._store = store if store is not None and store.conn is conn else EventStore(
            conn, consumer_name=consumer_name
        )

    @classmethod
    def preflight_world_event_tables(
        cls,
        conn: sqlite3.Connection,
        *,
        consumer_name: str = "edli_reactor_v1",
    ) -> None:
        """Validate and retain one EventStore before a bounded writer lock."""

        store = EventStore(conn, consumer_name=consumer_name)
        store._require_world_event_tables()
        with _preflight_lock:
            _preflight_stores[(id(conn), consumer_name)] = store

    @classmethod
    def forget_preflight_world_event_tables(
        cls,
        conn: sqlite3.Connection,
        *,
        consumer_name: str = "edli_reactor_v1",
    ) -> None:
        """Drop a connection-scoped preflight before its connection is closed."""

        key = (id(conn), consumer_name)
        with _preflight_lock:
            store = _preflight_stores.get(key)
            if store is not None and store.conn is conn:
                _preflight_stores.pop(key, None)

    @classmethod
    @contextlib.contextmanager
    def write_phase_telemetry(cls, hook: Callable[[], None]) -> Iterator[None]:
        """Mark a write unit without coupling EventWriter to a lock owner."""

        token = _write_phase_hook.set(hook)
        try:
            yield
        finally:
            _write_phase_hook.reset(token)

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
        hook = _write_phase_hook.get()
        if events and hook is not None:
            hook()
        return [self.write(event) for event in events]
