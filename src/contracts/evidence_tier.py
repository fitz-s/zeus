# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T1
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""EvidenceTier IntEnum — 8-state strategy lifecycle ladder.

Replaces the 3-state live_status gate (live | shadow | blocked) with a typed
8-tier ladder that supports Bayesian promotion gates and audit-trail comparisons.

Ordering is intentional: higher tier = more evidence = broader live eligibility.
IntEnum comparison operators work naturally: tier >= LIVE_PILOT_TINY.

Tier→Kelly mapping (resolved at strategy_profile level, NOT inside Tribunal):
  LIVE_PILOT_TINY (5): hard position cap via tribunal-issued tier_target
  LIVE_LIMITED_HAIRCUT (6): kelly_default_multiplier from strategy_profile registry
  LIVE_NORMAL (7): kelly_haircut = 1.0

Phase 6 ships the MECHANISM only; no strategy is auto-promoted.
"""
from __future__ import annotations

from enum import IntEnum


class EvidenceTier(IntEnum):
    """8-state evidence ladder per dossier §9."""

    IDEA = 0                   # concept only; no trade ever
    DETERMINISTIC_SEMANTICS = 1  # deterministic semantics pass
    REPLAY_PASS = 2            # replay pass against historical data
    SHADOW_PASS = 3            # shadow pass with no-trade decision logging
    PAPER_COHORT = 4           # paper cohort pass with quote feasibility
    LIVE_PILOT_TINY = 5        # tiny live pilot under hard position cap
    LIVE_LIMITED_HAIRCUT = 6   # limited live with strategy-specific Kelly haircut
    LIVE_NORMAL = 7            # normal live eligible (full Kelly allowed)
