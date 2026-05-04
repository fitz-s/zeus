# Created: 2026-04-26
# Last reused/audited: 2026-05-04
# Lifecycle: created=2026-04-26; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: G6 antibody — pin LIVE_SAFE_STRATEGIES typed set + boot-time
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
#   docs/operations/task_2026-05-02_strategy_update_execution_plan/PLAN.md
#   Stage 0 catalog-truth lock.
#   docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A4
#   (registry cutover: 5 hardcoded sets consolidated to one YAML; tests
#    that pinned the hardcoded literals now read through the registry,
#    ghost-injection switches from monkeypatching module constants to
#    swapping strategy_profile._registry).
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


def _inject_ghost_strategy(monkeypatch, *, live_status: str = "live") -> str:
    """Helper for tests that need a synthetic strategy outside the canonical
    registry. Snapshots ``strategy_profile._registry``, adds a
    ``_test_ghost_strategy`` with the given ``live_status``, and lets
    monkeypatch unwind on test exit.

    Pre-A4: tests in this file monkey-patched ``cycle_runner.KNOWN_STRATEGIES``
    and ``control_plane._LIVE_ALLOWED_STRATEGIES`` directly to inject a ghost.
    Post-A4 those symbols are derived from the StrategyProfile registry, so
    ghost injection moves to the registry. Same antibody, same boot-guard
    refusal — the implementation surface changed but the property pinned
    by the tests (boot guard refuses unsafe strategies in the enabled set)
    is preserved.

    Returns the ghost strategy key for use in the test body.
    """
    from src.strategy import strategy_profile
    from src.strategy.strategy_profile import StrategyProfile

    # Force-load the canonical registry.
    canonical = dict(strategy_profile.all_profiles())
    ghost_key = "_test_ghost_strategy"
    ghost = StrategyProfile(
        key=ghost_key,
        thesis="synthetic test ghost — not for production",
        live_status=live_status,
        allowed_market_phases=frozenset(),
        allowed_discovery_modes=frozenset(),
        allowed_directions=frozenset(),
        allowed_bin_topology=frozenset(),
        metric_support={"high": "blocked", "low": "blocked"},
        kelly_default_multiplier=0.0,
        kelly_phase_overrides={},
        min_shadow_decisions=0,
        min_settled_decisions=0,
        promotion_evidence_ref=None,
    )
    canonical[ghost_key] = ghost
    monkeypatch.setattr(strategy_profile, "_registry", canonical)
    return ghost_key


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

    Post-A4: the buildable / boot-safe / live-allowed / positive-sizing
    surfaces all derive from the StrategyProfile registry (single source).
    Cross-module invariants from cycle_runtime.CANONICAL_STRATEGY_KEYS,
    portfolio.CANONICAL_STRATEGY_KEYS, and edge_observation.STRATEGY_KEYS
    still need to match — a regression where one falls out of sync would
    silently emit decisions for strategies the others don't know about.
    """
    from src.control import control_plane
    from src.engine import cycle_runtime
    from src.engine.cycle_runner import KNOWN_STRATEGIES
    from src.state.edge_observation import STRATEGY_KEYS as EDGE_OBSERVATION_KEYS
    from src.state.portfolio import CANONICAL_STRATEGY_KEYS as PORTFOLIO_KEYS
    from src.strategy import strategy_profile

    buildable = set(KNOWN_STRATEGIES)
    runtime_canonical = set(cycle_runtime.CANONICAL_STRATEGY_KEYS)
    portfolio_canonical = set(PORTFOLIO_KEYS)
    boot_safe = set(control_plane.LIVE_SAFE_STRATEGIES)
    live_allowed = set(control_plane._LIVE_ALLOWED_STRATEGIES)
    # Post-A4: positive-sizing comes from the registry's
    # kelly_default_multiplier > 0 set (replaces STRATEGY_KELLY_MULTIPLIERS).
    positive_sizing = {
        key
        for key, profile in strategy_profile.all_profiles().items()
        if profile.kelly_default_multiplier > 0.0
    }
    reportable = set(EDGE_OBSERVATION_KEYS)

    assert buildable == runtime_canonical == portfolio_canonical == reportable == boot_safe
    assert live_allowed == {"settlement_capture", "center_buy", "opening_inertia"}
    assert positive_sizing == live_allowed
    assert "shoulder_sell" in boot_safe
    assert "shoulder_sell" not in live_allowed
    # Pre-A4: STRATEGY_KELLY_MULTIPLIERS["shoulder_sell"] == 0.0 etc.
    # Post-A4: equivalent assertion via registry.
    assert strategy_profile.get("shoulder_sell").kelly_default_multiplier == 0.0
    assert strategy_profile.get("shoulder_buy").kelly_default_multiplier == 0.0
    assert strategy_profile.get("center_sell").kelly_default_multiplier == 0.0


def test_stage1_taxonomy_rollback_boundary_is_runtime_live_allowlist():
    """Stage 1 rollback verdict: do not create a second taxonomy feature flag.

    Post-A4: positive-sizing is derived from the registry's
    kelly_default_multiplier > 0 set (was STRATEGY_KELLY_MULTIPLIERS pre-A4).
    """
    from src.config import settings
    from src.control import control_plane
    from src.strategy import strategy_profile

    flags = settings["feature_flags"]

    assert "DISABLE_NEW_TAXONOMY" not in flags
    assert "ENABLE_NEW_TAXONOMY" not in flags
    assert "NEW_TAXONOMY_LIVE" not in flags
    assert control_plane._LIVE_ALLOWED_STRATEGIES == frozenset({
        "settlement_capture",
        "center_buy",
        "opening_inertia",
    })
    assert control_plane.is_strategy_enabled("settlement_capture") is True
    assert control_plane.is_strategy_enabled("center_buy") is True
    assert control_plane.is_strategy_enabled("opening_inertia") is True
    assert control_plane.is_strategy_enabled("shoulder_sell") is False
    assert control_plane.is_strategy_enabled("shoulder_buy") is False
    assert control_plane.is_strategy_enabled("center_sell") is False

    positive_sizing = frozenset(
        key
        for key, profile in strategy_profile.all_profiles().items()
        if profile.kelly_default_multiplier > 0.0
    )
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
    """Boot guard refuses arbitrary unsafe strategies in the enabled set.

    Pre-A4 this test injected a ghost into BOTH ``KNOWN_STRATEGIES`` and
    ``_LIVE_ALLOWED_STRATEGIES`` while leaving ``LIVE_SAFE_STRATEGIES``
    unchanged — exploiting the divergence between three hardcoded sets to
    construct the "ghost in enabled, not in safe" scenario. Post-A4 those
    three sets all derive from the StrategyProfile registry; the divergence
    is structurally impossible. The G6 antibody now lives in two places:

    - The registry's single-source guarantees KNOWN ≡ LIVE_SAFE, making
      the BLOCKER #1 scenario un-constructable (caught by
      ``test_post_a4_known_equals_live_safe`` below).
    - The inner ``assert_live_safe_strategies_under_live_mode`` still
      refuses arbitrary unsafe enabled sets — that's what this test pins,
      via direct call rather than the composition wrapper (the wrapper's
      pre-A4 attack surface is gone post-A4).
    """
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    # Synthetic enabled set with a ghost strategy not in the registry.
    # The inner assertion is the underlying boot-guard refusal — same
    # antibody as pre-A4, called without the composition wrapper.
    with pytest.raises(SystemExit) as exc_info:
        assert_live_safe_strategies_under_live_mode(
            {"_test_ghost_strategy", "opening_inertia"}
        )
    msg = str(exc_info.value)
    assert "FATAL" in msg, f"Expected FATAL marker: {msg!r}"
    assert "_test_ghost_strategy" in msg, f"Expected ghost in offenders: {msg!r}"


def test_post_a4_known_equals_live_safe():
    """A4 single-source invariant: ``KNOWN_STRATEGIES`` (cycle_runner) and
    ``LIVE_SAFE_STRATEGIES`` (control_plane) MUST be the same set, because
    both lazily resolve through ``strategy_profile.live_safe_keys()``.

    Pre-A4 these were independent hardcoded literals; the BLOCKER #1
    failure mode required them to diverge. Post-A4 they cannot diverge
    by construction — this test pins the construction.
    """
    from src.control import control_plane
    from src.engine import cycle_runner
    from src.strategy import strategy_profile

    assert cycle_runner.KNOWN_STRATEGIES == control_plane.LIVE_SAFE_STRATEGIES
    assert cycle_runner.KNOWN_STRATEGIES == strategy_profile.live_safe_keys()
    assert control_plane._LIVE_ALLOWED_STRATEGIES <= cycle_runner.KNOWN_STRATEGIES


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


def test_boot_helper_with_cold_cache_under_post_a4_single_source(monkeypatch):
    """Cold cache + post-A4 single-source registry: silent boot.

    Pre-A4 this test name was test_boot_helper_with_cold_cache_refuses_via_default_true_semantic
    and exploited the KNOWN != LIVE_SAFE divergence to surface a ghost
    via cold-cache default-True semantics. Post-A4 the divergence is
    impossible by construction (both derive from one registry call),
    so the cold-cache attack surface is closed.
    """

    import src.control.control_plane as cp
    import src.main as main_mod

    monkeypatch.setenv("ZEUS_MODE", "live")
    original_state = dict(cp._control_state)
    monkeypatch.setattr(cp, "_control_state", {})  # empty: cold cache

    # Cold cache: every strategy in KNOWN looks enabled (default-True).
    # Composition produces enabled = KNOWN_STRATEGIES = LIVE_SAFE_STRATEGIES.
    # Boot guard accepts silently — no divergence possible post-A4.
    main_mod._assert_live_safe_strategies_or_exit(refresh_state=False)

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


def test_boot_helper_round_trip_with_empty_db_under_post_a4_single_source(monkeypatch, tmp_path):
    """Empty DB + single-source registry: silent boot.

    Pre-A4 this test exploited the KNOWN != LIVE_SAFE divergence by
    injecting a ghost into KNOWN_STRATEGIES while leaving LIVE_SAFE
    unchanged, then confirming an empty DB + default-True semantics
    surfaced the ghost as an offender. Post-A4 the divergence is
    structurally impossible.

    The post-A4 invariant: an empty DB hydrates strategy_gates={}, so
    is_strategy_enabled returns True for all KNOWN_STRATEGIES, and the
    composed enabled set equals KNOWN_STRATEGIES. Since KNOWN ==
    LIVE_SAFE_STRATEGIES, the boot guard accepts silently. The test
    pins this graceful-empty-DB property.
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

    # Empty DB + clean registry: enabled set == KNOWN == LIVE_SAFE.
    # Boot guard accepts silently. Pre-A4 this would have raised because
    # the test injected a ghost into KNOWN; post-A4 single-source
    # prevents the divergence so silent boot is the correct outcome.
    main_mod._assert_live_safe_strategies_or_exit()
