# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T1
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""EvidenceTier IntEnum — strategy evidence lifecycle ladder.

Replaces the old live_status-only gate with a typed ladder that supports
promotion gates and audit-trail comparisons.

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
    """Evidence ladder per dossier §9."""

    IDEA = 0                   # concept only; no trade ever
    DETERMINISTIC_SEMANTICS = 1  # deterministic semantics pass
    REPLAY_PASS = 2            # replay pass against historical data
    PAPER_COHORT = 4           # paper cohort pass with quote feasibility
    LIVE_PILOT_TINY = 5        # tiny live pilot under hard position cap
    LIVE_LIMITED_HAIRCUT = 6   # limited live with strategy-specific Kelly haircut
    LIVE_NORMAL = 7            # normal live eligible (full Kelly allowed)


_ORDERED_TIERS = tuple(EvidenceTier)
_TIER_INDEX = {tier: index for index, tier in enumerate(_ORDERED_TIERS)}


def next_evidence_tier(tier: EvidenceTier) -> EvidenceTier:
    """Return the next supported tier, preserving LIVE_NORMAL as the cap."""
    index = _TIER_INDEX[tier]
    if index >= len(_ORDERED_TIERS) - 1:
        return tier
    return _ORDERED_TIERS[index + 1]


def previous_evidence_tier(tier: EvidenceTier) -> EvidenceTier:
    """Return the previous supported tier, preserving IDEA as the floor."""
    index = _TIER_INDEX[tier]
    if index <= 0:
        return tier
    return _ORDERED_TIERS[index - 1]
