# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T1
"""T1 invariant tests for EvidenceTier IntEnum."""
from __future__ import annotations

import pytest

from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# T1-1: 8-member enum with correct values
# ---------------------------------------------------------------------------

def test_t1_eight_members() -> None:
    """EvidenceTier has exactly 8 members."""
    assert len(EvidenceTier) == 8


def test_t1_member_values() -> None:
    """Members have the correct integer values 0..7."""
    expected = {
        "IDEA": 0,
        "DETERMINISTIC_SEMANTICS": 1,
        "REPLAY_PASS": 2,
        "SHADOW_PASS": 3,
        "PAPER_COHORT": 4,
        "LIVE_PILOT_TINY": 5,
        "LIVE_LIMITED_HAIRCUT": 6,
        "LIVE_NORMAL": 7,
    }
    for name, value in expected.items():
        assert EvidenceTier[name] == value, (
            f"EvidenceTier.{name} expected {value}, got {EvidenceTier[name]}"
        )


# ---------------------------------------------------------------------------
# T1-2: ordering preserved (IntEnum comparison)
# ---------------------------------------------------------------------------

def test_t1_ordering_ascending() -> None:
    """Tiers are strictly ordered IDEA < ... < LIVE_NORMAL."""
    members = list(EvidenceTier)
    for i in range(len(members) - 1):
        assert members[i] < members[i + 1], (
            f"{members[i].name} should be < {members[i+1].name}"
        )


def test_t1_live_pilot_tiny_ge_check() -> None:
    """LIVE_PILOT_TINY (5) satisfies >= LIVE_PILOT_TINY; SHADOW_PASS (3) does not."""
    assert EvidenceTier.LIVE_PILOT_TINY >= EvidenceTier.LIVE_PILOT_TINY
    assert EvidenceTier.LIVE_NORMAL >= EvidenceTier.LIVE_PILOT_TINY
    assert not (EvidenceTier.SHADOW_PASS >= EvidenceTier.LIVE_PILOT_TINY)
    assert not (EvidenceTier.IDEA >= EvidenceTier.LIVE_PILOT_TINY)


# ---------------------------------------------------------------------------
# T1-3: IntEnum lookup by name
# ---------------------------------------------------------------------------

def test_t1_lookup_by_name() -> None:
    """EvidenceTier['LIVE_NORMAL'] returns the correct member."""
    assert EvidenceTier["LIVE_NORMAL"] is EvidenceTier.LIVE_NORMAL
    assert EvidenceTier["IDEA"] is EvidenceTier.IDEA


def test_t1_unknown_name_raises_key_error() -> None:
    """EvidenceTier['UNKNOWN'] raises KeyError (fail-closed at loader level)."""
    with pytest.raises(KeyError):
        _ = EvidenceTier["UNKNOWN_TIER"]
