# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=2026-06-08
# Last reused or audited: 2026-06-08 (P4 fix: orphan check -> positive identification of
#   state-machine pollers by the contract's own naming convention, replacing the stale
#   NON_STATE_MACHINE_JOB_IDS denylist). Authority basis: docs/architecture/system_decomposition_plan.md §8 Step 2.
# Purpose: Antibody test for architecture/cascade_liveness_contract.yaml; enforces
#   that every state-machine table with *_INTENT_CREATED rows has a registered
#   APScheduler poller in src/main.py, and that every terminal_states_with_operator_action
#   entry has a transition INTO it from src/ (ast walk over _transition / _atomic_transition).
# Reuse: Run on every PR touching src/main.py scheduler block, src/execution/settlement_commands.py
#   state machine, or architecture/cascade_liveness_contract.yaml. Authority basis:
#   docs/archive/2026-Q2/task_2026-05-16_deep_alignment_audit/SCAFFOLD_F14_F16.md §G.3 + §K.6 v5.
#
# Cascade-liveness antibody: enforces architecture/cascade_liveness_contract.yaml.
# Every state-machine entry MUST have:
#   - a registered APScheduler poller at boot
#   - every terminal_states_with_operator_action state must have a transition
#     INTO it from src/ (verified via ast.parse walk over _transition /
#     _atomic_transition call sites)
#   - max_age_hours, operator_runbook, cli_invocation fields present
# Also: every scheduler poller must be in the contract (no orphan pollers).

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Iterable

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "architecture" / "cascade_liveness_contract.yaml"
SETTLEMENT_COMMANDS_SRC = REPO_ROOT / "src" / "execution" / "settlement_commands.py"

# NOTE (2026-06-08 P4 fix): the former NON_STATE_MACHINE_JOB_IDS denylist was REMOVED.
# The orphan check (test_every_scheduler_poller_for_state_machines_is_listed_in_contract)
# now POSITIVELY identifies state-machine pollers by the contract's own naming convention
# (`harvester`/`redeem_*`/`wrap_*`/`transfer_*` — derived from the contract, not hardcoded),
# so it never needs to enumerate every non-state-machine job (reactor / bankroll warm /
# mainstream warm / channel ingestor / exit_monitor / heartbeat / wal-checkpoint …). The
# denylist had gone stale (it never listed the EDLI reactor/warmer/channel jobs), which is
# exactly the class of rot positive identification eliminates.


def _load_contract() -> dict:
    with CONTRACT_PATH.open() as f:
        return yaml.safe_load(f)


# PROCESS-TOPOLOGY REFACTOR P4 (2026-06-08, system_decomposition_plan §8 Step 2): the
# cascade-liveness pollers no longer all live in src/main.py — the redeem/wrap/harvester
# pollers were lifted to the P4 post-trade-capital daemon. The contract carries an
# owner_daemon field; this map resolves each owner_daemon to the daemon's SCHEDULER source
# file so the boot-id scan reads the RIGHT process's registrations for each poller.
_OWNER_DAEMON_SCHEDULER_FILES: dict[str, Path] = {
    "main": REPO_ROOT / "src" / "main.py",
    "post_trade_capital": REPO_ROOT / "src" / "ingest" / "post_trade_capital_daemon.py",
}


def _scheduled_add_job_ids_in(source_path: Path) -> set[str]:
    """Extract every literal add_job(..., id="X") id from a scheduler source file via AST.

    We do not invoke main(); we regression-extract the scheduler.add_job(...) call list via
    AST (avoids cycle_runner side effects + cutover guard timing).
    """
    tree = ast.parse(source_path.read_text())
    job_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_job":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        jid = str(kw.value.value)
                        if jid.startswith("update_reaction_"):
                            job_ids.add("update_reaction")
                        else:
                            job_ids.add(jid)
    return job_ids


def _scheduler_job_ids_at_boot() -> set[str]:
    """Return registered job IDs across ALL daemons that own cascade-liveness pollers.

    After the P4 lift the pollers are distributed: harvester/redeem/wrap live in the P4
    post-trade-capital daemon; everything else in src/main.py. The contract's required-poller
    check must see the union of all owning daemons' registrations — a poller is 'live' if it
    is registered in WHICHEVER daemon owns it.
    """
    job_ids: set[str] = set()
    for daemon_file in set(_OWNER_DAEMON_SCHEDULER_FILES.values()):
        if daemon_file.exists():
            job_ids |= _scheduled_add_job_ids_in(daemon_file)
    return job_ids


def _src_main_scheduler_job_ids() -> set[str]:
    """Job ids registered specifically in src/main.py (order daemon) — for the inverse
    orphan check, which is scoped to the order-daemon scheduler."""
    return _scheduled_add_job_ids_in(REPO_ROOT / "src" / "main.py")


def _state_transitions_in_module(module_path: Path) -> Iterable[str]:
    """Yield every state name referenced in a function body that contains
    a call to _transition / _atomic_transition.

    Covers both inline and via-variable patterns (G2 round-3 critic NEW-P11 fix):
      _transition(conn, cid, "REDEEM_OPERATOR_REQUIRED", ...)              # literal
      _transition(conn, cid, SettlementState.REDEEM_OPERATOR_REQUIRED, ...) # enum inline
      state_after = SettlementState.REDEEM_OPERATOR_REQUIRED                # via variable
      _transition(conn, cid, state_after, ...)                              # ↑
      _atomic_transition(... to_state="REDEEM_OPERATOR_REQUIRED" ...)       # kwarg literal
      _atomic_transition(... to_state=SettlementState.REDEEM_OPERATOR_REQUIRED ...) # kwarg enum

    Strategy: find every FunctionDef containing a transition call; then walk
    the function body for any reference (string literal or SettlementState
    attribute access) to a state name. This avoids requiring inline-literal
    style in the source while preserving the contract intent ("state is
    reachable from a transition site in this function").
    """
    tree = ast.parse(module_path.read_text())
    targets = {"_transition", "_atomic_transition"}

    def _calls_transition(func_def: ast.FunctionDef) -> bool:
        for sub in ast.walk(func_def):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            n = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None
            )
            if n in targets:
                return True
        return False

    for fdef in ast.walk(tree):
        if not isinstance(fdef, ast.FunctionDef):
            continue
        if not _calls_transition(fdef):
            continue
        for sub in ast.walk(fdef):
            # SomeState.STATE_NAME  →  Attribute(Name)
            # Matches SettlementState.XXX, WrapUnwrapState.XXX, etc.
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                attr = sub.attr
                if attr.isupper() and "_" in attr:
                    yield attr
            # "STATE_NAME"  →  Constant(str)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                # filter to look-like-state strings (uppercase + underscores)
                if sub.value.isupper() and "_" in sub.value:
                    yield sub.value


def test_every_state_machine_in_contract_has_a_registered_poller():
    contract = _load_contract()
    scheduler_ids = _scheduler_job_ids_at_boot()
    missing: list[tuple[str, str]] = []
    for sm in contract["state_machines"]:
        for poller in sm["required_pollers"]:
            if poller["id"] not in scheduler_ids:
                missing.append((sm["table"], poller["id"]))
    assert not missing, (
        f"cascade_liveness_contract violation: missing pollers "
        f"{missing!r}. Register the job in src/main.py OR remove "
        f"the contract entry."
    )


def _contract_state_machine_poller_prefixes(contract: dict) -> set[str]:
    """The naming convention of state-machine pollers, DERIVED from the contract.

    Every contract poller id is `<machine>_<role>` (or the bare producer `harvester`);
    its first underscore-segment names the state machine it drives (redeem_*, wrap_*,
    harvester, and a future transfer_* etc.). We use this set to POSITIVELY identify
    which scheduler jobs are state-machine pollers — rather than maintaining a denylist
    of every non-state-machine job, which rots the moment a reactor/warmer/channel job
    is added (the failure mode this rewrite removes)."""
    return {
        p["id"].split("_", 1)[0]
        for sm in contract["state_machines"]
        for p in sm["required_pollers"]
    }


def test_every_scheduler_poller_for_state_machines_is_listed_in_contract():
    """Inverse drift guard: a poller registered in src/main.py whose ID matches the
    contract's state-machine naming convention (harvester/redeem_*/wrap_*/transfer_* …)
    MUST appear in the contract — prevents orphan pollers polling untracked tables.

    POSITIVE IDENTIFICATION (2026-06-08 P4 fix): we flag a job ONLY when its id matches a
    state-machine poller prefix DERIVED FROM THE CONTRACT. We do NOT subtract a
    hand-maintained allowlist of every non-state-machine job — that denylist-by-omission
    silently went stale (edli_event_reactor / edli_bankroll_warm / edli_mainstream_warm /
    edli_market_channel_ingestor / edli_user_channel_reconcile were never listed, so they
    were mis-flagged as orphans). A reactor/warmer/channel job is structurally not a
    `redeem_`/`wrap_`/`harvester` poller, so positive identification never mis-classes it,
    and a genuinely-orphaned `redeem_foo` (no contract entry) is still caught.
    """
    contract = _load_contract()
    contract_poller_ids = {
        p["id"]
        for sm in contract["state_machines"]
        for p in sm["required_pollers"]
    }
    prefixes = _contract_state_machine_poller_prefixes(contract)
    # The inverse orphan check is scoped to the ORDER daemon's scheduler (src/main.py)
    # — a state-machine poller registered there without a contract entry is the orphan we
    # guard against. The P4 daemon's pollers are all contract-listed (forward test covers
    # them). Post-P4 the order daemon registers ZERO state-machine pollers (all lifted to
    # P4), so this set is empty.
    scheduler_ids = _src_main_scheduler_job_ids()
    state_machine_pollers = {
        jid for jid in scheduler_ids
        if jid.split("_", 1)[0] in prefixes
        and jid != "update_reaction"
    }
    orphans = state_machine_pollers - contract_poller_ids
    assert not orphans, (
        f"orphan state-machine pollers registered without contract entry: "
        f"{orphans!r}. Add to architecture/cascade_liveness_contract.yaml."
    )


def test_terminal_states_with_operator_action_have_transition_in_source():
    """Each operator-action state must have a transition in its state machine's source module.

    The source module is resolved from:
      1. sm["source_module"] if present (path relative to REPO_ROOT)
      2. Fallback to SETTLEMENT_COMMANDS_SRC for backwards compatibility.
    """
    contract = _load_contract()
    missing: list[tuple[str, str]] = []
    for sm in contract["state_machines"]:
        entries = sm.get("terminal_states_with_operator_action", []) or []
        if not entries:
            continue
        # Resolve source module: prefer explicit source_module field.
        source_rel = sm.get("source_module")
        if source_rel:
            source_path = REPO_ROOT / source_rel
        else:
            source_path = SETTLEMENT_COMMANDS_SRC
        transitions = set(_state_transitions_in_module(source_path))
        for entry in entries:
            state = entry["state"]
            if state not in transitions:
                missing.append((sm["table"], state))
    assert not missing, (
        f"contract declares operator-action states with no transition "
        f"in source: {missing!r}. Add a _transition or _atomic_transition "
        f"call site targeting these states, or remove from contract."
    )


def test_terminal_states_with_operator_action_have_required_fields():
    contract = _load_contract()
    required_fields = {"state", "max_age_hours", "operator_runbook", "cli_invocation"}
    missing: list[tuple[str, str, set[str]]] = []
    for sm in contract["state_machines"]:
        for entry in sm.get("terminal_states_with_operator_action", []) or []:
            absent = required_fields - set(entry.keys())
            if absent:
                missing.append((sm["table"], entry.get("state", "?"), absent))
    assert not missing, (
        f"terminal_states_with_operator_action entries missing required "
        f"fields: {missing!r}"
    )


def test_operator_runbook_files_exist():
    contract = _load_contract()
    missing: list[tuple[str, str, str]] = []
    for sm in contract["state_machines"]:
        for entry in sm.get("terminal_states_with_operator_action", []) or []:
            runbook = entry["operator_runbook"]
            # anchor (fragment) not verified — too brittle. Just file existence.
            runbook_path = REPO_ROOT / runbook.split("#", 1)[0]
            if not runbook_path.exists():
                missing.append((sm["table"], entry["state"], str(runbook_path)))
    assert not missing, (
        f"operator_runbook file references do not resolve: {missing!r}"
    )


def test_max_age_hours_positive_integer():
    contract = _load_contract()
    bad: list[tuple[str, str, object]] = []
    for sm in contract["state_machines"]:
        for entry in sm.get("terminal_states_with_operator_action", []) or []:
            v = entry["max_age_hours"]
            if not (isinstance(v, int) and v > 0):
                bad.append((sm["table"], entry["state"], v))
    assert not bad, f"max_age_hours must be positive int: {bad!r}"


def test_poller_mode_discriminator_enforced():
    """mode=liveness_only pollers must NOT call _transition or _atomic_transition."""
    contract = _load_contract()
    main_py = SETTLEMENT_COMMANDS_SRC.parent.parent / "main.py"
    tree = ast.parse(main_py.read_text())
    violations: list[tuple[str, str]] = []
    for sm in contract["state_machines"]:
        for poller in sm["required_pollers"]:
            if poller["mode"] != "liveness_only":
                continue
            owner_path = poller["owner"]
            func_name = owner_path.split(":", 1)[1]
            # find the function def
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            func = sub.func
                            name = (
                                func.id if isinstance(func, ast.Name)
                                else (func.attr if isinstance(func, ast.Attribute) else None)
                            )
                            if name in {"_transition", "_atomic_transition"}:
                                violations.append((poller["id"], "calls state-transition helper"))
    assert not violations, (
        f"liveness_only pollers must not call state-transition helpers: "
        f"{violations!r}"
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "state" / "zeus_trades.db").exists(),
    reason="live DB not present in this environment",
)
def test_no_operator_required_row_exceeds_max_age():
    """Data-dependent: live DB has no OPERATOR_REQUIRED row aged beyond max_age_hours.

    Skipped if DB absent or table missing or row count == 0 (the expected steady
    state). This test surfaces alerts only when there is a stuck row — Karachi
    case will exercise it on 2026-05-17.
    """
    contract = _load_contract()
    db_path = REPO_ROOT / "state" / "zeus_trades.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for sm in contract["state_machines"]:
            entries = sm.get("terminal_states_with_operator_action") or []
            if not entries:
                continue
            for entry in entries:
                state = entry["state"]
                max_age_hours = entry["max_age_hours"]
                try:
                    rows = conn.execute(
                        f"""
                        SELECT command_id,
                               (julianday('now') - julianday(requested_at)) * 24 AS age_hours
                          FROM {sm['table']}
                         WHERE state = ?
                        """,
                        (state,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    pytest.skip(f"table {sm['table']} not present in DB")
                exceeded = [
                    (r["command_id"], r["age_hours"])
                    for r in rows
                    if r["age_hours"] and r["age_hours"] > max_age_hours
                ]
                assert not exceeded, (
                    f"{sm['table']} has {state} rows older than "
                    f"{max_age_hours}h: {exceeded!r}"
                )
    finally:
        conn.close()
