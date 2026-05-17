# Created: 2026-04-26
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S2
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
"""Venue command bus — typed surface for the durable command journal.

Pure type contract. No I/O, no DB, no side effects. P1.S3 wires the executor
to write through `src/state/venue_command_repo.py` using these types.

Public surface:
    CommandState           — closed command-side state enum
    CommandEventType       — closed command-side event enum
    IntentKind             — enum, 4 closed intent kinds
    IdempotencyKey         — frozen value object with deterministic factory
    VenueCommand           — frozen dataclass mirroring the venue_commands row
    TERMINAL_STATES        — frozenset of states from which no entry/fill events fire
    IN_FLIGHT_STATES       — frozenset of states the recovery loop must scan

Grammar guarantees (INV-29):
    - All enums are closed `str, Enum` types — `CommandState("BANANA")` raises ValueError.
    - VenueCommand is `dataclass(frozen=True)` — mutation raises FrozenInstanceError.
    - IdempotencyKey.from_inputs is deterministic: same inputs across separate
      Python processes produce byte-identical key strings.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


__all__ = [
    "CommandState",
    "CommandEventType",
    "IntentKind",
    "IdempotencyKey",
    "VenueCommand",
    "IN_FLIGHT_STATES",
    "TERMINAL_STATES",
]


class CommandState(str, Enum):
    """Closed grammar of venue_commands.state values.

    The repo's `_TRANSITIONS` table at src/state/venue_command_repo.py uses
    these exact string values. A `test_command_state_strings_match_repo`
    asserts the round-trip.
    """
    INTENT_CREATED = "INTENT_CREATED"
    SNAPSHOT_BOUND = "SNAPSHOT_BOUND"
    SUBMITTING = "SUBMITTING"
    SIGNED_PERSISTED = "SIGNED_PERSISTED"
    POSTING = "POSTING"
    POST_ACKED = "POST_ACKED"
    ACKED = "ACKED"
    UNKNOWN = "UNKNOWN"
    SUBMIT_UNKNOWN_SIDE_EFFECT = "SUBMIT_UNKNOWN_SIDE_EFFECT"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    SUBMIT_REJECTED = "SUBMIT_REJECTED"
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CommandEventType(str, Enum):
    """Closed grammar of venue_command_events.event_type values."""
    INTENT_CREATED = "INTENT_CREATED"
    SNAPSHOT_BOUND = "SNAPSHOT_BOUND"
    SIGNED_PERSISTED = "SIGNED_PERSISTED"
    POSTING = "POSTING"
    POST_ACKED = "POST_ACKED"
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"
    SUBMIT_ACKED = "SUBMIT_ACKED"
    SUBMIT_REJECTED = "SUBMIT_REJECTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    SUBMIT_TIMEOUT_UNKNOWN = "SUBMIT_TIMEOUT_UNKNOWN"
    CLOSED_MARKET_UNKNOWN = "CLOSED_MARKET_UNKNOWN"
    PARTIAL_FILL_OBSERVED = "PARTIAL_FILL_OBSERVED"
    FILL_CONFIRMED = "FILL_CONFIRMED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACKED = "CANCEL_ACKED"
    CANCEL_FAILED = "CANCEL_FAILED"
    CANCEL_REPLACE_BLOCKED = "CANCEL_REPLACE_BLOCKED"
    EXPIRED = "EXPIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT = "REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT"
    REVIEW_CLEARED_NO_VENUE_EXPOSURE = "REVIEW_CLEARED_NO_VENUE_EXPOSURE"


class IntentKind(str, Enum):
    """What a command is trying to do at the venue."""
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCEL = "CANCEL"
    DERISK = "DERISK"


# Recovery loop categories. Used by P1.S4 command_recovery to decide what to
# scan. Sourced from the same definitions to avoid drift.
# CANCEL_PENDING included per code-reviewer MEDIUM-2 (2026-04-26): a process
# restart between CANCEL_REQUESTED and CANCEL_ACKED leaves a row whose final
# venue state may differ from local intent; recovery must reconcile.
IN_FLIGHT_STATES: frozenset[CommandState] = frozenset({
    CommandState.SUBMITTING,
    CommandState.POSTING,
    CommandState.UNKNOWN,
    CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT,
    CommandState.REVIEW_REQUIRED,
    CommandState.CANCEL_PENDING,
})

# A command in any of these states will not move via fill/ack events; only
# REVIEW_REQUIRED can re-enter the system from these.
TERMINAL_STATES: frozenset[CommandState] = frozenset({
    CommandState.SUBMIT_REJECTED,
    CommandState.FILLED,
    CommandState.CANCELLED,
    CommandState.EXPIRED,
    CommandState.REJECTED,
})

# Post-live-order E2E (2026-05-15): REVIEW_REQUIRED is quasi-terminal for
# ordinary recovery, but has one proof-backed clearance event. It exists only
# for commands whose evidence proves no venue side effect occurred; payload
# validation in venue_command_repo rejects bare/manual state edits.

# Post-critic MAJOR-4 (2026-04-26): IdempotencyKey factory token.
# Direct construction `IdempotencyKey(value="...")` is forbidden — callers
# must use from_inputs() (for new commands) or from_external() (for row
# projection). The sentinel object below is module-private; external code
# cannot mint a matching token, so __post_init__ rejects any external
# construction. Closes the "hand-rolled key monopolizes idempotency slot"
# attack surface.
_FACTORY_TOKEN: object = object()


@dataclass(frozen=True)
class IdempotencyKey:
    """SHA-256-derived deterministic key (D-P1-4-a).

    Construction policy: callers must use one of two factories:
      - `from_inputs(...)` — for NEW commands; deterministic from canonical inputs
      - `from_external(value)` — for row projection; wraps a value already
        persisted to venue_commands.idempotency_key

    Direct `IdempotencyKey(value="...")` is forbidden (raises TypeError).
    The `_provenance` field is a private sentinel; external code cannot mint
    a matching token.
    """
    value: str
    _provenance: object = None

    def __post_init__(self) -> None:
        if self._provenance is not _FACTORY_TOKEN:
            raise TypeError(
                "IdempotencyKey must be constructed via from_inputs(...) for new "
                "commands or from_external(...) for row projection; direct "
                "construction is forbidden (post-critic MAJOR-4)."
            )
        if not isinstance(self.value, str):
            raise TypeError(f"IdempotencyKey.value must be str, got {type(self.value).__name__}")
        if len(self.value) != 32:
            raise ValueError(f"IdempotencyKey.value must be 32 hex chars, got {len(self.value)}")

    @classmethod
    def from_external(cls, value: str) -> "IdempotencyKey":
        """Wrap a value already persisted to the venue_commands row.

        Used by VenueCommand.from_row and any code that reads an idempotency
        key string back from storage. Performs the same length/type checks as
        from_inputs but skips canonicalization.
        """
        return cls(value=value, _provenance=_FACTORY_TOKEN)

    @staticmethod
    def from_inputs(
        *,
        decision_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        intent_kind: IntentKind,
    ) -> "IdempotencyKey":
        """Build a deterministic key from canonical inputs.

        Canonicalization rules (frozen — changing any breaks idempotency
        across versions, and any change MUST be paired with a migration):
          - decision_id, token_id, side: passed through unchanged
          - price, size: formatted as '{:.4f}' (4-decimal precision; absorbs
            float-representation noise without losing the values that drive
            order economics)
          - intent_kind: enum value string

        Fields joined with NUL (\\x00) separators. decision_id and token_id
        MUST NOT contain NUL bytes themselves — rejected at the boundary so
        the canonicalization is unambiguous (post-reviewer-HIGH 2026-04-26:
        without this rejection, ("\\x00", "\\x00x") and ("\\x00\\x00", "x")
        canonicalize identically and produce key collisions). Realistic Zeus
        IDs are UUID-like and contain no NULs, so this rejection is free.

        price/size must be finite (NaN/Inf rejected). sha256[:32] = 128 bits;
        birthday-collision probability ~10^14 vs 2^128 for ~10^7 commands —
        decisively safe within scope.
        """
        if not isinstance(intent_kind, IntentKind):
            raise TypeError(
                f"intent_kind must be IntentKind enum, got {type(intent_kind).__name__}"
            )
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if "\x00" in decision_id:
            raise ValueError("decision_id must not contain NUL bytes (\\x00)")
        if "\x00" in token_id:
            raise ValueError("token_id must not contain NUL bytes (\\x00)")
        if not math.isfinite(float(price)):
            raise ValueError(f"price must be finite, got {price!r}")
        if not math.isfinite(float(size)):
            raise ValueError(f"size must be finite, got {size!r}")
        canonical = "\x00".join([
            decision_id,
            token_id,
            side,
            f"{float(price):.4f}",
            f"{float(size):.4f}",
            intent_kind.value,
        ])
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return IdempotencyKey(value=digest[:32], _provenance=_FACTORY_TOKEN)


@dataclass(frozen=True)
class VenueCommand:
    """In-memory representation of a venue_commands row.

    Frozen by design: a command is constructed pre-submit and never mutated
    in place. State transitions append events through the repo, which writes
    a NEW VenueCommand projection on read.

    Fields mirror `venue_commands` schema columns. Optional fields default
    to "" / None matching SQL NULL semantics.
    """
    command_id: str
    position_id: str
    decision_id: str
    idempotency_key: IdempotencyKey
    intent_kind: IntentKind
    market_id: str
    token_id: str
    side: str
    size: float
    price: float
    state: CommandState
    # Post-critic MAJOR-2 (2026-04-26): Optional[str] = None preserves SQL NULL
    # semantics on the venue_order_id and last_event_id columns. P1.S3 row
    # projection must distinguish "no order placed yet" (None) from "venue
    # returned an empty order id" (which would be a venue bug worth surfacing).
    venue_order_id: Optional[str] = None
    last_event_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    review_required_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"VenueCommand.side must be 'BUY' or 'SELL', got {self.side!r}")
        if not isinstance(self.intent_kind, IntentKind):
            raise TypeError(
                f"VenueCommand.intent_kind must be IntentKind enum, got {type(self.intent_kind).__name__}"
            )
        if not isinstance(self.state, CommandState):
            raise TypeError(
                f"VenueCommand.state must be CommandState enum, got {type(self.state).__name__}"
            )
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise TypeError(
                f"VenueCommand.idempotency_key must be IdempotencyKey, got {type(self.idempotency_key).__name__}"
            )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "VenueCommand":
        """Project a venue_commands row dict into a typed VenueCommand.

        Used by P1.S3 executor (after insert_command/append_event), P1.S4
        recovery loop (scanning find_unresolved_commands), and P1.S5
        idempotency lookups. Centralizing the dict→typed coercion here
        prevents IntentKind(row["..."]) boilerplate from spreading.

        Re-raises with the offending column name on enum mismatch so a corrupt
        row fails diagnosably rather than via a generic enum ValueError.
        """
        try:
            intent_kind_val = IntentKind(row["intent_kind"])
        except ValueError as exc:
            raise ValueError(
                f"venue_commands.intent_kind={row.get('intent_kind')!r} is not a valid IntentKind"
            ) from exc
        try:
            state_val = CommandState(row["state"])
        except ValueError as exc:
            raise ValueError(
                f"venue_commands.state={row.get('state')!r} is not a valid CommandState"
            ) from exc
        # NULL preservation: do NOT collapse SQL NULL → "" via `or ""`.
        # venue_order_id and last_event_id pass through as None when NULL.
        # created_at and updated_at are NOT NULL in schema, so default to ""
        # only as a defensive fallback for malformed rows (would surface in
        # tests).
        return cls(
            command_id=row["command_id"],
            position_id=row["position_id"],
            decision_id=row["decision_id"],
            idempotency_key=IdempotencyKey.from_external(row["idempotency_key"]),
            intent_kind=intent_kind_val,
            market_id=row["market_id"],
            token_id=row["token_id"],
            side=row["side"],
            size=float(row["size"]),
            price=float(row["price"]),
            state=state_val,
            venue_order_id=row.get("venue_order_id"),
            last_event_id=row.get("last_event_id"),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            review_required_reason=row.get("review_required_reason"),
        )
