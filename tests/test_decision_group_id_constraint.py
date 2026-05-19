# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.1, topology packet "phase0-pr4-decision-group-id"
"""R-4.1: DecisionGroupId NOT NULL constraint tests.

SCAFFOLD — test bodies marked xfail(strict=False, reason="SCAFFOLD").
Activate (remove xfail) in PR 4 implementation phase once:
  - decision_group_id_v1_hash() is implemented
  - calibration_pairs_v2.decision_group_id has NOT NULL constraint
  - migration scripts have been run on a test fixture DB

Test plan:
    T1: SQLite schema enforces NOT NULL — INSERT with NULL decision_group_id
        must raise IntegrityError.
    T2: Schema enforces NOT NULL — INSERT with empty-string decision_group_id
        must succeed (empty string is not NULL in SQLite).
    T3: decision_group_id_v1_hash() returns a DecisionGroupId (str subtype),
        not None, not empty.
    T4: Round-trip: decision_group_id_v1_hash() is deterministic —
        same args always return identical value.
    T5: decision_group_id_v1_hash() raises ValueError for invalid args.
"""

import pytest


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: NOT NULL constraint not yet applied")
def test_not_null_constraint_rejects_null_decision_group_id():
    """INSERT with NULL decision_group_id must raise IntegrityError.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T1: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: NOT NULL constraint not yet applied")
def test_not_null_constraint_allows_empty_string():
    """Empty-string decision_group_id is NOT NULL — must succeed.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T2: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_returns_nonempty_string():
    """decision_group_id_v1_hash() returns a non-empty str (DecisionGroupId).

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T3: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_is_deterministic():
    """Same args always return identical DecisionGroupId.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T4: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_raises_on_invalid_args():
    """ValueError for negative bin_index or lead_days_bucket.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T5: SCAFFOLD only")
