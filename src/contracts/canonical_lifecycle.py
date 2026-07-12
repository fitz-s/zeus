# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   §1 reducer semantic contract (consult round-2). Live-DB-grounded canonical value-sets.

"""Canonical lifecycle vocabulary + the single sanctioned ingress normalizers.

This module owns the *typed* canonical state vocabulary for the trading lifecycle
and the ONLY sanctioned conversion from raw venue / API / DB status text into those
types. It exists to convert the codebase's value-coupling (modules branching on raw
`.value` strings, which drift independently) into type-coupling.

INV-CL-1 (single-ingress-normalizer invariant):
  Raw venue status strings ("LIVE", "OPEN", "CANCELED", "PARTIAL", ...) may appear
  ONLY in:
    - this module's normalizer bodies + its tests,
    - ingress adapter code immediately before calling a normalize_*() function,
    - DB CHECK definitions / migration scripts,
    - fixture files.
  Every other module must branch on the typed canonical states / typed predicates,
  never on raw status strings. A CI lint (semgrep/ruff/grep) enforces this so a
  re-introduced synonym becomes a build failure, not a silent runtime drift.

Committed decisions (live-DB grounded, 2026-06-29):
  - Persisted DB-canonical order-fact value stays CANCEL_CONFIRMED; CANCELLED/CANCELED
    are raw venue spellings folded here (no needless DB rename migration).
  - position_lots.state holds only active-exposure values (OPTIMISTIC/CONFIRMED);
    closure/exit/settlement/quarantine are derived elsewhere, not lot states.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


# --------------------------------------------------------------------------- #
# Ingress provenance                                                          #
# --------------------------------------------------------------------------- #

class VenueStatusIngress(StrEnum):
    """Where a raw status string entered the system (for provenance / future
    per-source rules). The normalizers accept it for stability; folding is
    currently source-independent."""

    REST = "REST"
    WS = "WS"
    SDK_SUBMIT_RESPONSE = "SDK_SUBMIT_RESPONSE"
    SDK_CANCEL_RESPONSE = "SDK_CANCEL_RESPONSE"
    DB = "DB"


# --------------------------------------------------------------------------- #
# Canonical typed state vocabularies                                          #
# --------------------------------------------------------------------------- #

class VenueOrderStatus(StrEnum):
    """A2 — venue CLOB order fact. DB-canonical persisted spellings."""

    LIVE = "LIVE"
    PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
    MATCHED = "MATCHED"
    CANCEL_CONFIRMED = "CANCEL_CONFIRMED"
    EXPIRED = "EXPIRED"
    VENUE_WIPED = "VENUE_WIPED"


class VenueTradeStatus(StrEnum):
    """A3 — post-match trade / on-chain confirmation chain."""

    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"


class CommandTruthState(StrEnum):
    """A1 — Zeus-local command / outbox side-effect lifecycle. NOT venue truth.

    Folds the legacy REJECTED+SUBMIT_REJECTED and UNKNOWN+SUBMIT_UNKNOWN_SIDE_EFFECT
    pairs. Venue terminal outcomes (FILLED/CANCELLED/EXPIRED) are NOT command truth —
    they are projected from A2/A3 (project_legacy_command_display)."""

    INTENT_CREATED = "INTENT_CREATED"
    SNAPSHOT_BOUND = "SNAPSHOT_BOUND"
    SUBMITTING = "SUBMITTING"
    SIGNED_PERSISTED = "SIGNED_PERSISTED"
    POSTING = "POSTING"
    POST_ACKED = "POST_ACKED"
    ACKED = "ACKED"
    CANCEL_PENDING = "CANCEL_PENDING"
    REJECTED = "REJECTED"                      # folded from REJECTED + SUBMIT_REJECTED
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"  # folded from UNKNOWN + SUBMIT_UNKNOWN_SIDE_EFFECT
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ExposureState(StrEnum):
    """A4 — per-lot active exposure claim. Live DB uses ONLY these two values;
    closure/exit/settlement/quarantine are derived, not lot states."""

    OPTIMISTIC_EXPOSURE = "OPTIMISTIC_EXPOSURE"
    CONFIRMED_EXPOSURE = "CONFIRMED_EXPOSURE"


class OrderProofClass(StrEnum):
    """Monotonic reduction class produced by the sole VenueOrderTruthReducer —
    the proof strength of an order's fill/no-fill state. Never regresses from a
    stronger to a weaker proof. Owned here; order_truth_reducer.py emits it."""

    TERMINAL_NO_FILL = "TERMINAL_NO_FILL"
    TERMINAL_FILLED = "TERMINAL_FILLED"
    TERMINAL_PARTIAL = "TERMINAL_PARTIAL"
    PARTIAL_WITH_REMAINDER = "PARTIAL_WITH_REMAINDER"
    LIVE_RESTING = "LIVE_RESTING"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ExitProgress(StrEnum):
    """A6 — sell-command progression. A PURE PROJECTION over the exit command's
    order truth (A1/A2/A3), never a stored source of truth. The single owner of
    sell state is the exit command + its venue facts; this view is derived."""

    NONE = ""
    EXIT_INTENT = "exit_intent"
    SELL_OPEN = "sell_open"
    SELL_PARTIALLY_FILLED = "sell_partially_filled"
    SELL_FILLED = "sell_filled"
    RETRY_PENDING = "retry_pending"
    BACKOFF_EXHAUSTED = "backoff_exhausted"
    REVIEW_REQUIRED = "review_required"


class LegacyOrderResultStatus(StrEnum):
    """Coarse executor-return status (the legacy OrderResult.status values).
    Derived from command + order truth; fill size / proof class carry the
    economically-relevant detail. A 'partial' is intentionally NOT a status here
    (that was a cross-axis leak) — it is `filled` with a positive filled_size."""

    FILLED = "filled"
    PENDING = "pending"
    REJECTED = "rejected"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class PositionPhase(StrEnum):
    """A5 — coarse position lifecycle phase. A PROJECTION over command/order/trade/
    chain/settlement truth, not a source. Values match the existing LifecyclePhase
    (which becomes an alias of this in the A5 unification step). UNKNOWN is a
    runtime-only fallback never stored in the position_current.phase DB CHECK.

    T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
    QUARANTINED retired — no writer mints it. A confirmed-fill/chain-absence
    dispute keeps its TRUE phase (ACTIVE/PENDING_EXIT) and the dispute lives
    in a typed ReviewWorkItem (src.contracts.review_work_item), never in this
    enum. The position_current.phase DB CHECK still permits the literal
    'quarantined' until the T5 schema migration (docs/rebuild item 5)."""

    PENDING_ENTRY = "pending_entry"
    ACTIVE = "active"
    DAY0_WINDOW = "day0_window"
    PENDING_EXIT = "pending_exit"
    ECONOMICALLY_CLOSED = "economically_closed"
    SETTLED = "settled"
    VOIDED = "voided"
    ADMIN_CLOSED = "admin_closed"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Raw synonym fold tables (raw strings live ONLY here per INV-CL-1)            #
# --------------------------------------------------------------------------- #

_ORDER_STATUS_FOLD: dict[str, VenueOrderStatus] = {
    "LIVE": VenueOrderStatus.LIVE,
    "RESTING": VenueOrderStatus.LIVE,
    "OPEN": VenueOrderStatus.LIVE,
    "ACCEPTED": VenueOrderStatus.LIVE,
    "UNMATCHED": VenueOrderStatus.LIVE,
    "PARTIAL": VenueOrderStatus.PARTIALLY_MATCHED,
    "PARTIALLY_MATCHED": VenueOrderStatus.PARTIALLY_MATCHED,
    "PARTIALLY_FILLED": VenueOrderStatus.PARTIALLY_MATCHED,
    "MATCHED": VenueOrderStatus.MATCHED,
    "CANCELLED": VenueOrderStatus.CANCEL_CONFIRMED,
    "CANCELED": VenueOrderStatus.CANCEL_CONFIRMED,
    "CANCEL_CONFIRMED": VenueOrderStatus.CANCEL_CONFIRMED,
    "EXPIRED": VenueOrderStatus.EXPIRED,
    "VENUE_WIPED": VenueOrderStatus.VENUE_WIPED,
}

_COMMAND_REJECT_RAW = {"REJECTED", "SUBMIT_REJECTED"}
_COMMAND_UNKNOWN_RAW = {"UNKNOWN", "SUBMIT_UNKNOWN_SIDE_EFFECT"}
_COMMAND_LEGACY_VENUE_OUTCOME_RAW = {"FILLED", "CANCELLED", "EXPIRED"}


def _key(raw: str | None) -> str:
    if raw is None:
        raise ValueError("status string is None")
    key = str(raw).strip().upper()
    if not key:
        raise ValueError("status string is empty")
    return key


# --------------------------------------------------------------------------- #
# The ONLY sanctioned raw -> typed conversions (INV-CL-1)                      #
# --------------------------------------------------------------------------- #

def normalize_venue_order_status(
    raw: str | None,
    *,
    ingress: VenueStatusIngress,
    remaining_size: Decimal | None = None,
    matched_size: Decimal | None = None,  # reserved: future per-ingress disambiguation
) -> VenueOrderStatus:
    """Fold a raw venue/API/DB order status into the canonical VenueOrderStatus.

    Aliases: LIVE/RESTING/OPEN/ACCEPTED/UNMATCHED -> LIVE;
    PARTIAL/PARTIALLY_MATCHED/PARTIALLY_FILLED -> PARTIALLY_MATCHED;
    MATCHED, and FILLED when remaining_size == 0 -> MATCHED;
    CANCELLED/CANCELED/CANCEL_CONFIRMED -> CANCEL_CONFIRMED; EXPIRED; VENUE_WIPED.
    Raises on context-ambiguous FILLED (unknown remainder) and on unmapped text.
    """
    key = _key(raw)
    if key == "FILLED":
        if remaining_size is not None and remaining_size == Decimal("0"):
            return VenueOrderStatus.MATCHED
        raise ValueError(
            "ambiguous raw order status 'FILLED' without zero remaining_size; "
            "FILLED is order-level only at zero remainder"
        )
    folded = _ORDER_STATUS_FOLD.get(key)
    if folded is None:
        raise ValueError(f"unmapped raw venue order status: {raw!r} (ingress={ingress})")
    return folded


def is_cancel_confirmed_status(raw: object) -> bool:
    """True iff a raw venue order status is a cancel-confirmed synonym
    (CANCELLED / CANCELED / CANCEL_CONFIRMED).

    A NON-RAISING cancel-detection predicate for venue / exit seams: unlike
    normalize_venue_order_status (which raises on unmapped input), this tolerates any
    value and returns False for non-cancel / unknown / garbage. Derived from the single
    fold map so the synonym set cannot drift; centralizes the cancel-synonym knowledge so
    consumers stop re-encoding the set inline (INV-CL-1)."""
    # Inline normalization (NOT _key, which raises on empty/None) keeps this total.
    return _ORDER_STATUS_FOLD.get(str(raw).strip().upper()) is VenueOrderStatus.CANCEL_CONFIRMED


def normalize_venue_trade_status(raw: str | None) -> VenueTradeStatus:
    """Fold a raw trade/on-chain status into VenueTradeStatus.

    Accepts MATCHED/MINED/CONFIRMED/RETRYING/FAILED. Rejects FILLED — FILLED is
    order-level only unless the caller supplies an explicit trade-finality source.
    """
    key = _key(raw)
    if key == "FILLED":
        raise ValueError(
            "raw 'FILLED' is not a trade status; it is order-level only "
            "(use the order normalizer or supply explicit trade finality)"
        )
    try:
        return VenueTradeStatus[key]
    except KeyError:
        raise ValueError(f"unmapped raw venue trade status: {raw!r}") from None


def normalize_command_truth_state(raw: str | None) -> CommandTruthState:
    """Fold a raw command status into the local CommandTruthState.

    REJECTED/SUBMIT_REJECTED -> REJECTED; UNKNOWN/SUBMIT_UNKNOWN_SIDE_EFFECT ->
    UNKNOWN_SIDE_EFFECT. Legacy venue outcomes (FILLED/CANCELLED/EXPIRED) are NOT
    command truth and are refused — read them via project_legacy_command_display().
    """
    key = _key(raw)
    if key in _COMMAND_REJECT_RAW:
        return CommandTruthState.REJECTED
    if key in _COMMAND_UNKNOWN_RAW:
        return CommandTruthState.UNKNOWN_SIDE_EFFECT
    if key in _COMMAND_LEGACY_VENUE_OUTCOME_RAW:
        raise ValueError(
            f"{raw!r} is a venue/order outcome persisted on venue_commands.state for "
            "compatibility, NOT command-side truth; use project_legacy_command_display()"
        )
    try:
        return CommandTruthState[key]
    except KeyError:
        raise ValueError(f"unmapped raw command status: {raw!r}") from None
