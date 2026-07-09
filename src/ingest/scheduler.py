# Created: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R3 (ingest 契约化);
#   docs/rebuild/whole_system_first_principles_2026-07-07.md §2.1 ("source_clock_update_probe.py
#   (239 lines) IS the minimal target form — cursor-diff -> idempotent events — narrowed to
#   OpenMeteo; generalize it").
"""The ONE generic scheduler driving every SourceContract row.

This generalizes ``src/data/source_clock_update_probe.py``'s shape (cursor-diff over a
persisted per-source cursor -> at most one idempotent ``SOURCE_RUN_ARRIVED`` event per new
cycle) so it drives ANY :class:`~src.ingest.contract.SourceContract` row, not only OpenMeteo
models. The mechanism is intentionally pure and injectable (no live network/DB calls in this
module) so the cursor-diff idempotency and the station-clock antibody are provable with plain
unit tests; ``clock_check``/``event_writer``/``dependents_dispatch`` are supplied by the caller
(a thin daemon-side adapter resolves the row's ``clock_check_ref``/``fetch_ref`` dotted paths
into real callables — see ``resolve_ref``).

STATION-CLOCK LAW (antibody): a row's ``clock_law`` decides whether the tick consults the
shared gridded-ceiling gate. ``own_clock`` rows (station forecast adapters, every non-forecast
family) NEVER consult it — this is the notepad law encoded as a branch, not a per-call-site
convention someone can forget (which is exactly how ``_replacement_cycle_availability_poll_if_
needed`` avoided the bug in practice but never encoded the rule declaratively).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Optional, Protocol

from src.ingest.contract import SourceContract

TickStatus = Literal[
    "ARRIVED", "NO_CHANGE", "SKIPPED", "GATED_BY_GRIDDED_CEILING",
]


class CursorStore(Protocol):
    """Persisted last-seen cursor per source_id. A real implementation is a small JSON/DB-backed
    store (mirroring source_clock_update_probe.py's cursor JSON file); tests use InMemoryCursorStore."""

    def get(self, source_id: str) -> Optional[str]: ...

    def set(self, source_id: str, value: str) -> None: ...


class InMemoryCursorStore:
    """Dict-backed CursorStore — the default for tests and for callers that persist elsewhere."""

    def __init__(self) -> None:
        self._cursors: dict[str, str] = {}

    def get(self, source_id: str) -> Optional[str]:
        return self._cursors.get(source_id)

    def set(self, source_id: str, value: str) -> None:
        self._cursors[source_id] = value


@dataclass(frozen=True)
class SourceRunTickResult:
    """One tick's outcome for one SourceContract row."""

    source_id: str
    status: TickStatus
    cursor_value: Optional[str] = None
    event_id: Optional[str] = None
    dependents_dispatched: tuple[str, ...] = ()


def resolve_ref(dotted: str) -> Callable[..., object]:
    """Resolve a ``"module.path:function"`` reference into a callable (lazy import).

    SourceContract rows store fetch/parse/clock_check as dotted-path STRINGS, not live callables
    — the table declares WHERE the mechanics live without importing every fetcher's dependency
    tree just to build the registry (the same discipline ``source_job_registry.callable_ref``
    already uses)."""
    module_path, _, attr = dotted.partition(":")
    if not attr:
        raise ValueError(f"ref {dotted!r} must be 'module.path:function'")
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def run_source_contract_tick(
    contract: SourceContract,
    *,
    clock_check: Callable[[], Optional[str]],
    cursor_store: CursorStore,
    gridded_ceiling_ready: Optional[Callable[[], bool]] = None,
    make_event: Optional[Callable[[SourceContract, str], object]] = None,
    event_writer: Optional[object] = None,
    dependents_dispatch: Optional[Callable[[str, SourceContract], None]] = None,
) -> SourceRunTickResult:
    """Cursor-diff -> at most one idempotent arrival per tick, generalized over any SourceContract row.

    ``clock_check()`` returns the source's current cursor value (e.g. a run-init-time isoformat
    string) or ``None`` when the source has nothing to report this tick (fail-soft: network
    error, no metadata yet, etc. — the caller's clock_check absorbs those, this function only
    sees "no signal"). Idempotency: calling this twice with a clock_check that keeps returning
    the SAME value produces exactly one ARRIVED (the first call) and then NO_CHANGE on every
    subsequent call — the cursor_store is the single source of "have we already reacted to this".

    GRIDDED CEILING GATE: consulted ONLY when ``contract.clock_law == "gridded_ceiling"`` AND a
    ``gridded_ceiling_ready`` callable was supplied. An ``own_clock`` row is NEVER gated by it,
    regardless of what the caller passes — this is the station-clock antibody enforced at the
    call-site-independent layer (a caller cannot accidentally gate a station row by passing
    gridded_ceiling_ready, because the branch below only reads it for gridded_ceiling rows)."""
    if (
        contract.clock_law == "gridded_ceiling"
        and gridded_ceiling_ready is not None
        and not gridded_ceiling_ready()
    ):
        return SourceRunTickResult(contract.source_id, "GATED_BY_GRIDDED_CEILING")

    new_cursor = clock_check()
    if new_cursor is None:
        return SourceRunTickResult(contract.source_id, "SKIPPED")

    old_cursor = cursor_store.get(contract.source_id)
    if old_cursor == new_cursor:
        return SourceRunTickResult(contract.source_id, "NO_CHANGE", cursor_value=new_cursor)

    event_id: Optional[str] = None
    if make_event is not None and event_writer is not None:
        event = make_event(contract, new_cursor)
        result = event_writer.write(event)  # type: ignore[attr-defined]
        event_id = result.event_id

    cursor_store.set(contract.source_id, new_cursor)

    dispatched: tuple[str, ...] = ()
    if dependents_dispatch is not None and contract.dependents:
        for dependent in contract.dependents:
            dependents_dispatch(dependent, contract)
        dispatched = contract.dependents

    return SourceRunTickResult(
        contract.source_id, "ARRIVED", cursor_value=new_cursor,
        event_id=event_id, dependents_dispatched=dispatched,
    )


def run_all_due(
    contracts: Iterable[SourceContract],
    *,
    clock_check_for: Callable[[SourceContract], Callable[[], Optional[str]]],
    cursor_store: CursorStore,
    gridded_ceiling_ready: Optional[Callable[[], bool]] = None,
    make_event: Optional[Callable[[SourceContract, str], object]] = None,
    event_writer: Optional[object] = None,
    dependents_dispatch: Optional[Callable[[str, SourceContract], None]] = None,
) -> list[SourceRunTickResult]:
    """Drive every row in ``contracts`` through one tick each — the single generic loop that
    replaces per-source hand-written poll functions (goal 3: ONE scheduler for ALL rows)."""
    results: list[SourceRunTickResult] = []
    for contract in contracts:
        results.append(
            run_source_contract_tick(
                contract,
                clock_check=clock_check_for(contract),
                cursor_store=cursor_store,
                gridded_ceiling_ready=gridded_ceiling_ready,
                make_event=make_event,
                event_writer=event_writer,
                dependents_dispatch=dependents_dispatch,
            )
        )
    return results
