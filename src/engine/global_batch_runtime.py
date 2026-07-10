"""Runtime ownership for one current cross-family auction epoch."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Sequence

from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    current_portfolio_wealth_witness,
    current_venue_auction_identity,
    scan_current_global_auction_scope,
)
from src.engine.global_single_order_auction import select_prepared_global_auction
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent
from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
from src.solve.solver import CurrentFamilyProbabilityAuthority
from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS

UTC = timezone.utc


def _family_key(event: OpportunityEvent, payload: Mapping[str, object]) -> str:
    return weather_family_id(
        city=str(payload.get("city") or ""),
        target_date=str(payload.get("target_date") or ""),
        metric=str(payload.get("metric") or "").lower(),
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
    current_probability: Callable[[OpportunityEvent, object, datetime], object | None],
    current_execution: Callable[[object, datetime], object | None],
    current_time_provider: Callable[[], datetime],
    portfolio_state_provider: Callable[[], object] | None = None,
    current_book_epoch_provider: Callable[
        [Mapping[str, object], datetime],
        tuple[Mapping[str, object], CurrentGlobalBookEpoch],
    ]
    | None = None,
) -> GlobalBatchSubmitResult:
    """Prepare all current families, select once, then actuate one claimed owner."""

    if decision_time.tzinfo is None:
        raise ValueError("GLOBAL_AUCTION_DECISION_TIME_NAIVE")
    decision_time = decision_time.astimezone(UTC)
    event_tuple = tuple(events)

    def current_time() -> datetime:
        now = current_time_provider()
        if now.tzinfo is None:
            raise ValueError("GLOBAL_AUCTION_CURRENT_TIME_NAIVE")
        now = now.astimezone(UTC)
        if now < decision_time:
            raise ValueError("GLOBAL_AUCTION_CLOCK_REGRESSION")
        return now

    def reject(reason: str) -> GlobalBatchSubmitResult:
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
        )

    try:
        scope_at = current_time()
        scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=scope_at,
        )
        claimed_by_family = {}
        for event in event_tuple:
            family_key = _family_key(event, payload_reader(event))
            if family_key in claimed_by_family:
                return reject("GLOBAL_CLAIMED_FAMILY_AMBIGUOUS")
            claimed_by_family[family_key] = event

        prepared_by_event = {}
        scope_event_by_family = dict(scope.events_by_family)
        for family_key, scope_event in scope.events_by_family:
            owner = claimed_by_family.get(family_key, scope_event)
            prepared_receipt = prepare_event(scope_event, scope_at)
            prepared = prepared_receipt.prepared_global_family
            if prepared is None:
                return reject(
                    "GLOBAL_PREPARED_FAMILY_INCOMPLETE:"
                    f"{family_key}:{prepared_receipt.reason or 'missing'}"
                )
            prepared_by_event[owner.event_id] = prepared

        probabilities = {
            prepared.probability_witness.family_key: prepared.probability_witness
            for prepared in prepared_by_event.values()
        }
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
        # Probability preparation + the complete public-CLOB sweep can legitimately
        # take longer than one quote TTL.  The family set is not a price quote: renew
        # its observation time by re-enumerating the exact current scope immediately
        # before selection.  Identity must stay byte-identical; a changed family or
        # probability carrier still invalidates the whole epoch.
        selection_at = current_time()
        refreshed_scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=selection_at,
        )
        if refreshed_scope.scope_identity != scope.scope_identity:
            return reject("GLOBAL_SCOPE_SUPERSEDED_BEFORE_SELECTION")
        scope = refreshed_scope
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

        def current_scope_identity():
            try:
                checked_at = current_time()
                return scan_current_global_auction_scope(
                    world_conn=world_conn,
                    forecasts_conn=forecast_conn,
                    decision_at_utc=checked_at,
                ).scope_identity
            except Exception:
                return None

        def current_venue_identity():
            try:
                if book_epoch is not None:
                    return book_epoch.current_identity(current_time())
                return current_venue_auction_identity(
                    trade_conn,
                    probability_witnesses=probabilities,
                )
            except Exception:
                return None

        probability_cache: dict[str, object | None] = {}

        def probability_resolver(family_key):
            if family_key in probability_cache:
                return probability_cache[family_key]
            event = scope_event_by_family.get(family_key)
            witness = probabilities.get(family_key)
            resolved = (
                None
                if event is None or witness is None
                else current_probability(event, witness, current_time())
            )
            probability_cache[family_key] = resolved
            return resolved

        def execution_resolver(candidate):
            if book_epoch is not None:
                return book_epoch.execution_authority(
                    candidate,
                    checked_at_utc=current_time(),
                )
            return current_execution(candidate, current_time())

        def wealth_identity():
            try:
                state_now = (
                    portfolio_state_provider() if portfolio_state_provider else None
                )
                return current_portfolio_wealth_witness(
                    trade_conn,
                    decision_at_utc=current_time(),
                    max_age=wealth_age,
                    portfolio_state=state_now,
                ).economic_identity
            except Exception:
                return None

        selected = select_prepared_global_auction(
            prepared_by_event,
            current_scope=scope,
            current_scope_identity_resolver=current_scope_identity,
            venue_universe_identity=venue_identity,
            current_venue_universe_identity_resolver=current_venue_identity,
            universe_max_age=(
                book_epoch.max_age
                if book_epoch is not None
                else FRESHNESS_WINDOW_DEFAULT
            ),
            current_probability_resolver=probability_resolver,
            current_execution_resolver=execution_resolver,
            current_wealth_identity_resolver=wealth_identity,
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
        if winner is None or selected.actuation is None:
            return reject("GLOBAL_WINNER_AWAITS_CLAIM")

        # Selection can take long enough for one q/book/collateral fact to move.
        # Revalidate the complete epoch once more before handing its sole winner to
        # the side-effect path.  A moved loser can never be silently discarded in
        # favour of the old runner-up.
        probability_cache.clear()
        if current_scope_identity() != scope.scope_identity:
            return reject("GLOBAL_SCOPE_SUPERSEDED_BEFORE_ACTUATION")
        if current_venue_identity() != venue_identity:
            return reject("GLOBAL_VENUE_SUPERSEDED_BEFORE_ACTUATION")
        if wealth_identity() != wealth.economic_identity:
            return reject("GLOBAL_WEALTH_SUPERSEDED_BEFORE_ACTUATION")
        for family_key, witness in probabilities.items():
            expected = CurrentFamilyProbabilityAuthority.from_witness(witness)
            if probability_resolver(family_key) != expected:
                return reject("GLOBAL_PROBABILITY_SUPERSEDED_BEFORE_ACTUATION")

        before_calls = venue_submit_count()
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
