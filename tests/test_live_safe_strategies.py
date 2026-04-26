# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
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
    Under ZEUS_MODE=live, any enabled strategy outside LIVE_SAFE_STRATEGIES
    refuses daemon start via SystemExit (matching existing FATAL pattern at
    src/main.py:472-477).
"""

from __future__ import annotations

import os
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
    monkeypatch.setenv("ZEUS_MODE", "live")
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    # Must not raise.
    assert_live_safe_strategies_under_live_mode({"opening_inertia"}) is None


def test_assert_live_safe_strategies_raises_on_unsafe_set(monkeypatch):
    """Under ZEUS_MODE=live, helper raises SystemExit when an enabled strategy is outside the allowlist.

    SystemExit (not RuntimeError) matches the existing FATAL boot pattern at
    src/main.py:472-477 — daemon launchers consume SystemExit and refuse to
    start; RuntimeError would leak past launchd and create zombie state.
    """
    monkeypatch.setenv("ZEUS_MODE", "live")
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    with pytest.raises(SystemExit) as exc_info:
        assert_live_safe_strategies_under_live_mode({"center_buy", "opening_inertia"})

    msg = str(exc_info.value)
    assert "FATAL" in msg, f"SystemExit message must contain FATAL marker: {msg!r}"
    assert "center_buy" in msg, f"SystemExit message must name the offender: {msg!r}"


def test_assert_live_safe_strategies_silent_under_paper_mode(monkeypatch):
    """Under ZEUS_MODE!='live', helper is silent regardless of enabled set.

    Live-only enforcement — paper sessions are experimental and may run
    arbitrary strategies. The boot refusal applies ONLY to live mode.
    """
    monkeypatch.setenv("ZEUS_MODE", "paper")
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    # Must not raise even with center_buy in the set.
    assert_live_safe_strategies_under_live_mode({"center_buy"}) is None


def test_assert_live_safe_strategies_silent_when_zeus_mode_unset(monkeypatch):
    """If ZEUS_MODE is unset entirely, helper is silent.

    Defends against import-time evaluation: tests / CI may import the helper
    without setting ZEUS_MODE. Production boot path sets ZEUS_MODE before
    invoking the helper, so the unset-case is test/CI-only.
    """
    monkeypatch.delenv("ZEUS_MODE", raising=False)
    from src.control.control_plane import assert_live_safe_strategies_under_live_mode

    assert_live_safe_strategies_under_live_mode({"center_buy"}) is None


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
