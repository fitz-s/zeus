from __future__ import annotations

import json
from typing import Any

from src.contracts.decision_evidence import DecisionEvidence
from src.state.chain_reconciliation import resolve_position_metric
from src.state.lifecycle_manager import (
    LifecyclePhase,
    fold_lifecycle_phase,
    phase_for_runtime_position,
)

CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"

PENDING_ENTRY = LifecyclePhase.PENDING_ENTRY.value
ACTIVE = LifecyclePhase.ACTIVE.value
DAY0_WINDOW = LifecyclePhase.DAY0_WINDOW.value
PENDING_EXIT = LifecyclePhase.PENDING_EXIT.value
ECONOMICALLY_CLOSED = LifecyclePhase.ECONOMICALLY_CLOSED.value
SETTLED = LifecyclePhase.SETTLED.value
QUARANTINED = LifecyclePhase.QUARANTINED.value


def _normalized_state(value: object) -> str:
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value or "")


def _non_empty(*values: object) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    raise ValueError("missing required timestamp for canonical lifecycle builder")


def _nullable(value: object) -> object | None:
    return None if value in (None, "") else value


def _strategy_key(position: Any) -> str:
    strategy_key = str(
        getattr(position, "strategy_key", "") or getattr(position, "strategy", "") or ""
    ).strip()
    if not strategy_key:
        raise ValueError("missing strategy_key for canonical lifecycle builder")
    return strategy_key


def canonical_phase_for_position(position: Any) -> str:
    return phase_for_runtime_position(
        state=getattr(position, "state", ""),
        exit_state=getattr(position, "exit_state", ""),
        chain_state=getattr(position, "chain_state", ""),
    ).value


def projection_updated_at(position: Any) -> str:
    return _non_empty(
        getattr(position, "last_exit_at", ""),
        getattr(position, "chain_verified_at", ""),
        getattr(position, "day0_entered_at", ""),
        getattr(position, "entered_at", ""),
        getattr(position, "order_posted_at", ""),
    )


def build_position_current_projection(position: Any) -> dict:
    _position_metric = resolve_position_metric(position)
    return {
        "position_id": getattr(position, "trade_id"),
        "phase": canonical_phase_for_position(position),
        "trade_id": getattr(position, "trade_id"),
        "market_id": getattr(position, "market_id"),
        "city": getattr(position, "city"),
        "cluster": getattr(position, "cluster"),
        "target_date": getattr(position, "target_date"),
        "bin_label": getattr(position, "bin_label"),
        "direction": getattr(position, "direction"),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "shares": getattr(position, "shares", 0.0),
        "cost_basis_usd": getattr(position, "cost_basis_usd", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "p_posterior": getattr(position, "p_posterior", 0.0),
        "last_monitor_prob": _nullable(getattr(position, "last_monitor_prob", None)),
        "last_monitor_edge": _nullable(getattr(position, "last_monitor_edge", None)),
        "last_monitor_market_price": _nullable(getattr(position, "last_monitor_market_price", None)),
        "decision_snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "entry_method": getattr(position, "entry_method", ""),
        "strategy_key": _strategy_key(position),
        "edge_source": _nullable(getattr(position, "edge_source", "")),
        "discovery_mode": _nullable(getattr(position, "discovery_mode", "")),
        "chain_state": _nullable(getattr(position, "chain_state", "")),
        "token_id": _nullable(getattr(position, "token_id", "")),
        "no_token_id": _nullable(getattr(position, "no_token_id", "")),
        "condition_id": _nullable(getattr(position, "condition_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "order_status": _nullable(getattr(position, "order_status", "")),
        "updated_at": projection_updated_at(position),
        # Slice P2-C2 (PR #19 phase 2, 2026-04-26): route through canonical
        # resolver so the event payload carries authority + provenance for
        # downstream filters. Pre-fix, silent HIGH default discarded the
        # provenance signal; analytics could not distinguish materialized
        # rows from defaulted ones.
        "temperature_metric": _position_metric[0],
        "temperature_metric_authority": _position_metric[1],
        "temperature_metric_source": _position_metric[2],
    }


def _entry_event_payload(
    position: Any,
    *,
    phase_after: str,
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> str:
    # T4.1b 2026-04-23 (D4 Option E): attach DecisionEvidence envelope or
    # a reason sentinel onto ENTRY_ORDER_POSTED payloads. The two keys are
    # mutually exclusive semantic variants — `decision_evidence_envelope`
    # is the verbatim `DecisionEvidence.to_json()` output (read-side uses
    # `json_extract(payload_json, '$.decision_evidence_envelope')` then
    # `DecisionEvidence.from_json(...)`); `decision_evidence_reason`
    # records a known-missing-evidence context (e.g. legacy-position
    # backfill) so T4.2-Phase1 exit-side audit can distinguish
    # missing-because-legacy from missing-because-bug.
    payload: dict[str, Any] = {
        "city": getattr(position, "city", ""),
        "target_date": getattr(position, "target_date", ""),
        "bin_label": getattr(position, "bin_label", ""),
        "direction": getattr(position, "direction", ""),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "shares": getattr(position, "shares", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "order_status": getattr(position, "order_status", ""),
        "chain_state": getattr(position, "chain_state", ""),
        "entry_method": getattr(position, "entry_method", ""),
        "phase_after": phase_after,
    }
    if decision_evidence is not None:
        payload["decision_evidence_envelope"] = decision_evidence.to_json()
    if decision_evidence_reason is not None:
        payload["decision_evidence_reason"] = decision_evidence_reason
    return json.dumps(payload, default=str, sort_keys=True)


def _entry_event(
    *,
    position: Any,
    event_type: str,
    sequence_no: int,
    occurred_at: str,
    phase_before: str | None,
    phase_after: str,
    decision_id: str | None,
    source_module: str,
    order_id: str | None,
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> dict:
    trade_id = str(getattr(position, "trade_id"))
    slug = event_type.lower()
    return {
        "event_id": f"{trade_id}:{slug}",
        "position_id": trade_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "strategy_key": _strategy_key(position),
        "decision_id": decision_id,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": order_id,
        "command_id": None,
        "caused_by": None,
        "idempotency_key": f"{trade_id}:{slug}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "payload_json": _entry_event_payload(
            position,
            phase_after=phase_after,
            decision_evidence=decision_evidence,
            decision_evidence_reason=decision_evidence_reason,
        ),
    }


def build_entry_canonical_write(
    position: Any,
    *,
    decision_id: str | None = None,
    source_module: str = "src.engine.lifecycle_events",
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> tuple[list[dict], dict]:
    # T4.1b 2026-04-23 (D4 Option E): `decision_evidence` lands as a
    # `decision_evidence_envelope` payload sidecar on the ENTRY_ORDER_POSTED
    # event only (the single event that represents the committed decision
    # with full data still in frame). POSITION_OPEN_INTENT precedes the
    # statistical decision fully materializing; ENTRY_ORDER_FILLED arrives
    # after the decision frame has released. Callers without evidence
    # (legacy-position backfill from src.execution.exit_lifecycle) pass
    # `decision_evidence_reason` instead so the exit-side audit can
    # distinguish missing-because-legacy from missing-because-bug.
    projection = build_position_current_projection(position)
    canonical_phase = projection["phase"]
    if canonical_phase not in {PENDING_ENTRY, ACTIVE, DAY0_WINDOW}:
        raise ValueError(
            f"entry canonical builder only supports pending/active entry states, got {canonical_phase!r}"
        )

    posted_at = _non_empty(
        getattr(position, "order_posted_at", ""),
        getattr(position, "entered_at", ""),
        getattr(position, "day0_entered_at", ""),
    )
    order_id = _nullable(getattr(position, "order_id", ""))
    events = [
        _entry_event(
            position=position,
            event_type="POSITION_OPEN_INTENT",
            sequence_no=1,
            occurred_at=posted_at,
            phase_before=None,
            phase_after=fold_lifecycle_phase(None, PENDING_ENTRY).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=None,
        ),
        _entry_event(
            position=position,
            event_type="ENTRY_ORDER_POSTED",
            sequence_no=2,
            occurred_at=posted_at,
            phase_before=PENDING_ENTRY,
            phase_after=fold_lifecycle_phase(PENDING_ENTRY, PENDING_ENTRY).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=order_id,
            decision_evidence=decision_evidence,
            decision_evidence_reason=decision_evidence_reason,
        ),
    ]

    if canonical_phase in {ACTIVE, DAY0_WINDOW}:
        filled_at = _non_empty(
            getattr(position, "entered_at", ""),
            getattr(position, "day0_entered_at", ""),
            posted_at,
        )
        events.append(
            _entry_event(
                position=position,
                event_type="ENTRY_ORDER_FILLED",
                sequence_no=3,
                occurred_at=filled_at,
                phase_before=PENDING_ENTRY,
                phase_after=fold_lifecycle_phase(PENDING_ENTRY, canonical_phase).value,
                decision_id=decision_id,
                source_module=source_module,
                order_id=order_id,
            )
        )

    return events, projection


def build_day0_window_entered_canonical_write(
    position: Any,
    *,
    day0_entered_at: str,
    sequence_no: int,
    previous_phase: str = ACTIVE,
    source_module: str = "src.engine.cycle_runtime",
) -> tuple[list[dict], dict]:
    """Day0-canonical-event feature slice (2026-04-24): emit a canonical
    DAY0_WINDOW_ENTERED event when cycle_runtime transitions a position from
    active/holding into the day0_window lifecycle phase.

    Pre-T4.1b / pre-Day0-canonical: cycle_runtime.execute_monitoring_phase
    updated position_current.phase via update_trade_lifecycle and
    optionally wrote a legacy POSITION_LIFECYCLE_UPDATED trade_decisions
    row, but did NOT emit a canonical position_events record for the
    day0 transition. Post-this-slice: the transition emits a typed
    position_events row with event_type=DAY0_WINDOW_ENTERED, phase_before=
    previous_phase, phase_after=day0_window, and a payload carrying
    day0_entered_at plus the standard position identity fields.

    Args:
        position: Position instance AFTER the state transition (state must
            already be "day0_window" in memory). Used for identity fields.
        day0_entered_at: ISO8601 UTC timestamp of the day0 transition.
            Caller should pass pos.day0_entered_at immediately after setting.
        sequence_no: The event sequence number relative to the caller's
            canonical write batch. For in-cycle single-event emissions,
            callers typically use 1; ledger append_many_and_project will
            assign the global monotonic position-level sequence.
        previous_phase: The lifecycle phase the position was in before the
            transition (ACTIVE / PENDING_ENTRY). Defaults to ACTIVE because
            that's the common path (entry → holding/active → day0_window).
        source_module: Caller module name for audit provenance.

    Returns:
        (events, projection) tuple suitable for append_many_and_project.
        events is a single-element list containing the DAY0_WINDOW_ENTERED
        event; projection is build_position_current_projection(position)
        reflecting the post-transition state.

    Raises:
        ValueError: if the position is not in the DAY0_WINDOW phase
            post-transition (enforced to catch caller ordering bugs —
            pos.state must be mutated to "day0_window" BEFORE this builder
            is invoked so the projection reflects the transition).
    """
    projection = build_position_current_projection(position)
    canonical_phase = projection["phase"]
    if canonical_phase != DAY0_WINDOW:
        raise ValueError(
            f"day0 canonical builder requires post-transition position "
            f"to be in day0_window phase, got {canonical_phase!r}. "
            f"Caller must set pos.state='day0_window' before invoking."
        )

    if not day0_entered_at:
        raise ValueError(
            "day0_entered_at must be a non-empty ISO8601 timestamp"
        )

    trade_id = str(getattr(position, "trade_id"))
    slug = "day0_window_entered"

    payload: dict[str, Any] = {
        "city": getattr(position, "city", ""),
        "target_date": getattr(position, "target_date", ""),
        "bin_label": getattr(position, "bin_label", ""),
        "direction": getattr(position, "direction", ""),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "day0_entered_at": day0_entered_at,
        "entry_method": getattr(position, "entry_method", ""),
        "phase_before": previous_phase,
        "phase_after": DAY0_WINDOW,
    }

    event = {
        "event_id": f"{trade_id}:{slug}",
        "position_id": trade_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "DAY0_WINDOW_ENTERED",
        "occurred_at": day0_entered_at,
        "phase_before": previous_phase,
        "phase_after": DAY0_WINDOW,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": None,
        "idempotency_key": f"{trade_id}:{slug}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "payload_json": json.dumps(payload, default=str, sort_keys=True),
    }

    return [event], projection


def build_entry_fill_only_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    decision_id: str | None = None,
    source_module: str = "src.execution.fill_tracker",
) -> tuple[list[dict], dict]:
    """Emit ONLY the ENTRY_ORDER_FILLED event for a position whose
    POSITION_OPEN_INTENT and ENTRY_ORDER_POSTED events already exist.

    Used by fill detection (fill_tracker._mark_entry_filled) to advance a
    position from pending_entry → active without re-inserting the earlier
    two entry events (which would violate the unique (position_id, seq) key).

    The caller must pass the next available sequence_no. The position's
    runtime state must have already been updated so that
    canonical_phase_for_position(position) == 'active' or 'day0_window'.
    """
    projection = build_position_current_projection(position)
    canonical_phase = projection["phase"]
    if canonical_phase not in {ACTIVE, DAY0_WINDOW}:
        raise ValueError(
            f"entry fill-only builder requires active/day0_window phase, got {canonical_phase!r}"
        )
    filled_at = _non_empty(
        getattr(position, "entered_at", ""),
        getattr(position, "day0_entered_at", ""),
        getattr(position, "order_posted_at", ""),
    )
    order_id = _nullable(getattr(position, "order_id", ""))
    events = [
        _entry_event(
            position=position,
            event_type="ENTRY_ORDER_FILLED",
            sequence_no=sequence_no,
            occurred_at=filled_at,
            phase_before=PENDING_ENTRY,
            phase_after=fold_lifecycle_phase(PENDING_ENTRY, canonical_phase).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=order_id,
        )
    ]
    return events, projection


def build_settlement_canonical_write(
    position: Any,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    sequence_no: int,
    phase_before: str,
    source_module: str = "src.execution.harvester",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    if projection["phase"] != SETTLED:
        raise ValueError("settlement canonical builder requires a settled position projection")

    occurred_at = _non_empty(
        getattr(position, "last_exit_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "contract_version": CANONICAL_POSITION_SETTLED_CONTRACT_VERSION,
            "winning_bin": winning_bin,
            "position_bin": getattr(position, "bin_label", ""),
            "won": bool(won),
            "outcome": int(outcome),
            "p_posterior": getattr(position, "p_posterior", None),
            "exit_price": getattr(position, "exit_price", None),
            "pnl": getattr(position, "pnl", None),
            "exit_reason": getattr(position, "exit_reason", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:settled:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "SETTLED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": fold_lifecycle_phase(phase_before, SETTLED).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "harvester_settlement",
        "idempotency_key": f"{getattr(position, 'trade_id')}:settled:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "payload_json": payload,
    }
    return [event], projection


def build_economic_close_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    phase_before: str,
    source_module: str = "src.execution.exit_lifecycle",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    if projection["phase"] != ECONOMICALLY_CLOSED:
        raise ValueError("economic close canonical builder requires an economically_closed position projection")

    occurred_at = _non_empty(
        getattr(position, "last_exit_at", ""),
        projection["updated_at"],
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:exit_filled:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "EXIT_ORDER_FILLED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": fold_lifecycle_phase(phase_before, ECONOMICALLY_CLOSED).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(
            getattr(position, "last_exit_order_id", "") or getattr(position, "order_id", "")
        ),
        "command_id": None,
        "caused_by": "exit_order_filled",
        "idempotency_key": f"{getattr(position, 'trade_id')}:exit_filled:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "payload_json": json.dumps(
            {
                "exit_price": getattr(position, "exit_price", None),
                "fill_price": getattr(position, "exit_price", None),
                "best_bid": getattr(position, "last_monitor_best_bid", None),
                "current_market_price": getattr(position, "last_monitor_market_price", None),
                "pnl": getattr(position, "pnl", None),
                "exit_reason": getattr(position, "exit_reason", ""),
                "exit_state": getattr(position, "exit_state", ""),
                "pre_exit_state": getattr(position, "pre_exit_state", ""),
            },
            default=str,
            sort_keys=True,
        ),
    }
    return [event], projection


def build_reconciliation_rescue_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    if projection["phase"] != ACTIVE:
        raise ValueError("reconciliation rescue canonical builder requires an active position projection")

    occurred_at = _non_empty(
        getattr(position, "entered_at", ""),
        getattr(position, "chain_verified_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "status": "entered",
            "source": "chain_reconciliation",
            "reason": "pending_fill_rescued",
            "from_state": "pending_tracked",
            "to_state": "entered",
            "entry_order_id": getattr(position, "entry_order_id", "") or getattr(position, "order_id", ""),
            "entry_method": getattr(position, "entry_method", ""),
            "selected_method": getattr(position, "selected_method", "") or getattr(position, "entry_method", ""),
            "historical_entry_method": getattr(position, "entry_method", ""),
            "historical_selected_method": getattr(position, "selected_method", "") or getattr(position, "entry_method", ""),
            "applied_validations": list(getattr(position, "applied_validations", []) or []),
            "entry_fill_verified": getattr(position, "entry_fill_verified", False),
            "shares": getattr(position, "shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "condition_id": getattr(position, "condition_id", ""),
            "rescue_condition_id": getattr(position, "condition_id", ""),
            "order_status": getattr(position, "order_status", ""),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_synced:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_SYNCED",
        "occurred_at": occurred_at,
        "phase_before": PENDING_ENTRY,
        "phase_after": fold_lifecycle_phase(PENDING_ENTRY, ACTIVE).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "pending_fill_rescued",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_synced:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "payload_json": payload,
    }
    return [event], projection




def build_chain_size_corrected_canonical_write(
    position: Any,
    *,
    local_shares_before: float,
    sequence_no: int,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    phase = fold_lifecycle_phase(projection["phase"], projection["phase"]).value
    occurred_at = _non_empty(
        getattr(position, "chain_verified_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": "chain_size_corrected",
            "local_shares_before": local_shares_before,
            "chain_shares_after": getattr(position, "chain_shares", None),
            "shares_after": getattr(position, "shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "condition_id": getattr(position, "condition_id", ""),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_size_corrected:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_SIZE_CORRECTED",
        "occurred_at": occurred_at,
        "phase_before": phase,
        "phase_after": phase,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "chain_size_corrected",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_size_corrected:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "payload_json": payload,
    }
    return [event], projection


def build_chain_quarantined_canonical_write(
    position: Any,
    *,
    strategy_key: str,
    sequence_no: int,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    if not strategy_key:
        raise ValueError("chain quarantine canonical builder requires explicit strategy_key")

    original_strategy_key = getattr(position, "strategy_key", "")
    original_strategy = getattr(position, "strategy", "")
    setattr(position, "strategy_key", strategy_key)
    setattr(position, "strategy", strategy_key)
    try:
        projection = build_position_current_projection(position)
    finally:
        setattr(position, "strategy_key", original_strategy_key)
        setattr(position, "strategy", original_strategy)

    if projection["phase"] != QUARANTINED:
        raise ValueError("chain quarantine canonical builder requires a quarantined position projection")

    occurred_at = _non_empty(
        getattr(position, "quarantined_at", ""),
        getattr(position, "chain_verified_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": "chain_only_quarantined",
            "condition_id": getattr(position, "condition_id", ""),
            "token_id": getattr(position, "token_id", ""),
            "chain_shares": getattr(position, "chain_shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_quarantined:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_QUARANTINED",
        "occurred_at": occurred_at,
        "phase_before": None,
        "phase_after": fold_lifecycle_phase(None, QUARANTINED).value,
        "strategy_key": strategy_key,
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "chain_only_quarantined",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_quarantined:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "payload_json": payload,
    }
    projection["strategy_key"] = strategy_key
    return [event], projection
