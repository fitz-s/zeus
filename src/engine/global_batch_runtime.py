"""Runtime ownership for one current cross-family auction epoch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging
import sqlite3
import time
from typing import Callable, Mapping, Sequence

from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
from src.data.market_topology_rows import prime_frozen_schema_reads
from src.engine.global_auction_universe import (
    CurrentGlobalBookAsset,
    CurrentGlobalBookEpoch,
    current_global_book_epoch_identity,
    current_global_auction_scope_from_events,
    current_portfolio_wealth_witness,
    current_venue_auction_identity,
    scan_current_global_auction_scope,
)
from src.engine.global_single_order_auction import select_prepared_global_auction
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
from src.solve.solver import CurrentFamilyProbabilityAuthority, executable_curve_identity
from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS

UTC = timezone.utc
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlobalWinnerPreflight:
    """Typed, venue-side-effect-free binding of one selected winner."""

    status: str
    binding_token: object | None = None
    replacement_candidate: object | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"STABLE", "CURVE_SUPERSEDED", "BLOCKED"}:
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_STATUS_INVALID")
        if (self.status == "STABLE") != (self.binding_token is not None):
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_TOKEN_INVALID")
        if (self.status == "CURVE_SUPERSEDED") != (
            self.replacement_candidate is not None
        ):
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_REPLACEMENT_INVALID")
        if self.status != "STABLE" and not str(self.reason or "").strip():
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_REASON_MISSING")


@dataclass(frozen=True)
class GlobalPreflightAuthority:
    """Frozen whole-universe authority carried by one one-shot preflight."""

    probability_manifest: tuple[tuple[str, str], ...]
    book_epoch_identity: str
    book_economics_manifest: tuple[tuple[object, ...], ...]
    wealth_witness_identity: str

    def __post_init__(self) -> None:
        if (
            not self.probability_manifest
            or not self.book_epoch_identity
            or not self.book_economics_manifest
            or not self.wealth_witness_identity
        ):
            raise ValueError("GLOBAL_PREFLIGHT_AUTHORITY_INCOMPLETE")


class GlobalOneShotActuator:
    """Consume exactly one final-actuation capability for one batch."""

    def __init__(self, callback: Callable[..., EventSubmissionReceipt]) -> None:
        self._callback = callback
        self._consumed = False

    def consume(self, *args) -> EventSubmissionReceipt:
        if self._consumed:
            raise RuntimeError("GLOBAL_ACTUATION_CAPABILITY_CONSUMED")
        self._consumed = True
        return self._callback(*args)


def _probability_manifest(probabilities: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """Freeze q plus token bindings while allowing only book and wealth to move."""

    return tuple(
        sorted(
            (
                str(family_key),
                str(getattr(witness, "witness_identity", "") or ""),
            )
            for family_key, witness in probabilities.items()
        )
    )


def _book_economics_manifest(
    book_epoch: CurrentGlobalBookEpoch,
) -> tuple[tuple[object, ...], ...]:
    """Compare the complete native YES/NO economy without evidence carriers."""

    rows = []
    for asset in book_epoch.assets:
        curve = asset.curve
        rows.append(
            (
                asset.family_key,
                asset.bin_id,
                asset.condition_id,
                asset.market_event_id,
                asset.side,
                asset.token_id,
                str(curve.fee_model.fee_rate),
                str(curve.min_tick),
                str(curve.min_order_size),
                tuple((str(level.price), str(level.size)) for level in curve.levels),
            )
        )
    manifest = tuple(sorted(rows, key=repr))
    if not manifest:
        raise ValueError("GLOBAL_BOOK_ECONOMICS_MISSING")
    return manifest


def _overlay_current_global_book_epoch(
    book_epoch: CurrentGlobalBookEpoch,
    selected_candidate: object,
    replacement_candidate: object,
) -> CurrentGlobalBookEpoch:
    """Replace only the JIT winner curve in one frozen complete book epoch."""

    identity_fields = (
        "family_key",
        "bin_id",
        "condition_id",
        "side",
        "token_id",
        "probability_witness_identity",
        "resolution_identity",
        "ledger_snapshot_id",
    )
    selected_identity = tuple(
        str(getattr(selected_candidate, field, "") or "")
        for field in identity_fields
    )
    replacement_identity = tuple(
        str(getattr(replacement_candidate, field, "") or "")
        for field in identity_fields
    )
    if not all(selected_identity) or replacement_identity != selected_identity:
        raise ValueError("GLOBAL_JIT_OVERLAY_IDENTITY_MISMATCH")
    selected_key = selected_identity[:5]
    replacement_curve = getattr(replacement_candidate, "executable_cost_curve", None)
    replacement_at = getattr(replacement_candidate, "book_captured_at_utc", None)
    selected_at = getattr(selected_candidate, "book_captured_at_utc", None)
    if (
        replacement_curve is None
        or replacement_curve.side != selected_key[3]
        or replacement_curve.token_id != selected_key[4]
        or executable_curve_identity(replacement_curve)
        != str(
            getattr(replacement_candidate, "execution_curve_identity", "") or ""
        )
        or replacement_at is None
        or replacement_at.tzinfo is None
        or selected_at is None
        or selected_at.tzinfo is None
        or replacement_at < selected_at
    ):
        raise ValueError("GLOBAL_JIT_OVERLAY_CURVE_INVALID")

    assets: list[CurrentGlobalBookAsset] = []
    matched_asset = 0
    selected_book_hash = ""
    for asset in book_epoch.assets:
        asset_key = (
            asset.family_key,
            asset.bin_id,
            asset.condition_id,
            asset.side,
            asset.token_id,
        )
        if asset_key != selected_key:
            assets.append(asset)
            continue
        if executable_curve_identity(asset.curve) != str(
            getattr(selected_candidate, "execution_curve_identity", "") or ""
        ):
            raise ValueError("GLOBAL_JIT_OVERLAY_SELECTED_CURVE_MISMATCH")
        matched_asset += 1
        selected_book_hash = str(asset.curve.book_hash)
        assets.append(
            replace(
                asset,
                curve=replacement_curve,
                captured_at_utc=replacement_at,
            )
        )
    if matched_asset != 1:
        raise ValueError(f"GLOBAL_JIT_OVERLAY_ASSET_CARDINALITY:{matched_asset}")

    states: list[tuple[str, ...]] = []
    matched_state = 0
    for state in book_epoch.asset_states:
        if tuple(state[:5]) != selected_key:
            states.append(state)
            continue
        if (
            len(state) != 8
            or state[5] != "EXECUTABLE"
            or state[6] != selected_book_hash
        ):
            raise ValueError("GLOBAL_JIT_OVERLAY_STATE_INVALID")
        matched_state += 1
        states.append((*state[:6], str(replacement_curve.book_hash), state[7]))
    if matched_state != 1:
        raise ValueError(f"GLOBAL_JIT_OVERLAY_STATE_CARDINALITY:{matched_state}")

    witness_identity = current_global_book_epoch_identity(
        asset_states=states,
        captured_at_utc=book_epoch.captured_at_utc,
    )
    return CurrentGlobalBookEpoch(
        assets=tuple(assets),
        asset_states=tuple(states),
        captured_at_utc=book_epoch.captured_at_utc,
        max_age=book_epoch.max_age,
        witness_identity=witness_identity,
    )


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
    preflight_winner: Callable[
        [OpportunityEvent, object, datetime, GlobalPreflightAuthority],
        GlobalWinnerPreflight,
    ]
    | None = None,
    actuate_preflighted_winner: GlobalOneShotActuator | None = None,
    portfolio_state_provider: Callable[[], object] | None = None,
    current_book_epoch_provider: Callable[
        [Mapping[str, object], datetime],
        tuple[Mapping[str, object], CurrentGlobalBookEpoch],
    ]
    | None = None,
    selection_snapshot_connections: Sequence[sqlite3.Connection] = (),
    current_capital_limit_resolver: Callable[[object, str, str], object]
    | None = None,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
) -> GlobalBatchSubmitResult:
    """Select once from every family holding a current q certificate."""

    if decision_time.tzinfo is None:
        raise ValueError("GLOBAL_AUCTION_DECISION_TIME_NAIVE")
    decision_time = decision_time.astimezone(UTC)
    event_tuple = tuple(events)
    release_selection_snapshot: Callable[[], None] = lambda: None
    batch_started = time.monotonic()
    stage_started = batch_started

    def log_stage(stage: str, *, families: int | None = None) -> None:
        nonlocal stage_started
        now = time.monotonic()
        _LOG.info(
            "global batch stage completed: %s elapsed_s=%.3f total_s=%.3f "
            "events=%d families=%s",
            stage,
            now - stage_started,
            now - batch_started,
            len(event_tuple),
            families if families is not None else "unknown",
        )
        stage_started = now

    def log_no_trade(stage: str, decision: object) -> None:
        counts: dict[str, int] = {}
        for reason in getattr(decision, "rejection_reasons", {}).values():
            key = str(reason or "unknown")
            counts[key] = counts.get(key, 0) + 1
        _LOG.info(
            "global batch no-trade detail: stage=%s reason=%s rejections=%s",
            stage,
            str(getattr(decision, "no_trade_reason", "") or "unknown"),
            dict(sorted(counts.items())),
        )

    def log_winner(
        stage: str,
        selected: object,
        witnesses: Mapping[str, object],
    ) -> None:
        decision = getattr(selected, "decision", None)
        candidate = getattr(decision, "candidate", None)
        if candidate is None:
            return
        family_key = str(getattr(candidate, "family_key", "") or "")
        bin_id = str(getattr(candidate, "bin_id", "") or "")
        side = str(getattr(candidate, "side", "") or "")
        if not family_key or not bin_id or side not in {"YES", "NO"}:
            return
        witness = witnesses.get(family_key)
        q_mean = None
        if witness is not None:
            try:
                column = tuple(witness.bin_ids).index(bin_id)
                yes = witness.yes_q_samples[:, column]
                q_mean = float(yes.mean())
                if side == "NO":
                    q_mean = 1.0 - q_mean
            except (AttributeError, TypeError, ValueError):
                q_mean = None
        _LOG.info(
            "global batch winner detail: stage=%s family=%s bin=%s side=%s "
            "condition=%s token=%s "
            "q_mean=%s shares=%s cost_usd=%s fill_price=%s limit_price=%s "
            "max_spend_usd=%s robust_ev_usd=%.6f robust_dlog=%.12f "
            "capital_efficiency=%.12f candidate=%s",
            stage,
            family_key,
            bin_id,
            side,
            getattr(candidate, "condition_id", "unknown"),
            getattr(candidate, "token_id", "unknown"),
            "unknown" if q_mean is None else f"{q_mean:.9f}",
            getattr(decision, "shares", "unknown"),
            getattr(decision, "cost_usd", "unknown"),
            getattr(decision, "expected_fill_price_before_fee", "unknown"),
            getattr(decision, "limit_price", "unknown"),
            getattr(decision, "max_spend_usd", "unknown"),
            float(getattr(decision, "robust_ev_usd", 0.0) or 0.0),
            float(getattr(decision, "robust_delta_log_wealth", 0.0) or 0.0),
            float(getattr(decision, "capital_efficiency", 0.0) or 0.0),
            getattr(candidate, "candidate_id", "unknown"),
        )

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
        release_schema = prime_frozen_schema_reads(selection_snapshot_connections)
        release_snapshot_only = release_selection_snapshot
        released_schema = False

        def release_schema_snapshot() -> None:
            nonlocal released_schema
            if released_schema:
                return
            released_schema = True
            try:
                release_schema()
            finally:
                release_snapshot_only()

        release_selection_snapshot = release_schema_snapshot
        log_stage("selection_snapshot")
        scope_at = current_time()
        full_scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=scope_at,
        )
        log_stage("scope_scan", families=len(full_scope.events_by_family))
        from src.data.replacement_input_hwm import (
            prime_frozen_replacement_artifact_hwm,
        )

        release_hwm = prime_frozen_replacement_artifact_hwm(
            forecast_conn,
            requests=(
                (
                    str(payload.get("city") or ""),
                    str(payload.get("target_date") or ""),
                    str(payload.get("metric") or ""),
                )
                for _, event in full_scope.events_by_family
                for payload in (payload_reader(event),)
            ),
            decision_time=scope_at,
        )
        release_read_snapshot = release_selection_snapshot
        released_hwm = False

        def release_primed_snapshot() -> None:
            nonlocal released_hwm
            if released_hwm:
                return
            released_hwm = True
            try:
                release_hwm()
            finally:
                release_read_snapshot()

        release_selection_snapshot = release_primed_snapshot
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
        log_stage("prepare_families", families=len(prepared_by_event))
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
        log_stage("book_epoch_initial", families=len(prepared_by_event))
        probability_manifest = _probability_manifest(probabilities)
        # Selection is a comparison over one immutable information vector.  Scope and
        # q are frozen at ``scope_at``; the complete native YES/NO book and wealth
        # witnesses join that vector below.  A later family update belongs to the next
        # epoch.  Only the selected winner is allowed to cross into the side-effect
        # path, where probability, exact book/curve, and free cash are rebuilt JIT.
        wealth_age = timedelta(seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS))

        def select_once(
            attempt_probabilities: Mapping[str, object],
            attempt_book_epoch: CurrentGlobalBookEpoch | None,
            attempt_prepared: Mapping[str, object],
        ):
            selection_at = current_time()
            state = portfolio_state_provider() if portfolio_state_provider else None
            wealth = current_portfolio_wealth_witness(
                trade_conn,
                decision_at_utc=selection_at,
                max_age=wealth_age,
                portfolio_state=state,
            )
            venue_identity = (
                attempt_book_epoch.witness_identity
                if attempt_book_epoch is not None
                else current_venue_auction_identity(
                    trade_conn,
                    probability_witnesses=attempt_probabilities,
                )
            )

            def probability_resolver(family_key):
                witness = attempt_probabilities.get(family_key)
                return (
                    CurrentFamilyProbabilityAuthority.from_witness(witness)
                    if witness is not None
                    else None
                )

            def execution_resolver(candidate):
                if attempt_book_epoch is not None:
                    return attempt_book_epoch.execution_authority(
                        candidate,
                        checked_at_utc=selection_at,
                    )
                return current_execution(candidate, selection_at)

            return select_prepared_global_auction(
                attempt_prepared,
                selection_epoch_identity=selection_epoch_identity,
                selection_cut_at_utc=scope_at,
                current_scope=scope,
                current_scope_identity_resolver=lambda: scope.scope_identity,
                venue_universe_identity=venue_identity,
                current_venue_universe_identity_resolver=lambda: venue_identity,
                universe_max_age=(
                    attempt_book_epoch.max_age
                    if attempt_book_epoch is not None
                    else FRESHNESS_WINDOW_DEFAULT
                ),
                current_probability_resolver=probability_resolver,
                current_execution_resolver=execution_resolver,
                current_wealth_identity_resolver=lambda: wealth.economic_identity,
                wealth_witness=wealth,
                capital_limit_usd=wealth.spendable_cash_usd,
                fractional_kelly_multiplier=fractional_kelly_multiplier,
                decision_at_utc=selection_at,
                book_epoch=attempt_book_epoch,
                current_capital_limit_resolver=current_capital_limit_resolver,
            )

        selected = select_once(probabilities, book_epoch, prepared_by_event)
        log_stage("select_initial", families=len(prepared_by_event))
        if selected.decision.candidate is None:
            log_no_trade("select_initial", selected.decision)
            return reject(
                "GLOBAL_AUCTION_NO_TRADE:"
                f"{selected.decision.no_trade_reason or 'unknown'}"
            )
        log_winner("select_initial", selected, probabilities)
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

        binding_token = None
        if preflight_winner is not None:
            if actuate_preflighted_winner is None:
                return reject("GLOBAL_PREFLIGHT_ACTUATOR_MISSING")
            if current_book_epoch_provider is None or book_epoch is None:
                return reject("GLOBAL_PREFLIGHT_BOOK_PROVIDER_MISSING")
            probabilities_fence, book_epoch_fence = current_book_epoch_provider(
                probabilities,
                current_time(),
            )
            log_stage("book_epoch_fence", families=len(prepared_by_event))
            if _probability_manifest(probabilities_fence) != probability_manifest:
                return reject("GLOBAL_PREFLIGHT_PROBABILITY_CUT_DRIFT")
            fence_economics = _book_economics_manifest(book_epoch_fence)
            prepared_fence = {
                event_id: replace(
                    prepared,
                    probability_witness=probabilities_fence[
                        prepared.probability_witness.family_key
                    ],
                )
                for event_id, prepared in prepared_by_event.items()
            }
            # The fence is also the single permitted re-auction even when books are
            # economically unchanged: selection-time wealth must be reacquired at the
            # same late boundary as the whole-universe book.
            selected = select_once(
                probabilities_fence,
                book_epoch_fence,
                prepared_fence,
            )
            log_stage("select_fence", families=len(prepared_by_event))
            if selected.decision.candidate is None:
                log_no_trade("select_fence", selected.decision)
                return reject(
                    "GLOBAL_REAUCTION_NO_TRADE:"
                    f"{selected.decision.no_trade_reason or 'unknown'}"
                )
            log_winner("select_fence", selected, probabilities_fence)
            winner_id = selected.winner_event_id
            winner = next(
                (event for event in event_tuple if event.event_id == winner_id),
                None,
            )
            if selected.actuation is None:
                return reject("GLOBAL_REAUCTION_ACTUATION_MISSING")
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
                    return reject("GLOBAL_REAUCTION_WINNER_IDENTITY_MISSING")
                return reject(
                    "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM",
                    next_claim_event=_next_claim_carrier(
                        target,
                        targeted_at=current_time(),
                        economic_identity=selected.actuation.economic_identity,
                        payload=payload_reader(target),
                    ),
                )
            preflight_authority = GlobalPreflightAuthority(
                probability_manifest=probability_manifest,
                book_epoch_identity=book_epoch_fence.witness_identity,
                book_economics_manifest=fence_economics,
                wealth_witness_identity=selected.actuation.wealth_witness_identity,
            )
            before_preflight = venue_submit_count()
            preflight = preflight_winner(
                winner,
                selected.actuation,
                current_time(),
                preflight_authority,
            )
            log_stage("winner_preflight", families=len(prepared_by_event))
            if venue_submit_count() != before_preflight:
                return reject("GLOBAL_PREFLIGHT_VENUE_SIDE_EFFECT")
            if preflight.status == "CURVE_SUPERSEDED":
                try:
                    book_epoch_1 = _overlay_current_global_book_epoch(
                        book_epoch_fence,
                        selected.decision.candidate,
                        preflight.replacement_candidate,
                    )
                except ValueError as exc:
                    return reject(f"GLOBAL_REAUCTION_OVERLAY_FAILED:{exc}")
                probabilities_1 = probabilities_fence
                prepared_1 = {
                    event_id: replace(
                        prepared,
                        probability_witness=probabilities_1[
                            prepared.probability_witness.family_key
                        ],
                    )
                    for event_id, prepared in prepared_by_event.items()
                }
                selected = select_once(probabilities_1, book_epoch_1, prepared_1)
                if selected.decision.candidate is None:
                    log_no_trade("select_curve_overlay", selected.decision)
                    return reject(
                        "GLOBAL_REAUCTION_NO_TRADE:"
                        f"{selected.decision.no_trade_reason or 'unknown'}"
                    )
                winner_id = selected.winner_event_id
                winner = next(
                    (event for event in event_tuple if event.event_id == winner_id),
                    None,
                )
                if selected.actuation is None:
                    return reject("GLOBAL_REAUCTION_ACTUATION_MISSING")
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
                        return reject("GLOBAL_REAUCTION_WINNER_IDENTITY_MISSING")
                    return reject(
                        "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM",
                        next_claim_event=_next_claim_carrier(
                            target,
                            targeted_at=current_time(),
                            economic_identity=selected.actuation.economic_identity,
                            payload=payload_reader(target),
                        ),
                    )
                preflight_authority = GlobalPreflightAuthority(
                    probability_manifest=probability_manifest,
                    book_epoch_identity=book_epoch_1.witness_identity,
                    book_economics_manifest=_book_economics_manifest(book_epoch_1),
                    wealth_witness_identity=selected.actuation.wealth_witness_identity,
                )
                before_second_preflight = venue_submit_count()
                preflight = preflight_winner(
                    winner,
                    selected.actuation,
                    current_time(),
                    preflight_authority,
                )
                if venue_submit_count() != before_second_preflight:
                    return reject("GLOBAL_PREFLIGHT_VENUE_SIDE_EFFECT")
                if preflight.status != "STABLE":
                    return reject(
                        "GLOBAL_REAUCTION_EXHAUSTED:"
                        f"{preflight.reason or preflight.status}"
                    )
            elif preflight.status != "STABLE":
                return reject(
                    f"GLOBAL_WINNER_PREFLIGHT_BLOCKED:{preflight.reason}"
                )
            binding_token = preflight.binding_token

        before_calls = venue_submit_count()
        release_selection_snapshot()
        winner_receipt = (
            actuate_preflighted_winner.consume(
                winner,
                selected.actuation,
                current_time(),
                binding_token,
                preflight_authority,
            )
            if preflight_winner is not None
            else actuate_winner(winner, selected.actuation, current_time())
        )
        venue_delta = venue_submit_count() - before_calls
        if venue_delta not in {0, 1}:
            raise RuntimeError("GLOBAL_ACTUATION_VENUE_COUNT_INVALID")
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
            venue_submit_count=venue_delta,
        )
    except Exception as exc:  # noqa: BLE001 - one authority fault invalidates epoch
        return reject(f"GLOBAL_AUCTION_FAILED:{type(exc).__name__}:{exc}")
