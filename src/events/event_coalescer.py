"""EDLI event coalescing under market-data backpressure."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass

from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import EventType, OpportunityEvent

MARKET_EVENT_TYPES: frozenset[EventType] = frozenset(
    {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED", "NEW_MARKET_DISCOVERED"}
)


@dataclass
class CoalescerStats:
    enqueued_lossless: int = 0
    enqueued_market: int = 0
    coalesced_market_events: int = 0
    backpressure_drops: int = 0


class EventCoalescer:
    """Keep forecast/Day0 lossless while coalescing noisy market data."""

    def __init__(self, *, max_market_keys: int = 1000) -> None:
        if max_market_keys < 1:
            raise ValueError("max_market_keys must be >= 1")
        self._lossless: deque[OpportunityEvent] = deque()
        self._market_latest: OrderedDict[tuple[str, str], OpportunityEvent] = OrderedDict()
        self._max_market_keys = max_market_keys
        self.stats = CoalescerStats()

    def enqueue(self, event: OpportunityEvent) -> None:
        if event.event_type in MARKET_EVENT_TYPES:
            self._enqueue_market(event)
            return
        self._lossless.append(event)
        self.stats.enqueued_lossless += 1

    def flush(self, writer: EventWriter, *, market_budget: int | None = None) -> list[EventWriteResult]:
        """Write queued events. Lossless events are not budgeted or dropped."""

        written: list[EventWriteResult] = []
        while self._lossless:
            written.append(writer.write(self._lossless.popleft()))

        remaining_budget = len(self._market_latest) if market_budget is None else max(0, market_budget)
        for key in list(self._market_latest.keys()):
            if remaining_budget <= 0:
                break
            event = self._market_latest.pop(key)
            written.append(writer.write(event))
            remaining_budget -= 1
        return written

    def pending_counts(self) -> dict[str, int]:
        return {"lossless": len(self._lossless), "market": len(self._market_latest)}

    def _enqueue_market(self, event: OpportunityEvent) -> None:
        key = (event.event_type, event.entity_key)
        if key in self._market_latest:
            self.stats.coalesced_market_events += 1
            self._market_latest.pop(key)
        elif len(self._market_latest) >= self._max_market_keys:
            self._market_latest.popitem(last=False)
            self.stats.backpressure_drops += 1
        self._market_latest[key] = event
        self.stats.enqueued_market += 1
