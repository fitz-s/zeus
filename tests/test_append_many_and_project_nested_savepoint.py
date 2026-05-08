# Created: 2026-04-24
# Last reused/audited: 2026-05-07
# Authority basis: DR-33-B (data-readiness-tail) atomicity refactor of
# `src/state/ledger.py::append_many_and_project`. Reused for Wave26 canonical
# event env authority while preserving the DR-33-B savepoint contract. Replaces pre-DR-33-B
# `with conn:` with explicit SAVEPOINT so callers that already hold an
# outer SAVEPOINT can invoke this function without silent release per
# memory rule L30.

"""Antibody for DR-33-B nested-SAVEPOINT safety.

Pre-DR-33-B: `append_many_and_project` used `with conn:` which commits +
releases the innermost SAVEPOINT on clean exit. If a caller held an
outer SAVEPOINT (e.g. `sp_candidate_*` in `cycle_runtime.py`) and
invoked `append_many_and_project`, the caller's SAVEPOINT was silently
released — breaking ROLLBACK semantics on subsequent errors.

Post-DR-33-B: the function uses explicit `SAVEPOINT sp_ampp_<token>` +
`RELEASE` / `ROLLBACK TO` so it nests cleanly inside a caller's
SAVEPOINT.

This test file pins:
1. Top-level call (no outer SAVEPOINT): works as before.
2. Nested call inside caller's SAVEPOINT: caller's SAVEPOINT survives
   clean release of the inner SAVEPOINT.
3. Caller's ROLLBACK after successful inner call correctly rolls back
   the inner writes — the inner SAVEPOINT did NOT silently commit them.
4. Exception inside `append_many_and_project`: the inner SAVEPOINT
   rolls back; caller's SAVEPOINT + state prior to the call is intact.
5. Multiple nested invocations from same caller work (each creates
   a unique SAVEPOINT name via secrets.token_hex).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import get_connection, init_schema
from src.state.ledger import append_many_and_project


def _canonical_event(position_id: str, event_type: str = "ENTRY_ORDER_POSTED",
                     sequence_no: int = 1) -> dict:
    """Minimal canonical event payload covering CANONICAL_POSITION_EVENT_COLUMNS."""
    return {
        "event_id": f"evt-{position_id}-{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "occurred_at": "2026-04-23T00:00:00Z",
        "phase_before": None,
        "phase_after": "pending_entry",
        "strategy_key": "settlement_capture",
        "decision_id": "test_decision",
        "snapshot_id": "snap1",
        "order_id": None,
        "command_id": None,
        "caused_by": None,
        "idempotency_key": f"idem-{position_id}-{sequence_no}",
        "venue_status": None,
        "source_module": "tests.test_dr33b_nested_savepoint",
        "env": "live",
        "payload_json": "{}",
    }


def _canonical_projection(position_id: str) -> dict:
    """Minimal canonical projection payload covering CANONICAL_POSITION_CURRENT_COLUMNS."""
    return {
        "position_id": position_id,
        "phase": "pending_entry",
        "trade_id": f"{position_id}-trade",
        "market_id": "market1",
        "city": "paris",
        "cluster": "test_cluster",
        "target_date": "2026-04-23",
        "bin_label": "15-16",
        "direction": "buy_yes",
        "unit": "C",
        "size_usd": 0.5,
        "shares": 1.0,
        "cost_basis_usd": 0.5,
        "entry_price": 0.5,
        "p_posterior": 0.5,
        "last_monitor_prob": 0.5,
        "last_monitor_edge": 0.0,
        "last_monitor_market_price": 0.5,
        "decision_snapshot_id": "snap1",
        "entry_method": "test_method",
        "strategy_key": "settlement_capture",
        "edge_source": "test_source",
        "discovery_mode": "day0_capture",
        "chain_state": "pending_tracked",
        "token_id": "tok1",
        "no_token_id": "notok1",
        "condition_id": "cond1",
        "order_id": None,
        "order_status": None,
        "updated_at": "2026-04-23T00:00:00Z",
        "temperature_metric": "high",
    }


def _setup(tmp_path) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "dr33b_nested_savepoint.db")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Baseline: top-level call still works
# ---------------------------------------------------------------------------


def test_top_level_call_commits_events_and_projection(tmp_path):
    """No outer SAVEPOINT — function creates its own SAVEPOINT + commits
    on clean release. Events and projection visible after call returns."""
    conn = _setup(tmp_path)
    events = [_canonical_event("p1")]
    projection = _canonical_projection("p1")
    append_many_and_project(conn, events, projection)
    n_events = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    n_proj = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
    assert n_events == 1
    assert n_proj == 1


# ---------------------------------------------------------------------------
# Core DR-33-B invariant: caller's outer SAVEPOINT survives
# ---------------------------------------------------------------------------


def test_nested_call_leaves_outer_savepoint_intact(tmp_path):
    """Caller creates outer SAVEPOINT, calls append_many_and_project
    (which creates inner SAVEPOINT + releases it), then rolls back outer
    SAVEPOINT. All writes from the nested call must be rolled back — this
    proves the outer SAVEPOINT was not silently released mid-call."""
    conn = _setup(tmp_path)
    conn.execute("SAVEPOINT sp_outer")
    try:
        events = [_canonical_event("p1")]
        projection = _canonical_projection("p1")
        append_many_and_project(conn, events, projection)
        # Verify writes are visible mid-outer-SAVEPOINT
        mid_events = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        assert mid_events == 1
        # Now rollback the outer SAVEPOINT
        conn.execute("ROLLBACK TO SAVEPOINT sp_outer")
        conn.execute("RELEASE SAVEPOINT sp_outer")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_outer")
        conn.execute("RELEASE SAVEPOINT sp_outer")
        raise
    # After outer rollback, the nested writes must have been rolled back
    post_events = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    post_proj = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
    assert post_events == 0, (
        f"DR-33-B failure: outer ROLLBACK did not revert inner writes "
        f"({post_events} events remain) — inner SAVEPOINT must have "
        f"silently released the outer"
    )
    assert post_proj == 0


def test_nested_call_commits_when_outer_releases(tmp_path):
    """Clean path: nested call + outer RELEASE SAVEPOINT → writes persist."""
    conn = _setup(tmp_path)
    conn.execute("SAVEPOINT sp_outer")
    events = [_canonical_event("p1")]
    projection = _canonical_projection("p1")
    append_many_and_project(conn, events, projection)
    conn.execute("RELEASE SAVEPOINT sp_outer")
    # After clean release, writes persist
    n_events = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert n_events == 1


# ---------------------------------------------------------------------------
# Exception inside inner call rolls back inner, caller SAVEPOINT intact
# ---------------------------------------------------------------------------


def test_inner_exception_rolls_back_inner_only(tmp_path):
    """If append_many_and_project raises (e.g., INTEGRITY_ERROR on a
    duplicate primary key), the inner SAVEPOINT rolls back; the caller's
    outer SAVEPOINT is unaffected and caller can continue or rollback
    independently."""
    conn = _setup(tmp_path)
    # Pre-seed one projection row
    append_many_and_project(conn, [_canonical_event("p1")], _canonical_projection("p1"))
    n_before = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert n_before == 1

    conn.execute("SAVEPOINT sp_outer")
    # Write some data via another append_many_and_project inside outer SAVEPOINT
    # (instead of a raw INSERT which would need full column coverage).
    append_many_and_project(conn, [_canonical_event("p_outer")], _canonical_projection("p_outer"))
    mid_count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert mid_count == 2

    # Now invoke append_many_and_project with a malformed payload that will raise
    # BEFORE the SAVEPOINT body executes — require_payload_fields raises ValueError
    # before SAVEPOINT opens. To exercise the inner-SAVEPOINT-rollback path, pass
    # a payload whose validators pass but the DB rejects (e.g., duplicate event_id).
    with pytest.raises(Exception):
        # Second call with same event_id (UNIQUE PRIMARY KEY on event_id) — first
        # insert inside inner SAVEPOINT succeeds, second raises IntegrityError,
        # inner SAVEPOINT rolls back.
        duplicate_event = _canonical_event("p_outer", sequence_no=1)  # same event_id
        append_many_and_project(
            conn,
            [duplicate_event],
            _canonical_projection("p_outer"),
        )

    # Inner SAVEPOINT rolled back; outer SAVEPOINT writes still present
    count_after_inner_fail = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert count_after_inner_fail == 2, (
        f"DR-33-B failure: inner SAVEPOINT rollback should leave outer "
        f"state intact, got {count_after_inner_fail} events (expected 2)"
    )

    # Caller can choose to roll back the outer SAVEPOINT
    conn.execute("ROLLBACK TO SAVEPOINT sp_outer")
    conn.execute("RELEASE SAVEPOINT sp_outer")
    final = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert final == 1  # back to pre-outer state


# ---------------------------------------------------------------------------
# Multiple nested invocations from same caller
# ---------------------------------------------------------------------------


def test_multiple_nested_invocations_have_unique_savepoint_names(tmp_path):
    """Each call to append_many_and_project generates a unique SAVEPOINT
    name via secrets.token_hex. Two invocations in same outer SAVEPOINT
    must not collide."""
    conn = _setup(tmp_path)
    conn.execute("SAVEPOINT sp_outer")
    try:
        append_many_and_project(conn, [_canonical_event("p1")], _canonical_projection("p1"))
        append_many_and_project(conn, [_canonical_event("p2")], _canonical_projection("p2"))
        append_many_and_project(conn, [_canonical_event("p3")], _canonical_projection("p3"))
        conn.execute("RELEASE SAVEPOINT sp_outer")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_outer")
        conn.execute("RELEASE SAVEPOINT sp_outer")
        raise
    n = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert n == 3


# ---------------------------------------------------------------------------
# Structural antibody: ledger.py must not use `with conn:` on the write body
# ---------------------------------------------------------------------------


def test_ledger_does_not_use_with_conn_for_append_path():
    """AST-level guard: future refactor reverting to `with conn:` would
    re-introduce the L30 collision silently. Uses ast to inspect only
    the function body (not docstrings, which may reference the pattern
    being avoided as prose)."""
    import ast
    from pathlib import Path
    ledger_path = Path(__file__).resolve().parents[1] / "src" / "state" / "ledger.py"
    tree = ast.parse(ledger_path.read_text())
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "append_many_and_project"
        ),
        None,
    )
    assert func is not None, "could not locate append_many_and_project FunctionDef"

    # Collect (a) all `with` statements and (b) all SAVEPOINT SQL literals
    # inside the executable body only. The docstring (ast.Expr + ast.Constant
    # at body[0]) is excluded naturally because `ast.With` only matches
    # `with ...:` statements, and the SAVEPOINT check below filters on
    # Call nodes rather than string literals.
    with_stmts = [n for n in ast.walk(func) if isinstance(n, ast.With)]
    assert len(with_stmts) == 0, (
        f"DR-33-B regression: append_many_and_project contains {len(with_stmts)} "
        f"`with` statement(s). The function must NOT use `with conn:` — it "
        f"releases the caller's outer SAVEPOINT silently (memory rule L30)."
    )

    # Confirm SAVEPOINT is used in at least one conn.execute call argument
    savepoint_calls = 0
    for n in ast.walk(func):
        if isinstance(n, ast.Call):
            for arg in n.args:
                if isinstance(arg, ast.JoinedStr):  # f-string
                    for value in arg.values:
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            if "SAVEPOINT" in value.value:
                                savepoint_calls += 1
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if "SAVEPOINT" in arg.value:
                        savepoint_calls += 1
    assert savepoint_calls >= 2, (
        f"DR-33-B regression: expected at least 2 SAVEPOINT conn.execute calls "
        f"(SAVEPOINT, RELEASE, ROLLBACK TO), found {savepoint_calls}."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
