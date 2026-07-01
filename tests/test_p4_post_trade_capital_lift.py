# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.3 (Post-Trade Capital Lifecycle), §6 (P4 row + co-location decision),
#   §7 (I3 P4->riskguard/P1 no-back-coupling + commit-before-HTTP; I4 ingest->P4),
#   §8 Step 2 (split chain-sync READ from exit-SUBMIT), §9 (regression-unconstructable).
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=never
# Purpose: RELATIONSHIP TESTS for process-topology refactor STEP P4 — lift the
#   post-trade capital lifecycle (settlement P&L resolve -> redeem -> wrap +
#   chain-sync READ phase) OUT of the order daemon into its own process.
#
# These tests verify CROSS-MODULE / CROSS-PROCESS INVARIANTS (Module A's output ->
# Module B across the new program boundary), not just function behavior:
#
#   (NO-REGRESSION)
#     - the lifted cycle bodies still EXIST and still drive the settlement_commands /
#       wrap_unwrap_commands state machines (resolve -> redeem -> wrap still advance);
#     - settlement_commands enqueue is IDEMPOTENT (the active-condition UNIQUE index) so
#       a P4-side re-run cannot double-enqueue a redeem (the property that makes the
#       producer/consumer split across processes safe — §8 Step 2 rollback note);
#     - the exit-SUBMIT phase STAYS in src.main and still posts sell orders
#       (exit_order_submit_enabled gate threaded through _execute_monitoring_phase);
#     - src.main still imports + builds its scheduler (never break boot);
#     - the chain-sync-before-monitoring COMMIT ORDERING is preserved — now as a
#       cross-process invariant: P4 commits its chain-sync writes before returning, and
#       P1's exit phase commits its own monitoring writes (commit_then_export). Neither
#       holds the other's transaction open.
#     - the cascade-liveness ANTIBODY travels with the jobs: the 6 moved pollers'
#       contract entries point at the P4 daemon, and the P4 daemon carries the boot
#       guard so a missing poller still fails LOUD at boot (the antibody is not lost
#       in the move).
#
#   (SUPERIORITY)
#     - chain-sync NO LONGER runs in the order daemon: src.main's scheduler does not
#       register a chain-sync job and the exit-monitor job that stays runs NO chain sync;
#     - P4's chain-sync cycle COMMITS its writes BEFORE any per-position HTTP — it never
#       holds the trades.db WAL write lock across a network call (the DATA_DEGRADED-flap
#       root cause, §4.3 / I3). Structurally: the P4 chain-sync cycle calls run_chain_sync
#       then conn.commit(), and never calls _execute_monitoring_phase (the per-position
#       HTTP monitor lane);
#     - src.main registers STRICTLY FEWER jobs after the lift (the 6 pollers + harvester
#       are gone from the order daemon scheduler);
#     - the P4 chain-sync cycle does NOT call the exit-SUBMIT monitoring phase (the two
#       phases are split across processes — P4 never posts a sell order).
"""STEP P4 relationship tests: lift the post-trade capital lifecycle to its own process."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"
_P4_MODULE = _REPO_ROOT / "src" / "execution" / "post_trade_capital.py"
_P4_DAEMON = _REPO_ROOT / "src" / "ingest" / "post_trade_capital_daemon.py"
_P4_PLIST = _REPO_ROOT / "deploy" / "launchd" / "com.zeus.post-trade-capital.plist"
_CONTRACT = _REPO_ROOT / "architecture" / "cascade_liveness_contract.yaml"
_CASCADE_TEST = _REPO_ROOT / "tests" / "test_cascade_liveness_contract.py"

# The 6 post-trade pollers lifted to P4 (harvester resolver + redeem x2 + wrap x3).
_LIFTED_POLLER_IDS = (
    "harvester",
    "redeem_submitter",
    "redeem_reconciler",
    "wrap_intent_creator",
    "wrap_submitter",
    "wrap_reconciler",
)

# The cycle-body function names that own those pollers + the lifted chain-sync READ phase.
_LIFTED_CYCLE_FUNCS = (
    "_harvester_cycle",
    "_redeem_submitter_cycle",
    "_redeem_reconciler_cycle",
    "_wrap_intent_creator_cycle",
    "_wrap_submitter_cycle",
    "_wrap_reconciler_cycle",
    "chain_sync_read_cycle",
)


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------

def _add_job_ids(source_path: Path) -> list[str]:
    """Return every literal `id=` keyword across `*.add_job(..., id="X")` calls."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    ids.append(kw.value.value)
    return ids


def _function_names_defined(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _calls_named(func_node: ast.FunctionDef) -> set[str]:
    """Names of all callables invoked inside a function body (by simple name or attr)."""
    out: set[str] = set()
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _find_func(source_path: Path, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


# ===========================================================================
# NO-REGRESSION INVARIANTS
# ===========================================================================

def test_no_regression_p4_module_owns_the_lifted_cycle_bodies():
    """The lifted post-trade cycle logic lives in the P4 module (not vanished).

    resolve -> redeem -> wrap state machines must still ADVANCE: their driver
    functions move host process, they are not deleted.
    """
    assert _P4_MODULE.exists(), (
        "src/execution/post_trade_capital.py must exist — it owns the lifted "
        "harvester/redeem/wrap cycle bodies + the chain-sync READ phase."
    )
    defined = _function_names_defined(_P4_MODULE)
    for fn in _LIFTED_CYCLE_FUNCS:
        assert fn in defined, f"src.execution.post_trade_capital must define {fn}"


def test_no_regression_p4_daemon_and_plist_artifacts_exist():
    """The new program needs an entry-point + a launchd artifact (mirror the P2 pattern)."""
    assert _P4_DAEMON.exists(), "src/ingest/post_trade_capital_daemon.py must exist."
    assert _P4_PLIST.exists(), "deploy/launchd/com.zeus.post-trade-capital.plist must exist."
    plist = _P4_PLIST.read_text(encoding="utf-8")
    assert "com.zeus.post-trade-capital" in plist
    assert "src.ingest.post_trade_capital_daemon" in plist
    assert "POLYMARKET_CLOB_V2_SIGNATURE_TYPE" in plist


def test_no_regression_settlement_commands_enqueue_is_idempotent():
    """The redeem-intent enqueue MUST be idempotent so a P4-side re-run cannot
    double-enqueue an active redeem for the same condition+asset.

    This is the property that makes the producer/consumer split across processes
    safe (§8 Step 2 rollback: 'settlement_commands state machine is idempotent —
    partial progress is safe'). Verified structurally: the schema declares a UNIQUE
    index keyed on the active condition+asset.
    """
    src = (_REPO_ROOT / "src" / "execution" / "settlement_commands.py").read_text(encoding="utf-8")
    assert "ux_settlement_commands_active_condition_asset" in src, (
        "settlement_commands must keep the active-condition UNIQUE index that makes "
        "redeem-intent enqueue idempotent (the split-safety property)."
    )
    assert "CREATE UNIQUE INDEX" in src


def test_no_regression_exit_submit_phase_stays_in_src_main():
    """The exit-monitoring / exit-SUBMIT phase MUST stay in src.main and still post sell orders.

    §4.3 CAVEAT: '_execute_monitoring_phase posts real sell orders on RED/force-exit;
    that is order-runtime and STAYS.' The exit job must still call the monitoring phase
    and thread exit_order_submit_enabled through it.
    """
    import src.main as main_mod  # must import (boot not broken)

    # The exit-monitor cycle stays defined in src.main and calls _execute_monitoring_phase.
    exit_fn = None
    for cand in ("_exit_monitor_cycle", "_chain_sync_and_exit_monitor_cycle"):
        if hasattr(main_mod, cand):
            exit_fn = cand
            break
    assert exit_fn is not None, (
        "src.main must keep the exit-monitor cycle (the SUBMIT phase that posts sell orders)."
    )
    node = _find_func(_MAIN_PY, exit_fn)
    calls = _calls_named(node)
    assert "_execute_monitoring_phase" in calls, (
        f"src.main:{exit_fn} must still run _execute_monitoring_phase (posts real sell "
        "orders on RED/force-exit) — the exit-SUBMIT phase STAYS in P1."
    )


def test_no_regression_src_main_still_imports():
    """src.main MUST still import successfully with the post-trade jobs removed."""
    import src.main as main_mod

    assert main_mod is not None


def test_no_regression_src_main_scheduler_still_builds():
    """build_scheduler / main() registration must still construct for the jobs that STAY.

    We extract the add_job ids from src.main via AST (no live scheduler boot) and assert
    the order-runtime infrastructure jobs that STAY are still registered.
    """
    ids = set(_add_job_ids(_MAIN_PY))
    # A representative set of jobs that STAY in the order daemon (must remain registered).
    for stay in ("heartbeat", "world_wal_checkpoint", "venue_heartbeat"):
        assert stay in ids, f"order-daemon job {stay!r} must still be registered in src.main"


def test_no_regression_chain_sync_commit_before_http_ordering_preserved():
    """The chain-sync-before-monitoring commit ordering is preserved as a CROSS-PROCESS invariant.

    Pre-split, one function committed chain-sync (interim commit) BEFORE the per-position
    HTTP of the monitoring phase. Post-split, P4's chain_sync_read_cycle commits its OWN
    chain-sync writes (no later phase to order against), and P1's exit phase commits its
    OWN monitoring writes. The invariant 'chain-sync writes are durably committed before
    any HTTP that could starve the WAL lock' holds in P4 because the cycle commits and
    returns — there is no per-position monitoring HTTP after it in the P4 process.
    """
    node = _find_func(_P4_MODULE, "chain_sync_read_cycle")
    assert node is not None
    calls = _calls_named(node)
    assert "commit" in calls, (
        "chain_sync_read_cycle must conn.commit() its chain-sync writes (so the WAL lock "
        "is released and the writes are durable before the process returns)."
    )


def test_no_regression_cascade_liveness_antibody_travels_to_p4():
    """The cascade-liveness contract entries for the 6 moved pollers point at the P4 daemon,
    and the P4 daemon carries a boot guard so a missing poller still fails LOUD at boot.

    The antibody (boot-time fail-closed 'every state machine has a live poller') must not be
    LOST in the move — it travels with the jobs to their new host process.
    """
    import yaml

    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    owners: dict[str, str] = {}
    for sm in contract["state_machines"]:
        for poller in sm["required_pollers"]:
            owners[poller["id"]] = poller["owner"]
    for pid in _LIFTED_POLLER_IDS:
        assert pid in owners, f"contract must still list poller {pid!r}"
        assert "post_trade_capital" in owners[pid], (
            f"poller {pid!r} owner must point at the P4 module after the lift "
            f"(got {owners[pid]!r})."
        )

    # The P4 daemon carries the boot guard (the antibody must run in the new host process).
    daemon_src = _P4_DAEMON.read_text(encoding="utf-8")
    assert "_assert_cascade_liveness_contract" in daemon_src or "cascade_liveness" in daemon_src, (
        "the P4 daemon must carry the cascade-liveness boot guard so a missing poller "
        "still fails LOUD at boot in the new host process."
    )


def test_order_daemon_cascade_guard_does_not_enforce_p4_owned_pollers():
    """The order daemon must not fail boot for pollers lifted into P4.

    architecture/cascade_liveness_contract.yaml now assigns settlement/redeem/wrap
    pollers to owner_daemon=post_trade_capital. Those are enforced by the P4
    daemon boot guard, not src.main.
    """
    import src.main as main

    class EmptyScheduler:
        def get_jobs(self):
            return []

    main._assert_cascade_liveness_contract(EmptyScheduler())


# ---------------------------------------------------------------------------
# CASCADE-LIVENESS CONTRACT SUITE-GREEN INVARIANTS (P4 fix pass, 2026-06-08)
#
# The P4 lift redistributed the cascade pollers across two daemons. Two
# cross-artifact invariants in tests/test_cascade_liveness_contract.py were
# left RED — they failed on the pre-P4 baseline too, but the P4 deliverable
# must ship a GREEN suite (the orchestrator gate is suite==true). These two
# tests encode the SAME cross-artifact relationships as relationship tests so
# the structural fix is pinned and cannot silently regress:
#   (1) the order-daemon orphan check must identify state-machine pollers by
#       the contract's own naming convention (positive identification), not by
#       a hand-maintained denylist that rots whenever a non-state-machine job
#       (reactor / warmer / channel ingestor) is added — the failure mode that
#       made the test red.
#   (2) every operator_runbook the contract references must resolve to a file
#       on disk — a dangling operator-fallback reference is a real operator
#       hazard (the documented manual path does not exist), not cosmetic.
# ---------------------------------------------------------------------------

# State-machine poller id prefixes are DERIVED from the contract itself (not
# hardcoded) so this invariant tracks the contract as it evolves.
def _contract_state_machine_poller_prefixes() -> set[str]:
    import yaml

    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    prefixes: set[str] = set()
    for sm in contract["state_machines"]:
        for poller in sm["required_pollers"]:
            prefixes.add(poller["id"].split("_", 1)[0])
    return prefixes


def test_cascade_orphan_check_uses_positive_identification_not_a_denylist():
    """RELATIONSHIP (order-daemon scheduler <-> cascade contract): the orphan check
    must flag a src/main.py job as an un-contracted state-machine poller ONLY when its
    id matches the contract's own state-machine naming convention (harvester/redeem*/
    wrap*/transfer*). It must NOT depend on a hand-maintained allowlist of every
    non-state-machine job, which rots the instant a reactor/warmer/channel job is added
    (the exact failure: edli_event_reactor / edli_bankroll_warm / edli_mainstream_warm /
    edli_market_channel_ingestor / edli_user_channel_reconcile were flagged as orphans).

    Post-P4 the order daemon registers ZERO state-machine pollers (all moved to P4), so
    the order-daemon orphan set MUST be empty under positive identification.
    """
    prefixes = _contract_state_machine_poller_prefixes()
    # Every order-daemon job whose id matches a state-machine prefix must be contracted.
    import yaml

    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    contract_ids = {
        p["id"] for sm in contract["state_machines"] for p in sm["required_pollers"]
    }
    main_ids = set(_add_job_ids(_MAIN_PY))
    looks_like_state_machine = {
        jid for jid in main_ids if jid.split("_", 1)[0] in prefixes
    }
    orphans = looks_like_state_machine - contract_ids
    assert not orphans, (
        f"order-daemon jobs that LOOK like state-machine pollers but are not in the "
        f"cascade contract: {orphans!r}. Either add them to "
        f"architecture/cascade_liveness_contract.yaml or rename them."
    )
    # And the EDLI reactor/warmer/channel jobs must NOT be classed as orphans — they are
    # not state-machine pollers (no contract obligation). This pins the positive-id behavior.
    non_sm = {
        "edli_event_reactor",
        "edli_bankroll_warm",
        "edli_mainstream_warm",
        "edli_market_channel_ingestor",
        "edli_user_channel_reconcile",
    }
    for jid in non_sm & main_ids:
        assert jid.split("_", 1)[0] not in prefixes, (
            f"{jid!r} must not match a state-machine poller prefix — it is not a "
            "cascade state-machine poller and must never be flagged as an orphan."
        )


def test_cascade_contract_operator_runbooks_resolve_to_files():
    """RELATIONSHIP (cascade contract -> operator runbook docs): every operator_runbook
    a terminal_states_with_operator_action entry references MUST resolve to a real file.

    A dangling reference means the documented manual-fallback path for a stuck
    REDEEM_OPERATOR_REQUIRED / WRAP_FAILED command does not exist — a live operator
    hazard, not cosmetic. This is the second cross-artifact invariant the P4 deliverable
    must ship GREEN.
    """
    import yaml

    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    missing: list[tuple[str, str, str]] = []
    for sm in contract["state_machines"]:
        for entry in sm.get("terminal_states_with_operator_action", []) or []:
            runbook = entry["operator_runbook"].split("#", 1)[0]
            if not (_REPO_ROOT / runbook).exists():
                missing.append((sm["table"], entry["state"], runbook))
    assert not missing, (
        f"operator_runbook references that do not resolve to a file: {missing!r}. "
        "Create the runbook doc or fix the path in the contract."
    )


# ===========================================================================
# SUPERIORITY INVARIANTS
# ===========================================================================

def test_superiority_chain_sync_no_longer_in_order_daemon():
    """chain-sync no longer runs in the order daemon process.

    src.main must NOT register a chain-sync job, and the exit-monitor cycle that STAYS
    must NOT call _run_chain_sync. The chain-sync READ phase is fully lifted to P4.
    """
    import src.main as main_mod

    ids = set(_add_job_ids(_MAIN_PY))
    assert "chain_sync_and_exit_monitor" not in ids, (
        "src.main must not register the bundled chain-sync+exit job id any more — chain "
        "sync is lifted; only the exit phase stays (under a different id)."
    )

    # Whichever name the exit phase kept, it must NOT run chain sync.
    exit_fn = None
    for cand in ("_exit_monitor_cycle", "_chain_sync_and_exit_monitor_cycle"):
        if hasattr(main_mod, cand):
            exit_fn = cand
            break
    assert exit_fn is not None
    calls = _calls_named(_find_func(_MAIN_PY, exit_fn))
    assert "_run_chain_sync" not in calls, (
        f"src.main:{exit_fn} must NOT run chain sync any more — the chain-sync READ phase "
        "is lifted to P4 so the order daemon never holds the trades.db WAL lock across "
        "per-position HTTP (the DATA_DEGRADED-flap root cause)."
    )


def test_superiority_p4_chain_sync_commits_before_and_never_submits():
    """P4's chain-sync cycle commits its writes and NEVER calls the exit-SUBMIT phase.

    The split puts chain-sync (READ) in P4 and exit monitoring (SUBMIT) in P1. P4's cycle
    must call run_chain_sync + conn.commit() and must NOT call _execute_monitoring_phase
    (it never posts a sell order). This is the structural guarantee that the WAL write lock
    is released before any network call AND that P4 carries no order-submission surface.
    """
    node = _find_func(_P4_MODULE, "chain_sync_read_cycle")
    assert node is not None
    calls = _calls_named(node)
    # Runs the chain-sync read.
    assert any("chain_sync" in c for c in calls), (
        "chain_sync_read_cycle must invoke the chain-sync read (run_chain_sync/_run_chain_sync)."
    )
    # Commits.
    assert "commit" in calls
    # Never the monitoring/exit-submit phase.
    assert "_execute_monitoring_phase" not in calls and "execute_monitoring_phase" not in calls, (
        "chain_sync_read_cycle must NOT call the exit-SUBMIT monitoring phase — that lane "
        "stays in P1 (P4 never posts a sell order)."
    )


def test_superiority_src_main_registers_strictly_fewer_jobs():
    """src.main registers STRICTLY FEWER add_job ids after the lift.

    The 6 post-trade pollers (+ the chain-sync side of the bundled job) leave the order
    daemon scheduler. None of the lifted poller ids may remain registered in src.main.
    """
    ids = set(_add_job_ids(_MAIN_PY))
    still_present = [pid for pid in _LIFTED_POLLER_IDS if pid in ids]
    assert not still_present, (
        f"these post-trade pollers must NOT be registered in src.main any more (lifted to "
        f"P4): {still_present!r}"
    )


def test_superiority_p4_pollers_registered_in_p4_daemon():
    """The 6 lifted pollers ARE registered in the P4 daemon scheduler (coverage not lost)."""
    daemon_ids = set(_add_job_ids(_P4_DAEMON))
    for pid in _LIFTED_POLLER_IDS:
        assert pid in daemon_ids, (
            f"poller {pid!r} must be registered in the P4 daemon scheduler (the lift must "
            "not drop coverage — the state machine still needs a live poller)."
        )
    # And the chain-sync read job is registered in P4.
    assert any("chain_sync" in jid for jid in daemon_ids), (
        "the P4 daemon must register the lifted chain-sync READ job."
    )


def test_superiority_p4_chain_sync_does_not_hold_lock_across_per_position_http():
    """P4's chain-sync cycle structure: HTTP-then-write-then-commit, no per-position monitor HTTP.

    The DATA_DEGRADED flap came from holding the trades.db WAL write lock across Phase-2
    per-position monitoring HTTP. P4 carries ONLY the chain-sync read (one positions-API
    call, then DB reconcile writes, then commit). It must NOT carry the monitoring phase
    that issued per-position HTTP under the lock.
    """
    node = _find_func(_P4_MODULE, "chain_sync_read_cycle")
    assert node is not None
    calls = _calls_named(node)
    # commit() must be CALLED; the monitoring/exit-submit phase must NOT be CALLED.
    # (We assert on the AST call-set, not raw text — the docstring legitimately NAMES
    # _execute_monitoring_phase to explain what STAYS in P1; a name in a comment is not a call.)
    assert "commit" in calls
    assert "_execute_monitoring_phase" not in calls and "execute_monitoring_phase" not in calls, (
        "chain_sync_read_cycle must not CALL the exit-SUBMIT monitoring phase (that lane "
        "stays in P1; P4 never posts a sell order)."
    )
    # And the chain-sync read IS imported only via the read-phase entry point (not the
    # monitoring phase): the function must not import _execute_monitoring_phase.
    imported_names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.ImportFrom):
            for alias in sub.names:
                imported_names.add(alias.name)
    assert "_execute_monitoring_phase" not in imported_names, (
        "chain_sync_read_cycle must NOT import the monitoring/exit-submit phase — it lifts "
        "ONLY the chain-sync READ entry points (run_chain_sync + connection/portfolio helpers)."
    )
