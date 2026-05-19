# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5.6
"""INV-nowcast-horizon-bound antibody.

Invariant: Day0Nowcast.evaluate() MUST raise NotApplicableHorizon when
market.max_hours_to_resolution > 6.

xfail-strict rationale:
    SCAFFOLD stub: Day0Nowcast.evaluate() currently raises NotImplementedError.
    pytest.raises(NotApplicableHorizon) does not catch NotImplementedError —
    the unexpected exception propagates and the test errors (not fails).
    xfail-strict counts this as an expected failure at SCAFFOLD time.

    Production pass: evaluate() is implemented. First action inside evaluate() is
    the horizon guard — raises NotApplicableHorizon when hours > 6. Test transitions
    from xfail to strict-pass on the same commit. No test modification needed.

Relationship: Cross-module invariant (Fitz §3 pattern). Tests that the Day0Nowcast
contract (src/signal/day0_nowcast.py) correctly enforces the applicability boundary
— live mode must not silently fall back to forecast pipeline output relabeled as nowcast.
"""
import types

import pytest

from src.signal.day0_nowcast import Day0Nowcast, NotApplicableHorizon


def _make_market(max_hours_to_resolution: float):
    """Minimal market stub carrying only the field the horizon guard checks."""
    m = types.SimpleNamespace()
    m.max_hours_to_resolution = max_hours_to_resolution
    m.market_slug = "test-market-stub"
    return m


def _make_observation():
    """Minimal observation stub."""
    obs = types.SimpleNamespace()
    obs.value = 72.0
    obs.source = "wu_asos"
    obs.observation_time = "2026-05-19T12:00:00Z"
    return obs


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SCAFFOLD: Day0Nowcast.evaluate() raises NotImplementedError (stub body). "
        "pytest.raises(NotApplicableHorizon) does not catch NotImplementedError — "
        "test errors as expected-fail. Production pass implements horizon guard "
        "(first line of evaluate() raises NotApplicableHorizon when hours > 6), "
        "causing this test to transition to strict-pass on the same commit."
    ),
)
def test_day0_nowcast_horizon_bound_enforces_6h_ceiling():
    """INV-nowcast-horizon-bound: evaluate() raises NotApplicableHorizon for >6h markets.

    Fail-closed: markets with max_hours_to_resolution > 6 must not be served by
    the nowcast model. Silent fallback relabeling forecast output as nowcast would
    corrupt P_fused semantics and the downstream decision pipeline.
    """
    market = _make_market(max_hours_to_resolution=8.0)
    obs = _make_observation()
    nowcast = Day0Nowcast(temperature_metric="high")

    with pytest.raises(NotApplicableHorizon):
        nowcast.evaluate(obs, "afternoon", market)
