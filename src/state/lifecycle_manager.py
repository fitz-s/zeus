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
            LifecyclePhase.VOIDED,
        }
    ),
    LifecyclePhase.ACTIVE: frozenset(
        {
            LifecyclePhase.ACTIVE,
            LifecyclePhase.DAY0_WINDOW,
            LifecyclePhase.PENDING_EXIT,
            LifecyclePhase.SETTLED,
            LifecyclePhase.VOIDED,
        }
    ),
    LifecyclePhase.DAY0_WINDOW: frozenset(
        {
            LifecyclePhase.DAY0_WINDOW,
            LifecyclePhase.PENDING_EXIT,
            LifecyclePhase.SETTLED,
            LifecyclePhase.VOIDED,
        }
    ),
    LifecyclePhase.PENDING_EXIT: frozenset(
        {
            LifecyclePhase.PENDING_EXIT,
            LifecyclePhase.ACTIVE,
            LifecyclePhase.DAY0_WINDOW,
            LifecyclePhase.ECONOMICALLY_CLOSED,
            LifecyclePhase.SETTLED,
            LifecyclePhase.ADMIN_CLOSED,
            LifecyclePhase.VOIDED,
        }
    ),
    LifecyclePhase.ECONOMICALLY_CLOSED: frozenset(
        {
            LifecyclePhase.ECONOMICALLY_CLOSED,
            LifecyclePhase.SETTLED,
            LifecyclePhase.VOIDED,
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


def enter_pending_exit_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    fold_lifecycle_phase(current_phase, LifecyclePhase.PENDING_EXIT)
    return LifecyclePhase.PENDING_EXIT.value


def enter_day0_window_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.ACTIVE:
        raise ValueError(
            f"day0 transition requires active runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.DAY0_WINDOW)
    return LifecyclePhase.DAY0_WINDOW.value


def rescue_pending_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.PENDING_ENTRY:
        raise ValueError(
            f"pending rescue requires pending_entry runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.ACTIVE)
    return "entered"


def enter_chain_quarantined_runtime_state() -> str:
    fold_lifecycle_phase(None, LifecyclePhase.QUARANTINED)
    return LifecyclePhase.QUARANTINED.value


def enter_economically_closed_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.PENDING_EXIT:
        raise ValueError(
            f"economic close requires pending_exit runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.ECONOMICALLY_CLOSED)
    return LifecyclePhase.ECONOMICALLY_CLOSED.value


def enter_settled_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    normalized_exit_state = _normalized_state(exit_state)
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase == LifecyclePhase.PENDING_EXIT:
        if normalized_exit_state != "backoff_exhausted":
            raise ValueError(
                "settlement requires active/day0/economically_closed runtime phase, "
                "or pending_exit with backoff_exhausted"
            )
        fold_lifecycle_phase(current_phase, LifecyclePhase.SETTLED)
        return LifecyclePhase.SETTLED.value
    if current_phase not in {
        LifecyclePhase.ACTIVE,
        LifecyclePhase.DAY0_WINDOW,
        LifecyclePhase.ECONOMICALLY_CLOSED,
    }:
        raise ValueError(
            f"settlement requires active/day0/economically_closed runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.SETTLED)
    return LifecyclePhase.SETTLED.value


def enter_admin_closed_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.PENDING_EXIT:
        raise ValueError(
            f"admin close requires pending_exit runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.ADMIN_CLOSED)
    return LifecyclePhase.ADMIN_CLOSED.value


def enter_voided_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase not in {
        LifecyclePhase.PENDING_ENTRY,
        LifecyclePhase.ACTIVE,
        LifecyclePhase.DAY0_WINDOW,
        LifecyclePhase.PENDING_EXIT,
        LifecyclePhase.ECONOMICALLY_CLOSED,
    }:
        raise ValueError(
            "void transition requires pending/active/day0/pending_exit/economically_closed runtime phase, "
            f"got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.VOIDED)
    return LifecyclePhase.VOIDED.value


def initial_entry_runtime_state_for_order_status(status: object) -> str:
    normalized = _normalized_state(status).lower()
    if normalized == "filled":
        return "entered"
    return "pending_tracked"


def enter_filled_entry_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.PENDING_ENTRY:
        raise ValueError(
            f"entry fill requires pending_entry runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.ACTIVE)
    return "entered"


def enter_voided_entry_runtime_state(
    current_state: object,
    *,
    exit_state: object = "",
    chain_state: object = "",
) -> str:
    current_phase = phase_for_runtime_position(
        state=current_state,
        exit_state=exit_state,
        chain_state=chain_state,
    )
    if current_phase is not LifecyclePhase.PENDING_ENTRY:
        raise ValueError(
            f"entry void requires pending_entry runtime phase, got {current_phase.value!r}"
        )
    fold_lifecycle_phase(current_phase, LifecyclePhase.VOIDED)
    return LifecyclePhase.VOIDED.value


def release_pending_exit_runtime_state(
    previous_state: object,
    *,
    day0_entered_at: object = "",
) -> str:
    candidate = _normalized_state(previous_state) or (
        LifecyclePhase.DAY0_WINDOW.value if day0_entered_at else "holding"
    )
    restored_phase = phase_for_runtime_position(state=candidate)
    fold_lifecycle_phase(LifecyclePhase.PENDING_EXIT, restored_phase)
    return candidate
