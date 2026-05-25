"""Execution certificate builders and verifier entrypoints."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Iterable

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate
from src.decision_kernel.verifier import (
    verify_execution_command,
    verify_execution_receipt,
    verify_executor_expressibility,
    verify_final_intent,
)


def build_final_intent_certificate_from_actionable(
    *,
    actionable_cert: DecisionCertificate,
    decision_time: datetime,
    executable_snapshot_cert: DecisionCertificate | None = None,
    order_type: str = "POST_ONLY_LIMIT",
    time_in_force: str = "GTC",
    tick_size: float = 0.01,
    min_order_size: float = 1.0,
    fee_rate: float = 0.0,
) -> DecisionCertificate:
    action = actionable_cert.payload
    limit_price = float(action["c_fee_adjusted"])
    reserved_notional = float(action.get("live_cap_reserved_notional_usd") or action.get("kelly_size_usd") or 0.0)
    size = max(float(min_order_size), reserved_notional / limit_price)
    notional = size * limit_price
    snapshot_payload = executable_snapshot_cert.payload if executable_snapshot_cert is not None else {}
    executable_snapshot_hash = str(
        snapshot_payload.get("executable_snapshot_hash")
        or snapshot_payload.get("snapshot_hash")
        or stable_hash(
            {
                "certificate_hash": executable_snapshot_cert.certificate_hash if executable_snapshot_cert is not None else None,
                "executable_snapshot_id": action["executable_snapshot_id"],
                "condition_id": action["condition_id"],
                "token_id": action["token_id"],
            }
        )
    )
    cost_basis_hash = stable_hash(
        {
            "event_id": action["event_id"],
            "executable_snapshot_hash": executable_snapshot_hash,
            "token_id": action["token_id"],
            "direction": action["direction"],
            "limit_price": limit_price,
            "size": size,
            "order_policy": "post_only_passive_limit",
            "time_in_force": time_in_force,
        }
    )
    decision_source_context = _decision_source_context(action, decision_time)
    passive_maker_context = _passive_maker_context(action)
    payload = {
        "event_id": action["event_id"],
        "actionable_certificate_hash": actionable_cert.certificate_hash,
        "final_intent_id": action["final_intent_id"],
        "family_id": action["family_id"],
        "candidate_id": action["candidate_id"],
        "condition_id": action["condition_id"],
        "token_id": action["token_id"],
        "direction": action["direction"],
        "side": _side_for_direction(str(action["direction"])),
        "order_type": order_type,
        "executor_order_type": time_in_force,
        "time_in_force": time_in_force,
        "post_only": True,
        "maker_intent": True,
        "limit_price": limit_price,
        "size": size,
        "notional_usd": notional,
        "executable_snapshot_id": action["executable_snapshot_id"],
        "execution_price_type": "ExecutionPrice",
        "fee_deducted": True,
        "executable_snapshot_hash": executable_snapshot_hash,
        "cost_basis_hash": cost_basis_hash,
        "cost_basis_id": f"cost_basis:{cost_basis_hash[:16]}",
        "decision_source_context": decision_source_context,
        "passive_maker_context": passive_maker_context,
        "neg_risk": bool(action.get("neg_risk", False)),
        "tick_size": float(tick_size),
        "min_order_size": float(min_order_size),
        "fee_rate": float(fee_rate),
        "live_cap_usage_id": action["live_cap_usage_id"],
        "source": "existing_final_intent_builder",
        "submitted": False,
        "venue_order_id": None,
    }
    return _build_cert(
        claims.FINAL_INTENT,
        f"final_intent:{payload['event_id']}:{payload['final_intent_id']}",
        payload,
        decision_time,
        (actionable_cert,),
    )


def build_executor_expressibility_certificate(
    *,
    final_intent_cert: DecisionCertificate,
    executable_snapshot_cert: DecisionCertificate,
    live_cap_cert: DecisionCertificate,
    decision_time: datetime,
    executor_native_intent_hash: str,
    executor_name: str = "execute_final_intent",
    executor_capability_version: str = "existing_executor_passive_limit_v1",
) -> DecisionCertificate:
    if not executor_native_intent_hash:
        raise ValueError("executor_native_intent_hash required")
    final_intent = final_intent_cert.payload
    payload = {
        "event_id": final_intent["event_id"],
        "final_intent_id": final_intent["final_intent_id"],
        "executor_name": executor_name,
        "executor_capability_version": executor_capability_version,
        "can_express": True,
        "passed": True,
        "reason_code": "OK",
        "executor_native_intent_hash": executor_native_intent_hash,
        "order_type": final_intent["order_type"],
        "side": final_intent["side"],
        "direction": final_intent["direction"],
        "token_id": final_intent["token_id"],
        "condition_id": final_intent["condition_id"],
        "limit_price": final_intent["limit_price"],
        "size": final_intent["size"],
        "time_in_force": final_intent["time_in_force"],
        "post_only": final_intent["post_only"],
        "maker_intent": final_intent["maker_intent"],
        "tick_size": final_intent["tick_size"],
        "min_order_size": final_intent["min_order_size"],
        "neg_risk": final_intent["neg_risk"],
        "fee_rate": final_intent["fee_rate"],
    }
    return _build_cert(
        claims.EXECUTOR_EXPRESSIBILITY,
        f"executor_expressibility:{payload['event_id']}:{payload['final_intent_id']}",
        payload,
        decision_time,
        (final_intent_cert, executable_snapshot_cert, live_cap_cert),
    )


def build_execution_command_certificate_from_final_intent(
    *,
    actionable_cert: DecisionCertificate,
    final_intent_cert: DecisionCertificate,
    executor_expressibility_cert: DecisionCertificate,
    live_cap_cert: DecisionCertificate,
    decision_time: datetime,
) -> DecisionCertificate:
    action = actionable_cert.payload
    final_intent = final_intent_cert.payload
    execution_command_id = (
        f"edli_exec_cmd:{action['event_id']}:{final_intent['final_intent_id']}:"
        f"{final_intent['token_id']}:{final_intent['direction']}"
    )
    idempotency_key = stable_hash(
        {
            "event_id": action["event_id"],
            "causal_snapshot_id": action["causal_snapshot_id"],
            "final_intent_id": final_intent["final_intent_id"],
            "token_id": final_intent["token_id"],
            "direction": final_intent["direction"],
            "limit_price": final_intent["limit_price"],
            "size": final_intent["size"],
            "mode": "LIVE",
        }
    )
    payload = {
        "event_id": action["event_id"],
        "actionable_certificate_hash": actionable_cert.certificate_hash,
        "final_intent_id": final_intent["final_intent_id"],
        "execution_command_id": execution_command_id,
        "executor_name": executor_expressibility_cert.payload["executor_name"],
        "order_type": final_intent["order_type"],
        "side": final_intent["side"],
        "direction": final_intent["direction"],
        "condition_id": final_intent["condition_id"],
        "token_id": final_intent["token_id"],
        "limit_price": final_intent["limit_price"],
        "size": final_intent["size"],
        "time_in_force": final_intent["time_in_force"],
        "post_only": final_intent["post_only"],
        "maker": final_intent["maker_intent"],
        "maker_intent": final_intent["maker_intent"],
        "neg_risk": final_intent["neg_risk"],
        "tick_size": final_intent["tick_size"],
        "min_order_size": final_intent["min_order_size"],
        "fee_rate": final_intent["fee_rate"],
        "idempotency_key": idempotency_key,
        "submitted": False,
        "venue_order_id": None,
    }
    return _build_cert(
        claims.EXECUTION_COMMAND,
        f"execution_command:{payload['event_id']}:{payload['execution_command_id']}",
        payload,
        decision_time,
        (actionable_cert, final_intent_cert, executor_expressibility_cert, live_cap_cert),
    )


def build_execution_receipt_certificate(
    *,
    execution_command_cert: DecisionCertificate,
    decision_time: datetime,
    status: str = "SUBMIT_DISABLED",
    reason_code: str = "REAL_ORDER_SUBMIT_DISABLED",
    submit_started_at: str | None = None,
    submit_finished_at: str | None = None,
    venue_order_id: str | None = None,
    raw_response: Mapping[str, object] | None = None,
    raw_response_hash: str | None = None,
    reconciliation_followup_required: bool | None = None,
) -> DecisionCertificate:
    command = execution_command_cert.payload
    response_hash = raw_response_hash or stable_hash(
        {
            "status": status,
            "reason_code": reason_code,
            "venue_order_id": venue_order_id,
            "raw_response": dict(raw_response or {}),
        }
    )
    payload = {
        "event_id": command["event_id"],
        "execution_command_id": command["execution_command_id"],
        "final_intent_id": command["final_intent_id"],
        "executor_name": command["executor_name"],
        "status": status,
        "submit_started_at": submit_started_at,
        "submit_finished_at": submit_finished_at,
        "venue_order_id": venue_order_id,
        "raw_response_hash": response_hash,
        "idempotency_key": command["idempotency_key"],
        "reason_code": reason_code,
    }
    if reconciliation_followup_required is not None:
        payload["reconciliation_followup_required"] = reconciliation_followup_required
    return _build_cert(
        claims.EXECUTION_RECEIPT,
        f"execution_receipt:{payload['event_id']}:{payload['execution_command_id']}:{status}",
        payload,
        decision_time,
        (execution_command_cert,),
    )


def _build_cert(
    certificate_type: str,
    semantic_key: str,
    payload: dict,
    decision_time: datetime,
    parents: Iterable[DecisionCertificate],
) -> DecisionCertificate:
    parent_tuple = tuple(parents)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        parent_edges=tuple(
            ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type)
            for parent in parent_tuple
        ),
        parent_certificates=parent_tuple,
        authority_id="edli.final_intent_executor_boundary",
        authority_version="v1",
        algorithm_id="edli.event_bound_execution_certificate_builder",
        algorithm_version="v1",
    )


def _side_for_direction(direction: str) -> str:
    if direction in {"buy_yes", "buy_no"}:
        return "BUY"
    if direction in {"sell_yes", "sell_no"}:
        return "SELL"
    raise ValueError(f"unsupported EDLI direction: {direction!r}")


def _decision_source_context(action: Mapping[str, object], decision_time: datetime) -> dict[str, object]:
    available_at = str(action.get("source_available_at") or decision_time.isoformat())
    snapshot_hash = stable_hash(
        {
            "event_id": action["event_id"],
            "causal_snapshot_id": action["causal_snapshot_id"],
            "executable_snapshot_id": action["executable_snapshot_id"],
        }
    )
    return {
        "source_id": str(action.get("source_id") or "edli_event_bound"),
        "model_family": str(action.get("model_family") or "edli_v1"),
        "forecast_issue_time": str(action.get("forecast_issue_time") or available_at),
        "forecast_valid_time": str(action.get("forecast_valid_time") or available_at),
        "forecast_fetch_time": str(action.get("forecast_fetch_time") or available_at),
        "forecast_available_at": available_at,
        "raw_payload_hash": snapshot_hash,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": decision_time.isoformat(),
        "decision_time_status": "OK",
        "observation_time": available_at,
        "observation_available_at": available_at,
        "polymarket_end_anchor_source": "gamma_explicit",
        "first_member_observed_time": available_at,
        "run_complete_time": available_at,
        "zeus_submit_intent_time": decision_time.isoformat(),
        "venue_ack_time": decision_time.isoformat(),
    }


def _passive_maker_context(action: Mapping[str, object]) -> dict[str, object]:
    p_fill_lcb = float(action.get("p_fill_lcb") or 0.01)
    return {
        "spread_usd": "0.01",
        "quote_age_ms": 0,
        "expected_fill_probability": str(max(min(p_fill_lcb, 1.0), 0.0001)),
        "queue_depth_ahead": None,
        "adverse_selection_score": None,
        "orderbook_hash_age_ms": 0,
    }


def _role(certificate_type: str) -> str:
    return certificate_type.removesuffix("Certificate").replace("Evidence", "").lower()


__all__ = [
    "build_execution_command_certificate_from_final_intent",
    "build_execution_receipt_certificate",
    "build_executor_expressibility_certificate",
    "build_final_intent_certificate_from_actionable",
    "verify_execution_command",
    "verify_execution_receipt",
    "verify_executor_expressibility",
    "verify_final_intent",
]
