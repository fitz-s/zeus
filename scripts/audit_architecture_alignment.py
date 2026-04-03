#!/usr/bin/env python3
"""Audit Zeus alignment with its architecture authority and Venus/OpenClaw host surfaces."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
OPENCLAW_ROOT = WORKSPACE_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_assumptions import run_validation as validate_assumptions


def run_audit() -> dict:
    heartbeat = WORKSPACE_ROOT / "HEARTBEAT.md"
    runbook = WORKSPACE_ROOT / "OPERATOR_RUNBOOK.md"
    known_gaps = WORKSPACE_ROOT / "memory" / "known_gaps.md"
    workspace_agents = WORKSPACE_ROOT / "AGENTS.md"
    workspace_identity = WORKSPACE_ROOT / "IDENTITY.md"
    openclaw_config = OPENCLAW_ROOT / "openclaw.json"
    cycle_runner = PROJECT_ROOT / "src" / "engine" / "cycle_runner.py"
    cycle_runtime = PROJECT_ROOT / "src" / "engine" / "cycle_runtime.py"
    replay = PROJECT_ROOT / "src" / "engine" / "replay.py"

    assumptions = validate_assumptions()
    cycle_lines = sum(1 for _ in cycle_runner.open())
    cycle_text = cycle_runner.read_text(encoding="utf-8")
    replay_text = replay.read_text(encoding="utf-8")
    runtime_exists = cycle_runtime.exists()
    openclaw_payload = json.loads(openclaw_config.read_text()) if openclaw_config.exists() else {}
    phase_delegation = all(
        needle in cycle_text
        for needle in [
            "_runtime.run_chain_sync(",
            "_runtime.cleanup_orphan_open_orders(",
            "_runtime.entry_bankroll_for_cycle(",
            "_runtime.materialize_position(",
            "_runtime.reconcile_pending_positions(",
            "_runtime.execute_monitoring_phase(",
            "_runtime.execute_discovery_phase(",
        ]
    )

    external_surface_assumptions = {
        "operator_surfaces_present": {
            "workspace_HEARTBEAT.md": heartbeat.exists(),
            "workspace_OPERATOR_RUNBOOK.md": runbook.exists(),
            "workspace_memory_known_gaps.md": known_gaps.exists(),
            "workspace_AGENTS.md": workspace_agents.exists(),
            "workspace_IDENTITY.md": workspace_identity.exists(),
        },
        "openclaw_acp_enabled": bool(openclaw_payload.get("acp", {}).get("enabled")),
        "openclaw_allowed_agents": openclaw_payload.get("acp", {}).get("allowedAgents", []),
    }

    findings = {
        "external_surface_assumptions": external_surface_assumptions,
        "assumptions_valid": assumptions["valid"],
        "assumption_mismatches": assumptions["mismatches"],
        "cycle_runner_lines": cycle_lines,
        "cycle_runtime_exists": runtime_exists,
        "cycle_runner_phase_delegation": phase_delegation,
        "cycle_runner_blueprint_aligned": runtime_exists and phase_delegation and cycle_lines <= 300,
        "replay_decision_time_guard_present": "datetime(available_at) <= datetime(?)" in replay_text,
        "replay_uses_actual_trade_reference": "get_decision_reference_for" in replay_text,
    }

    blocking = []
    advisory_external = []
    if not all(external_surface_assumptions["operator_surfaces_present"].values()):
        advisory_external.append("operator_surfaces_missing")
    if not findings["assumptions_valid"]:
        blocking.append("assumptions_invalid")
    if not external_surface_assumptions["openclaw_acp_enabled"]:
        advisory_external.append("openclaw_acp_disabled")
    if not findings["replay_decision_time_guard_present"]:
        blocking.append("replay_future_data_guard_missing")
    if not findings["replay_uses_actual_trade_reference"]:
        blocking.append("replay_decision_reference_missing")
    if not findings["cycle_runner_blueprint_aligned"]:
        blocking.append("cycle_runner_scope_drift")

    return {
        "blocking": blocking,
        "advisory_external": advisory_external,
        "repo_verdict": "pass" if not blocking else "fail",
        "external_boundary_verdict": "advisory-only",
        "findings": findings,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
