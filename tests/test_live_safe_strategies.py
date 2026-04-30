# Created: 2026-04-26
# Last reused/audited: 2026-04-30
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: G6 antibody — pin LIVE_SAFE_STRATEGIES typed frozenset + boot-time
#          refusal to launch live daemon when any non-allowlisted strategy is
#          enabled. Closes the gap between universe-of-strategies (KNOWN_STRATEGIES,
#          4 entries) and live-execution-permitted-strategies (1 entry today).
# Reuse: Covers src/control/control_plane.py public LIVE_SAFE_STRATEGIES + helper
#        assert_live_safe_strategies_under_live_mode. If a future refactor
#        broadens the allowlist or removes the boot guard, these tests fire.
# Authority basis: docs/operations/task_2026-04-26_g6_live_safe_strategies/plan.md
#   §4 antibody design + parent packet
#   docs/operations/task_2026-04-26_live_readiness_completion/plan.md §5 K1.G6.
"""G6 antibody — LIVE_SAFE_STRATEGIES typed frozenset + boot-time refusal.

Cross-module relationship pinned:
    KNOWN_STRATEGIES (cycle_runner.py)  ⊇  LIVE_SAFE_STRATEGIES (control_plane.py)
    (every name in the live allowlist exists in the engine's universe)

Behavioral pin:
    LIVE_SAFE_STRATEGIES == {"opening_inertia"}
    (single operator-approved strategy as of 2026-04-26 per pro/con-Opus
    converged verdict in the archived live-readiness workbook)

Boot guard:
    Runtime is live-only. Any enabled strategy outside LIVE_SAFE_STRATEGIES
    refuses daemon start via SystemExit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Atom-shape tests (1-3): typed frozenset properties
# ---------------------------------------------------------------------------


def test_live_safe_strategies_is_frozenset_of_str():
    """Type discipline: frozenset of str, not list/set/tuple."""
    from src.control.control_plane import LIVE_SAFE_STRATEGIES

    assert isinstance(LIVE_SAFE_STRATEGIES, frozenset), (
        f"LIVE_SAFE_STRATEGIES must be frozenset, got {type(LIVE_SAFE_STRATEGIES).__name__}"
    )
    for name in LIVE_SAFE_STRATEGIES:
        assert isinstance(name, str), (
            f"LIVE_SAFE_STRATEGIES entries must be str, got {type(name).__name__} for {name!r}"
        )


def test_live_safe_strategies_pins_current_allowlist():
    """Pin current operator-approved set (2026-04-26).

    Future expansion REQUIRES an explicit packet — accidental list growth
    via copy/paste is caught here. See parent packet
    docs/operations/task_2026-04-26_live_readiness_completion/plan.md §5.
    """
    from src.control.control_plane import LIVE_SAFE_STRATEGIES

    assert LIVE_SAFE_STRATEGIES == frozenset({"opening_inertia"}), (
        f"LIVE_SAFE_STRATEGIES drift detected. Expected {{'opening_inertia'}}, "
        f"got {sorted(LIVE_SAFE_STRATEGIES)}. If this is a deliberate expansion, "
        f"update this pin AND the parent packet plan.md authority basis."
    )


def test_live_safe_strategies_subset_of_known_strategies():
    """Cross-module invariant: every live-safe name must exist in the engine's universe.

    KNOWN_STRATEGIES (src/engine/cycle_runner.py) is the buildable universe.
    LIVE_SAFE_STRATEGIES is the live-execution subset. A name in the allowlist
    that the engine doesn't recognize would silently never run — appearing
    safe but providing no live coverage. This test fires before that drift.
    """
    from src.control.control_plane import LIVE_SAFE_STRATEGIES
    from src.engine.cycle_runner import KNOWN_STRATEGIES

    orphans = LIVE_SAFE_STRATEGIES - KNOWN_STRATEGIES
    assert not orphans, (
        f"LIVE_SAFE_STRATEGIES contains names unknown to the engine: {sorted(orphans)}. "
        f"Either add them to KNOWN_STRATEGIES or remove from the allowlist."
    )


# ---------------------------------------------------------------------------
# Helper-behavior tests (4-6): assert_live_safe_strategies_under_live_mode
# ---------------------------------------------------------------------------


def test_assert_live_safe_strategies_silent_on_safe_set(monkeypatch):
    """Helper returns silently when enabled set is subset of allowlist."""
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    # Must not raise.
    assert_live_safe_strategies_under_live_mode({"opening_inertia"}) is None


def test_assert_live_safe_strategies_raises_on_unsafe_set(monkeypatch):
    """Helper raises SystemExit when an enabled strategy is outside the allowlist."""
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit) as exc_info:
        assert_live_safe_strategies_under_live_mode({"center_buy", "opening_inertia"})

    msg = str(exc_info.value)
    assert "FATAL" in msg, f"SystemExit message must contain FATAL marker: {msg!r}"
    assert "center_buy" in msg, f"SystemExit message must name the offender: {msg!r}"


def test_assert_live_safe_strategies_ignores_retired_paper_env(monkeypatch):
    """The retired ZEUS_MODE switch cannot bypass live strategy allowlisting."""
    monkeypatch.setenv("ZEUS_MODE", "paper")
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit):
        assert_live_safe_strategies_under_live_mode({"center_buy"})


def test_assert_live_safe_strategies_enforces_when_zeus_mode_unset(monkeypatch):
    """The live-only runtime no longer needs ZEUS_MODE to activate the guard."""
    monkeypatch.delenv("ZEUS_MODE", raising=False)
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit):
        assert_live_safe_strategies_under_live_mode({"center_buy"})


# ---------------------------------------------------------------------------
# Boot-wiring relationship test (7): main.py invokes the helper under live mode
# ---------------------------------------------------------------------------


def test_main_boot_wiring_imports_assert_helper():
    """src/main.py must import the helper symbol so the boot guard is present.

    Stronger than a grep — actually parses src/main.py and confirms the
    import + call survive. If a future refactor drops the import, this fires.
    """
    main_src = (PROJECT_ROOT / "src" / "main.py").read_text(encoding="utf-8")
    assert "assert_live_safe_strategies_under_live_mode" in main_src, (
        "src/main.py must import + call assert_live_safe_strategies_under_live_mode "
        "to enforce G6 boot guard. Found no reference."
    )
    assert "LIVE_SAFE_STRATEGIES" in main_src or "is_strategy_enabled" in main_src, (
        "src/main.py boot wiring should reference is_strategy_enabled (to compose "
        "the enabled set) or LIVE_SAFE_STRATEGIES directly. Neither found."
    )


# ---------------------------------------------------------------------------
# Boot-integration tests (8-10): exercise the cold-cache vs hydrated-cache
# distinction via _assert_live_safe_strategies_or_exit() helper. These tests
# fix the gap that allowed BLOCKER #1 (con-nyx review 2026-04-26): atom-shape
# tests + literal-arg helper tests + string-grep tests do NOT prove that the
# production composition path (KNOWN_STRATEGIES ∩ is_strategy_enabled, with
# is_strategy_enabled reading hydrated _control_state) actually works.
# ---------------------------------------------------------------------------


def _populate_strategy_gates(_control_state: dict, gates: dict[str, bool]) -> None:
    """Test helper: install strategy_gates into the control_plane module cache.

    Mirrors the EXACT post-refresh shape that
    src/state/db.py::query_control_override_state emits (BLOCKER #2 fix
    2026-04-26): each value is a full GateDecision-shaped dict with
    enabled / reason_code / reason_snapshot / gated_at / gated_by keys.
    Fixtures that drift from production shape were how BLOCKER #2 hid in
    G6 first-pass tests.
    """
    _control_state["strategy_gates"] = {
        name: {
            "enabled": enabled,
            "reason_code": "operator_override",
            "reason_snapshot": {},
            "gated_at": "2026-04-26T00:00:00Z",
            "gated_by": "test_setup",
        }
        for name, enabled in gates.items()
    }


def test_boot_helper_refuses_when_unsafe_strategy_enabled(monkeypatch):
    """Production composition path: hydrated state with center_buy enabled → SystemExit.

    Replaces the missing relationship test that masked BLOCKER #1.
    Sets up _control_state via the same shape refresh_control_state() would
    populate, then invokes the boot guard with refresh_state=False (we already
    populated it ourselves to avoid touching a real DB).
    """
    import src.control.control_plane as cp
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")

    # Snapshot + restore _control_state to avoid leaking into other tests.
    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})

    # Production scenario: opening_inertia enabled (safe), center_buy also
    # enabled (NOT safe). Operator forgot to disable center_buy.
    _populate_strategy_gates(
        cp._control_state,
        {
            "opening_inertia": True,
            "center_buy": True,
            "shoulder_sell": False,
            "settlement_capture": False,
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

    msg = str(exc_info.value)
    assert "FATAL" in msg, f"Expected FATAL marker: {msg!r}"
    assert "center_buy" in msg, f"Expected center_buy in offenders: {msg!r}"

    # Restore (defensive — monkeypatch.setattr handles this, but explicit on dict).
    cp._control_state.clear()
    cp._control_state.update(original_state)


def test_boot_helper_silent_when_only_safe_strategy_enabled(monkeypatch):
    """Production composition path: hydrated state with only opening_inertia enabled → silent.

    The post-fix happy path. Operator explicitly disabled center_buy /
    shoulder_sell / settlement_capture; only opening_inertia is enabled.
    """
    import src.control.control_plane as cp
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")
    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})

    _populate_strategy_gates(
        cp._control_state,
        {
            "opening_inertia": True,
            "center_buy": False,
            "shoulder_sell": False,
            "settlement_capture": False,
        },
    )

    # Must NOT raise.
    main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

    cp._control_state.clear()
    cp._control_state.update(original_state)


def test_boot_helper_with_cold_cache_refuses_via_default_true_semantic(monkeypatch):
    """The pre-fix BLOCKER scenario, now PINNED as expected behavior under refresh_state=False.

    Cold cache (empty _control_state) + is_strategy_enabled returns True for
    all KNOWN_STRATEGIES → guard refuses. This documents the contract
    operators MUST satisfy: hydration before guard. The production main()
    path always passes refresh_state=True (the default), which calls
    refresh_control_state() first; this test pins what happens if a future
    caller forgets to hydrate.
    """
    import src.control.control_plane as cp
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")
    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})  # empty: cold cache

    with pytest.raises(SystemExit) as exc_info:
        main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

    msg = str(exc_info.value)
    # All 3 non-safe strategies should be named (default-True semantic on empty cache).
    for offender in ("center_buy", "shoulder_sell", "settlement_capture"):
        assert offender in msg, (
            f"Cold-cache scenario must surface ALL non-safe strategies. "
            f"Missing {offender!r} in: {msg!r}"
        )

    cp._control_state.clear()
    cp._control_state.update(original_state)


# ---------------------------------------------------------------------------
# Real-DB round-trip integration tests (12-13) — con-nyx CONDITION C2 redo
# (BLOCKER #2 surfaced because the synthetic _populate_strategy_gates fixture
# bypasses query_control_override_state entirely. These tests round-trip
# through the actual DB writer + reader path so a future regression of the
# bool/dict shape mismatch fires here.)
# ---------------------------------------------------------------------------


def test_boot_helper_round_trips_real_db_gate(monkeypatch, tmp_path):
    """Operator-remediation scenario: set_strategy_gate writes DB → restart → guard reads.

    This is the path operators are instructed to take by the FATAL message.
    Pre-BLOCKER-2-fix it crashed with `ValueError: Legacy bool strategy gate
    found for ...` because query_control_override_state returned bare bool
    that strategy_gates() rejected. Post-fix, the reader emits
    GateDecision-shaped dicts and the round-trip succeeds.

    Uses real sqlite DB on disk (tmp_path), real init_schema, real
    upsert_control_override, real refresh_control_state. Only
    get_world_connection is monkeypatched to point at the temp DB.
    """
    import sqlite3
    import src.control.control_plane as cp
    import src.main as main_mod
    import src.state.db as db
    from src.state.db import init_schema, upsert_control_override

    monkeypatch.setenv("ZEUS_MODE", "live")
    db_path = tmp_path / "round_trip.db"

    def fake_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    # Setup: operator issues set_strategy_gate for all 3 non-safe strategies.
    conn = fake_conn()
    init_schema(conn)
    for strategy in ("center_buy", "shoulder_sell", "settlement_capture"):
        upsert_control_override(
            conn,
            override_id=f"cp:strategy:{strategy}:gate",
            target_type="strategy",
            target_key=strategy,
            action_type="gate",
            value="true",  # gate=true means strategy DISABLED
            issued_by="operator",
            issued_at="2026-04-26T00:00:00Z",
            reason="G6_remediation_round_trip_test",
            precedence=10,
        )
    conn.commit()
    conn.close()

    # Simulate fresh process: empty _control_state, refresh from real DB.
    monkeypatch.setattr(db, "get_world_connection", fake_conn)
    monkeypatch.setattr(cp, "get_world_connection", fake_conn)
    monkeypatch.setattr(cp, "_control_state", {})

    # Production path: refresh_state=True (the default — what main() uses).
    # Pre-BLOCKER-2-fix this raised ValueError. Post-fix it must NOT raise.
    main_mod._assert_live_safe_strategies_or_exit()

    # Post-condition: gates were hydrated with GateDecision-shaped dicts.
    gates = cp._control_state.get("strategy_gates", {})
    assert "center_buy" in gates, f"center_buy gate missing after refresh: {list(gates.keys())}"
    assert isinstance(gates["center_buy"], dict), (
        f"BLOCKER #2 regression: gate value is {type(gates['center_buy']).__name__}, "
        f"expected dict (GateDecision shape). query_control_override_state "
        f"must emit dicts, not bare bools."
    )
    assert gates["center_buy"]["enabled"] is False, (
        f"value='true' (gate active) should resolve enabled=False, got {gates['center_buy']!r}"
    )


def test_boot_helper_round_trip_refuses_when_db_gate_missing(monkeypatch, tmp_path):
    """Inverse of the above: empty DB → refresh yields strategy_gates={} → default-True → SystemExit.

    Confirms the post-fix path is still fail-closed when the operator has
    NOT issued any set_strategy_gate commands. Without this control, the
    bool-to-dict fix could over-correct by silently treating empty as safe.
    """
    import sqlite3
    import src.control.control_plane as cp
    import src.main as main_mod
    import src.state.db as db
    from src.state.db import init_schema

    monkeypatch.setenv("ZEUS_MODE", "live")
    db_path = tmp_path / "empty.db"

    def fake_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    # Setup: empty DB, schema only (no overrides).
    conn = fake_conn()
    init_schema(conn)
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "get_world_connection", fake_conn)
    monkeypatch.setattr(cp, "get_world_connection", fake_conn)
    monkeypatch.setattr(cp, "_control_state", {})

    with pytest.raises(SystemExit) as exc_info:
        main_mod._assert_live_safe_strategies_or_exit()

    msg = str(exc_info.value)
    for offender in ("center_buy", "shoulder_sell", "settlement_capture"):
        assert offender in msg, (
            f"Empty-DB scenario must surface non-safe strategies. "
            f"Missing {offender!r} in: {msg!r}"
        )
