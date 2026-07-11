"""Pure cross-family coordinator for one current executable order.

This module has no venue or DB imports.  It joins already-prepared complete
family simplexes into one auction, materializes every native full-depth order,
and delegates the terminal-wealth objective to ``select_global_single_order``.
Actuation and JIT recapture remain outside this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    CurrentGlobalAuctionScope,
    global_universe_witness_from_scope,
)
from src.solve.solver import (
    CurrentExecutionAuthority,
    CurrentFamilyProbabilityAuthority,
    GlobalSingleOrderCandidate,
    GlobalSingleOrderDecision,
    PortfolioWealthWitness,
    global_candidate_from_native,
    select_global_single_order,
)


@dataclass(frozen=True)
class PreparedGlobalAuctionResult:
    """One selection result plus the event that may own later actuation."""

    decision: GlobalSingleOrderDecision
    winner_event_id: str | None
    actuation: "GlobalSingleOrderActuation | None" = None

    def __post_init__(self) -> None:
        if (self.decision.candidate is None) != (self.winner_event_id is None):
            raise ValueError("global auction winner event must match the trade decision")
        if (self.winner_event_id is None) != (self.actuation is None):
            raise ValueError("global auction actuation must match the unique winner")
        if self.actuation is not None and (
            self.actuation.decision != self.decision
            or self.actuation.winner_event_id != self.winner_event_id
        ):
            raise ValueError("global auction result and actuation disagree")


def global_single_order_actuation_identity(
    *,
    decision: GlobalSingleOrderDecision,
    winner_event_id: str,
    universe_witness_identity: str,
    wealth_witness_identity: str,
    selection_epoch_identity: str,
    selection_cut_at_utc: datetime,
    decision_at_utc: datetime,
) -> str:
    candidate = decision.candidate
    if candidate is None:
        raise ValueError("global actuation requires a trade decision")
    digest = hashlib.sha256()
    for value in (
        winner_event_id,
        universe_witness_identity,
        wealth_witness_identity,
        selection_epoch_identity,
        selection_cut_at_utc.isoformat(),
        decision_at_utc.isoformat(),
        candidate.candidate_id,
        candidate.family_key,
        candidate.condition_id,
        candidate.side,
        candidate.token_id,
        candidate.probability_witness_identity,
        candidate.book_snapshot_id,
        candidate.execution_curve_identity,
        candidate.executable_cost_curve.book_hash,
        decision.shares,
        decision.cost_usd,
        decision.limit_price,
        decision.expected_fill_price_before_fee,
        decision.max_spend_usd,
        repr(decision.robust_delta_log_wealth),
        repr(decision.robust_ev_usd),
        repr(decision.capital_efficiency),
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def global_single_order_economic_identity(
    *,
    decision: GlobalSingleOrderDecision,
    probability_witness: Any,
    wealth_economic_identity: str,
) -> str:
    """Bind one order's economics without observation or epoch clocks."""

    candidate = decision.candidate
    if candidate is None or not str(wealth_economic_identity or "").strip():
        raise ValueError("global economic identity requires a trade and current wealth")
    if getattr(probability_witness, "family_key", None) != candidate.family_key:
        raise ValueError("global economic identity probability family mismatch")
    curve = candidate.executable_cost_curve
    digest = hashlib.sha256()
    for value in (
        candidate.family_key,
        candidate.bin_id,
        candidate.condition_id,
        candidate.side,
        candidate.token_id,
        candidate.resolution_identity,
        probability_witness.family_binding_identity,
        probability_witness.sample_matrix_identity,
        probability_witness.q_version,
        probability_witness.band_alpha,
        probability_witness.band_basis,
        wealth_economic_identity,
        curve.book_hash,
        curve.fee_model.fee_rate,
        curve.min_tick,
        curve.min_order_size,
        curve.quote_ttl.total_seconds(),
        decision.shares,
        decision.cost_usd,
        decision.limit_price,
        decision.expected_fill_price_before_fee,
        decision.max_spend_usd,
        repr(decision.robust_delta_log_wealth),
        repr(decision.robust_ev_usd),
        repr(decision.capital_efficiency),
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    for level in curve.levels:
        digest.update(str(level.price).encode("utf-8"))
        digest.update(b"\x1e")
        digest.update(str(level.size).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


@dataclass(frozen=True)
class GlobalSingleOrderActuation:
    """Certificate-bound handoff from the global selector to one live event."""

    decision: GlobalSingleOrderDecision
    winner_event_id: str
    universe_witness_identity: str
    wealth_witness_identity: str
    selection_epoch_identity: str
    probability_witness: Any
    selection_cut_at_utc: datetime
    decision_at_utc: datetime
    actuation_identity: str
    wealth_economic_identity: str
    economic_identity: str

    def __post_init__(self) -> None:
        if (
            self.decision.candidate is None
            or not self.winner_event_id
            or not self.universe_witness_identity
            or not self.wealth_witness_identity
            or not self.selection_epoch_identity
            or self.selection_cut_at_utc.tzinfo is None
            or self.decision_at_utc.tzinfo is None
            or self.selection_cut_at_utc > self.decision_at_utc
            or getattr(self.probability_witness, "witness_identity", None)
            != self.decision.candidate.probability_witness_identity
        ):
            raise ValueError("global actuation authority is incomplete")
        expected = global_single_order_actuation_identity(
            decision=self.decision,
            winner_event_id=self.winner_event_id,
            universe_witness_identity=self.universe_witness_identity,
            wealth_witness_identity=self.wealth_witness_identity,
            selection_epoch_identity=self.selection_epoch_identity,
            selection_cut_at_utc=self.selection_cut_at_utc,
            decision_at_utc=self.decision_at_utc,
        )
        if self.actuation_identity != expected:
            raise ValueError("global actuation identity mismatch")
        economic = global_single_order_economic_identity(
            decision=self.decision,
            probability_witness=self.probability_witness,
            wealth_economic_identity=self.wealth_economic_identity,
        )
        if self.economic_identity != economic:
            raise ValueError("global actuation economic identity mismatch")


def _no_trade(reason: str) -> PreparedGlobalAuctionResult:
    return PreparedGlobalAuctionResult(
        decision=GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
        ),
        winner_event_id=None,
        actuation=None,
    )


def select_prepared_global_auction(
    prepared_by_event: Mapping[str, Any],
    *,
    selection_epoch_identity: str,
    selection_cut_at_utc: datetime,
    current_scope: CurrentGlobalAuctionScope,
    current_scope_identity_resolver: Callable[[], str | None],
    venue_universe_identity: str,
    current_venue_universe_identity_resolver: Callable[[], str | None],
    universe_max_age: timedelta,
    current_probability_resolver: Callable[
        [str], CurrentFamilyProbabilityAuthority | None
    ],
    current_execution_resolver: Callable[
        [GlobalSingleOrderCandidate], CurrentExecutionAuthority | None
    ],
    current_wealth_identity_resolver: Callable[[], str | None],
    wealth_witness: PortfolioWealthWitness,
    capital_limit_usd: Decimal,
    decision_at_utc: datetime,
    book_epoch: CurrentGlobalBookEpoch | None = None,
    current_capital_limit_resolver: Callable[
        [GlobalSingleOrderCandidate, str, str], Decimal
    ]
    | None = None,
) -> PreparedGlobalAuctionResult:
    """Compare every prepared family and return at most one current winner.

    A family is the probability authority, while an event is only the later
    actuation owner.  Therefore exactly one prepared event may represent each
    family in an epoch.  A malformed seed is fatal to the epoch: silently
    dropping it would shrink the feasible set and make ``global`` false.
    """

    if (
        not str(selection_epoch_identity or "").strip()
        or selection_cut_at_utc.tzinfo is None
        or selection_cut_at_utc > decision_at_utc
    ):
        return _no_trade("GLOBAL_SELECTION_EPOCH_IDENTITY_MISSING")
    probability_witnesses = {}
    event_by_family: dict[str, str] = {}
    candidates: list[GlobalSingleOrderCandidate] = []
    for event_id, prepared in sorted(prepared_by_event.items()):
        event_key = str(event_id or "").strip()
        probability = getattr(prepared, "probability_witness", None)
        family_key = str(getattr(probability, "family_key", "") or "").strip()
        if not event_key or probability is None or not family_key:
            return _no_trade("GLOBAL_PREPARED_FAMILY_INVALID")
        if family_key in event_by_family:
            return _no_trade("GLOBAL_FAMILY_EVENT_AMBIGUOUS")
        event_by_family[family_key] = event_key
        probability_witnesses[family_key] = probability
        if book_epoch is None:
            for seed in getattr(prepared, "candidate_seeds", ()):
                try:
                    candidates.append(
                        global_candidate_from_native(
                            seed.native_candidate,
                            probability_witness=probability,
                            ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
                            book_captured_at_utc=seed.book_captured_at_utc,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one missing asset invalidates globality
                    return _no_trade(
                        "GLOBAL_CANDIDATE_MATERIALIZATION_FAILED:"
                        f"{type(exc).__name__}:{exc}"
                    )

    if book_epoch is not None:
        if venue_universe_identity != book_epoch.witness_identity:
            return _no_trade("GLOBAL_BOOK_EPOCH_IDENTITY_MISMATCH")
        candidates = []
        for asset in book_epoch.assets:
            probability = probability_witnesses.get(asset.family_key)
            if probability is None:
                return _no_trade("GLOBAL_BOOK_FAMILY_PROBABILITY_MISSING")
            native = SimpleNamespace(
                no_trade_reason=None,
                executable_cost_curve=asset.curve,
                family_key=asset.family_key,
                bin_id=asset.bin_id,
                condition_id=asset.condition_id,
                side=asset.side,
                token_id=asset.token_id,
                hypothesis_id=(
                    f"GLOBAL_BOOK:{asset.family_key}:{asset.bin_id}:{asset.side}"
                ),
            )
            try:
                candidates.append(
                    global_candidate_from_native(
                        native,
                        probability_witness=probability,
                        ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
                        book_captured_at_utc=asset.captured_at_utc,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one malformed asset invalidates globality
                return _no_trade(
                    "GLOBAL_BOOK_CANDIDATE_MATERIALIZATION_FAILED:"
                    f"{type(exc).__name__}:{exc}"
                )

    try:
        universe_witness = global_universe_witness_from_scope(
            current_scope,
            probability_witnesses=probability_witnesses,
            venue_universe_identity=venue_universe_identity,
            max_age=universe_max_age,
        )
    except ValueError as exc:
        return _no_trade(str(exc))

    def _current_universe_identity() -> str | None:
        try:
            if current_scope_identity_resolver() != current_scope.scope_identity:
                return None
            if current_venue_universe_identity_resolver() != venue_universe_identity:
                return None
        except Exception:  # noqa: BLE001 - authority loss means no current universe
            return None
        return universe_witness.witness_identity

    def _candidate_capital_limit(candidate: GlobalSingleOrderCandidate) -> Decimal:
        if current_capital_limit_resolver is None:
            return capital_limit_usd
        if book_epoch is None:
            raise ValueError("GLOBAL_CAPITAL_LIMIT_BOOK_EPOCH_MISSING")
        asset = book_epoch.asset_by_key.get(
            (
                candidate.family_key,
                candidate.bin_id,
                candidate.side,
                candidate.token_id,
            )
        )
        owner_event_id = event_by_family.get(candidate.family_key)
        if asset is None or owner_event_id is None:
            raise ValueError("GLOBAL_CAPITAL_LIMIT_SCOPE_MISSING")
        return current_capital_limit_resolver(
            candidate,
            asset.market_event_id,
            owner_event_id,
        )

    decision = select_global_single_order(
        tuple(candidates),
        probability_witnesses=probability_witnesses,
        universe_witness=universe_witness,
        current_universe_identity_resolver=_current_universe_identity,
        current_probability_resolver=current_probability_resolver,
        current_execution_resolver=current_execution_resolver,
        current_wealth_identity_resolver=current_wealth_identity_resolver,
        wealth_witness=wealth_witness,
        capital_limit_usd=capital_limit_usd,
        decision_at_utc=decision_at_utc,
        candidate_capital_limit_resolver=_candidate_capital_limit,
    )
    if decision.candidate is None:
        return PreparedGlobalAuctionResult(decision=decision, winner_event_id=None)
    winner_event_id = event_by_family.get(decision.candidate.family_key)
    if winner_event_id is None:
        return _no_trade("GLOBAL_WINNER_EVENT_BINDING_MISSING")
    return PreparedGlobalAuctionResult(
        decision=decision,
        winner_event_id=winner_event_id,
        actuation=GlobalSingleOrderActuation(
            decision=decision,
            winner_event_id=winner_event_id,
            universe_witness_identity=universe_witness.witness_identity,
            wealth_witness_identity=wealth_witness.witness_identity,
            selection_epoch_identity=selection_epoch_identity,
            probability_witness=probability_witnesses[
                decision.candidate.family_key
            ],
            selection_cut_at_utc=selection_cut_at_utc,
            decision_at_utc=decision_at_utc,
            actuation_identity=global_single_order_actuation_identity(
                decision=decision,
                winner_event_id=winner_event_id,
                universe_witness_identity=universe_witness.witness_identity,
                wealth_witness_identity=wealth_witness.witness_identity,
                selection_epoch_identity=selection_epoch_identity,
                selection_cut_at_utc=selection_cut_at_utc,
                decision_at_utc=decision_at_utc,
            ),
            wealth_economic_identity=wealth_witness.economic_identity,
            economic_identity=global_single_order_economic_identity(
                decision=decision,
                probability_witness=probability_witnesses[
                    decision.candidate.family_key
                ],
                wealth_economic_identity=wealth_witness.economic_identity,
            ),
        ),
    )
