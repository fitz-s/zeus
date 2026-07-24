# Lifecycle: created=2026-05-21; last_reviewed=2026-07-24; last_reused=2026-07-24
# Purpose: one-law successor to INV-3 — a blocked-key (shoulder_sell) sizes 0.0
#   UNCONDITIONALLY, even if a registry entry carries a non-zero
#   kelly_default_multiplier. The [0.05,0.20] shoulder clamp was label
#   economics; under the one law "blocked" is an operator prohibition, not a
#   band. Strictly more conservative than the clamp it replaces.
# Reuse: run on any change to src/strategy/kelly.py's identity/permission gate
#   or the registry live_status semantics.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/
#   COLLISION.md group B + FINAL_SPEC.md §What remains (supersedes
#   PHASE_3_SHOULDER_PLAN.md §3 Cross-Track Invariant 3).

"""Blocked-key sizing law: live_status != live → 0.0, no clamp band.

Pre-one-law, INV-3 clamped a blocked shoulder path with a non-zero registry
multiplier into [0.05, 0.20] — i.e. a prohibited strategy could still size
5-20%. The one law removes label-owned economics entirely: prohibition means
zero. These antibodies pin that the clamp machinery is gone and cannot
resurrect a blocked key's sizing through any registry configuration.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.strategy.strategy_profile as sp


_SHOULDER_SELL_YAML = """\
shoulder_sell:
  thesis: test entry — blocked key with non-zero kelly to prove prohibition wins
  live_status: blocked
  allowed_market_phases: [pre_settlement_day, settlement_day]
  allowed_discovery_modes: [update_reaction]
  cycle_axis_dispatch_mode: update_reaction
  allowed_directions: [buy_no]
  allowed_bin_topology: [open_shoulder]
  metric_support:
    high: blocked
    low: blocked
  kelly_default_multiplier: 0.15
  kelly_phase_overrides: {}
  min_settled_decisions: 0
  promotion_evidence_ref: null
"""


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    sp._reload_for_test()


def _resolve_shoulder() -> float:
    from src.strategy.kelly import phase_aware_kelly_multiplier

    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    now = datetime.datetime(2026, 7, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)
    return phase_aware_kelly_multiplier(
        strategy_key="shoulder_sell",
        market_phase=None,
        city=city,
        temperature_metric="high",
        decision_time_utc=now,
        target_local_date=datetime.date(2026, 7, 15),
        phase_source=None,
    )


def test_blocked_key_sizes_zero_even_with_nonzero_registry_multiplier(
    tmp_path: Path, monkeypatch
):
    """A blocked key with kelly_default_multiplier=0.15 in the registry still
    sizes 0.0 — prohibition is absolute, not clamped into [0.05, 0.20]."""
    reg_path = tmp_path / "test_registry.yaml"
    reg_path.write_text(_SHOULDER_SELL_YAML)
    sp._reload_for_test(reg_path)

    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda *a, **kw: SimpleNamespace(penalty_multiplier=1.0),
    )
    assert _resolve_shoulder() == 0.0


def test_blocked_key_sizes_zero_on_real_registry(monkeypatch):
    """Real registry (shoulder_sell live_status=blocked) → 0.0."""
    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda *a, **kw: SimpleNamespace(penalty_multiplier=1.0),
    )
    assert _resolve_shoulder() == 0.0


def test_clamp_machinery_is_gone():
    """The [0.05, 0.20] clamp bounds are absent from both resolvers — the
    band cannot silently return."""
    import inspect
    from src.strategy.kelly import phase_aware_kelly_multiplier, strategy_kelly_multiplier

    phase_src = inspect.getsource(phase_aware_kelly_multiplier)
    strategy_src = inspect.getsource(strategy_kelly_multiplier)
    for src_text in (phase_src, strategy_src):
        assert "0.05" not in src_text and "0.20" not in src_text, (
            "shoulder clamp bounds must not reappear in the one-law resolvers"
        )
