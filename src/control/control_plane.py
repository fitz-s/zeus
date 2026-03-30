"""Control plane: runtime commands from OpenClaw/Venus without process restart.

Blueprint v2 §10: Supported commands read from state/control_plane.json.
Narrow-by-intent: each command does exactly one thing.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import STATE_DIR

logger = logging.getLogger(__name__)

CONTROL_PATH = STATE_DIR / "control_plane.json"

# Supported commands
COMMANDS = {
    "pause_entries",       # Stop entering, keep monitoring
    "resume",             # Resume after pause
    "tighten_risk",       # Double edge thresholds temporarily
    "request_status",     # Force status_summary write
    "set_strategy_gate",  # Enable/disable individual strategies
}


def process_commands() -> list[str]:
    """Read and process pending commands. Returns list of processed command names."""
    if not CONTROL_PATH.exists():
        return []

    try:
        with open(CONTROL_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    commands = data.get("commands", [])
    if not commands:
        return []

    processed = []
    acks = data.get("acks", [])

    for cmd in commands:
        name = cmd.get("command")
        if name not in COMMANDS:
            logger.warning("Unknown control command: %s", name)
            continue

        logger.info("CONTROL: executing %s", name)

        if name == "pause_entries":
            _set_state("entries_paused", True)
        elif name == "resume":
            _set_state("entries_paused", False)
        elif name == "tighten_risk":
            _set_state("edge_threshold_multiplier", 2.0)
        elif name == "request_status":
            from src.observability.status_summary import write_status
            write_status()

        acks.append({
            "command": name,
            "acked_at": datetime.now(timezone.utc).isoformat(),
            "status": "executed",
        })
        processed.append(name)

    # Write acks and clear commands
    with open(CONTROL_PATH, "w") as f:
        json.dump({"commands": [], "acks": acks[-20:]}, f, indent=2)

    return processed


# Simple state store for control flags
_control_state: dict = {}


def _set_state(key: str, value) -> None:
    _control_state[key] = value


def is_entries_paused() -> bool:
    return _control_state.get("entries_paused", False)


def get_edge_threshold_multiplier() -> float:
    return _control_state.get("edge_threshold_multiplier", 1.0)
