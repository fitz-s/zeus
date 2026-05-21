# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: SCAFFOLD stubs for classify_shoulder_candidate — entry/no-trade routing probes
# Reuse: SCAFFOLD xfail; activate only after classify_shoulder_candidate is implemented in T2 production pass

"""SCAFFOLD test stubs for classify_shoulder_candidate (ShoulderStrategyVNext classifier).

Registers the test contract for T2 production pass activation.
All tests are @pytest.mark.skip until _classify_via_registry and
classify_shoulder_candidate production bodies are wired.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass owns classify_shoulder_candidate body")
def test_classify_shoulder_candidate_returns_vnext_for_valid_shoulder():
    """classify_shoulder_candidate returns ShoulderStrategyVNext for a valid open-shoulder edge."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass owns classify_shoulder_candidate body")
def test_classify_shoulder_candidate_returns_none_for_finite_bin():
    """classify_shoulder_candidate returns None when is_open_shoulder is False."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass owns _classify_via_registry body in strategy_profile.py")
def test_classify_via_registry_replaces_evaluator_hardcoded_shoulder_branch():
    """_classify_via_registry is called from evaluator.py shoulder branches
    (L1584/L1600/L1616 post-rebase); hardcoded string literals removed per §3 INV-2."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass owns _classify_via_registry body")
def test_classify_via_registry_fail_closed_on_unknown_strategy():
    """_classify_via_registry returns None (not raises) for unknown strategy_id."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass owns classifier body")
def test_inv_classifier_equals_registry_for_all_boot_safe_strategies():
    """Relationship test: _classify_via_registry output matches registry profile
    for all boot-safe shoulder strategies (shoulder_sell, shoulder_buy)."""
    pass
