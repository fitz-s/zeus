# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S2
"""Venue command bus — typed surface for the durable command journal.

Pure type contract. No I/O, no DB, no side effects. P1.S3 wires the executor
to write through `src/state/venue_command_repo.py` using these types.

Public surface:
    CommandState           — enum, 11 closed states
    CommandEventType       — enum, 11 closed event types
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
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CommandState(str, Enum):
    """Closed grammar of venue_commands.state values.

    The repo's `_TRANSITIONS` table at src/state/venue_command_repo.py uses
    these exact string values. A `test_command_state_strings_match_repo`
    asserts the round-trip.
    """
    INTENT_CREATED = "INTENT_CREATED"
    SUBMITTING = "SUBMITTING"
    ACKED = "ACKED"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CommandEventType(str, Enum):
    """Closed grammar of venue_command_events.event_type values."""
    INTENT_CREATED = "INTENT_CREATED"
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"
    SUBMIT_ACKED = "SUBMIT_ACKED"
    SUBMIT_REJECTED = "SUBMIT_REJECTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    PARTIAL_FILL_OBSERVED = "PARTIAL_FILL_OBSERVED"
    FILL_CONFIRMED = "FILL_CONFIRMED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACKED = "CANCEL_ACKED"
    EXPIRED = "EXPIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class IntentKind(str, Enum):
    """What a command is trying to do at the venue."""
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCEL = "CANCEL"
    DERISK = "DERISK"


# Recovery loop categories. Used by P1.S4 command_recovery to decide what to
# scan. Sourced from the same definitions to avoid drift.
IN_FLIGHT_STATES: frozenset[CommandState] = frozenset({
    CommandState.SUBMITTING,
    CommandState.UNKNOWN,
    CommandState.REVIEW_REQUIRED,
})

# A command in any of these states will not move via fill/ack events; only
# REVIEW_REQUIRED can re-enter the system from these.
TERMINAL_STATES: frozenset[CommandState] = frozenset({
    CommandState.FILLED,
    CommandState.CANCELLED,
    CommandState.EXPIRED,
    CommandState.REJECTED,
})


@dataclass(frozen=True)
class IdempotencyKey:
    """SHA-256-derived deterministic key (D-P1-4-a).

    Always construct via `from_inputs(...)`; the bare `value` field carries
    the resulting 32-char hex prefix. Stable across separate Python process
    invocations because hashlib is content-addressed and the canonicalization
    is fully specified.
    """
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"IdempotencyKey.value must be str, got {type(self.value).__name__}")
        if len(self.value) != 32:
            raise ValueError(f"IdempotencyKey.value must be 32 hex chars, got {len(self.value)}")

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

        Joined with NUL separators (\\x00) — guarantees no field-boundary
        ambiguity even if a value contains '|' or other ASCII separators.
        """
        if not isinstance(intent_kind, IntentKind):
            raise TypeError(
                f"intent_kind must be IntentKind enum, got {type(intent_kind).__name__}"
            )
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        canonical = "\x00".join([
            decision_id,
            token_id,
            side,
            f"{float(price):.4f}",
            f"{float(size):.4f}",
            intent_kind.value,
        ])
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return IdempotencyKey(value=digest[:32])


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
    venue_order_id: str = ""
    last_event_id: str = ""
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
