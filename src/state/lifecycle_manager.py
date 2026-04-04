from __future__ import annotations

from enum import Enum


class LifecyclePhase(str, Enum):
    PENDING_ENTRY = "pending_entry"
    ACTIVE = "active"
    DAY0_WINDOW = "day0_window"
    PENDING_EXIT = "pending_exit"
    ECONOMICALLY_CLOSED = "economically_closed"
    SETTLED = "settled"
    VOIDED = "voided"
    QUARANTINED = "quarantined"
    ADMIN_CLOSED = "admin_closed"


PENDING_EXIT_RUNTIME_STATES = frozenset(
    {
        "exit_intent",
        "sell_placed",
        "sell_pending",
        "retry_pending",
        "backoff_exhausted",
    }
)

LIFECYCLE_PHASE_VOCABULARY = tuple(phase.value for phase in LifecyclePhase)

LEGAL_LIFECYCLE_FOLDS: dict[LifecyclePhase | None, frozenset[LifecyclePhase]] = {
    None: frozenset({LifecyclePhase.PENDING_ENTRY, LifecyclePhase.QUARANTINED}),
    LifecyclePhase.PENDING_ENTRY: frozenset(
        {
            LifecyclePhase.PENDING_ENTRY,
            LifecyclePhase.ACTIVE,
            LifecyclePhase.DAY0_WINDOW,
        }
    ),
    LifecyclePhase.ACTIVE: frozenset(
        {
            LifecyclePhase.ACTIVE,
            LifecyclePhase.SETTLED,
        }
    ),
    LifecyclePhase.DAY0_WINDOW: frozenset(
        {
            LifecyclePhase.DAY0_WINDOW,
            LifecyclePhase.SETTLED,
        }
    ),
    LifecyclePhase.PENDING_EXIT: frozenset({LifecyclePhase.PENDING_EXIT}),
    LifecyclePhase.ECONOMICALLY_CLOSED: frozenset(
        {
            LifecyclePhase.ECONOMICALLY_CLOSED,
            LifecyclePhase.SETTLED,
        }
    ),
    LifecyclePhase.SETTLED: frozenset({LifecyclePhase.SETTLED}),
    LifecyclePhase.VOIDED: frozenset({LifecyclePhase.VOIDED}),
    LifecyclePhase.QUARANTINED: frozenset({LifecyclePhase.QUARANTINED}),
    LifecyclePhase.ADMIN_CLOSED: frozenset({LifecyclePhase.ADMIN_CLOSED}),
}


def _normalized_state(value: object) -> str:
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value or "")


def coerce_lifecycle_phase(value: LifecyclePhase | str | None) -> LifecyclePhase | None:
    if value is None:
        return None
    if isinstance(value, LifecyclePhase):
        return value
    normalized = _normalized_state(value)
    if not normalized:
        return None
    return LifecyclePhase(normalized)


def phase_for_runtime_position(
    *,
    state: object,
    exit_state: object = "",
    chain_state: object = "",
) -> LifecyclePhase:
    normalized_state = _normalized_state(state)
    normalized_exit_state = _normalized_state(exit_state)
    normalized_chain_state = _normalized_state(chain_state)

    if normalized_state == "voided":
        return LifecyclePhase.VOIDED
    if normalized_state == "settled":
        return LifecyclePhase.SETTLED
    if normalized_state == "economically_closed":
        return LifecyclePhase.ECONOMICALLY_CLOSED
    if normalized_state == "admin_closed":
        return LifecyclePhase.ADMIN_CLOSED
    if normalized_state == "quarantined":
        return LifecyclePhase.QUARANTINED
    if normalized_state == "pending_exit":
        return LifecyclePhase.PENDING_EXIT
    if normalized_chain_state in {"quarantined", "quarantine_expired"}:
        return LifecyclePhase.QUARANTINED
    if (
        normalized_exit_state in PENDING_EXIT_RUNTIME_STATES
        or normalized_chain_state == "exit_pending_missing"
    ):
        return LifecyclePhase.PENDING_EXIT
    if normalized_state == "pending_tracked":
        return LifecyclePhase.PENDING_ENTRY
    if normalized_state == "day0_window":
        return LifecyclePhase.DAY0_WINDOW
    if normalized_state in {"entered", "holding"}:
        return LifecyclePhase.ACTIVE
    raise ValueError(
        f"unsupported runtime position state for canonical phase mapping: {normalized_state!r}"
    )


def fold_lifecycle_phase(
    phase_before: LifecyclePhase | str | None,
    phase_after: LifecyclePhase | str,
) -> LifecyclePhase:
    current = coerce_lifecycle_phase(phase_before)
    next_phase = coerce_lifecycle_phase(phase_after)
    if next_phase is None:
        raise ValueError("phase_after is required for lifecycle fold")

    allowed_next = LEGAL_LIFECYCLE_FOLDS.get(current)
    if allowed_next is None or next_phase not in allowed_next:
        before_label = None if current is None else current.value
        raise ValueError(f"illegal lifecycle phase fold: {before_label!r} -> {next_phase.value!r}")
    return next_phase
