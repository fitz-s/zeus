# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + §3 Cross-Track Invariant 4
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: M3 invariant probe — cluster cap short-circuits Kelly compute before phase_aware_kelly_multiplier
# Reuse: SCAFFOLD xfail; activate only after classify_shoulder_candidate + cluster_cap logic is implemented

"""M3 invariant: cluster cap fires BEFORE phase_aware_kelly_multiplier / dynamic_kelly_mult.

Cross-Track Invariant 4 (plan §3):
  "Cluster cap fires BEFORE phase_aware_kelly_multiplier — wasted compute on
  capped-out entries is the failure mode."

Test approach (dispatch brief M3):
  - mock.spy on phase_aware_kelly_multiplier and dynamic_kelly_mult
  - assert call_count == 0 when cluster cap is exceeded
  - gates T3 work (ShoulderExposureLedger + cluster_cap_usd enforcement live in T3)
  - stub lives here in T2 scope so the invariant contract is registered

All tests are SCAFFOLD-skipped until T3 production pass wires
check_shoulder_cluster_cap() and ShoulderExposureLedger.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# M3: cluster cap short-circuits Kelly before compute
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason="SCAFFOLD — gates T3 work; check_shoulder_cluster_cap() and "
    "ShoulderExposureLedger land in T3. "
    "Test stub registers M3 invariant contract (plan §3 INV-4)."
)
def test_m3_cluster_cap_exceeded_zero_kelly_calls():
    """M3: When cluster cap is exceeded, phase_aware_kelly_multiplier call_count == 0.

    Scenario: synthetic heat-dome cluster with cap already saturated.
    Expected: evaluator emits SHOULDER_CLUSTER_CAP_EXCEEDED and does NOT
    call phase_aware_kelly_multiplier (or dynamic_kelly_mult) for the capped entry.

    Implementation note for T3 production pass:
      from unittest import mock
      with mock.patch(
          "src.strategy.kelly.phase_aware_kelly_multiplier",
          wraps=...,
      ) as spy:
          result = evaluate_shoulder_candidate(...)
          assert result.no_trade_reason == NoTradeReason.SHOULDER_CLUSTER_CAP_EXCEEDED
          assert spy.call_count == 0
    """
    pass


@pytest.mark.skip(
    reason="SCAFFOLD — gates T3 work; requires ShoulderExposureLedger + cluster_cap_usd"
)
def test_m3_cluster_cap_not_exceeded_kelly_called():
    """M3 negative case: when cluster cap is NOT exceeded, phase_aware_kelly_multiplier
    IS called (normal sizing path proceeds)."""
    pass


@pytest.mark.skip(
    reason="SCAFFOLD — gates T3 work; synthetic heat-dome 3-city probe per plan §3 + "
    "10_VERIFIER_PROBES.md probe #11"
)
def test_m3_heat_dome_three_city_cluster_cap_sequence():
    """M3 + P-3-11: Synthetic heat-dome 3-city cluster cap sequence.

    Scenario (verbatim from verifier probe #11):
      WeatherRegimeTag.HEAT_DOME tagged cluster, 3 cities same-direction
      shoulder sell attempted in sequence → only first city passes;
      subsequent 2 rejected with SHOULDER_CLUSTER_CAP_EXCEEDED.

    Asserts:
      - city_1: trade passes, kelly called once
      - city_2: SHOULDER_CLUSTER_CAP_EXCEEDED, kelly call_count unchanged
      - city_3: SHOULDER_CLUSTER_CAP_EXCEEDED, kelly call_count unchanged
    """
    pass


@pytest.mark.xfail(
    reason="pending Phase 5/6 Day0BoundState 6-class upgrade per dossier §6.2",
    strict=False,
)
def test_inv_shoulder_day0_bound_eliminates_tail():
    """P-3-7: Shoulder is safer ONLY after Day0 bound has eliminated tail.

    Relationship test (§7.6): after Day0 bound classified as
    HIGH_IMPOSSIBLE_DETERMINISTIC + source-matched observation, upper shoulder
    sell transitions from 'dangerous' to 'near-deterministic settlement capture'.

    xfail until Phase 5/6 lands the full 6-class Day0BoundState (dossier §6.2).
    Current BoundClassification on origin/main is 3-class scaffold from Phase 0 PR 5.
    """
    pass
