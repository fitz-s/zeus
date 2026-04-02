"""Control plane: runtime commands from OpenClaw/Venus without process restart.

Blueprint v2 §10: Supported commands read from state/control_plane.json.
Narrow-by-intent: each command does exactly one thing.
"""

import json
import logging
from datetime import datetime, timezone

from src.config import state_path

logger = logging.getLogger(__name__)

CONTROL_PATH = state_path("control_plane.json")

COMMANDS = {
    "pause_entries",                # Stop entering, keep monitoring
    "resume",                       # Resume after pause
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



def get_edge_threshold_multiplier() -> float:
    return _control_state.get("edge_threshold_multiplier", 1.0)


def strategy_gates() -> dict[str, bool]:
    return dict(_control_state.get("strategy_gates", {}))


def is_strategy_enabled(strategy: str) -> bool:
    gates = _control_state.get("strategy_gates", {})
    if not strategy:
        return True
    return gates.get(strategy, True)



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
    _control_state["acknowledged_quarantine_clear_tokens"] = get_acknowledged_quarantine_clear_tokens()
    data = _load_control_payload()
    gates: dict[str, bool] = {}
    for ack in data.get("acks", []):
        if ack.get("command") != "set_strategy_gate":
            continue
        if ack.get("status") != "executed":
            continue
        strategy = str(ack.get("strategy") or "")
        enabled = ack.get("enabled")
        if strategy and isinstance(enabled, bool):
            gates[strategy] = enabled
    _control_state["strategy_gates"] = gates



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
    if name == "pause_entries":
        _set_state("entries_paused", True)
        return True, ""
    if name == "resume":
        _set_state("entries_paused", False)
        return True, ""
    if name == "tighten_risk":
        _set_state("edge_threshold_multiplier", 2.0)
        return True, ""
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
        return True, ""
    if name == "acknowledge_quarantine_clear":
        if not _extract_quarantine_token_id(cmd):
            return False, "missing_token_id"
        return True, ""
    return True, ""



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


def recommended_commands_from_status(status: dict) -> list[dict]:
    """Build explicit control-plane commands from surfaced recommendation drift.

    This is intentionally non-mutating. It gives external automation a stable
    contract for turning diagnosis into commands without silently applying them.
    """
    control = (status or {}).get("control", {}) or {}
    commands: list[dict] = []
    for recommendation in control.get("recommended_controls_not_applied", []) or []:
        if recommendation == "tighten_risk":
            commands.append({"command": "tighten_risk"})
    for strategy in control.get("recommended_but_not_gated", []) or []:
        commands.append(
            {
                "command": "set_strategy_gate",
                "strategy": strategy,
                "enabled": False,
            }
        )
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
