# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: 守護 blocker — settlement→redeem resolver unscheduled in EDLI modes
#   (memory #56 "settled-target-still-active" reproducing on Shanghai cca68b44).
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Antibody — _harvester_cycle (the settlement P&L + redeem-intent PRODUCER)
#   MUST be scheduled in EDLI event-driven modes, not only legacy_cron. Without it
#   a FILLED position that rides to market settlement sits phase=active forever:
#   the redeem pollers (consumers) have nothing to consume.
"""Antibody: harvester (settlement→redeem resolver) scheduled in EDLI modes.

RED-first contract:
  (a) _harvester_should_register(mode) is True for EVERY EDLI event-driven mode
      (edli_shadow_no_submit, edli_submit_disabled_bridge, edli_live_canary,
      edli_live) AND for legacy_cron. Before the fix the harvester was gated to
      `live_execution_mode == "legacy_cron"` only, so this is RED for EDLI.

  (b) The scheduler.add_job(_harvester_cycle, ...) call in src/main.py is NOT
      nested under an `if live_execution_mode == "legacy_cron":` exclusive gate
      (AST check). Before the fix the only registration site is inside that
      branch.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"

_EDLI_EVENT_DRIVEN_MODES = (
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
)


def test_harvester_should_register_predicate_covers_edli_modes():
    """(a) The mode-gate predicate registers the harvester in EDLI modes + legacy_cron."""
    import src.main as main_mod

    assert hasattr(main_mod, "_harvester_should_register"), (
        "src/main.py must expose a _harvester_should_register(mode) predicate so the "
        "harvester registration gate is testable and shared with the boot-recovery path."
    )
    pred = main_mod._harvester_should_register

    # legacy_cron keeps the harvester (no regression).
    assert pred("legacy_cron") is True

    # RED before fix: every EDLI event-driven mode must register the resolver.
    for mode in _EDLI_EVENT_DRIVEN_MODES:
        assert pred(mode) is True, (
            f"harvester resolver MUST be scheduled in {mode!r}: a FILLED position that "
            "rides to settlement would otherwise never close/redeem (capital stuck)."
        )

    # 'disabled' mode does not run the trading scheduler — no harvester needed.
    assert pred("disabled") is False


def _harvester_add_job_enclosing_gate() -> list[str]:
    """Return the string conditions guarding scheduler.add_job(_harvester_cycle,...).

    Walks src/main.py AST; for the add_job call whose first positional arg is the
    Name `_harvester_cycle`, collects the source of every enclosing `if` test up to
    the function body. An empty list means unconditional registration.
    """
    tree = ast.parse(_MAIN_PY.read_text())
    src_lines = _MAIN_PY.read_text().splitlines()

    # Locate the target call node.
    target_call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_job":
                if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "_harvester_cycle":
                    target_call = node
                    break
    assert target_call is not None, "scheduler.add_job(_harvester_cycle, ...) not found in src/main.py"
    target_lineno = target_call.lineno

    # Find enclosing if-tests by line range containment.
    enclosing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            start = node.test.lineno
            end = getattr(node, "end_lineno", node.body[-1].end_lineno if node.body else start)
            # Only consider If whose body region contains the call (not the orelse).
            body_start = node.body[0].lineno
            body_end = node.body[-1].end_lineno if node.body else body_start
            if body_start <= target_lineno <= body_end:
                test_src = "\n".join(src_lines[node.test.lineno - 1: getattr(node.test, "end_lineno", node.test.lineno)])
                enclosing.append(test_src.strip())
    return enclosing


def test_harvester_add_job_not_exclusively_legacy_cron():
    """(b) The harvester registration is NOT gated exclusively to legacy_cron.

    RED before fix: the sole add_job(_harvester_cycle, ...) site sits under
    `if live_execution_mode == "legacy_cron":`.
    """
    gates = _harvester_add_job_enclosing_gate()
    legacy_only = [g for g in gates if 'live_execution_mode == "legacy_cron"' in g]
    assert not legacy_only, (
        "scheduler.add_job(_harvester_cycle, ...) is gated exclusively to legacy_cron: "
        f"{gates!r}. It must also register in EDLI event-driven modes."
    )


def test_cascade_contract_requires_harvester_producer():
    """(c) The cascade-liveness contract names `harvester` as a required poller.

    RED before fix: harvester is absent from required_pollers, so the boot guard
    (_assert_cascade_liveness_contract) and the antibody test do not enforce its
    presence — the scheduling gap can silently recur. After the fix the contract
    lists harvester as the producer of REDEEM_INTENT_CREATED on settlement_commands.
    """
    import yaml

    contract = yaml.safe_load(
        (_REPO_ROOT / "architecture" / "cascade_liveness_contract.yaml").read_text()
    )
    poller_ids = {
        p["id"]
        for sm in contract["state_machines"]
        for p in sm.get("required_pollers", []) or []
    }
    assert "harvester" in poller_ids, (
        "cascade_liveness_contract.yaml must list 'harvester' as a required poller "
        "(producer of REDEEM_INTENT_CREATED) so the boot guard enforces its presence "
        "in live modes and the unscheduled-resolver gap cannot recur."
    )

    # The producer entry must be discoverable on the settlement_commands machine,
    # and must NOT be mode=liveness_only (the harvester DOES drive state transitions).
    sc_machine = next(
        sm for sm in contract["state_machines"] if sm["table"] == "settlement_commands"
    )
    harvester_poller = next(
        p for p in sc_machine["required_pollers"] if p["id"] == "harvester"
    )
    assert harvester_poller.get("mode") != "liveness_only", (
        "harvester drives _settle_positions state transitions; mode must not be "
        "liveness_only (that mode forbids state-transition helpers)."
    )
