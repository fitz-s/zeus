from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReasonCode(str, Enum):
    MANUAL_KILL_FOR_LOSSES = "manual_kill_for_losses"
    EDGE_COMPRESSION = "edge_compression"
    EXECUTION_DECAY = "execution_decay"
    OPERATOR_OVERRIDE = "operator_override"
    PHASE_1_CANARY_RESTRICTION = "phase_1_canary_restriction"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class GateDecision:
    enabled: bool
    reason_code: ReasonCode
    reason_snapshot: dict[str, Any]   # data that justifies the gate
    gated_at: str                     # ISO timestamp
    gated_by: str                     # "operator" | "auto:<rule_name>"

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "reason_code": self.reason_code.value,
            "reason_snapshot": self.reason_snapshot,
            "gated_at": self.gated_at,
            "gated_by": self.gated_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GateDecision":
        return cls(
            enabled=d["enabled"],
            reason_code=ReasonCode(d.get("reason_code", "unspecified")),
            reason_snapshot=d.get("reason_snapshot", {}),
            gated_at=d.get("gated_at", ""),
            gated_by=d.get("gated_by", "unknown"),
        )


def reason_refuted(decision: GateDecision, current_data: dict) -> bool:
    """Per-reason-code refutation rules. Returns True if the gate reason no longer holds.

    Default: False (conservative -- don't recommend un-gate without evidence).
    Manual un-gate via explicit operator command bypasses this entirely.
    """
    # Future: add per-reason refutation logic as data becomes available.
    # For Phase 1, all reasons are conservatively un-refutable.
    return False
