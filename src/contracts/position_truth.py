# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md (PR B — typed model scaffold; consumed by PR C/D)
"""Typed objects for chain/local position truth.

Source-of-truth law (audit § 1, target architecture):

    Venue facts + immutable local intent
      → venue_commands / venue_order_facts / venue_trade_facts / venue_position_facts
      → canonical position events
      → position_current / LocalProjection
      → runtime Position adapter
      → monitor / exit / reporting / learning

This module declares the typed objects that replace string/sentinel-based
flows currently in `src/state/chain_reconciliation.py` and
`src/state/portfolio.py`. The dataclasses are deliberately small and
immutable — they are the type boundaries the refactor depends on.

PR B introduces the types. Producers/consumers wire up in PR C (reconcile)
and PR D (projection + runtime). Until then, these classes are reachable
by import but not yet emitted by main runtime paths.

After the refactor lands, these laws become unconstructable:

    - No chain-only fake `Position` (replaced by `ChainOnlyFact`).
    - No arbitrary `Position.state` outside `LifecycleState` (canonical
      review event paths carry the substate).
    - No aggregate chain balance masquerading as verified fill
      (`VenuePositionFact` with weaker authority is the recovery channel).
    - No direct projection mutation without canonical event append
      (every venue observation is a typed fact).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Authority labels                                                            #
# --------------------------------------------------------------------------- #

class FillAuthority(str, Enum):
    """How strongly we know a position's fill economics.

    Mirrors and extends the legacy string constants in
    `src/state/portfolio.py` (FILL_AUTHORITY_*). The enum here is the new
    type boundary; the legacy strings remain wire-compatible until PR E
    removes them.
    """

    NONE = "none"
    OPTIMISTIC_SUBMITTED = "optimistic_submitted"
    VENUE_POSITION_OBSERVED = "venue_position_observed"  # NEW (PR B) — degraded recovery
    VENUE_CONFIRMED_PARTIAL = "venue_confirmed_partial"
    VENUE_CONFIRMED_FULL = "venue_confirmed_full"
    CANCELLED_REMAINDER = "cancelled_remainder"
    SETTLED = "settled"


class CausalityStatus(str, Enum):
    """Causality status for rescue / recovery facts.

    Used by the learning/training boundary (Finding 9): only
    CausalityStatus.OK rescue facts may produce a `VerifiedTrainingExample`.
    """

    OK = "OK"
    UNKNOWN = "UNKNOWN"
    UNVERIFIED = "UNVERIFIED"


class RecoveryAuthority(str, Enum):
    """Strength of a recovery / rescue fact (Finding 5).

    BALANCE_ONLY means: chain shows balance for this token, AND a local
    intent / command exists, BUT no exact venue trade fact links the
    intent to the balance. Tradable as active exposure, but never
    fill-verified; never `training_eligible=true`.

    TRADE_VERIFIED means: exact venue trade fact exists linking the
    intent to the venue fill economics. Eligible for training and full
    P&L attribution.
    """

    BALANCE_ONLY = "balance_only"
    TRADE_VERIFIED = "trade_verified"


# --------------------------------------------------------------------------- #
# Core typed facts                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LocalIntent:
    """Local decision to submit / cancel / exit / redeem.

    Immutable. May exist BEFORE the venue acknowledges the side-effect.
    Persisted via `venue_submission_envelopes` in current main; the typed
    boundary here will become the canonical seed in PR C.
    """

    decision_id: str
    snapshot_id: str
    position_id: str
    market_id: str
    condition_id: str
    token_id: str  # held-side token id (YES or NO depending on direction)
    direction: str  # "buy_yes" / "buy_no" — UNKNOWN is forbidden
    intended_notional_usd: float
    submitted_limit_price: float
    created_at: str  # ISO timestamp

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.direction not in ("buy_yes", "buy_no"):
            raise ValueError(
                f"LocalIntent.direction must be buy_yes or buy_no, got {self.direction!r}. "
                "Synthetic chain-only inventory MUST NOT enter LocalIntent."
            )


@dataclass(frozen=True)
class VenueOrderFact:
    """Venue-returned order status fact (append-only).

    Mirrors `venue_order_facts` rows; this dataclass is the in-memory
    representation used by consumers above the persistence layer.
    """

    venue_order_id: str
    order_state: str  # "open" / "matched" / "filled" / "cancelled" / "rejected"
    accepted_at: Optional[str]
    observed_at: str
    raw_payload_hash: str = ""


@dataclass(frozen=True)
class VenueTradeFact:
    """Venue-returned fill / trade fact (append-only).

    Mirrors `venue_trade_facts`. Required to set
    `FillAuthority.VENUE_CONFIRMED_*` — aggregate balance alone is NOT
    sufficient (Finding 5).
    """

    venue_trade_id: str
    venue_order_id: str
    fill_state: str  # "matched" / "filled" / "partially_filled" / "failed"
    filled_size: float
    avg_fill_price: float
    observed_at: str
    authority: FillAuthority


@dataclass(frozen=True)
class VenuePositionFact:
    """Venue / chain balance snapshot for a single token (append-only).

    Independent of any local intent. Carries `snapshot_completeness` so
    consumers can distinguish CHAIN_EMPTY from CHAIN_UNKNOWN at the fact
    level (Finding 1).
    """

    token_id: str
    condition_id: str
    size: float
    avg_price: float
    cost_basis: float
    snapshot_id: str
    snapshot_completeness: str  # ChainSnapshotCompleteness value
    observed_at: str


# --------------------------------------------------------------------------- #
# Exceptional / review facts                                                  #
# --------------------------------------------------------------------------- #


class ChainOnlyReviewState(str, Enum):
    """Lifecycle status for a `ChainOnlyFact` (Finding D1, Part-2 audit, 2026-05-27).

    Replaces the implicit "any chain_only suppression row blocks entries
    forever" semantics with a typed lifecycle that mirrors the operator
    workflow:

      UNRESOLVED   — chain-only token detected; entry gate fires.
      EXPIRED      — chain-only persisted past the 48h review window;
                     no longer freezes unrelated new entries, but remains
                     flagged for operator/reconciliation attention.
      ACKNOWLEDGED — operator has reviewed and chosen to keep the fact
                     active (e.g. waiting for redeem); equivalent to
                     UNRESOLVED for gating purposes but reduces noise in
                     ops dashboards.
      RESOLVED     — operator cleared the fact (suppression_reason flipped
                     to "operator_quarantine_clear") or the token settled
                     (`settled_position`). Entry gate does NOT fire.
    """

    UNRESOLVED = "unresolved"
    EXPIRED = "expired"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


CHAIN_ONLY_REVIEW_WINDOW_HOURS: float = 48.0


@dataclass(frozen=True)
class ChainOnlyFact:
    """Venue token inventory with NO matching local intent (Finding 3).

    Replaces the synthetic `Position` constructors at
    `src/state/chain_reconciliation.py:~1055` and
    `src/state/portfolio.py:_chain_only_quarantine_position_from_row`.

    These tokens are review queue entries, not tradable positions. They
    block entry only via an explicit review gate, not by polluting the
    runtime portfolio.

    PR D1 (Finding D1, Part-2 audit, 2026-05-27): adds `review_state` so
    the entry gate and operator dashboards can discriminate unresolved
    (entry-blocking) from resolved/expired (informational) facts.
    `review_state` is derived from the underlying `suppression_reason` +
    `first_seen_at` age by `src.state.portfolio._chain_only_fact_from_row`.
    """

    token_id: str
    condition_id: str
    size: float
    avg_price: float
    cost_basis: float
    first_seen_at: str
    last_seen_at: str
    # PR D1: lifecycle status; default UNRESOLVED so legacy producers
    # that don't populate this field still block entries (fail-safe).
    review_state: ChainOnlyReviewState = ChainOnlyReviewState.UNRESOLVED
    # Chain-only inventory is unknown exposure and blocks new entries globally.
    # Entry-proof review facts for a represented local position use
    # "position_only": the position must not be auto-managed, but unrelated new
    # fully proven entries are not frozen.
    entry_block_scope: str = "global"

    @property
    def is_review_required(self) -> bool:
        return True

    @property
    def blocks_entry(self) -> bool:
        """True iff this fact should block new entries this cycle.

        Only fresh unresolved/operator-acknowledged facts block unrelated new
        entries. EXPIRED remains visible as review debt, but stale review debt
        is not current chain truth and must not permanently freeze the live
        entry engine.
        """
        return (
            self.review_state
            in {ChainOnlyReviewState.UNRESOLVED, ChainOnlyReviewState.ACKNOWLEDGED}
            and self.entry_block_scope != "position_only"
        )

    @property
    def blocks_position_management(self) -> bool:
        return self.review_state != ChainOnlyReviewState.RESOLVED


@dataclass(frozen=True)
class LocalIntentWithoutVenueAck:
    """Local intent created but venue did not acknowledge before crash / reload."""

    intent: LocalIntent
    last_command_state: str  # e.g. "submitted_no_ack"


@dataclass(frozen=True)
class VenueOrderFactWithoutProjection:
    """Venue order fact exists, but the local projection has no row to fold it into.

    Surfaced during crash recovery; consumed by command_recovery / exchange
    reconciliation to either rebuild the projection or quarantine for review.
    """

    order_fact: VenueOrderFact
    matching_command_id: Optional[str]


@dataclass(frozen=True)
class VenuePositionFactWithoutIntent:
    """Venue position observed but no local intent exists for the token.

    Promotion path: emit `ChainOnlyFact` (review queue). Never construct a
    `Position`.
    """

    position_fact: VenuePositionFact


@dataclass(frozen=True)
class PartialFillFact:
    """A venue trade fact whose filled size is strictly less than submitted size."""

    trade_fact: VenueTradeFact
    submitted_size: float
    remainder_size: float


@dataclass(frozen=True)
class CancelRemainderFact:
    """Cancellation of an order's unfilled remainder (post-partial-fill)."""

    venue_order_id: str
    cancelled_size: float
    observed_at: str


@dataclass(frozen=True)
class SettlementFact:
    """Market settlement fact: outcome known, position resolved economically."""

    market_id: str
    condition_id: str
    outcome: str  # "YES" / "NO" / "VOIDED"
    settled_at: str


@dataclass(frozen=True)
class RedeemFact:
    """CTF redeem fact: token converted to USDC."""

    token_id: str
    condition_id: str
    redeemed_size: float
    redeemed_at: str
    tx_hash: str = ""


@dataclass(frozen=True)
class ApiSnapshotCompleteness:
    """Per-call snapshot completeness signal (Finding 1).

    Distinct from per-position visibility. Consumed by the per-cycle
    classifier `src/state/chain_state.py.classify_chain_state()`.
    """

    fetched_at: Optional[str]
    completeness: str  # "chain_synced" / "chain_empty" / "chain_unknown"


@dataclass(frozen=True)
class RecoveryGapFact:
    """A position with an inferred-but-not-proven fill linkage (Finding 5).

    The recovery_authority field expresses how strong the linkage is.
    Consumers that produce training rows must reject any RecoveryGapFact
    whose `recovery_authority != TRADE_VERIFIED`.
    """

    intent: LocalIntent
    position_fact: VenuePositionFact
    recovery_authority: RecoveryAuthority
    causality_status: CausalityStatus
    notes: str = ""

    @property
    def training_eligible(self) -> bool:
        return (
            self.recovery_authority == RecoveryAuthority.TRADE_VERIFIED
            and self.causality_status == CausalityStatus.OK
        )


# --------------------------------------------------------------------------- #
# Canonical event grammar (PR C/D will emit these via                         #
# src/engine/lifecycle_events.py)                                             #
# --------------------------------------------------------------------------- #


class CanonicalPositionEventKind(str, Enum):
    """The closed grammar of durable lifecycle event types.

    Each value names an event row that may appear in `position_events`.
    Producers must NOT invent new strings; readers must NOT accept strings
    outside this enum.

    PR #352 (Part-3 audit Finding 1, 2026-05-27): this enum is the SINGLE wire
    vocabulary for `position_events.event_type`. Values are the exact UPPERCASE
    wire strings the DB CHECK accepts and the runtime builders emit — there is
    no lowercase variant. `tests/state/test_inv_position_event_wire_grammar.py`
    asserts in CI that `{k.value} == position_events.event_type CHECK set` and
    that every event_type literal emitted by src/engine/lifecycle_events.py is a
    member here, so a wire string added in one place without the others fails
    the build. (The earlier lowercase/aspirational member set was unused.)
    """

    POSITION_OPEN_INTENT = "POSITION_OPEN_INTENT"
    ENTRY_ORDER_POSTED = "ENTRY_ORDER_POSTED"
    ENTRY_ORDER_FILLED = "ENTRY_ORDER_FILLED"
    ENTRY_ORDER_VOIDED = "ENTRY_ORDER_VOIDED"
    ENTRY_ORDER_REJECTED = "ENTRY_ORDER_REJECTED"
    DAY0_WINDOW_ENTERED = "DAY0_WINDOW_ENTERED"
    CHAIN_SYNCED = "CHAIN_SYNCED"
    CHAIN_SIZE_CORRECTED = "CHAIN_SIZE_CORRECTED"
    CHAIN_QUARANTINED = "CHAIN_QUARANTINED"
    MONITOR_REFRESHED = "MONITOR_REFRESHED"
    EXIT_INTENT = "EXIT_INTENT"
    EXIT_ORDER_POSTED = "EXIT_ORDER_POSTED"
    EXIT_ORDER_FILLED = "EXIT_ORDER_FILLED"
    EXIT_ORDER_VOIDED = "EXIT_ORDER_VOIDED"
    EXIT_ORDER_REJECTED = "EXIT_ORDER_REJECTED"
    EXIT_RETRY_RELEASED = "EXIT_RETRY_RELEASED"
    SETTLED = "SETTLED"
    ADMIN_VOIDED = "ADMIN_VOIDED"
    MANUAL_OVERRIDE_APPLIED = "MANUAL_OVERRIDE_APPLIED"
    VENUE_POSITION_OBSERVED = "VENUE_POSITION_OBSERVED"  # PR B — degraded recovery
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # PR #352 F4 — durable size-mismatch / chain-only review


__all__ = [
    "FillAuthority",
    "CausalityStatus",
    "RecoveryAuthority",
    "ChainOnlyReviewState",
    "CHAIN_ONLY_REVIEW_WINDOW_HOURS",
    "LocalIntent",
    "VenueOrderFact",
    "VenueTradeFact",
    "VenuePositionFact",
    "ChainOnlyFact",
    "LocalIntentWithoutVenueAck",
    "VenueOrderFactWithoutProjection",
    "VenuePositionFactWithoutIntent",
    "PartialFillFact",
    "CancelRemainderFact",
    "SettlementFact",
    "RedeemFact",
    "ApiSnapshotCompleteness",
    "RecoveryGapFact",
    "CanonicalPositionEventKind",
]
