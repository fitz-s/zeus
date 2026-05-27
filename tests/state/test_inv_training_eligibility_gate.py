# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 9, PR D2)
"""Antibody invariants: training eligibility is decided by typed fill authority.

Finding 9 (P2 complexity debt): training/learning row writers in the
harvester previously relied on string-scan antibodies and snapshot-level
flags to refuse UNVERIFIED rescue events. Per Fitz Universal Methodology
#3 (immune system, not security guard), the policy must be enforced as a
typed boundary that makes the wrong call unconstructable.

PR D2 introduces the typed gate `is_training_eligible_position(pos)` and
the underlying `TRAINING_ELIGIBLE_FILL_AUTHORITIES` set. This test pins
the policy: degraded recovery (Finding 5 — `venue_position_observed`)
must be rejected, while venue-confirmed authorities pass.

Wiring this gate into the harvester learning write site is deliberately
out of scope for PR D2 (it requires per-position context threading
through `maybe_write_learning_pair`, which is snapshot-keyed today). PR
E or a follow-up wave does that wiring. PR D2 ships the policy boundary
that the wiring must respect.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.state.portfolio import (
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
    TRAINING_ELIGIBLE_FILL_AUTHORITIES,
    is_training_eligible_position,
)


@dataclass
class _PosStub:
    fill_authority: str = ""


@pytest.mark.parametrize(
    "authority",
    sorted(TRAINING_ELIGIBLE_FILL_AUTHORITIES),
)
def test_training_eligible_authorities_pass(authority: str) -> None:
    pos = _PosStub(fill_authority=authority)
    assert is_training_eligible_position(pos) is True


def test_venue_position_observed_is_NOT_training_eligible() -> None:
    """The PR C3 degraded-recovery slot must be rejected by the training gate."""
    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED)
    assert is_training_eligible_position(pos) is False


def test_optimistic_submitted_is_NOT_training_eligible() -> None:
    pos = _PosStub(fill_authority=FILL_AUTHORITY_OPTIMISTIC_SUBMITTED)
    assert is_training_eligible_position(pos) is False


def test_none_authority_is_NOT_training_eligible() -> None:
    pos = _PosStub(fill_authority=FILL_AUTHORITY_NONE)
    assert is_training_eligible_position(pos) is False


def test_legacy_unknown_authority_is_NOT_training_eligible() -> None:
    """Pre-typed-authority rows fail closed by default."""
    pos = _PosStub(fill_authority="legacy_unknown")
    assert is_training_eligible_position(pos) is False


def test_unrecognized_authority_is_NOT_training_eligible() -> None:
    """Fail-closed for any unknown authority value."""
    pos = _PosStub(fill_authority="something_brand_new")
    assert is_training_eligible_position(pos) is False


def test_missing_fill_authority_attribute_is_NOT_training_eligible() -> None:
    class _Bare:
        pass

    assert is_training_eligible_position(_Bare()) is False


def test_eligible_set_intersects_with_partial_and_full() -> None:
    """Sanity: the eligible set MUST include the two strongest authorities."""
    assert FILL_AUTHORITY_VENUE_CONFIRMED_FULL in TRAINING_ELIGIBLE_FILL_AUTHORITIES
    assert FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL in TRAINING_ELIGIBLE_FILL_AUTHORITIES
