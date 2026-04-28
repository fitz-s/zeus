# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z1.yaml; docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
"""CutoverGuard: fail-closed runtime state machine for CLOB V2 cutover.

Z1 deliberately keeps this surface small:

* state is explicit and enum-backed;
* transitions require an HMAC-signed operator token;
* LIVE_ENABLED additionally requires a concrete operator evidence artifact;
* executor-facing decisions are computed before any venue command row or SDK
  side effect can happen.

This module does not decide the cutover date and does not perform exchange
reconciliation. Later Z2/M5/T1 phases own V2 adapter mechanics, cutover-wipe
classification, and live-money integration simulations. Cancel/redemption
decisions exposed here are decision surfaces only until the later direct
cancel/redeem side-effect paths are wired through them.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from src.config import state_path
from src.execution.command_bus import IntentKind


CUTOVER_STATE_PATH = state_path("cutover_guard.json")
OPERATOR_TOKEN_SECRET_ENV = "ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET"
MAX_TRANSITION_EVENTS = 50


class CutoverError(RuntimeError):
    """Base exception for CutoverGuard failures."""


class OperatorTokenRequired(CutoverError):
    """Transition attempted without an explicit operator token."""


class OperatorTokenInvalid(CutoverError):
    """Transition attempted with a token that is not operator-signed."""


class OperatorEvidenceRequired(CutoverError):
    """LIVE_ENABLED transition attempted without a cutover evidence artifact."""


class OperatorEvidenceInvalid(CutoverError):
    """LIVE_ENABLED transition attempted with evidence that does not prove G1 readiness."""


class IllegalTransition(CutoverError):
    """Transition is not in the allowed cutover state graph."""


class CutoverPending(CutoverError):
    """Venue side effect blocked because CutoverGuard is not LIVE_ENABLED."""


class CutoverState(str, Enum):
    NORMAL = "NORMAL"
    PRE_CUTOVER_FREEZE = "PRE_CUTOVER_FREEZE"
    CUTOVER_DOWNTIME = "CUTOVER_DOWNTIME"
    POST_CUTOVER_RECONCILE = "POST_CUTOVER_RECONCILE"
    LIVE_ENABLED = "LIVE_ENABLED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CutoverDecision:
    allow_submit: bool
    allow_cancel: bool
    allow_redemption: bool
    block_reason: Optional[str]
    state: CutoverState

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_submit": self.allow_submit,
            "allow_cancel": self.allow_cancel,
            "allow_redemption": self.allow_redemption,
            "block_reason": self.block_reason,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class ReconcileReport:
    status: str
    reason: str
    findings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class OperatorTokenClaims:
    operator_id: str
    nonce: str
    fingerprint: str


_ALLOWED_TRANSITIONS: dict[CutoverState, frozenset[CutoverState]] = {
    CutoverState.NORMAL: frozenset({
        CutoverState.PRE_CUTOVER_FREEZE,
        CutoverState.BLOCKED,
    }),
    CutoverState.PRE_CUTOVER_FREEZE: frozenset({
        CutoverState.NORMAL,
        CutoverState.CUTOVER_DOWNTIME,
        CutoverState.BLOCKED,
    }),
    CutoverState.CUTOVER_DOWNTIME: frozenset({
        CutoverState.PRE_CUTOVER_FREEZE,
        CutoverState.POST_CUTOVER_RECONCILE,
        CutoverState.BLOCKED,
    }),
    CutoverState.POST_CUTOVER_RECONCILE: frozenset({
        CutoverState.PRE_CUTOVER_FREEZE,
        CutoverState.LIVE_ENABLED,
        CutoverState.BLOCKED,
    }),
    CutoverState.LIVE_ENABLED: frozenset({
        CutoverState.PRE_CUTOVER_FREEZE,
        CutoverState.BLOCKED,
    }),
    CutoverState.BLOCKED: frozenset({
        CutoverState.PRE_CUTOVER_FREEZE,
    }),
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_state(value: object) -> CutoverState:
    try:
        return CutoverState(str(value))
    except ValueError:
        # Unknown/corrupt state must not accidentally permit live side effects.
        return CutoverState.BLOCKED


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {"state": CutoverState.NORMAL.value, "transitions": []}
    except OSError:
        return {"state": CutoverState.BLOCKED.value, "transitions": []}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": CutoverState.BLOCKED.value, "transitions": []}
    if not isinstance(payload, dict):
        return {"state": CutoverState.BLOCKED.value, "transitions": []}
    if "transitions" not in payload or not isinstance(payload.get("transitions"), list):
        payload["transitions"] = []
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def current_state(*, path: Path | None = None) -> CutoverState:
    payload = _load_payload(path or CUTOVER_STATE_PATH)
    return _coerce_state(payload.get("state", CutoverState.NORMAL.value))


def read_transition_events(*, path: Path | None = None) -> list[dict[str, Any]]:
    payload = _load_payload(path or CUTOVER_STATE_PATH)
    events = payload.get("transitions", [])
    return list(events) if isinstance(events, list) else []


def _validate_operator_token(operator_token: str) -> OperatorTokenClaims:
    token = str(operator_token or "").strip()
    if not token:
        raise OperatorTokenRequired("CutoverGuard transition requires an operator token")
    secret = os.environ.get(OPERATOR_TOKEN_SECRET_ENV, "").strip()
    if not secret:
        raise OperatorTokenInvalid(
            f"CutoverGuard operator signing secret is not configured: {OPERATOR_TOKEN_SECRET_ENV}"
        )
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise OperatorTokenInvalid("CutoverGuard operator token must be v1.<operator_id>.<nonce>.<hmac>")
    _, operator_id, nonce, signature = parts
    if not operator_id.strip() or len(nonce.strip()) < 8:
        raise OperatorTokenInvalid("CutoverGuard operator token has invalid claims")
    message = f"v1.{operator_id}.{nonce}".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise OperatorTokenInvalid("CutoverGuard operator token signature mismatch")
    fingerprint = hashlib.sha256(token.encode()).hexdigest()[:16]
    return OperatorTokenClaims(operator_id=operator_id, nonce=nonce, fingerprint=fingerprint)


def _validate_operator_evidence(target: CutoverState, operator_evidence_path: Path | None) -> str:
    if target is not CutoverState.LIVE_ENABLED:
        return ""
    if operator_evidence_path is None:
        raise OperatorEvidenceRequired("LIVE_ENABLED transition requires G1 readiness evidence")
    evidence_path = Path(operator_evidence_path)
    if not evidence_path.exists():
        raise OperatorEvidenceRequired(
            f"LIVE_ENABLED transition evidence file does not exist: {evidence_path}"
        )
    _validate_live_readiness_evidence(evidence_path)
    return str(evidence_path)


def _validate_live_readiness_evidence(evidence_path: Path) -> None:
    """Require the runtime cutover switch to point at a full G1 readiness report.

    The live-readiness script is intentionally unable to authorize live deploy
    (`live_deploy_authorized=false`). CutoverGuard therefore still requires the
    signed operator token, but the evidence file must prove that the 17-gate
    readiness suite and staged smoke passed. A generic runbook note is not
    sufficient to flip the live-money switch.
    """

    try:
        payload = json.loads(evidence_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorEvidenceInvalid(
            f"LIVE_ENABLED evidence must be a JSON live-readiness report: {evidence_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise OperatorEvidenceInvalid("LIVE_ENABLED evidence must be a JSON object")

    checks = {
        "status": payload.get("status") == "PASS",
        "gate_count": int(payload.get("gate_count", -1)) == 17,
        "passed_gates": int(payload.get("passed_gates", -1)) == 17,
        "staged_smoke_status": payload.get("staged_smoke_status") == "PASS",
        "live_deploy_authorized": payload.get("live_deploy_authorized") is False,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, ok in checks.items() if not ok)
        raise OperatorEvidenceInvalid(
            "LIVE_ENABLED evidence does not satisfy G1 readiness report contract: "
            f"{failed}"
        )


def _validate_transition(current: CutoverState, target: CutoverState) -> None:
    if current == target:
        return
    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise IllegalTransition(f"illegal cutover transition: {current.value} -> {target.value}")


def transition(
    target: CutoverState,
    *,
    operator_token: str,
    path: Path | None = None,
    reason: str = "",
    context: dict[str, Any] | None = None,
    operator_evidence_path: Path | None = None,
) -> CutoverState:
    """Persist a cutover transition atomically.

    Rejected transitions do not write state or events. A failed write leaves the
    previous JSON file intact because writes use a temp file + `os.replace`.
    """

    token_claims = _validate_operator_token(operator_token)
    target_state = target if isinstance(target, CutoverState) else CutoverState(str(target))
    evidence = _validate_operator_evidence(target_state, operator_evidence_path)
    state_path_obj = path or CUTOVER_STATE_PATH
    payload = _load_payload(state_path_obj)
    prior = _coerce_state(payload.get("state", CutoverState.NORMAL.value))
    _validate_transition(prior, target_state)

    event = {
        "from_state": prior.value,
        "to_state": target_state.value,
        "recorded_at": _utcnow(),
        "operator_id": token_claims.operator_id,
        "operator_token_fingerprint": token_claims.fingerprint,
        "reason": reason,
        "context": context or {},
    }
    if evidence:
        event["operator_evidence_path"] = evidence
    transitions = list(payload.get("transitions", []))
    transitions.append(event)
    payload.update({
        "state": target_state.value,
        "updated_at": event["recorded_at"],
        "transitions": transitions[-MAX_TRANSITION_EVENTS:],
    })
    _atomic_write_json(state_path_obj, payload)
    return target_state


def _blocked(intent_kind: IntentKind | None, state: CutoverState) -> CutoverDecision:
    suffix = f":{intent_kind.value}" if isinstance(intent_kind, IntentKind) else ""
    return CutoverDecision(
        allow_submit=False,
        allow_cancel=False,
        allow_redemption=False,
        block_reason=f"{state.value}{suffix}",
        state=state,
    )


def gate_for_intent(intent_kind: IntentKind, *, path: Path | None = None) -> CutoverDecision:
    state = current_state(path=path)
    if state is CutoverState.LIVE_ENABLED:
        if intent_kind is IntentKind.CANCEL:
            return CutoverDecision(False, True, False, None, state)
        return CutoverDecision(True, False, False, None, state)
    if state is CutoverState.PRE_CUTOVER_FREEZE and intent_kind is IntentKind.CANCEL:
        return CutoverDecision(False, True, False, None, state)
    return _blocked(intent_kind, state)


def redemption_decision(*, path: Path | None = None) -> CutoverDecision:
    state = current_state(path=path)
    if state is CutoverState.LIVE_ENABLED:
        return CutoverDecision(False, False, True, None, state)
    return _blocked(None, state)


def assert_submit_allowed(intent_kind: IntentKind, *, path: Path | None = None) -> None:
    decision = gate_for_intent(intent_kind, path=path)
    if not decision.allow_submit:
        raise CutoverPending(decision.block_reason or decision.state.value)


def summary(*, path: Path | None = None) -> dict[str, Any]:
    state = current_state(path=path)
    entry = gate_for_intent(IntentKind.ENTRY, path=path)
    exit_ = gate_for_intent(IntentKind.EXIT, path=path)
    cancel = gate_for_intent(IntentKind.CANCEL, path=path)
    redemption = redemption_decision(path=path)
    return {
        "state": state.value,
        "entry": entry.to_dict(),
        "exit": exit_.to_dict(),
        "cancel": cancel.to_dict(),
        "redemption": redemption.to_dict(),
    }


def post_cutover_reconcile(*args, **kwargs) -> ReconcileReport:
    """Placeholder seam for M5-owned cutover-wipe reconciliation.

    Z1 exposes the seam so callers cannot mistake absence for success, but it
    does not invent exchange reconciliation semantics ahead of the M5 phase.
    """

    return ReconcileReport(
        status="deferred",
        reason="M5 exchange reconciliation owns cutover-wipe classification",
    )
