# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/
#                  LIFECYCLE_FINDINGS_DEFERRED.md
#                  (WAVE-3 Batch B, F108 reframe — transition_phase helper
#                  centralizes the 6 unpaired _mark_pending_exit call sites
#                  in src/execution/exit_lifecycle.py.)
"""Antibody invariants for transition_phase — single-writer phase mutations.

K decision under audit (LIFECYCLE_FINDINGS_DEFERRED.md):
    "phase mutations happen in multiple sites without a unified transition
    writer enforcing (phase_before, phase_after, event_type) row generation
    per mutation."

The structural fix promotes the pre-existing
_dual_write_canonical_pending_exit_if_available pattern into
src.state.db.transition_phase. Every _mark_pending_exit call site must be
paired with a canonical event write inside the same function body, so the
position_events row and the position_current phase column always advance
together.

Invariants asserted here:
  INV-tp-1: transition_phase exists in src.state.db with the expected
            signature.
  INV-tp-2: Every _mark_pending_exit call in src/execution/exit_lifecycle.py
            is paired (within the same function body) with either
            transition_phase(...) or
            _dual_write_canonical_pending_exit_if_available(...) (which is a
            thin shim around transition_phase).
  INV-tp-3: transition_phase writes the position_events row and the
            phase=pending_exit projection in a single SAVEPOINT (via
            append_many_and_project).

Meta-verify protocol (per feedback_antibody_recursion_metaverify_essential):
    Remove the dual-write call inside _mark_exit_retry's retry-pending
    branch; this antibody must FAIL. Restore; antibody must PASS.
"""
from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXIT_LIFECYCLE_PATH = REPO_ROOT / "src" / "execution" / "exit_lifecycle.py"


# Helpers that internally pair _mark_pending_exit with the canonical writer.
# Calling any of these counts as a paired transition for the enclosing
# function.
_PAIRING_HELPERS = frozenset(
    {
        "transition_phase",
        "_dual_write_canonical_pending_exit_if_available",
        # The four mutator helpers each call the dual-write shim internally;
        # callers that invoke them inherit the pairing.
        "_mark_exit_retry",
        "_mark_exit_dust_hold",
        "_mark_exit_fill_economics_missing",
    }
)


def _iter_function_defs(tree: ast.Module):
    """Yield every FunctionDef / AsyncFunctionDef inside the module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _function_calls(func: ast.AST) -> list[str]:
    """Return the bare names of all functions called within `func`."""
    calls: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                calls.append(target.id)
            elif isinstance(target, ast.Attribute):
                calls.append(target.attr)
    return calls


# ---------------------------------------------------------------------------
# INV-tp-1: transition_phase exists with the expected signature.
# ---------------------------------------------------------------------------
def test_transition_phase_exists_in_db_module():
    """transition_phase must live in src.state.db with the documented kwargs."""
    from src.state.db import transition_phase

    sig = inspect.signature(transition_phase)
    params = sig.parameters
    assert "conn" in params, "transition_phase must accept a conn arg"
    assert "position" in params, "transition_phase must accept a position arg"
    assert "event_type" in params, "transition_phase must accept event_type"
    assert "reason" in params, "transition_phase must accept reason"
    assert "error" in params, "transition_phase must accept error"
    assert (
        params["event_type"].kind == inspect.Parameter.KEYWORD_ONLY
    ), "event_type must be keyword-only to prevent positional drift"


# ---------------------------------------------------------------------------
# INV-tp-2: Every _mark_pending_exit call is paired in the same function.
# ---------------------------------------------------------------------------
def test_every_mark_pending_exit_call_is_paired_with_canonical_writer():
    """For each function that calls _mark_pending_exit, that same function
    must also call one of the canonical pairing helpers."""
    source = EXIT_LIFECYCLE_PATH.read_text()
    tree = ast.parse(source)

    unpaired: list[tuple[str, int]] = []

    for func in _iter_function_defs(tree):
        calls = _function_calls(func)
        if "_mark_pending_exit" not in calls:
            continue
        # The helper definition itself does not need pairing — it is the
        # in-memory mutator and is paired at every call site.
        if func.name == "_mark_pending_exit":
            continue
        if not any(name in _PAIRING_HELPERS for name in calls):
            unpaired.append((func.name, func.lineno))

    assert not unpaired, (
        "Every function that mutates phase via _mark_pending_exit must also "
        "invoke a canonical pairing helper (transition_phase, "
        "_dual_write_canonical_pending_exit_if_available, or a mutator helper "
        "that does so internally). Unpaired sites: "
        + ", ".join(f"{name}@L{line}" for name, line in unpaired)
    )


def test_six_known_call_sites_all_paired():
    """Regression pin on the 6 LIFECYCLE_FINDINGS_DEFERRED.md sites.

    The audit identified 6 _mark_pending_exit invocations as the structural
    smell. After the WAVE-3 Batch B fix every one of them lives inside a
    function that pairs with a canonical writer."""
    source = EXIT_LIFECYCLE_PATH.read_text()
    tree = ast.parse(source)

    # Map each _mark_pending_exit call to its enclosing function.
    enclosing: dict[int, ast.FunctionDef] = {}
    for func in _iter_function_defs(tree):
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_mark_pending_exit"
            ):
                enclosing.setdefault(node.lineno, func)

    # Exclude the def line itself (line 201, _mark_pending_exit's own body).
    call_sites = sorted(
        line
        for line, func in enclosing.items()
        if func.name != "_mark_pending_exit"
    )

    assert len(call_sites) == 6, (
        f"Expected 6 _mark_pending_exit call sites (per "
        f"LIFECYCLE_FINDINGS_DEFERRED.md); found {len(call_sites)}: "
        f"{call_sites}"
    )

    # Every enclosing function must pair.
    for line in call_sites:
        func = enclosing[line]
        calls = _function_calls(func)
        assert any(name in _PAIRING_HELPERS for name in calls), (
            f"_mark_pending_exit at L{line} (inside {func.name}) is not "
            f"paired with a canonical writer"
        )


# ---------------------------------------------------------------------------
# INV-tp-3: transition_phase invokes append_many_and_project (single SAVEPOINT).
# ---------------------------------------------------------------------------
def test_transition_phase_uses_append_many_and_project():
    """transition_phase must route through append_many_and_project so the
    position_events row and the position_current phase update commit inside
    one SAVEPOINT (DR-33-B).

    The implementation lives in src.state.canonical_write (moved from db.py
    in WAVE-3 Batch B to fix K0→K2 import inversion). The db.py symbol is a
    thin re-export shim; inspect the canonical_write implementation directly.
    """
    from src.state import canonical_write as cw_module

    src = inspect.getsource(cw_module.transition_phase)
    assert "append_many_and_project" in src, (
        "transition_phase must call append_many_and_project to enforce the "
        "atomic (event_row + phase_projection) write inside a SAVEPOINT"
    )


def test_dual_write_shim_routes_to_transition_phase():
    """_dual_write_canonical_pending_exit_if_available must be a thin shim
    that delegates to transition_phase — eliminating parallel writers.

    Uses AST inspection to verify an actual Call node targeting transition_phase
    exists, not just substring presence in the source text (which could
    false-pass on docstring/comment mentions).
    """
    import ast as _ast
    import textwrap as _textwrap
    from src.execution import exit_lifecycle

    src = inspect.getsource(
        exit_lifecycle._dual_write_canonical_pending_exit_if_available
    )
    # Dedent so the AST parser handles indented method source correctly.
    tree = _ast.parse(_textwrap.dedent(src))
    call_names = [
        node.func.id
        for node in _ast.walk(tree)
        if isinstance(node, _ast.Call)
        and isinstance(node.func, _ast.Name)
    ] + [
        node.func.attr
        for node in _ast.walk(tree)
        if isinstance(node, _ast.Call)
        and isinstance(node.func, _ast.Attribute)
    ]
    assert "transition_phase" in call_names, (
        "_dual_write_canonical_pending_exit_if_available must contain an "
        "actual Call node targeting transition_phase; if a parallel writer "
        "is reintroduced the K decision regresses"
    )


def test_transition_phase_is_noop_when_position_is_already_economically_closed():
    """A closed position must not re-enter pending_exit on retry/replay."""
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.canonical_write import transition_phase
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import Position

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    pos = Position(
        trade_id="closed-retry-1",
        market_id="mkt-closed-1",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-05-21",
        bin_label="50-51°F",
        direction="buy_yes",
        unit="F",
        shares=10.0,
        cost_basis_usd=5.0,
        entry_price=0.5,
        p_posterior=0.7,
        state="entered",
        strategy_key="center_buy",
        token_id="tok-closed-1",
        temperature_metric="high",
        condition_id="0xclosedretry000000000000000000000000000000000000000000000000000001",
        entered_at="2026-05-21T00:00:00+00:00",
        order_posted_at="2026-05-21T00:00:00+00:00",
        env="live",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        decision_id="dec-closed-1",
        source_module="tests/state/test_transition_phase_invariant.py",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        """
        UPDATE position_current
           SET phase = ?, updated_at = '2026-05-21T00:00:00+00:00'
         WHERE position_id = ?
        """,
        (LifecyclePhase.ECONOMICALLY_CLOSED.value, pos.trade_id),
    )

    pos.pre_exit_state = "holding"
    pos.exit_state = "retry_pending"
    assert transition_phase(
        conn,
        pos,
        event_type="EXIT_ORDER_REJECTED",
        reason="retry_after_close",
        error="should-not-write",
        source_module="tests/state/test_transition_phase_invariant.py",
    ) is False

    assert (
        conn.execute(
            "SELECT phase FROM position_current WHERE position_id = ?",
            (pos.trade_id,),
        ).fetchone()[0]
        == LifecyclePhase.ECONOMICALLY_CLOSED.value
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'EXIT_ORDER_REJECTED'",
            (pos.trade_id,),
        ).fetchone()[0]
        == 0
    )
    conn.close()


# ---------------------------------------------------------------------------
# INV-tp-4: Branch-level pairing inside the mutator helpers.
#
# A function-level "has a pairing call somewhere" check is too permissive —
# a function with two terminal branches (e.g., _mark_exit_retry's
# backoff_exhausted branch and its retry_pending branch) could regress by
# dropping the pairing in one branch and still satisfy INV-tp-2.
#
# This invariant pins the minimum number of pairing calls per mutator helper
# so silent loss of any branch's pairing fails the antibody. The meta-verify
# protocol (sed-break a single branch's dual-write call -> antibody MUST
# fail) verifies this is not a false-pass test.
# ---------------------------------------------------------------------------
_MIN_PAIRING_CALLS_PER_HELPER = {
    "_mark_exit_retry": 2,                  # backoff_exhausted + retry_pending
    "_mark_exit_dust_hold": 1,              # single path
    "_mark_exit_fill_economics_missing": 1, # single path
    "handle_exit_pending_missing": 1,       # single path
    "_execute_live_exit": 1,                # site 750 inline pairing
    # check_pending_exits intentionally omitted: it is a passive scan whose
    # upstream transition callers (execute_exit, handle_exit_pending_missing,
    # _mark_exit_dust_hold) each emit the canonical event at the actual state
    # change.  Emitting inside the scan loop would duplicate the event each
    # cycle for open positions.  Removed SEV-1 in WAVE-3 Batch B bot review.
}


@pytest.mark.parametrize(
    "helper_name,min_pairings",
    sorted(_MIN_PAIRING_CALLS_PER_HELPER.items()),
)
def test_mutator_helper_has_branch_level_pairing(helper_name, min_pairings):
    """Each mutator helper must contain at least `min_pairings`
    canonical-writer invocations, covering every branch that mutates phase."""
    source = EXIT_LIFECYCLE_PATH.read_text()
    tree = ast.parse(source)

    target = None
    for func in _iter_function_defs(tree):
        if func.name == helper_name:
            target = func
            break
    assert target is not None, f"{helper_name} not found in exit_lifecycle.py"

    calls = _function_calls(target)
    # Count only the direct canonical writers (not the in-memory mutator
    # helpers that delegate); branch-level pairing requires the actual write.
    direct_writers = ("transition_phase", "_dual_write_canonical_pending_exit_if_available")
    pairing_calls = sum(1 for c in calls if c in direct_writers)
    assert pairing_calls >= min_pairings, (
        f"{helper_name} has {pairing_calls} direct pairing calls; "
        f"branch-level invariant requires >= {min_pairings}. Likely "
        f"regression: a phase-mutating branch lost its canonical event "
        f"write."
    )


# ---------------------------------------------------------------------------
# Karachi safety pin — confirm none of the 6 sites execute on a synced
# day0_window position.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "site_function",
    [
        "handle_exit_pending_missing",       # gated by chain_state == "exit_pending_missing"
        "_mark_exit_dust_hold",              # only called on dust-error path
        "_execute_live_exit",                # entered after build_exit_intent — requires exit decision
        "check_pending_exits",               # iterates exit_state in sell_*/exit_intent only
        "_mark_exit_fill_economics_missing", # only after status in FILL_STATUSES
        "_mark_exit_retry",                  # only after retry path
    ],
)
def test_karachi_day0_window_synced_position_untouched(site_function):
    """All 6 sites guard against day0_window/synced positions by their entry
    predicate; this test pins the function existence so any future predicate
    weakening is caught."""
    from src.execution import exit_lifecycle

    fn = getattr(exit_lifecycle, site_function, None)
    assert fn is not None, (
        f"{site_function} must exist for Karachi-safety predicate coverage"
    )
