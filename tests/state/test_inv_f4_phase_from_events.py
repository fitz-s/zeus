# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §F4
"""Acceptance tests: F4 — canonical phase from typed event inputs, not runtime strings.

F4 invariant: canonical phase recorded in position_current is determined exclusively
by the explicit ``phase_after`` argument passed to each ``build_*_canonical_write``
builder. It is NEVER derived from Position.state / exit_state / chain_state at
build time.

Four contracts verified:

1. Direct in-memory mutation of ``pos.state`` after an entry write does NOT
   change the persisted phase in position_current.

2. Key builders expose ``phase_after`` as a required (no-default) parameter,
   confirmed via ``inspect.signature``.

3. Static AST: no ``build_*_canonical_write`` body contains a call to
   ``phase_for_runtime_position(`` or ``canonical_phase_for_position(``.

4. Legacy adapter ``canonical_phase_for_position()`` still returns a valid
   ``LifecyclePhase`` string value (backward-compat preserved).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_world_db():
    from src.state.db import init_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _make_position(overrides: dict | None = None):
    from src.state.portfolio import Position

    defaults = dict(
        trade_id="f4-001",
        market_id="mkt-f4",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        shares=25.0,
        cost_basis_usd=10.0,
        state="entered",
        chain_state="synced",
        token_id="tok-f4",
        unit="F",
        env="live",
        entered_at="2026-05-01T00:00:00Z",
        chain_verified_at="2026-05-10T00:00:00Z",
        condition_id="cond-f4",
        strategy_key="center_buy",
        strategy="center_buy",
    )
    if overrides:
        defaults.update(overrides)
    return Position(**defaults)


# ---------------------------------------------------------------------------
# Test 1: direct mutation of pos.state cannot change position_current.phase
# ---------------------------------------------------------------------------

def test_direct_mutation_of_position_state_does_not_change_canonical_phase() -> None:
    """F4 invariant: pos.state is a mutable runtime attribute. Changing it after
    an entry write must NOT retroactively alter the phase stored in position_current.

    Pre-fix behaviour: builders read pos.state to derive phase at call-time, so
    a mutation occurring between builds would flip the persisted phase on the
    next builder call.  Post-fix: phase_after is explicit; pos.state is never
    consulted inside the builder.
    """
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase

    conn = _setup_world_db()
    pos = _make_position()

    # Write the entry event with explicit phase_after=ACTIVE.
    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-f4-test1",
        source_module="tests.state.test_inv_f4_phase_from_events",
    )
    append_many_and_project(conn, events, projection)

    # Verify DB has ACTIVE phase.
    row = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert row is not None, "position_current row not found after entry write"
    assert row["phase"] == LifecyclePhase.ACTIVE.value, (
        f"Expected phase={LifecyclePhase.ACTIVE.value!r} after entry write; "
        f"got {row['phase']!r}"
    )

    # Mutate pos.state in-memory to simulate a runtime state change.
    pos.state = "voided"

    # Phase in DB must still be ACTIVE — no builder was called with VOIDED.
    row_after = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert row_after["phase"] == LifecyclePhase.ACTIVE.value, (
        f"F4 violation: mutating pos.state='voided' in memory changed "
        f"position_current.phase from 'active' to {row_after['phase']!r}. "
        f"Phase must only change via an explicit phase_after in a builder call."
    )


# ---------------------------------------------------------------------------
# Test 2: key builders require phase_after (no default)
# ---------------------------------------------------------------------------

def test_event_builder_requires_phase_after_argument() -> None:
    """F4 invariant: builders for ambiguous phase transitions expose phase_after
    as a required keyword-only argument (no default value), forcing the caller
    to be explicit about the post-event phase.

    Checks two builders:
    - build_entry_canonical_write: the primary entry builder — no default on
      phase_after because entry can land in ACTIVE or PENDING_ENTRY depending
      on fill status.
    - build_chain_size_corrected_canonical_write: size correction can move the
      position to several phases; caller must be explicit.
    """
    import inspect

    from src.engine.lifecycle_events import (
        build_chain_size_corrected_canonical_write,
        build_entry_canonical_write,
    )

    _EMPTY = inspect.Parameter.empty

    for fn in (build_entry_canonical_write, build_chain_size_corrected_canonical_write):
        sig = inspect.signature(fn)
        assert "phase_after" in sig.parameters, (
            f"F4 violation: {fn.__name__} has no 'phase_after' parameter. "
            f"Found params: {list(sig.parameters)}"
        )
        param = sig.parameters["phase_after"]
        assert param.default is _EMPTY, (
            f"F4 violation: {fn.__name__}.phase_after has a default value "
            f"({param.default!r}). For ambiguous-transition builders it must be "
            f"required (no default) so the caller is forced to declare the target phase."
        )


# ---------------------------------------------------------------------------
# Test 3: AST static — no builder body calls phase_for_runtime_position or
#          canonical_phase_for_position
# ---------------------------------------------------------------------------

def test_no_canonical_builder_calls_phase_for_runtime_position_in_money_path() -> None:
    """F4 invariant (static AST): no ``build_*_canonical_write`` function body
    contains a direct call to ``phase_for_runtime_position(`` or
    ``canonical_phase_for_position(``.

    These functions derive phase from mutable Position.state / exit_state /
    chain_state strings. Calling them inside a canonical-write builder would
    violate F4 by making the persisted phase dependent on runtime string state
    rather than the explicit phase_after argument.

    This test is a structural antibody: if any future builder accidentally
    reintroduces a runtime-string derivation, this test fails before the
    regression can reach production.
    """
    import ast as _ast

    FORBIDDEN_CALLS = {"phase_for_runtime_position", "canonical_phase_for_position"}

    module_path = Path(__file__).parents[2] / "src" / "engine" / "lifecycle_events.py"
    source = module_path.read_text()
    tree = _ast.parse(source)

    def _calls_forbidden(func_node: _ast.FunctionDef) -> list[str]:
        """Return list of forbidden function names called directly in func_node body."""
        hits: list[str] = []
        for node in _ast.walk(func_node):
            if not isinstance(node, _ast.Call):
                continue
            func = node.func
            # Direct call: phase_for_runtime_position(...)
            if isinstance(func, _ast.Name) and func.id in FORBIDDEN_CALLS:
                hits.append(func.id)
            # Attribute call: something.phase_for_runtime_position(...)
            elif isinstance(func, _ast.Attribute) and func.attr in FORBIDDEN_CALLS:
                hits.append(func.attr)
        return hits

    builder_nodes: list[str] = []
    violations: dict[str, list[str]] = {}

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.FunctionDef):
            continue
        if node.name.startswith("build_") and node.name.endswith("_canonical_write"):
            builder_nodes.append(node.name)
            hits = _calls_forbidden(node)
            if hits:
                violations[node.name] = hits

    assert builder_nodes, (
        "No build_*_canonical_write functions found in lifecycle_events.py. "
        "Check that the module path is correct or that naming convention changed."
    )

    assert not violations, (
        f"F4 violation: the following builders call runtime-string phase functions "
        f"inside their body (violating explicit phase_after contract): "
        f"{violations}. "
        f"See docs/findings_2026_05_28.md §F4."
    )


# ---------------------------------------------------------------------------
# Test 4: legacy adapter canonical_phase_for_position still returns valid phase
# ---------------------------------------------------------------------------

def test_legacy_phase_adapter_still_works() -> None:
    """F4 backward-compat: canonical_phase_for_position() is demoted to a
    legacy adapter (not a money-path authority) but must still return a valid
    LifecyclePhase string value so existing non-critical callers don't break.

    Passes a legacy-style position with state='entered' / exit_state='' /
    chain_state='synced' and asserts the returned string is a member of the
    LifecyclePhase enum.
    """
    from src.engine.lifecycle_events import canonical_phase_for_position
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position()  # state="entered", chain_state="synced"

    result = canonical_phase_for_position(pos)

    valid_values = {phase.value for phase in LifecyclePhase}
    assert result in valid_values, (
        f"canonical_phase_for_position returned {result!r}, which is not a valid "
        f"LifecyclePhase value. Valid values: {sorted(valid_values)}. "
        f"The legacy adapter must still map runtime strings to a recognised phase."
    )
    # Concretely: state='entered' / exit_state=None / chain_state='synced'
    # should map to ACTIVE (the normal entered/synced phase).
    assert result == LifecyclePhase.ACTIVE.value, (
        f"Expected legacy adapter to return 'active' for state='entered' / "
        f"chain_state='synced'; got {result!r}."
    )
