"""Runtime ownership for one current cross-family auction epoch."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
from typing import Callable, Mapping, Sequence

from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    current_global_auction_scope_from_events,
    current_portfolio_wealth_witness,
    current_venue_auction_identity,
    scan_current_global_auction_scope,
)
from src.engine.global_single_order_auction import select_prepared_global_auction
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
from src.solve.solver import CurrentFamilyProbabilityAuthority
from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS

UTC = timezone.utc


def _begin_selection_read_snapshot(
    connections: Sequence[sqlite3.Connection],
) -> Callable[[], None]:
    """Own one frozen read view for selection; reject caller-owned transactions."""

    owned: list[sqlite3.Connection] = []
    seen: set[int] = set()
    try:
        for conn in connections:
            identity = id(conn)
            if identity in seen:
                continue
            seen.add(identity)
            if not isinstance(conn, sqlite3.Connection):
                raise TypeError("GLOBAL_SELECTION_SNAPSHOT_CONNECTION_INVALID")
            if conn.in_transaction:
                raise RuntimeError("GLOBAL_SELECTION_SNAPSHOT_CALLER_TXN_OPEN")
            conn.execute("BEGIN")
            owned.append(conn)
            # A deferred transaction does not acquire its read view until the first
            # statement. Establish every authority view before the cut is named.
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except Exception:
        for conn in reversed(owned):
            conn.rollback()
        raise

    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        for conn in reversed(owned):
            conn.rollback()

    return release


def _current_probability_ineligible(receipt: EventSubmissionReceipt) -> bool:
    """A typed ValueError means this family has no current q certificate."""

    return (
        receipt.prepared_global_family is None
        and str(receipt.reason or "").startswith(
            "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        )
    )


def _family_key(event: OpportunityEvent, payload: Mapping[str, object]) -> str:
    return weather_family_id(
        city=str(payload.get("city") or ""),
        target_date=str(payload.get("target_date") or ""),
        metric=str(payload.get("metric") or "").lower(),
    )


def _forecast_carrier_matches(
    event: OpportunityEvent,
    payload: Mapping[str, object],
    witness: object,
) -> bool:
    """Bind forecast-scope identity to the exact prepared posterior carrier."""

    if event.event_type != "FORECAST_SNAPSHOT_READY":
        return True
    carrier = str(
        payload.get("source_run_id") or payload.get("snapshot_hash") or ""
    ).strip()
    return bool(carrier) and carrier == str(
        getattr(witness, "posterior_identity_hash", "") or ""
    ).strip()


def _selection_epoch_identity(
    *,
    full_scope: CurrentGlobalAuctionScope,
    eligible_scope: CurrentGlobalAuctionScope,
    probability_witnesses: Mapping[str, object],
    ineligible_by_family: Mapping[str, str],
) -> str:
    """Bind the full cut, its executable q manifest, and every typed exclusion."""

    digest = hashlib.sha256()
    rows = (
        ("cut_at", full_scope.captured_at_utc.isoformat()),
        ("full_scope", full_scope.scope_identity),
        ("eligible_scope", eligible_scope.scope_identity),
    )
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x1f")
    for family_key in full_scope.family_keys:
        witness = probability_witnesses.get(family_key)
        row = (
            family_key,
            str(getattr(witness, "witness_identity", "") or ""),
            str(getattr(witness, "q_version", "") or ""),
            str(getattr(witness, "posterior_identity_hash", "") or ""),
            str(ineligible_by_family.get(family_key) or ""),
        )
        if witness is None and not row[-1]:
            raise ValueError("GLOBAL_SELECTION_EPOCH_FAMILY_UNACCOUNTED")
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def _next_claim_carrier(
    event: OpportunityEvent,
    *,
    targeted_at: datetime,
    economic_identity: str,
    payload: Mapping[str, object],
) -> OpportunityEvent:
    """Create a fresh event identity for one selected current family fact."""

    stamp = targeted_at.astimezone(UTC).isoformat()
    identity = str(economic_identity or "").strip()
    if not identity:
        raise ValueError("GLOBAL_WINNER_ACTUATION_IDENTITY_MISSING")
    return make_opportunity_event(
        event_type=event.event_type,
        entity_key=event.entity_key,
        source=f"global_auction_winner_target:{event.event_id}:{identity}",
        observed_at=event.observed_at,
        available_at=event.available_at,
        received_at=stamp,
        causal_snapshot_id=event.causal_snapshot_id,
        payload=payload,
        priority=event.priority,
        expires_at=event.expires_at,
        created_at=stamp,
    )


def process_current_global_batch(
    events: Sequence[OpportunityEvent],
    *,
    decision_time: datetime,
    world_conn,
    forecast_conn,
    trade_conn,
    payload_reader: Callable[[OpportunityEvent], Mapping[str, object]],
    prepare_event: Callable[[OpportunityEvent, datetime], EventSubmissionReceipt],
    actuate_winner: Callable[[OpportunityEvent, object, datetime], EventSubmissionReceipt],
    stamp_receipt: Callable[[EventSubmissionReceipt], EventSubmissionReceipt],
    venue_submit_count: Callable[[], int],
    current_execution: Callable[[object, datetime], object | None],
    current_time_provider: Callable[[], datetime],
    portfolio_state_provider: Callable[[], object] | None = None,
    current_book_epoch_provider: Callable[
        [Mapping[str, object], datetime],
        tuple[Mapping[str, object], CurrentGlobalBookEpoch],
    ]
    | None = None,
    selection_snapshot_connections: Sequence[sqlite3.Connection] = (),
) -> GlobalBatchSubmitResult:
    """Select once from every family holding a current q certificate."""

    if decision_time.tzinfo is None:
        raise ValueError("GLOBAL_AUCTION_DECISION_TIME_NAIVE")
    decision_time = decision_time.astimezone(UTC)
    event_tuple = tuple(events)
    release_selection_snapshot: Callable[[], None] = lambda: None

    def current_time() -> datetime:
        now = current_time_provider()
        if now.tzinfo is None:
            raise ValueError("GLOBAL_AUCTION_CURRENT_TIME_NAIVE")
        now = now.astimezone(UTC)
        if now < decision_time:
            raise ValueError("GLOBAL_AUCTION_CLOCK_REGRESSION")
        return now

    def reject(
        reason: str,
        *,
        next_claim_event: OpportunityEvent | None = None,
    ) -> GlobalBatchSubmitResult:
        release_selection_snapshot()
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=reason,
                        proof_accepted=False,
                    )
                )
                for event in event_tuple
            },
            winner_event_id=None,
            venue_submit_count=0,
            next_claim_event=next_claim_event,
        )

    try:
        release_selection_snapshot = _begin_selection_read_snapshot(
            selection_snapshot_connections
        )
        scope_at = current_time()
        full_scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=scope_at,
        )
        claimed_by_family = {}
        duplicate_owner_by_event: dict[str, str] = {}
        for event in event_tuple:
            family_key = _family_key(event, payload_reader(event))
            if family_key in claimed_by_family:
                duplicate_owner_by_event[event.event_id] = claimed_by_family[
                    family_key
                ].event_id
                continue
            claimed_by_family[family_key] = event

        prepared_by_event = {}
        full_scope_event_by_family = dict(full_scope.events_by_family)
        ineligible_by_family: dict[str, str] = {}
        ineligible_by_event: dict[str, str] = {}
        for family_key, scope_event in full_scope.events_by_family:
            owner = claimed_by_family.get(family_key, scope_event)
            prepared_receipt = prepare_event(scope_event, scope_at)
            prepared = prepared_receipt.prepared_global_family
            if prepared is None:
                if _current_probability_ineligible(prepared_receipt):
                    reason = str(prepared_receipt.reason)
                    ineligible_by_family[family_key] = reason
                    if family_key in claimed_by_family:
                        ineligible_by_event[owner.event_id] = reason
                    continue
                return reject(
                    "GLOBAL_PREPARED_FAMILY_INCOMPLETE:"
                    f"{family_key}:{prepared_receipt.reason or 'missing'}"
                )
            if not _forecast_carrier_matches(
                scope_event,
                payload_reader(scope_event),
                prepared.probability_witness,
            ):
                return reject(
                    f"GLOBAL_PROBABILITY_EPOCH_CARRIER_MISMATCH:{family_key}"
                )
            prepared_by_event[owner.event_id] = prepared
        if not prepared_by_event:
            return reject("GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY")

        eligible_family_keys = frozenset(
            prepared.probability_witness.family_key
            for prepared in prepared_by_event.values()
        )
        scope = current_global_auction_scope_from_events(
            tuple(
                full_scope_event_by_family[family_key]
                for family_key in sorted(eligible_family_keys)
            ),
            captured_at_utc=scope_at,
        )
        probabilities = {
            prepared.probability_witness.family_key: prepared.probability_witness
            for prepared in prepared_by_event.values()
        }
        if any(
            getattr(witness, "captured_at_utc", None) != scope_at
            for witness in probabilities.values()
        ):
            return reject("GLOBAL_PROBABILITY_EPOCH_MIXED_CUT")
        selection_epoch_identity = _selection_epoch_identity(
            full_scope=full_scope,
            eligible_scope=scope,
            probability_witnesses=probabilities,
            ineligible_by_family=ineligible_by_family,
        )
        book_epoch = None
        if current_book_epoch_provider is not None:
            probabilities, book_epoch = current_book_epoch_provider(
                probabilities,
                current_time(),
            )
            prepared_by_event = {
                event_id: replace(
                    prepared,
                    probability_witness=probabilities[
                        prepared.probability_witness.family_key
                    ],
                )
                for event_id, prepared in prepared_by_event.items()
            }
        # Selection is a comparison over one immutable information vector.  Scope and
        # q are frozen at ``scope_at``; the complete native YES/NO book and wealth
        # witnesses join that vector below.  A later family update belongs to the next
        # epoch.  Only the selected winner is allowed to cross into the side-effect
        # path, where probability, exact book/curve, and free cash are rebuilt JIT.
        selection_at = current_time()
        state = portfolio_state_provider() if portfolio_state_provider else None
        wealth_age = timedelta(seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS))
        wealth = current_portfolio_wealth_witness(
            trade_conn,
            decision_at_utc=selection_at,
            max_age=wealth_age,
            portfolio_state=state,
        )
        venue_identity = (
            book_epoch.witness_identity
            if book_epoch is not None
            else current_venue_auction_identity(
                trade_conn,
                probability_witnesses=probabilities,
            )
        )

        def probability_resolver(family_key):
            witness = probabilities.get(family_key)
            return (
                CurrentFamilyProbabilityAuthority.from_witness(witness)
                if witness is not None
                else None
            )

        def execution_resolver(candidate):
            if book_epoch is not None:
                return book_epoch.execution_authority(
                    candidate,
                    checked_at_utc=selection_at,
                )
            return current_execution(candidate, selection_at)

        selected = select_prepared_global_auction(
            prepared_by_event,
            selection_epoch_identity=selection_epoch_identity,
            selection_cut_at_utc=scope_at,
            current_scope=scope,
            current_scope_identity_resolver=lambda: scope.scope_identity,
            venue_universe_identity=venue_identity,
            current_venue_universe_identity_resolver=lambda: venue_identity,
            universe_max_age=(
                book_epoch.max_age
                if book_epoch is not None
                else FRESHNESS_WINDOW_DEFAULT
            ),
            current_probability_resolver=probability_resolver,
            current_execution_resolver=execution_resolver,
            current_wealth_identity_resolver=lambda: wealth.economic_identity,
            wealth_witness=wealth,
            capital_limit_usd=wealth.spendable_cash_usd,
            decision_at_utc=selection_at,
            book_epoch=book_epoch,
        )
        if selected.decision.candidate is None:
            return reject(
                "GLOBAL_AUCTION_NO_TRADE:"
                f"{selected.decision.no_trade_reason or 'unknown'}"
            )
        winner_id = selected.winner_event_id
        winner = next(
            (event for event in event_tuple if event.event_id == winner_id),
            None,
        )
        if selected.actuation is None:
            return reject("GLOBAL_WINNER_ACTUATION_MISSING")
        if winner is None:
            target = next(
                (
                    event
                    for event in full_scope_event_by_family.values()
                    if event.event_id == winner_id
                ),
                None,
            )
            if target is None:
                return reject("GLOBAL_WINNER_IDENTITY_MISSING")
            return reject(
                "GLOBAL_WINNER_AWAITS_CLAIM",
                next_claim_event=_next_claim_carrier(
                    target,
                    targeted_at=current_time(),
                    economic_identity=selected.actuation.economic_identity,
                    payload=payload_reader(target),
                ),
            )

        before_calls = venue_submit_count()
        release_selection_snapshot()
        winner_receipt = actuate_winner(winner, selected.actuation, current_time())
        receipts = {
            event.event_id: (
                winner_receipt
                if event.event_id == winner_id
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_DUPLICATE_FAMILY_CARRIER:"
                            f"{duplicate_owner_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in duplicate_owner_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_FAMILY_INELIGIBLE:"
                            f"{ineligible_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in ineligible_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_NOT_SELECTED:"
                            f"{selected.actuation.actuation_identity}"
                        ),
                        proof_accepted=False,
                    )
                )
            )
            for event in event_tuple
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner_id,
            venue_submit_count=venue_submit_count() - before_calls,
        )
    except Exception as exc:  # noqa: BLE001 - one authority fault invalidates epoch
        return reject(f"GLOBAL_AUCTION_FAILED:{type(exc).__name__}:{exc}")
