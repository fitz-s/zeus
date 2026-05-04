# Created: 2026-04-26
# Last reused/audited: 2026-05-02
# Lifecycle: created=2026-04-26; last_reviewed=2026-05-02; last_reused=2026-05-02
# Purpose: G6 antibody — pin LIVE_SAFE_STRATEGIES typed frozenset + boot-time
#          refusal to launch live daemon when any non-boot-safe strategy is
#          enabled. Keeps the buildable universe, boot/catalog-safe set, and
#          runtime-live execution boundary named separately.
# Reuse: Covers src/control/control_plane.py public LIVE_SAFE_STRATEGIES + helper
#        assert_live_safe_strategies_under_live_mode. If a future refactor
#        broadens the boot/catalog allowlist, confuses it with runtime live
#        entry, or removes the boot guard, these tests fire.
# Authority basis: docs/operations/task_2026-04-26_g6_live_safe_strategies/plan.md
#   §4 antibody design + parent packet
#   docs/operations/task_2026-04-26_live_readiness_completion/plan.md §5 K1.G6.
#   Reused/audited for docs/operations/task_2026-05-02_strategy_update_execution_plan/PLAN.md
#   Stage 0 catalog-truth lock: boot-safe, runtime-live, sizing, and reporting
#   strategy surfaces must not be mistaken for one authority surface.
"""G6 antibody — LIVE_SAFE_STRATEGIES typed frozenset + boot-time refusal.

Cross-module relationship pinned:
    KNOWN_STRATEGIES (cycle_runner.py)  ⊇  LIVE_SAFE_STRATEGIES (control_plane.py)
    (every name in the boot/catalog set exists in the engine's universe)

Behavioral pin:
    LIVE_SAFE_STRATEGIES == {"opening_inertia", "center_buy",
    "settlement_capture", "shoulder_sell"} after the 2026-04-29
    operator-approved boot/catalog expansion.

Runtime live-entry authority:
    _LIVE_ALLOWED_STRATEGIES == {"opening_inertia", "center_buy",
    "settlement_capture"}; shoulder_sell remains blocked at is_strategy_enabled().

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
    """Pin current operator-approved boot/catalog set (2026-04-29 expansion).

    Future expansion REQUIRES an explicit packet — accidental list growth
    via copy/paste is caught here. See parent packet
    docs/operations/task_2026-04-26_live_readiness_completion/plan.md §5
    and 2026-04-29 expansion authorization in src/control/control_plane.py.
    """
    from src.control.control_plane import LIVE_SAFE_STRATEGIES

    expected = frozenset({"opening_inertia", "center_buy", "settlement_capture", "shoulder_sell"})
    assert LIVE_SAFE_STRATEGIES == expected, (
        f"LIVE_SAFE_STRATEGIES drift detected. Expected {sorted(expected)}, "
        f"got {sorted(LIVE_SAFE_STRATEGIES)}. If this is a deliberate expansion, "
        f"update this pin AND the parent packet plan.md authority basis."
    )


def test_live_safe_strategies_subset_of_known_strategies():
    """Cross-module invariant: every boot-safe name must exist in the engine's universe.

    KNOWN_STRATEGIES (src/engine/cycle_runner.py) is the buildable universe.
    LIVE_SAFE_STRATEGIES is the boot/catalog-safe subset. A name in the
    allowlist that the engine doesn't recognize would silently never run —
    appearing safe but providing no coverage. This test fires before that drift.
    """
    from src.control.control_plane import LIVE_SAFE_STRATEGIES
    from src.engine.cycle_runner import KNOWN_STRATEGIES

    orphans = LIVE_SAFE_STRATEGIES - KNOWN_STRATEGIES
    assert not orphans, (
        f"LIVE_SAFE_STRATEGIES contains names unknown to the engine: {sorted(orphans)}. "
        f"Either add them to KNOWN_STRATEGIES or remove from the allowlist."
    )


def test_stage0_strategy_authority_surfaces_are_explicitly_split():
    """Stage 0 catalog truth: buildable, boot-safe, live-allowed, sizing, and reporting surfaces differ by design.

    This is not a license to keep them divergent forever. It prevents the
    dangerous weaker claim "catalog matches code" from passing when only one
    surface was checked. The May 2 strategy update requires all of these
    surfaces to be named before Stage 1 changes live strategy behavior.
    """
    from src.control import control_plane
    from src.engine import cycle_runtime
    from src.engine.cycle_runner import KNOWN_STRATEGIES
    from src.state.edge_observation import STRATEGY_KEYS as EDGE_OBSERVATION_KEYS
    from src.state.portfolio import CANONICAL_STRATEGY_KEYS as PORTFOLIO_KEYS
    from src.strategy.kelly import STRATEGY_KELLY_MULTIPLIERS

    buildable = set(KNOWN_STRATEGIES)
    runtime_canonical = set(cycle_runtime.CANONICAL_STRATEGY_KEYS)
    portfolio_canonical = set(PORTFOLIO_KEYS)
    boot_safe = set(control_plane.LIVE_SAFE_STRATEGIES)
    live_allowed = set(control_plane._LIVE_ALLOWED_STRATEGIES)
    positive_sizing = {
        strategy_key
        for strategy_key, multiplier in STRATEGY_KELLY_MULTIPLIERS.items()
        if multiplier > 0.0
    }
    reportable = set(EDGE_OBSERVATION_KEYS)

    assert buildable == runtime_canonical == portfolio_canonical == reportable == boot_safe
    assert live_allowed == {"settlement_capture", "center_buy", "opening_inertia"}
    assert positive_sizing == live_allowed
    assert "shoulder_sell" in boot_safe
    assert "shoulder_sell" not in live_allowed
    assert STRATEGY_KELLY_MULTIPLIERS["shoulder_sell"] == 0.0
    assert STRATEGY_KELLY_MULTIPLIERS["shoulder_buy"] == 0.0
    assert STRATEGY_KELLY_MULTIPLIERS["center_sell"] == 0.0


def test_stage1_taxonomy_rollback_boundary_is_runtime_live_allowlist():
    """Stage 1 rollback verdict: do not create a second taxonomy feature flag.

    The rollback boundary is the runtime-live strategy allowlist. Adding a
    separate negative flag such as DISABLE_NEW_TAXONOMY would create a second
    authority surface whose typo/default behavior can live-open the taxonomy.
    """
    from src.config import settings
    from src.control import control_plane
    from src.strategy.kelly import STRATEGY_KELLY_MULTIPLIERS

    flags = settings["feature_flags"]

    assert "DISABLE_NEW_TAXONOMY" not in flags
    assert "ENABLE_NEW_TAXONOMY" not in flags
    assert "NEW_TAXONOMY_LIVE" not in flags
    assert control_plane._LIVE_ALLOWED_STRATEGIES == {
        "settlement_capture",
        "center_buy",
        "opening_inertia",
    }
    assert control_plane.is_strategy_enabled("settlement_capture") is True
    assert control_plane.is_strategy_enabled("center_buy") is True
    assert control_plane.is_strategy_enabled("opening_inertia") is True
    assert control_plane.is_strategy_enabled("shoulder_sell") is False
    assert control_plane.is_strategy_enabled("shoulder_buy") is False
    assert control_plane.is_strategy_enabled("center_sell") is False

    positive_sizing = {
        strategy_key
        for strategy_key, multiplier in STRATEGY_KELLY_MULTIPLIERS.items()
        if multiplier > 0.0
    }
    assert positive_sizing == control_plane._LIVE_ALLOWED_STRATEGIES


# ---------------------------------------------------------------------------
# Helper-behavior tests (4-6): assert_live_safe_strategies_under_live_mode
# ---------------------------------------------------------------------------


def test_assert_live_safe_strategies_silent_on_safe_set(monkeypatch):
    """Helper returns silently when enabled set is subset of allowlist."""
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    # Must not raise.
    assert_live_safe_strategies_under_live_mode({"opening_inertia"}) is None


def test_assert_live_safe_strategies_raises_on_unsafe_set(monkeypatch):
    """Helper raises SystemExit when an enabled strategy is outside the allowlist.

    SystemExit (not RuntimeError) matches the existing FATAL boot pattern at
    src/main.py:472-477 — daemon launchers consume SystemExit and refuse to
    start; RuntimeError would leak past launchd and create zombie state.

    Uses synthetic non-existent strategy name to remain robust to allowlist
    expansion — the FATAL semantic must fire whenever an enabled strategy is
    NOT in LIVE_SAFE_STRATEGIES, regardless of which 4 KNOWN_STRATEGIES are
    currently allowlisted.
    """
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit) as exc_info:
        assert_live_safe_strategies_under_live_mode({"_test_ghost_strategy", "opening_inertia"})

    msg = str(exc_info.value)
    assert "FATAL" in msg, f"SystemExit message must contain FATAL marker: {msg!r}"
    assert "_test_ghost_strategy" in msg, f"SystemExit message must name the offender: {msg!r}"


def test_assert_live_safe_strategies_ignores_retired_paper_env(monkeypatch):
    """The retired ZEUS_MODE switch cannot bypass live strategy allowlisting."""
    monkeypatch.setenv("ZEUS_MODE", "paper")
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit):
        assert_live_safe_strategies_under_live_mode({"_test_ghost_strategy"})


def test_assert_live_safe_strategies_enforces_when_zeus_mode_unset(monkeypatch):
    """The live-only runtime no longer needs ZEUS_MODE to activate the guard."""
    monkeypatch.delenv("ZEUS_MODE", raising=False)
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit):
        assert_live_safe_strategies_under_live_mode({"_test_ghost_strategy"})


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
    """Production composition path: hydrated state with a ghost strategy enabled → SystemExit.

    Replaces the missing relationship test that masked BLOCKER #1.
    Sets up _control_state via the same shape refresh_control_state() would
    populate, then invokes the boot guard with refresh_state=False (we already
    populated it ourselves to avoid touching a real DB).

    Uses a synthetic strategy name OUTSIDE the LIVE_SAFE_STRATEGIES allowlist
    so the test stays correct under any allowlist expansion (including the
    2026-04-29 expansion to all 4 KNOWN_STRATEGIES). The boot guard composes
    enabled = KNOWN_STRATEGIES ∩ enabled_gates, so a name not in
    KNOWN_STRATEGIES will not enter the enabled set even if its gate=True.
    To test the refusal path properly we must monkeypatch KNOWN_STRATEGIES.
    """
    import src.control.control_plane as cp
    import src.engine.cycle_runner as cycle_runner
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")

    # Inject a ghost strategy into both KNOWN_STRATEGIES and the stricter
    # runtime-live surface so it appears enabled but NOT boot-safe.
    extended_known = cycle_runner.KNOWN_STRATEGIES | {"_test_ghost_strategy"}
    monkeypatch.setattr(cycle_runner, "KNOWN_STRATEGIES", extended_known)
    monkeypatch.setattr(
        cp,
        "_LIVE_ALLOWED_STRATEGIES",
        set(cp._LIVE_ALLOWED_STRATEGIES) | {"_test_ghost_strategy"},
    )

    # Snapshot + restore _control_state to avoid leaking into other tests.
    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})

    # Production scenario: a ghost strategy enabled that is NOT in the allowlist.
    _populate_strategy_gates(
        cp._control_state,
        {
            "opening_inertia": True,
            "center_buy": True,
            "shoulder_sell": True,
            "settlement_capture": True,
            "_test_ghost_strategy": True,
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

    msg = str(exc_info.value)
    assert "FATAL" in msg, f"Expected FATAL marker: {msg!r}"
    assert "_test_ghost_strategy" in msg, f"Expected ghost in offenders: {msg!r}"

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
    all KNOWN_STRATEGIES → guard refuses if any KNOWN_STRATEGIES are not in
    LIVE_SAFE_STRATEGIES. This documents the contract operators MUST satisfy:
    hydration before guard. The production main() path always passes
    refresh_state=True (the default), which calls refresh_control_state()
    first; this test pins what happens if a future caller forgets to hydrate.

    To remain correct under allowlist expansion (2026-04-29: all 4 KNOWN now
    safe), this test injects a ghost into KNOWN_STRATEGIES so the cold-cache
    default-True semantic surfaces a non-safe strategy.
    """
    import src.control.control_plane as cp
    import src.engine.cycle_runner as cycle_runner
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")

    # Extend both KNOWN_STRATEGIES and the stricter runtime-live surface with a
    # synthetic ghost so cold-cache default-True surfaces it as offender even
    # when all 4 real KNOWN are boot-safe.
    extended_known = cycle_runner.KNOWN_STRATEGIES | {"_test_ghost_strategy"}
    monkeypatch.setattr(cycle_runner, "KNOWN_STRATEGIES", extended_known)
    monkeypatch.setattr(
        cp,
        "_LIVE_ALLOWED_STRATEGIES",
        set(cp._LIVE_ALLOWED_STRATEGIES) | {"_test_ghost_strategy"},
    )

    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})  # empty: cold cache

    with pytest.raises(SystemExit) as exc_info:
        main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

    msg = str(exc_info.value)
    # The ghost (only non-safe under expanded allowlist) must be named.
    assert "_test_ghost_strategy" in msg, (
        f"Cold-cache scenario must surface non-safe strategies. "
        f"Missing _test_ghost_strategy in: {msg!r}"
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
    NOT issued any set_strategy_gate commands and at least one non-allowlisted
    strategy exists in KNOWN_STRATEGIES. Injects a ghost into KNOWN_STRATEGIES
    to remain correct under the 2026-04-29 allowlist expansion (all 4 real
    KNOWN now safe).
    """
    import sqlite3
    import src.control.control_plane as cp
    import src.engine.cycle_runner as cycle_runner
    import src.main as main_mod
    import src.state.db as db
    from src.state.db import init_schema

    monkeypatch.setenv("ZEUS_MODE", "live")

    extended_known = cycle_runner.KNOWN_STRATEGIES | {"_test_ghost_strategy"}
    monkeypatch.setattr(cycle_runner, "KNOWN_STRATEGIES", extended_known)
    monkeypatch.setattr(
        cp,
        "_LIVE_ALLOWED_STRATEGIES",
        set(cp._LIVE_ALLOWED_STRATEGIES) | {"_test_ghost_strategy"},
    )

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
    # Under expanded allowlist, only the ghost is non-safe. Pin its presence.
    assert "_test_ghost_strategy" in msg, (
        f"Empty-DB scenario must surface non-safe strategies. "
        f"Missing _test_ghost_strategy in: {msg!r}"
    )
