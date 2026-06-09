# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: P1 ZERO-SUBMIT FIX B (2026-06-05, iron-rule-1, co-cause) —
#   task #107 INV-K7 in-flight reservation made rollback-aware so a candidate
#   rejected DOWNSTREAM of Kelly (DECISION_CERTIFICATE / EXECUTOR_EXPRESSIBILITY)
#   never inflates corr_committed_usd / raw_committed_usd for later same-cycle
#   candidates.
"""Per-cycle in-flight reservation ledger with provisional commit/rollback.

THE BUG THIS CLOSES (FIX B, co-cause of the P1 zero-submit defect): the EDLI
reactor adapter appended a candidate's stake to the per-cycle reservation the
moment it passed Kelly + RiskGuard — BEFORE the DECISION_CERTIFICATE compile
that can still REJECT it. The accumulator was an append-only ``list`` (no
pop/rollback), so a candidate that passed Kelly but was rejected downstream
STILL inflated the correlation-weighted and raw committed capital for every
later same-cycle candidate, compounding the budget exhaustion that zeroed them.

THE STRUCTURAL DECISION (Fitz #1 — make the category impossible): the
reservation is no longer "whatever passed Kelly"; it is "whatever the reactor
COMMITTED (emitted)". A reservation is PROVISIONAL when made and becomes part of
the committed in-flight book only when the reactor confirms the bet was emitted
(``commit``); if the reactor rejects it anywhere downstream of Kelly the
provisional reservation is removed (``rollback``).

LIFECYCLE (per event, events processed strictly sequentially by the reactor):
    reserve(event_id, city, stake)   # adapter, when Kelly+RiskGuard pass
    ... reactor post-submit phase ...
    commit(event_id)                 # reactor, on VERIFIED + ledger insert
      OR
    rollback(event_id)               # reactor, on ANY downstream rejection

Because events are processed one-at-a-time (``for event in events`` in
``OpportunityEventReactor.process_pending``), each event's reserve is finalized
(commit or rollback) BEFORE the next event's ``_submit`` reads the ledger — so a
provisional reservation is correctly netted for the next sibling while in flight
(INV-K7) yet a rejected one never reaches the next sibling's sizing.

READ INTERFACE: the ledger is ITERABLE as ``(city, stake_usd)`` tuples, a
drop-in for the legacy ``list[tuple[str, float]]`` the sizing read sites
(`correlated_committed_usd(extra_reserved=...)` and the raw-dollar sum) consume.
Both PROVISIONAL and COMMITTED entries are yielded (provisional must be netted
for the next in-flight sibling); only ROLLED-BACK entries are excluded.

SAFETY DIRECTION: ``commit`` is terminal — a committed (emitted) stake is real
in-flight capital and can NEVER be retroactively un-reserved by a later
``rollback`` (which would UNDER-count capital in flight = over-sizing risk).
``rollback`` only removes a still-provisional reservation. Unknown-event
commit/rollback are no-ops (defensive: a reject path may run before any
reserve).
"""

from __future__ import annotations

from collections.abc import Iterator


class PortfolioReservationLedger:
    """Per-reactor-cycle in-flight stake ledger (INV-K7, rollback-aware).

    One instance == one reactor cycle (constructed fresh per cycle, like the
    legacy closure-held list). Not thread-safe by design: the reactor processes
    events sequentially under the world-write mutex around the submit boundary.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        # event_id -> (city, stake_usd, committed?). Insertion-ordered so the
        # iteration order matches the legacy append-order list.
        self._entries: dict[str, tuple[str, float, bool]] = {}

    def reserve(self, event_id: str, city: str, stake_usd: float) -> None:
        """Provisionally reserve ``stake_usd`` for ``city`` under ``event_id``.

        Called by the adapter the moment a candidate passes Kelly + RiskGuard.
        The reservation is PROVISIONAL until the reactor confirms emission via
        ``commit`` (or removes it via ``rollback``). Re-reserving the same
        ``event_id`` overwrites (idempotent on retried sizing within one event).
        """
        self._entries[str(event_id)] = (str(city), float(stake_usd), False)

    def commit(self, event_id: str) -> None:
        """Mark the reservation for ``event_id`` as COMMITTED (emitted).

        Called by the reactor on the VERIFIED + ledger-insert success path. A
        committed reservation is terminal — immune to a later ``rollback``.
        No-op for an unknown event_id (defensive)."""
        key = str(event_id)
        entry = self._entries.get(key)
        if entry is None:
            return
        city, stake, _ = entry
        self._entries[key] = (city, stake, True)

    def seed_committed(self, reservation_id: str, city: str, stake_usd: float) -> None:
        """Seed already-emitted durable in-flight capital at cycle start.

        Used for cross-cycle live submissions that have left the process-local
        ledger but have not yet materialized into ``position_current``. These
        rows are already emitted, so they enter as terminal COMMITTED entries and
        cannot be removed by a later per-event rollback.
        """
        self._entries[str(reservation_id)] = (str(city), float(stake_usd), True)

    def rollback(self, event_id: str) -> None:
        """Remove the reservation for ``event_id`` IF it is still PROVISIONAL.

        Called by the reactor on ANY downstream rejection (DECISION_CERTIFICATE,
        EXECUTOR_EXPRESSIBILITY, money-path blocker, retry). A COMMITTED
        reservation is NOT removed (an emitted bet is real in-flight capital).
        No-op for an unknown event_id (defensive: the reject path may run before
        any reserve, e.g. a Kelly-failed candidate)."""
        key = str(event_id)
        entry = self._entries.get(key)
        if entry is None:
            return
        _, _, committed = entry
        if committed:
            return  # emitted capital — never un-reserve
        del self._entries[key]

    def __iter__(self) -> Iterator[tuple[str, float]]:
        """Yield ``(city, stake_usd)`` for every live (provisional OR committed)
        reservation, in reserve order — the drop-in for the legacy list."""
        for city, stake, _committed in self._entries.values():
            yield (city, stake)

    def __len__(self) -> int:
        return len(self._entries)
