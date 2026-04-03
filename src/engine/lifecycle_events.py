from __future__ import annotations

import json
from typing import Any


CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"

PENDING_EXIT_STATES = {
    "exit_intent",
    "sell_placed",
    "sell_pending",
    "retry_pending",
    "backoff_exhausted",
}


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
    state = _normalized_state(getattr(position, "state", ""))
    exit_state = _normalized_state(getattr(position, "exit_state", ""))
    chain_state = _normalized_state(getattr(position, "chain_state", ""))

    if state == "voided":
        return "voided"
    if state == "settled":
        return "settled"
    if state == "economically_closed":
        return "economically_closed"
    if state == "admin_closed":
        return "admin_closed"
    if state == "quarantined":
        return "quarantined"
    if state == "pending_exit":
        return "pending_exit"
    if chain_state in {"quarantined", "quarantine_expired"}:
        return "quarantined"
    if exit_state in PENDING_EXIT_STATES or chain_state == "exit_pending_missing":
        return "pending_exit"
    if state == "pending_tracked":
        return "pending_entry"
    if state == "day0_window":
        return "day0_window"
    if state in {"entered", "holding"}:
        return "active"
    raise ValueError(f"unsupported runtime position state for canonical phase mapping: {state!r}")


def projection_updated_at(position: Any) -> str:
    return _non_empty(
        getattr(position, "last_exit_at", ""),
        getattr(position, "chain_verified_at", ""),
        getattr(position, "day0_entered_at", ""),
        getattr(position, "entered_at", ""),
        getattr(position, "order_posted_at", ""),
    )


def build_position_current_projection(position: Any) -> dict:
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
        "order_id": _nullable(getattr(position, "order_id", "")),
        "order_status": _nullable(getattr(position, "order_status", "")),
        "updated_at": projection_updated_at(position),
    }


def _entry_event_payload(position: Any, *, phase_after: str) -> str:
    return json.dumps(
        {
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
        },
        default=str,
        sort_keys=True,
    )


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
        "payload_json": _entry_event_payload(position, phase_after=phase_after),
    }


def build_entry_canonical_write(
    position: Any,
    *,
    decision_id: str | None = None,
    source_module: str = "src.engine.lifecycle_events",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    canonical_phase = projection["phase"]
    if canonical_phase not in {"pending_entry", "active", "day0_window"}:
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
            phase_after="pending_entry",
            decision_id=decision_id,
            source_module=source_module,
            order_id=None,
        ),
        _entry_event(
            position=position,
            event_type="ENTRY_ORDER_POSTED",
            sequence_no=2,
            occurred_at=posted_at,
            phase_before="pending_entry",
            phase_after="pending_entry",
            decision_id=decision_id,
            source_module=source_module,
            order_id=order_id,
        ),
    ]

    if canonical_phase in {"active", "day0_window"}:
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
                phase_before="pending_entry",
                phase_after=canonical_phase,
                decision_id=decision_id,
                source_module=source_module,
                order_id=order_id,
            )
        )

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
    if projection["phase"] != "settled":
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
        "phase_after": "settled",
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


def build_reconciliation_rescue_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    if projection["phase"] != "active":
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
        "phase_before": "pending_entry",
        "phase_after": "active",
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
    phase = projection["phase"]
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

    if projection["phase"] != "quarantined":
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
        "phase_after": "quarantined",
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


def build_chain_size_corrected_canonical_write(
    position: Any,
    *,
    local_shares_before: float,
    sequence_no: int,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    projection = build_position_current_projection(position)
    phase = projection["phase"]
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

    if projection["phase"] != "quarantined":
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
        "phase_after": "quarantined",
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
