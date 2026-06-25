# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: 守護 blocker — settlement→redeem resolver unscheduled in EDLI modes
#   (memory #56 "settled-target-still-active" reproducing on Shanghai cca68b44).
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Antibody — _harvester_cycle (the settlement P&L + redeem-intent PRODUCER)
#   MUST be scheduled in EDLI event-driven modes, not only legacy_cron. Without it
#   a FILLED position that rides to market settlement sits phase=active forever:
#   the redeem pollers (consumers) have nothing to consume.
# Reuse: inspect src/main.py scheduler.add_job(_harvester_cycle, ...) registration
#   site and src/engine/harvest_cycle.py before re-running; verify the job appears in
#   the scheduler for edli_live, edli_submit_disabled_bridge modes.
"""Antibody: harvester (settlement→redeem resolver) scheduled in EDLI modes.

RED-first contract:
  (a) _harvester_should_register(mode) is True for EVERY EDLI event-driven mode
      (edli_shadow_no_submit, edli_submit_disabled_bridge, edli_live)
      AND for legacy_cron. Before the fix the harvester was gated to
      `live_execution_mode == "legacy_cron"` only, so this is RED for EDLI.

  (b) The scheduler.add_job(_harvester_cycle, ...) call in src/main.py is NOT
      nested under an `if live_execution_mode == "legacy_cron":` exclusive gate
      (AST check). Before the fix the only registration site is inside that
      branch.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import threading
import time
import types

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"
# PROCESS-TOPOLOGY REFACTOR P4 (2026-06-08, system_decomposition_plan §8 Step 2): the
# harvester resolver add_job moved from src/main.py to the P4 post-trade-capital daemon.
_P4_DAEMON = _REPO_ROOT / "src" / "ingest" / "post_trade_capital_daemon.py"

# The order daemon has one event-driven live mode. Historical shadow/bridge strings are
# experiment/archive semantics and must not re-enter live scheduler expectations.
_EDLI_EVENT_DRIVEN_MODES = ("edli_live",)


def test_harvester_should_register_predicate_covers_live_boot_recovery_modes():
    """(a) The order-daemon boot recovery predicate covers live + legacy only."""
    import src.main as main_mod

    assert hasattr(main_mod, "_harvester_should_register"), (
        "src/main.py must expose a _harvester_should_register(mode) predicate so the "
        "harvester registration gate is testable and shared with the boot-recovery path."
    )
    pred = main_mod._harvester_should_register

    # legacy_cron keeps the harvester (no regression).
    assert pred("legacy_cron") is True

    for mode in _EDLI_EVENT_DRIVEN_MODES:
        assert pred(mode) is True, (
            f"boot harvester recovery MUST run in {mode!r}: a FILLED position that "
            "already settled before restart should be drained immediately."
        )

    assert pred("edli_shadow_no_submit") is False
    assert pred("edli_submit_disabled_bridge") is False
    # 'disabled' mode does not run the trading scheduler — no harvester needed.
    assert pred("disabled") is False


def test_boot_settlement_redeem_recovery_queues_background_harvester(monkeypatch):
    """Boot settlement recovery must not block scheduler startup."""
    import src.main as main_mod

    started = threading.Event()
    release = threading.Event()

    def _fake_harvester_cycle():
        started.set()
        release.wait(timeout=2.0)

    monkeypatch.setitem(
        sys.modules,
        "src.execution.post_trade_capital",
        types.SimpleNamespace(_harvester_cycle=_fake_harvester_cycle),
    )
    monkeypatch.setattr(
        main_mod,
        "_settings_section",
        lambda name, default=None: {"enabled": True} if name == "edli" else (default or {}),
    )
    monkeypatch.setattr(main_mod, "_live_execution_mode", lambda _cfg: "edli_live")
    monkeypatch.setattr(main_mod, "_harvester_should_register", lambda _mode: True)

    t0 = time.monotonic()
    main_mod._edli_boot_settlement_redeem_recovery()
    elapsed = time.monotonic() - t0
    try:
        assert elapsed < 0.2, f"boot recovery blocked scheduler path for {elapsed:.3f}s"
        assert started.wait(timeout=1.0), "background harvester thread did not start"
    finally:
        release.set()


def _harvester_add_job_enclosing_gate() -> list[str]:
    """Return the string conditions guarding scheduler.add_job(_harvester_cycle,...).

    PROCESS-TOPOLOGY REFACTOR P4: the harvester add_job moved to the P4 post-trade-capital
    daemon, where it is registered UNCONDITIONALLY (the whole P4 process is the POST_TRADE
    lane — it always runs regardless of the order daemon's trading mode). This helper now
    walks the P4 daemon AST and collects the source of every enclosing `if` test around the
    add_job(_harvester_cycle, ...) call. An empty list means unconditional registration —
    the post-lift expectation.
    """
    tree = ast.parse(_P4_DAEMON.read_text())
    src_lines = _P4_DAEMON.read_text().splitlines()

    # Locate the target call node.
    target_call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_job":
                # The P4 daemon wraps the cycle in _scheduler_job("harvester")(_harvester_cycle);
                # match the add_job whose first positional arg references _harvester_cycle.
                arg0 = node.args[0] if node.args else None
                refs_harvester = False
                if isinstance(arg0, ast.Name) and arg0.id == "_harvester_cycle":
                    refs_harvester = True
                elif isinstance(arg0, ast.Call):
                    for sub in ast.walk(arg0):
                        if isinstance(sub, ast.Name) and sub.id == "_harvester_cycle":
                            refs_harvester = True
                            break
                if refs_harvester:
                    target_call = node
                    break
    assert target_call is not None, (
        "scheduler.add_job(_harvester_cycle, ...) not found in the P4 post-trade-capital daemon"
    )
    target_lineno = target_call.lineno

    # Find enclosing if-tests by line range containment.
    enclosing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body_start = node.body[0].lineno
            body_end = node.body[-1].end_lineno if node.body else body_start
            if body_start <= target_lineno <= body_end:
                test_src = "\n".join(src_lines[node.test.lineno - 1: getattr(node.test, "end_lineno", node.test.lineno)])
                enclosing.append(test_src.strip())
    return enclosing


def test_harvester_add_job_not_exclusively_legacy_cron():
    """(b) The harvester registration is NOT gated exclusively to legacy_cron.

    After the P4 lift the harvester is registered UNCONDITIONALLY in the post-trade-capital
    daemon (POST_TRADE follow-up that must run even when trading is paused/dead — the
    SUPERIORITY of the lift). So the enclosing-gate list must be empty (no mode gate at all),
    which trivially satisfies 'not gated exclusively to legacy_cron'.
    """
    gates = _harvester_add_job_enclosing_gate()
    legacy_only = [g for g in gates if 'live_execution_mode == "legacy_cron"' in g]
    assert not legacy_only, (
        "scheduler.add_job(_harvester_cycle, ...) is gated exclusively to legacy_cron: "
        f"{gates!r}. After the P4 lift it must register unconditionally in the post-trade "
        "daemon so settled positions get drained in EVERY mode."
    )
    assert gates == [], (
        "after the P4 lift the harvester must be registered UNCONDITIONALLY in the "
        f"post-trade-capital daemon (no mode gate), got enclosing gates: {gates!r}"
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
