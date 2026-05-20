# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=2026-05-20
# Purpose: Antibody test for architecture/cascade_liveness_contract.yaml; enforces
#   that every state-machine table with *_INTENT_CREATED rows has a registered
#   APScheduler poller in src/main.py, and that every terminal_states_with_operator_action
#   entry has a transition INTO it from src/ (ast walk over _transition / _atomic_transition).
# Reuse: Run on every PR touching src/main.py scheduler block, src/execution/settlement_commands.py
#   state machine, or architecture/cascade_liveness_contract.yaml. Authority basis:
#   docs/operations/task_2026-05-16_deep_alignment_audit/SCAFFOLD_F14_F16.md §G.3 + §K.6 v5.
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

# Non-state-machine scheduler jobs that may exist in src/main.py and are not
# required to appear in the cascade_liveness_contract registry. These are
# operational/scheduling jobs unrelated to *_INTENT_CREATED state machines.
NON_STATE_MACHINE_JOB_IDS = frozenset({
    "opening_hunt",
    "day0_capture",
    "imminent_open_capture",
    "market_discovery",
    "harvester",
    "heartbeat",
    "venue_heartbeat",
    # deployment_freshness + wu_daily + imminent_open_capture are operational,
    # not state-machine pollers.
    "deployment_freshness",
    "wu_daily",
    "imminent_open_capture",
})


def _load_contract() -> dict:
    with CONTRACT_PATH.open() as f:
        return yaml.safe_load(f)


def _scheduler_job_ids_at_boot() -> set[str]:
    """Boot src.main with a recording scheduler and return registered job IDs.

    Mirrors the fake-scheduler pattern used elsewhere in tests/. We do not
    invoke main(); we directly inspect what main.py would register.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from unittest.mock import patch

    recorded: list[str] = []

    class _RecordingScheduler(BackgroundScheduler):
        def add_job(self, *args, **kwargs):  # type: ignore[override]
            jid = kwargs.get("id") or (args[2] if len(args) > 2 else None)
            if jid:
                # update_reaction_<HH:MM> is parameterized at runtime; collapse
                # to a generic prefix for the registry check.
                if jid.startswith("update_reaction_"):
                    recorded.append("update_reaction")
                else:
                    recorded.append(jid)
            return None

        def start(self, *args, **kwargs):  # type: ignore[override]
            return None

        def get_jobs(self):  # type: ignore[override]
            class _Stub:
                def __init__(self, jid):
                    self.id = jid
            return [_Stub(j) for j in recorded]

    # We don't need to actually call main(); regression-extract the
    # scheduler.add_job(...) call list from src/main.py via AST. This avoids
    # cycle_runner side effects + cutover guard timing.
    src = SETTLEMENT_COMMANDS_SRC.parent.parent / "main.py"
    tree = ast.parse(src.read_text())
    job_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # match `scheduler.add_job(...)` calls
            if isinstance(func, ast.Attribute) and func.attr == "add_job":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        jid = str(kw.value.value)
                        if jid.startswith("update_reaction_"):
                            job_ids.add("update_reaction")
                        else:
                            job_ids.add(jid)
    return job_ids


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


def test_every_scheduler_poller_for_state_machines_is_listed_in_contract():
    """Inverse drift guard: a poller registered in src/main.py whose ID
    starts with redeem_/wrap_unwrap_/transfer_ etc. must appear in the
    contract — prevents orphan pollers polling untracked tables."""
    contract = _load_contract()
    contract_poller_ids = {
        p["id"]
        for sm in contract["state_machines"]
        for p in sm["required_pollers"]
    }
    scheduler_ids = _scheduler_job_ids_at_boot()
    state_machine_pollers = {
        jid for jid in scheduler_ids
        if jid not in NON_STATE_MACHINE_JOB_IDS
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
    conn = sqlite3.connect(str(db_path))
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
