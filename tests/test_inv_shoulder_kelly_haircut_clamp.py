# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + §3 Cross-Track Invariant 3 + 04_PHASE_3_SHOULDER.md §"Kelly + FDR + risk rules"
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: INV-3 probe — shoulder Kelly haircut clamp [0.05, 0.20] at phase_aware_kelly_multiplier
# Reuse: SCAFFOLD xfail; activate only after shoulder Kelly haircut logic is implemented in T2 production pass

"""INV-3: shoulder Kelly haircut clamp [0.05, 0.20] at phase_aware_kelly_multiplier.

Cross-Track Invariant 3 (plan §3):
  "Kelly haircut [0.05, 0.20] per §7.5 (only when live_status=shadow AND
  kelly_default_multiplier > 0.0; current 0.0 unchanged). Test test_inv_shoulder_kelly_haircut_clamp."

Authority (04_PHASE_3_SHOULDER.md §"Kelly + FDR + risk rules"):
  shoulder_kelly_multiplier := min(0.20, max(0.05, base_haircut_from_evidence_tier))
  Clamp applied at phase_aware_kelly_multiplier L198 call site (Interpretation B, AR2/G4).
  strategy_kelly_multiplier (L60-78) is NOT modified.

All tests are @pytest.mark.skip until the clamp guard is added to
phase_aware_kelly_multiplier in T2 production pass.

R-3 note: clamp applies only when live_status=shadow AND mult > 0.0; current
shoulder_sell kelly_default_multiplier=0.0 means clamp is a no-op today.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass adds [0.05, 0.20] clamp to phase_aware_kelly_multiplier L198")
def test_inv_shoulder_kelly_multiplier_within_5_to_20_pct():
    """INV-3: phase_aware_kelly_multiplier clamps shoulder paths to [0.05, 0.20].

    Verify for shoulder_sell with live_status=shadow and kelly_default_multiplier > 0.0:
      result >= 0.05
      result <= 0.20
    """
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass adds clamp; verify guard condition (live_status=shadow AND mult > 0.0)")
def test_inv_shoulder_kelly_clamp_guard_conditions():
    """INV-3 guard: clamp fires ONLY when live_status=shadow AND kelly_default_multiplier > 0.0.

    R-3: current shoulder_sell mult=0.0 means clamp does NOT fire (R-3 guard).
    Clamp fires when operator sets mult > 0.0 as part of shadow promotion.
    """
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass; verify strategy_kelly_multiplier (L60-78) is NOT modified")
def test_strategy_kelly_multiplier_unchanged_by_t2():
    """Verifier probe: strategy_kelly_multiplier (L60-78) does NOT contain the shoulder clamp.

    Clamp lives ONLY in phase_aware_kelly_multiplier (AR2/G4 interpretation B).
    This test imports both and verifies clamp guard appears in phase_aware but not strategy_.
    """
    pass
