"""Control plane: runtime commands from OpenClaw/Venus without process restart.

Blueprint v2 §10: Supported commands read from state/control_plane.json.
Narrow-by-intent: each command does exactly one thing.
"""

import json
import logging
from datetime import datetime, timezone

from src.config import state_path
from src.control.gate_decision import GateDecision, ReasonCode
from src.state.db import (
    DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    expire_control_override,
    get_shared_connection,
    query_control_override_state,
    upsert_control_override,
)

logger = logging.getLogger(__name__)

CONTROL_PATH = state_path("control_plane.json")
DEFAULT_EDGE_THRESHOLD_MULTIPLIER = 1.0
TIGHTENED_EDGE_THRESHOLD_MULTIPLIER = 2.0

COMMANDS = {
    "pause_entries",                # Stop entering, keep monitoring
    "resume",                       # Clear temporary global controls and resume entries
    "tighten_risk",                 # Double edge thresholds temporarily
    "request_status",               # Force status_summary write
    "set_strategy_gate",            # Enable/disable individual strategies
    "acknowledge_quarantine_clear", # Explicit operator intent before ignore/non-resurrection
}

_control_state: dict = {}


def _load_control_payload() -> dict:
    try:
        with open(CONTROL_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}



def _write_control_payload(commands: list[dict], acks: list[dict]) -> None:
    with open(CONTROL_PATH, "w") as f:
        json.dump({"commands": commands, "acks": acks[-20:]}, f, indent=2)



def _extract_quarantine_token_id(payload: dict) -> str:
    token_id = payload.get("token_id") or payload.get("tokenId") or ""
    return str(token_id) if token_id else ""



def _set_state(key: str, value) -> None:
    _control_state[key] = value



def is_entries_paused() -> bool:
    return _control_state.get("entries_paused", False)



def alert_auto_pause(reason_code: str) -> None:
    """Emit a structured log warning when entries are auto-paused by exception."""
    logger.warning(
        "auto_pause_entries",
        extra={"reason_code": reason_code},
    )



def pause_entries(reason_code: str) -> None:
    """Auto-pause entries after an unhandled exception in the entry path.

    Sets entries_paused and records the machine-readable reason_code in
    _control_state, then emits an alert. Also persists to DB so the pause
    survives a daemon restart. Operator must explicitly resume to re-enable.
    """
    _control_state["entries_paused"] = True
    _control_state["entries_pause_reason"] = reason_code
    alert_auto_pause(reason_code)
    # Persist so a daemon restart does not silently lose the pause.
    try:
        conn = get_shared_connection()
        upsert_control_override(
            conn,
            override_id="control_plane:global:entries_paused",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by=f"auto:{reason_code}",
            issued_at=datetime.now(timezone.utc).isoformat(),
            reason=reason_code,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Failed to persist auto-pause to DB: %s", exc)



def get_edge_threshold_multiplier() -> float:
    return _control_state.get("edge_threshold_multiplier", DEFAULT_EDGE_THRESHOLD_MULTIPLIER)


def strategy_gates() -> dict[str, GateDecision]:
    raw = _control_state.get("strategy_gates", {})
    result = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            result[k] = GateDecision.from_dict(v)
        else:
            # backward compat: bare bool from DB -> GateDecision with UNSPECIFIED reason
            result[k] = GateDecision(
                enabled=bool(v),
                reason_code=ReasonCode.UNSPECIFIED,
                reason_snapshot={},
                gated_at="",
                gated_by="unknown",
            )
    return result


def is_strategy_enabled(strategy: str) -> bool:
    if not strategy:
        return True
    decision = strategy_gates().get(strategy)
    if decision is None:
        return True
    return decision.enabled



def get_acknowledged_quarantine_clear_tokens() -> set[str]:
    data = _load_control_payload()
    acknowledged: set[str] = set()
    for ack in data.get("acks", []):
        if ack.get("command") != "acknowledge_quarantine_clear":
            continue
        if ack.get("status") != "executed":
            continue
        token_id = _extract_quarantine_token_id(ack)
        if token_id:
            acknowledged.add(token_id)
    return acknowledged



def refresh_control_state() -> None:
    data = _load_control_payload()
    acknowledged_tokens: set[str] = set()
    entries_paused = False
    edge_threshold_multiplier = DEFAULT_EDGE_THRESHOLD_MULTIPLIER
    gates: dict[str, bool] = {}
    durable_state = {"status": "skipped_no_connection"}
    conn = None
    try:
        conn = get_shared_connection()
        durable_state = query_control_override_state(conn)
    except Exception:
        durable_state = {"status": "query_error"}
    finally:
        if conn is not None:
            conn.close()
    if durable_state.get("status") == "ok":
        entries_paused = bool(durable_state.get("entries_paused", False))
        edge_threshold_multiplier = float(
            durable_state.get("edge_threshold_multiplier", DEFAULT_EDGE_THRESHOLD_MULTIPLIER)
        )
        gates = dict(durable_state.get("strategy_gates", {}))
    for ack in data.get("acks", []):
        if ack.get("status") != "executed":
            continue
        command = ack.get("command")
        if command == "acknowledge_quarantine_clear":
            token_id = _extract_quarantine_token_id(ack)
            if token_id:
                acknowledged_tokens.add(token_id)
            continue
    _control_state["entries_paused"] = entries_paused
    _control_state["edge_threshold_multiplier"] = edge_threshold_multiplier
    _control_state["acknowledged_quarantine_clear_tokens"] = acknowledged_tokens
    _control_state["strategy_gates"] = gates
    _control_state["durable_override_status"] = durable_state.get("status", "unknown")



def clear_control_state() -> None:
    _control_state.clear()
    refresh_control_state()



def acknowledged_quarantine_clear_tokens() -> set[str]:
    tokens = _control_state.get("acknowledged_quarantine_clear_tokens")
    if tokens is None:
        refresh_control_state()
        tokens = _control_state.get("acknowledged_quarantine_clear_tokens", set())
    return set(tokens)



def has_acknowledged_quarantine_clear(token_id: str) -> bool:
    return token_id in acknowledged_quarantine_clear_tokens()



def _acknowledge_command(name: str, cmd: dict, *, status: str, reason: str = "") -> dict:
    ack = {
        "command": name,
        "acked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
    }
    token_id = _extract_quarantine_token_id(cmd)
    if token_id:
        ack["token_id"] = token_id
    if cmd.get("strategy"):
        ack["strategy"] = cmd["strategy"]
    if isinstance(cmd.get("enabled"), bool):
        ack["enabled"] = cmd["enabled"]
    if cmd.get("condition_id"):
        ack["condition_id"] = cmd["condition_id"]
    if cmd.get("note"):
        ack["note"] = cmd["note"]
    if reason:
        ack["reason"] = reason
    return ack



def _apply_command(name: str, cmd: dict) -> tuple[bool, str]:
    conn = None
    issued_at = datetime.now(timezone.utc).isoformat()
    note = str(cmd.get("note") or cmd.get("reason") or "")
    issued_by = str(cmd.get("issued_by") or "control_plane")
    effective_until = cmd.get("effective_until")
    precedence = int(cmd.get("precedence") or DEFAULT_CONTROL_OVERRIDE_PRECEDENCE)
    try:
        conn = get_shared_connection()
    except Exception:
        conn = None
    try:
        if name == "pause_entries":
            result = upsert_control_override(
                conn,
                override_id="control_plane:global:entries_paused",
                target_type="global",
                target_key="entries",
                action_type="gate",
                value="true",
                issued_by=issued_by,
                issued_at=issued_at,
                reason=note or "control_plane:pause_entries",
                effective_until=effective_until,
                precedence=precedence,
            )
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "resume":
            expire_control_override(
                conn,
                override_id="control_plane:global:entries_paused",
                expired_at=issued_at,
            )
            expire_control_override(
                conn,
                override_id="control_plane:global:edge_threshold_multiplier",
                expired_at=issued_at,
            )
            return True, ""
        if name == "tighten_risk":
            result = upsert_control_override(
                conn,
                override_id="control_plane:global:edge_threshold_multiplier",
                target_type="global",
                target_key="entries",
                action_type="threshold_multiplier",
                value=str(TIGHTENED_EDGE_THRESHOLD_MULTIPLIER),
                issued_by=issued_by,
                issued_at=issued_at,
                reason=note or "control_plane:tighten_risk",
                effective_until=effective_until,
                precedence=precedence,
            )
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "request_status":
            from src.observability.status_summary import write_status
            write_status()
            return True, ""
        if name == "set_strategy_gate":
            strategy = str(cmd.get("strategy") or "")
            enabled = cmd.get("enabled")
            if not strategy:
                return False, "missing_strategy"
            if not isinstance(enabled, bool):
                return False, "missing_enabled_bool"
            decision = GateDecision(
                enabled=enabled,
                reason_code=ReasonCode(cmd.get("reason_code", "unspecified")),
                reason_snapshot=cmd.get("reason_snapshot", {}),
                gated_at=issued_at,
                gated_by=issued_by,
            )
            gates = dict(_control_state.get("strategy_gates", {}))
            gates[strategy] = decision.to_dict()
            _control_state["strategy_gates"] = gates
            result = upsert_control_override(
                conn,
                override_id=f"control_plane:strategy:{strategy}:gate",
                target_type="strategy",
                target_key=strategy,
                action_type="gate",
                value="false" if enabled else "true",
                issued_by=issued_by,
                issued_at=issued_at,
                reason=note or f"control_plane:set_strategy_gate:{'enable' if enabled else 'disable'}",
                effective_until=effective_until,
                precedence=precedence,
            )
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "acknowledge_quarantine_clear":
            if not _extract_quarantine_token_id(cmd):
                return False, "missing_token_id"
            return True, ""
        return True, ""
    finally:
        if conn is not None:
            conn.commit()
            conn.close()



def process_commands() -> list[str]:
    data = _load_control_payload()
    commands = data.get("commands", [])
    acks = data.get("acks", [])
    if not commands:
        refresh_control_state()
        return []

    processed = []
    for cmd in commands:
        name = cmd.get("command")
        if name not in COMMANDS:
            logger.warning("Unknown control command: %s", name)
            acks.append(_acknowledge_command(str(name or ""), cmd, status="rejected", reason="unknown_command"))
            continue

        logger.info("CONTROL: executing %s", name)
        ok, reason = _apply_command(name, cmd)
        acks.append(_acknowledge_command(name, cmd, status="executed" if ok else "rejected", reason=reason))
        if ok:
            processed.append(name)

    _write_control_payload([], acks)
    refresh_control_state()
    return processed


def enqueue_commands(new_commands: list[dict]) -> int:
    """Append commands to the durable control queue without duplicating identical payloads."""
    if not new_commands:
        return 0
    data = _load_control_payload()
    commands = list(data.get("commands", []))
    acks = list(data.get("acks", []))
    added = 0
    for cmd in new_commands:
        if cmd not in commands:
            commands.append(cmd)
            added += 1
    _write_control_payload(commands, acks)
    return added


def recommended_autosafe_commands_from_status(status: dict) -> list[dict]:
    """Build commands safe to auto-enqueue without extra operator review."""
    control = (status or {}).get("control", {}) or {}
    control_reasons = control.get("recommended_control_reasons", {}) or {}
    commands: list[dict] = []
    for recommendation in control.get("recommended_controls_not_applied", []) or []:
        if recommendation == "tighten_risk":
            command = {"command": "tighten_risk"}
            reasons = control_reasons.get("tighten_risk", [])
            if reasons:
                command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
            commands.append(command)
        if recommendation == "pause_entries":
            command = {"command": "pause_entries"}
            reasons = control_reasons.get("pause_entries", [])
            if reasons:
                command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
            commands.append(command)
    return commands


def review_required_commands_from_status(status: dict) -> list[dict]:
    """Build commands that remain operator-review-required even if recommended.

    K3 fix (bug #5): the previous version auto-generated un-gate commands for
    every strategy in `gated_but_not_recommended`, using the note
    "recommended_by=gate_drift_resolved". This made the resolver treat absence
    from today's recommendation list as a refutation of the original gating
    reason — which is invalid, because `recommended_strategy_gates` is only
    built from edge_compression + execution_decay signals, not from strategy
    health or settlement accuracy. A strategy gated manually for losing 8/8
    settlements would never generate an edge_compression alert (no new trades
    means no new edge signal), and therefore always showed up as "drift"
    → auto un-gate recommendation.

    Until full K3 (GateDecision with reason_code + reason_snapshot + explicit
    reason_refuted() check) lands, we SUPPRESS auto un-gate entirely. Operators
    who want to re-enable a gated strategy must do so via an explicit command,
    not via drift-resolution. This prevents the LLM reporter and daily review
    from suggesting re-enablement based on a broken heuristic.

    recommended_but_not_gated still generates gate-ON commands (those are new
    recommendations from RiskGuard, which IS the correct signal direction).
    """
    control = (status or {}).get("control", {}) or {}
    gate_reasons = control.get("recommended_strategy_gate_reasons", {}) or {}
    commands: list[dict] = []
    for strategy in control.get("recommended_but_not_gated", []) or []:
        command = {
            "command": "set_strategy_gate",
            "strategy": strategy,
            "enabled": False,
        }
        reasons = gate_reasons.get(strategy, [])
        if reasons:
            command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
        commands.append(command)
    # K3: gated_but_not_recommended → auto un-gate loop removed.
    # Do NOT re-add without implementing reason_refuted() on top of
    # reason_code-bearing GateDecision objects.
    return commands


def recommended_commands_from_status(
    status: dict,
    *,
    include_review_required: bool = False,
) -> list[dict]:
    """Build explicit control-plane commands from surfaced recommendation drift.

    Auto-safe commands (for example `tighten_risk`) are always included.
    Review-required commands (currently per-strategy gate flips) are only
    included when the caller explicitly opts in, keeping automation
    conservative by default and forcing all-callers surfaces to say so.
    """
    commands = recommended_autosafe_commands_from_status(status)
    if include_review_required:
        commands.extend(review_required_commands_from_status(status))
    return commands



def enqueue_command(command: dict) -> None:
    data = _load_control_payload()
    commands = data.get("commands", [])
    commands.append(command)
    _write_control_payload(commands, data.get("acks", []))
    refresh_control_state()



def write_commands(commands: list[dict], *, acks: list[dict] | None = None) -> None:
    data = _load_control_payload()
    _write_control_payload(commands, data.get("acks", []) if acks is None else acks)
    refresh_control_state()



def read_control_payload() -> dict:
    return _load_control_payload()



def build_quarantine_clear_command(*, token_id: str, condition_id: str = "", note: str = "") -> dict:
    command = {"command": "acknowledge_quarantine_clear", "token_id": token_id}
    if condition_id:
        command["condition_id"] = condition_id
    if note:
        command["note"] = note
    return command
