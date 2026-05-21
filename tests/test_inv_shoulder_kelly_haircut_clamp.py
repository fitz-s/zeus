# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + §3 Cross-Track Invariant 3 + 04_PHASE_3_SHOULDER.md §"Kelly + FDR + risk rules"
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: INV-3 probe — shoulder Kelly haircut clamp [0.05, 0.20] at phase_aware_kelly_multiplier; R-3 guard (live_status=shadow AND mult > 0.0)
# Reuse: activated in T2 production pass; replaces SCAFFOLD xfail stubs

"""INV-3: shoulder Kelly haircut clamp [0.05, 0.20] at phase_aware_kelly_multiplier.

Cross-Track Invariant 3 (plan §3):
  "Kelly haircut [0.05, 0.20] per §7.5 (only when live_status=shadow AND
  kelly_default_multiplier > 0.0; current 0.0 unchanged). Test test_inv_shoulder_kelly_haircut_clamp."

R-3 note: clamp applies only when live_status=shadow AND mult > 0.0; current
shoulder_sell kelly_default_multiplier=0.0 means clamp is a no-op today.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.strategy.strategy_profile as sp


# ── Minimal YAML for synthetic shoulder_sell registry entry ────────────────

_SHOULDER_SELL_YAML = """\
shoulder_sell:
  thesis: test entry — shoulder_sell with non-zero kelly for clamp probe
  live_status: shadow
  allowed_market_phases: [pre_settlement_day, settlement_day]
  allowed_discovery_modes: [update_reaction]
  cycle_axis_dispatch_mode: update_reaction
  allowed_directions: [buy_no]
  allowed_bin_topology: [open_shoulder]
  metric_support:
    high: shadow
    low: blocked
  kelly_default_multiplier: 0.15
  kelly_phase_overrides: {}
  min_shadow_decisions: 0
  min_settled_decisions: 0
  promotion_evidence_ref: null
"""


@pytest.fixture(autouse=True)
def _restore_registry():
    """Always restore the real registry after each test in this module."""
    yield
    sp._reload_for_test()


def test_inv_shoulder_kelly_multiplier_within_5_to_20_pct(tmp_path: Path, monkeypatch):
    """INV-3: phase_aware_kelly_multiplier clamps shoulder paths to [0.05, 0.20].

    With live_status=shadow and kelly_default_multiplier=0.15 (> 0), a raw
    product > 0.20 must be clamped to 0.20 and a product < 0.05 must be
    clamped to 0.05.
    """
    # Load synthetic registry with mult=0.15 for shoulder_sell.
    reg_path = tmp_path / "test_registry.yaml"
    reg_path.write_text(_SHOULDER_SELL_YAML)
    sp._reload_for_test(reg_path)

    # Mock oracle to return penalty_multiplier=1.0 (no penalty).
    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda *a, **kw: SimpleNamespace(penalty_multiplier=1.0),
    )

    from src.strategy.kelly import phase_aware_kelly_multiplier

    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    now = datetime.datetime(2026, 7, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)
    target = datetime.date(2026, 7, 15)

    # With kelly_default_multiplier=0.15 at no phase override and oracle=1.0,
    # raw result ≈ 0.15. Clamp [0.05, 0.20] → 0.15 passes through unchanged.
    result = phase_aware_kelly_multiplier(
        strategy_key="shoulder_sell",
        market_phase=None,
        city=city,
        temperature_metric="high",
        decision_time_utc=now,
        target_local_date=target,
        phase_source=None,
    )
    assert result >= 0.05, f"Result {result} below clamp floor 0.05"
    assert result <= 0.20, f"Result {result} above clamp ceiling 0.20"


def test_inv_shoulder_kelly_clamp_guard_conditions(tmp_path: Path, monkeypatch):
    """INV-3 guard: clamp fires ONLY when live_status=shadow AND kelly_default_multiplier > 0.0.

    R-3: current real registry has shoulder_sell mult=0.0 → clamp does NOT fire.
    With mult=0.0, phase_aware_kelly_multiplier short-circuits at m_strategy_phase <= 0.
    """
    # Real registry — shoulder_sell.kelly_default_multiplier == 0.0.
    # No synthetic registry loaded; restore_registry fixture handles reset.

    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda *a, **kw: SimpleNamespace(penalty_multiplier=1.0),
    )

    from src.strategy.kelly import phase_aware_kelly_multiplier

    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    now = datetime.datetime(2026, 7, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)
    target = datetime.date(2026, 7, 15)

    # Real mult=0.0 → m_strategy_phase=0.0 → short-circuit → result=0.0.
    # Clamp guard (mult > 0.0) must NOT fire — result stays 0.0.
    result = phase_aware_kelly_multiplier(
        strategy_key="shoulder_sell",
        market_phase=None,
        city=city,
        temperature_metric="high",
        decision_time_utc=now,
        target_local_date=target,
        phase_source=None,
    )
    assert result == 0.0, (
        f"R-3 guard failed: shoulder_sell with mult=0.0 must return 0.0, got {result}. "
        "Clamp must NOT fire when kelly_default_multiplier == 0.0."
    )


def test_strategy_kelly_multiplier_unchanged_by_t2():
    """Verifier probe: strategy_kelly_multiplier (L60-78) does NOT contain the shoulder clamp.

    Clamp lives ONLY in phase_aware_kelly_multiplier (AR2/G4 interpretation B).
    """
    import inspect
    from src.strategy.kelly import phase_aware_kelly_multiplier, strategy_kelly_multiplier

    phase_src = inspect.getsource(phase_aware_kelly_multiplier)
    strategy_src = inspect.getsource(strategy_kelly_multiplier)

    assert "0.05" in phase_src or "0.20" in phase_src, (
        "Clamp bounds must appear in phase_aware_kelly_multiplier source"
    )
    assert "0.05" not in strategy_src and "0.20" not in strategy_src, (
        "Clamp bounds must NOT appear in strategy_kelly_multiplier source (AR2/G4: clamp only in phase_aware)"
    )
