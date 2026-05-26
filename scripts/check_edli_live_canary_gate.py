#!/usr/bin/env python3
"""EDLI live canary release gate.

Created: 2026-05-26
Authority basis: PR332 EDLI live promotion package; read-only canary proof gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANARY_PROOF_PASS = "CANARY_PROOF_PASS"
WAITING_FOR_QUALIFYING_EVENT = "WAITING_FOR_QUALIFYING_EVENT"
FAIL = "FAIL"

_REQUIRED_FIELDS = (
    "event_id",
    "aggregate_id",
    "final_intent_id",
    "execution_command_id",
    "condition_id",
    "token_id",
    "direction",
    "side",
    "order_type",
    "time_in_force",
    "post_only",
    "book_hash",
    "quote_seen_at",
    "best_bid",
    "best_ask",
    "limit_price",
    "tickSize",
    "negRisk",
    "balance_allowance_witness",
    "heartbeat_witness",
    "idempotency_key",
    "live_cap_usage_id",
    "cap_transition",
    "order_lifecycle_projection",
    "expected_edge",
    "realized_state",
)


@dataclass(frozen=True)
class CanaryGateResult:
    status: str
    reasons: tuple[str, ...] = ()


def evaluate_canary_artifact(
    artifact: dict[str, Any] | None,
    *,
    max_quote_age_ms: int = 1000,
    min_expected_edge: float = 0.0,
) -> CanaryGateResult:
    if artifact is None:
        return CanaryGateResult(WAITING_FOR_QUALIFYING_EVENT, ("CANARY_ARTIFACT_MISSING",))
    missing = tuple(field for field in _REQUIRED_FIELDS if _missing(artifact.get(field)))
    reasons: list[str] = []
    if missing:
        reasons.append("CANARY_REQUIRED_FIELDS_MISSING:" + ",".join(missing))
    if _missing(artifact.get("venue_order_id")) and _missing(artifact.get("SubmitUnknown")) and _missing(
        artifact.get("submit_unknown")
    ):
        reasons.append("CANARY_REQUIRES_VENUE_ORDER_OR_SUBMIT_UNKNOWN")
    if _missing(artifact.get("user_channel_observation")) and _missing(artifact.get("reconcile_observation")):
        reasons.append("CANARY_REQUIRES_USER_CHANNEL_OR_RECONCILE")
    if bool(artifact.get("unresolved_submit_unknown", False)):
        reasons.append("CANARY_SUBMIT_UNKNOWN_UNRESOLVED")
    projection = artifact.get("order_lifecycle_projection") or {}
    if isinstance(projection, dict) and bool(projection.get("pending_reconcile", False)):
        reasons.append("CANARY_PENDING_RECONCILE")
    quote_age_ms = _quote_age_ms(artifact)
    if quote_age_ms is not None and quote_age_ms > max_quote_age_ms:
        reasons.append("CANARY_QUOTE_STALE")
    try:
        if float(artifact.get("expected_edge", 0.0)) <= min_expected_edge:
            reasons.append("CANARY_EXPECTED_EDGE_INSUFFICIENT")
    except (TypeError, ValueError):
        reasons.append("CANARY_EXPECTED_EDGE_INVALID")
    reasons.extend(_economic_object_mismatch_reasons(artifact))
    cap_transition = artifact.get("cap_transition") or {}
    if not isinstance(cap_transition, dict) or str(cap_transition.get("to_status") or "").upper() not in {
        "CONSUMED",
        "RELEASED",
        "PENDING_RECONCILE",
    }:
        reasons.append("CANARY_CAP_TRANSITION_INVALID")
    realized_state = str(artifact.get("realized_state") or "").upper()
    if realized_state not in {"CONFIRMED", "TERMINAL_NO_FILL", "RECONCILED", "PENDING_RECONCILE"}:
        reasons.append("CANARY_REALIZED_STATE_INVALID")
    if reasons:
        return CanaryGateResult(FAIL, tuple(reasons))
    return CanaryGateResult(CANARY_PROOF_PASS, ())


def load_canary_artifact(path: str | Path) -> dict[str, Any] | None:
    artifact_path = Path(path)
    if not artifact_path.exists():
        return None
    payload = json.loads(artifact_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("CANARY_ARTIFACT_NOT_OBJECT")
    return payload


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == {}


def _quote_age_ms(artifact: dict[str, Any]) -> int | None:
    if artifact.get("quote_age_ms") is not None:
        return int(artifact["quote_age_ms"])
    checked_at = artifact.get("checked_at") or artifact.get("canary_checked_at")
    quote_seen_at = artifact.get("quote_seen_at")
    if not checked_at or not quote_seen_at:
        return None
    checked = _parse_time(str(checked_at))
    seen = _parse_time(str(quote_seen_at))
    return int((checked - seen).total_seconds() * 1000)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _economic_object_mismatch_reasons(artifact: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for nested_key in ("pre_submit", "execution_command", "final_intent"):
        nested = artifact.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for field in ("condition_id", "token_id", "side"):
            if nested.get(field) is not None and str(nested.get(field)) != str(artifact.get(field)):
                reasons.append(f"CANARY_{nested_key.upper()}_{field.upper()}_MISMATCH")
    return reasons


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="Path to a JSON canary proof artifact.")
    parser.add_argument("--max-quote-age-ms", type=int, default=1000)
    parser.add_argument("--min-expected-edge", type=float, default=0.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifact = load_canary_artifact(args.artifact)
    result = evaluate_canary_artifact(
        artifact,
        max_quote_age_ms=args.max_quote_age_ms,
        min_expected_edge=args.min_expected_edge,
    )
    if args.json:
        print(json.dumps({"status": result.status, "reasons": list(result.reasons)}, sort_keys=True))
    else:
        print(result.status)
        for reason in result.reasons:
            print(reason)
    return 0 if result.status == CANARY_PROOF_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
