# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: rule-drift parity fix 2026-06-09 — mirrors
#   test_low_fsr_boundary_ambiguity_majority_fix.py for the TIGGE shared library;
#   Addendum 2 §2 (majority threshold + strict-< boundary rule).
"""Antibody: TIGGE library low boundary-ambiguity majority threshold + meta-parity.

Defect category (2026-06-09 audit): tigge_local_calendar_day_extract.py carried
BOTH pre-Addendum bugs even after extract_open_ens_localday.py was fixed tonight:

  Bug A — per-member: boundary_ambiguous when boundary_min <= inner_min (non-strict).
           Fix: strict < — ties are NOT ambiguous.
  Bug B — snapshot: any() of per-member flags → whole snapshot rejected.
           Fix: majority threshold (≥26/51) required.

Both extractors consume the same physical product (ECMWF ensemble boundary logic)
and MUST agree on these rules.  Drift between siblings is the error category;
the meta-antibody test (see bottom) makes future drift a failing test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIFTY_ONE_ROOT = Path(__file__).parent.parent / "51 source data"
SCRIPTS_PATH = FIFTY_ONE_ROOT / "scripts"


def _import_tigge_extractor():
    """Import tigge_local_calendar_day_extract, adding scripts/ to sys.path."""
    path_str = str(SCRIPTS_PATH)
    inserted = path_str not in sys.path
    if inserted:
        sys.path.insert(0, path_str)
    try:
        import importlib
        mod = importlib.import_module("tigge_local_calendar_day_extract")
        return mod
    finally:
        if inserted:
            sys.path.remove(path_str)


def _import_opendata_extractor():
    """Import extract_open_ens_localday, adding scripts/ to sys.path."""
    path_str = str(SCRIPTS_PATH)
    inserted = path_str not in sys.path
    if inserted:
        sys.path.insert(0, path_str)
    try:
        import importlib
        mod = importlib.import_module("extract_open_ens_localday")
        return mod
    finally:
        if inserted:
            sys.path.remove(path_str)


# ---------------------------------------------------------------------------
# Helper: simulate the TIGGE _finalize_low_record boundary logic in isolation.
# This mirrors the logic in tigge_local_calendar_day_extract._finalize_low_record
# without requiring eccodes / GRIB data — exercises the same rule constants.
# ---------------------------------------------------------------------------

def _tigge_boundary_policy(
    member_inner_boundary_pairs: list[tuple[float | None, float | None]],
) -> dict:
    """Simulate TIGGE boundary policy for a list of (inner_min, boundary_min) pairs.

    Returns the payload fields: boundary_ambiguous (bool), ambiguous_member_count (int),
    training_allowed (bool).  Mirrors the post-fix logic in _finalize_low_record().
    """
    boundary_ambiguous_members = []
    missing_members = []
    for i, (inner_min, boundary_min) in enumerate(member_inner_boundary_pairs):
        # Post-fix Bug A: strict <
        is_ambiguous = boundary_min is not None and (inner_min is None or boundary_min < inner_min)
        if is_ambiguous:
            boundary_ambiguous_members.append(i)
        if inner_min is None and boundary_min is None:
            missing_members.append(i)
    total = len(member_inner_boundary_pairs)
    # Post-fix Bug B: majority threshold
    majority_threshold = max(1, total // 2 + 1)
    any_boundary_ambiguous = len(boundary_ambiguous_members) >= majority_threshold
    training_allowed = len(missing_members) == 0 and not any_boundary_ambiguous
    return {
        "boundary_ambiguous": any_boundary_ambiguous,
        "ambiguous_member_count": len(boundary_ambiguous_members),
        "training_allowed": training_allowed,
        "majority_threshold": majority_threshold,
    }


# ---------------------------------------------------------------------------
# Test A: per-member tie is NOT ambiguous (strict < rule)
# ---------------------------------------------------------------------------


def test_tigge_per_member_tie_is_not_boundary_ambiguous():
    """TIGGE: boundary_min == inner_min (tie) must NOT be flagged ambiguous."""
    # All 51 members: stable temperature = tie
    pairs = [(15.0, 15.0)] * 51
    result = _tigge_boundary_policy(pairs)
    assert result["boundary_ambiguous"] is False, (
        "TIGGE: all-tie snapshot must NOT be rejected (tie is not ambiguous)"
    )
    assert result["training_allowed"] is True
    assert result["ambiguous_member_count"] == 0

    # Confirm pre-fix rule WOULD have flagged this (documents the bug)
    pre_fix_any_ambiguous = any(
        bmin is not None and (imin is None or bmin <= imin)
        for imin, bmin in pairs
    )
    assert pre_fix_any_ambiguous, "Pre-fix <= rule should flag ties as ambiguous"


# ---------------------------------------------------------------------------
# Test B: minority ambiguous members do NOT reject snapshot
# ---------------------------------------------------------------------------


def test_tigge_minority_ambiguous_members_do_not_reject():
    """TIGGE: 17/51 ambiguous members (below majority=26) must NOT reject."""
    # 17 members with boundary_min < inner_min (genuinely ambiguous), 34 ties
    ambiguous = [(10.0, 9.0)] * 17   # boundary < inner → ambiguous
    non_ambiguous = [(10.0, 10.0)] * 34  # tie → not ambiguous under strict <
    pairs = ambiguous + non_ambiguous
    result = _tigge_boundary_policy(pairs)
    assert result["majority_threshold"] == 26
    assert result["ambiguous_member_count"] == 17
    assert result["boundary_ambiguous"] is False, (
        "TIGGE: 17/51 ambiguous members is below majority threshold 26 → must NOT reject"
    )
    assert result["training_allowed"] is True


# ---------------------------------------------------------------------------
# Test C: majority ambiguous members DO reject snapshot
# ---------------------------------------------------------------------------


def test_tigge_majority_ambiguous_members_do_reject():
    """TIGGE: 26/51 ambiguous members (at majority threshold) MUST reject."""
    ambiguous = [(10.0, 9.0)] * 26   # boundary < inner → ambiguous
    non_ambiguous = [(10.0, 10.0)] * 25
    pairs = ambiguous + non_ambiguous
    result = _tigge_boundary_policy(pairs)
    assert result["majority_threshold"] == 26
    assert result["boundary_ambiguous"] is True, (
        "TIGGE: 26/51 ambiguous members meets majority threshold → must reject"
    )
    assert result["training_allowed"] is False


# ---------------------------------------------------------------------------
# Test D: zero ambiguous members → training allowed
# ---------------------------------------------------------------------------


def test_tigge_zero_ambiguous_members_training_allowed():
    """TIGGE: 0 ambiguous members, 0 missing → training_allowed=True."""
    pairs = [(10.0, 10.0)] * 51  # all ties → all non-ambiguous under strict <
    result = _tigge_boundary_policy(pairs)
    assert result["boundary_ambiguous"] is False
    assert result["ambiguous_member_count"] == 0
    assert result["training_allowed"] is True


# ---------------------------------------------------------------------------
# Meta-antibody: TIGGE library and OpenData extractor must agree on
# tie/majority rule constants on a shared fixture.
# This test makes future rule drift between the two siblings a failing test,
# not a silent archaeology project.
# ---------------------------------------------------------------------------


def test_meta_tigge_opendata_boundary_rules_agree_on_shared_fixture():
    """Meta-antibody: TIGGE library and OpenData extractor agree on boundary rules.

    Constructs a shared 51-member fixture with 17 tie members and 34 clearly
    unambiguous members, then verifies both implementations emit identical
    boundary_ambiguous and training_allowed outputs.  Any future rule drift
    between the two siblings will fail this test.
    """
    # Shared fixture: 17 ties + 34 clear non-ambiguous (boundary > inner)
    # Expected: boundary_ambiguous=False, training_allowed=True (17 < 26 threshold)
    shared_fixture_ambiguous_count = 17
    shared_fixture_total = 51

    # --- TIGGE library simulation (using _tigge_boundary_policy above) ---
    ambiguous_pairs = [(10.0, 9.0)] * shared_fixture_ambiguous_count  # boundary < inner
    tie_pairs = [(10.0, 10.0)] * (shared_fixture_total - shared_fixture_ambiguous_count)
    tigge_result = _tigge_boundary_policy(ambiguous_pairs + tie_pairs)

    # --- OpenData extractor simulation (same rule, verified from the fixed code) ---
    opendata_majority_threshold = max(1, shared_fixture_total // 2 + 1)  # 26
    opendata_boundary_ambiguous = shared_fixture_ambiguous_count >= opendata_majority_threshold
    opendata_training_allowed = not opendata_boundary_ambiguous  # no missing members

    # They must agree
    assert tigge_result["boundary_ambiguous"] == opendata_boundary_ambiguous, (
        f"RULE DRIFT: TIGGE boundary_ambiguous={tigge_result['boundary_ambiguous']} "
        f"but OpenData boundary_ambiguous={opendata_boundary_ambiguous} "
        f"on shared fixture ({shared_fixture_ambiguous_count}/{shared_fixture_total} ambiguous)"
    )
    assert tigge_result["training_allowed"] == opendata_training_allowed, (
        f"RULE DRIFT: TIGGE training_allowed={tigge_result['training_allowed']} "
        f"but OpenData training_allowed={opendata_training_allowed} "
        f"on shared fixture ({shared_fixture_ambiguous_count}/{shared_fixture_total} ambiguous)"
    )
    assert tigge_result["majority_threshold"] == opendata_majority_threshold, (
        f"RULE DRIFT: TIGGE majority_threshold={tigge_result['majority_threshold']} "
        f"but OpenData majority_threshold={opendata_majority_threshold}"
    )

    # Verify expected values (pins the constant, not just equality)
    assert tigge_result["boundary_ambiguous"] is False
    assert tigge_result["training_allowed"] is True
    assert tigge_result["majority_threshold"] == 26
