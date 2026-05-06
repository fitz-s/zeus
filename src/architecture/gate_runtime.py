# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 68-70 (Gate 5);
#                  ULTIMATE_DESIGN §5 Gate 5; ANTI_DRIFT_CHARTER §3 M1;
#                  RISK_REGISTER R2; capabilities.yaml blocked_when fields.

"""Gate 5: Runtime kill-switch and settlement-window-freeze enforcement.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

Consumes the capabilities.yaml ``blocked_when`` condition list
(``kill_switch_active``, ``settlement_window_freeze_active``, ``risk_level_halt``)
and refuses the named operation when any condition is active.

This gate is NON-BYPASSABLE by design:
  - There is no feature flag (no ZEUS_ROUTE_GATE_RUNTIME=off path).
  - Gate 5 is the runtime safety net. If a condition fires, the system MUST NOT
    run the blocked operation. A feature-flag bypass would allow the system to
    trade through a kill-switch or settlement freeze — the exact failures these
    conditions guard against.
  - Rationale documented in IMPLEMENTATION_PLAN §6 Gate 5: "Gate 5: documented
    manual override per CUTOVER_RUNBOOK." The manual override is a CUTOVER_RUNBOOK
    procedure, NOT a code flag.

Call-site contract:
  gate_runtime.check("live_venue_submit") raises RuntimeError if blocked.
  gate_runtime.check("settlement_write")  raises RuntimeError if blocked.
  call_sites that formerly did inline env-var checks delegate here instead.

ritual_signal schema (CHARTER §3 M1):
  {
    "helper": "gate_runtime",
    "cap_id": "<capability id being checked>",
    "condition": "<which blocked_when condition fired, or null>",
    "decision": "allow" | "refuse",
    "severity": "RUNTIME_BLOCK",
    "evidence_path": null,
    "invocation_ts": "<iso8601>",
    "charter_version": "1.0.0",
    "gate_id": "gate5_runtime",
    "ts": "<iso8601>"
  }
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_RITUAL_SIGNAL_DIR = REPO_ROOT / "logs" / "ritual_signal"
_CHARTER_VERSION = "1.0.0"
_GATE_NAME = "gate_runtime"
_GATE_ID = "gate5_runtime"
_SUNSET_DATE = "2026-08-04"

# ---------------------------------------------------------------------------
# Condition evaluators — map blocked_when strings to env-var checks.
# Each returns (active: bool, detail: str).
# ---------------------------------------------------------------------------

def _kill_switch_active() -> tuple[bool, str]:
    """ZEUS_KILL_SWITCH armed (any of 1/true/on/armed)."""
    val = os.environ.get("ZEUS_KILL_SWITCH", "").lower()
    armed = val in ("1", "true", "on", "armed")
    return armed, f"ZEUS_KILL_SWITCH={val!r}"


def _settlement_window_freeze_active() -> tuple[bool, str]:
    """ZEUS_SETTLEMENT_FREEZE active (any of 1/true/on)."""
    val = os.environ.get("ZEUS_SETTLEMENT_FREEZE", "").lower()
    active = val in ("1", "true", "on")
    return active, f"ZEUS_SETTLEMENT_FREEZE={val!r}"


def _risk_level_halt() -> tuple[bool, str]:
    """ZEUS_RISK_HALT active (any of 1/true/on)."""
    val = os.environ.get("ZEUS_RISK_HALT", "").lower()
    active = val in ("1", "true", "on")
    return active, f"ZEUS_RISK_HALT={val!r}"


# Map from capabilities.yaml blocked_when condition name → evaluator function.
_CONDITION_EVALUATORS: dict[str, object] = {
    "kill_switch_active": _kill_switch_active,
    "settlement_window_freeze_active": _settlement_window_freeze_active,
    "risk_level_halt": _risk_level_halt,
}

# Capabilities and their blocked_when conditions (mirrors capabilities.yaml).
# This is a local cache — if capabilities.yaml changes, update here too.
# Keeping this local (rather than parsing YAML at runtime) avoids I/O on every
# gate check and makes the gate dependency-free at import time.
_CAP_BLOCKED_WHEN: dict[str, list[str]] = {
    "live_venue_submit": ["kill_switch_active", "risk_level_halt"],
    "settlement_write": ["settlement_window_freeze_active"],
    "on_chain_mutation": ["kill_switch_active"],
    "control_write": [],
    "canonical_position_write": [],
    "calibration_persistence_write": [],
    "calibration_decision_group_write": [],
    "decision_artifact_write": [],
    "venue_command_write": [],
    "script_repair_write": [],
    "backtest_diagnostic_write": [],
    "authority_doc_rewrite": [],
    "archive_promotion": [],
    "source_validity_flip": [],
    "calibration_rebuild": [],
    "settlement_rebuild": [],
}


# ---------------------------------------------------------------------------
# ritual_signal emitter
# ---------------------------------------------------------------------------

def _emit_signal(
    cap_id: str,
    condition: str | None,
    decision: str,
) -> None:
    """Emit one ritual_signal JSON line per gate evaluation.

    Schema per ANTI_DRIFT_CHARTER §3 M1 — all required fields included:
      helper, task_id, fit_score, advisory_or_blocking, outcome,
      invocation_ts, charter_version.
    Extended fields: cap_id, condition, decision, severity, gate_id, ts.
    """
    _RITUAL_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    month = now[:7]  # YYYY-MM
    out_path = _RITUAL_SIGNAL_DIR / f"{month}.jsonl"
    # task_id: hash of cap_id + condition for uniqueness per evaluation.
    task_payload = f"{cap_id}|{condition or 'none'}"
    task_id = hashlib.sha256(task_payload.encode()).hexdigest()[:16]
    record = {
        # CHARTER §3 M1 required fields
        "helper": _GATE_NAME,
        "task_id": task_id,
        "fit_score": 1.0,
        "advisory_or_blocking": "blocking",  # Gate 5 is always blocking (non-bypassable)
        "outcome": "blocked" if decision == "refuse" else "applied",
        "invocation_ts": now,
        "charter_version": _CHARTER_VERSION,
        # Extended fields (Gate 5 specific)
        "cap_id": cap_id,
        "condition": condition,
        "decision": decision,
        "severity": "RUNTIME_BLOCK" if decision == "refuse" else "WORKING",
        "evidence_path": None,
        "gate_id": _GATE_ID,
        "ts": now,
    }
    with out_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check(cap_id: str) -> None:
    """Check whether cap_id is currently blocked by any active condition.

    Raises:
        RuntimeError: if any blocked_when condition for cap_id is active.
        Emits ritual_signal on every evaluation (allow or refuse).

    Non-bypassable: there is no env-var or feature flag to skip this check.
    See module docstring for the rationale.
    """
    conditions = _CAP_BLOCKED_WHEN.get(cap_id, [])

    for cond_name in conditions:
        evaluator = _CONDITION_EVALUATORS.get(cond_name)
        if evaluator is None:
            # Unknown condition — fail closed.
            _emit_signal(cap_id, cond_name, "refuse")
            raise RuntimeError(
                f"[gate_runtime] BLOCKED cap={cap_id!r}: "
                f"unknown condition {cond_name!r} — failing closed. "
                f"(Gate 5 non-bypassable: IMPLEMENTATION_PLAN §6 days 68-70)"
            )
        active, detail = evaluator()  # type: ignore[operator]
        if active:
            _emit_signal(cap_id, cond_name, "refuse")
            raise RuntimeError(
                f"[gate_runtime] BLOCKED cap={cap_id!r}: "
                f"condition {cond_name!r} is active ({detail}). "
                f"kill switch: condition={cond_name!r} — system must not execute this operation while condition holds. "
                f"(Gate 5 non-bypassable: IMPLEMENTATION_PLAN §6 days 68-70)"
            )

    # All conditions clear — allow.
    _emit_signal(cap_id, None, "allow")


def is_blocked(cap_id: str) -> tuple[bool, str | None]:
    """Non-raising variant: return (blocked, condition_name_or_None).

    Callers that need to query state before raising (e.g. status endpoints)
    use this. Does NOT emit ritual_signal — use check() for enforcement.
    """
    conditions = _CAP_BLOCKED_WHEN.get(cap_id, [])
    for cond_name in conditions:
        evaluator = _CONDITION_EVALUATORS.get(cond_name)
        if evaluator is None:
            return True, cond_name
        active, _ = evaluator()  # type: ignore[operator]
        if active:
            return True, cond_name
    return False, None
