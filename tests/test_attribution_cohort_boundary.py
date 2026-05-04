# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A1 + §6 INT2 (cohort boundary microsecond inclusivity is regression antibody I5).
"""Cohort boundary microsecond-inclusivity regression tests.

The cohort discriminator at ``src/state/cohort_boundary.py`` decides
whether an attribution row belongs to the pre-PR-#51 (legacy local-
clock scheduler) or post-PR-#51 (UTC-pinned scheduler) cohort. An
off-by-one error at the boundary would silently mislabel the cron
firings on either side of the merge instant, biasing the migration
report. These tests pin the boundary semantics so a future refactor
that flips the comparison from ``<`` to ``<=`` (or rounds to
seconds, or accepts naive datetimes) fails immediately.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.state.cohort_boundary import (
    ZEUS_PR51_MERGE_INSTANT_UTC,
    cohort_label,
    cohort_pre_utc_fix,
)


def test_boundary_constant_matches_git_fact() -> None:
    """`git log -1 e62710e6 --format=%cI` → 2026-05-03T22:57:08-05:00
    which equals 2026-05-04T03:57:08+00:00. Pin both the wall-clock
    components AND the tzinfo so a future edit that drops UTC by
    accident gets caught.
    """
    assert ZEUS_PR51_MERGE_INSTANT_UTC == datetime(
        2026, 5, 4, 3, 57, 8, tzinfo=timezone.utc
    )
    assert ZEUS_PR51_MERGE_INSTANT_UTC.tzinfo == timezone.utc


def test_boundary_instant_exactly_is_post_utc_fix() -> None:
    """The merge instant itself belongs to post_utc_fix: the fix
    took effect at the merge, so a decision recorded at exactly
    that instant ran on the new schedule. Strict less-than in
    ``cohort_pre_utc_fix`` enforces this.
    """
    assert cohort_pre_utc_fix(ZEUS_PR51_MERGE_INSTANT_UTC) is False
    assert cohort_label(ZEUS_PR51_MERGE_INSTANT_UTC) == "post_utc_fix"


def test_one_microsecond_before_is_pre_utc_fix() -> None:
    one_us_before = ZEUS_PR51_MERGE_INSTANT_UTC - timedelta(microseconds=1)
    assert cohort_pre_utc_fix(one_us_before) is True
    assert cohort_label(one_us_before) == "pre_utc_fix"


def test_one_microsecond_after_is_post_utc_fix() -> None:
    one_us_after = ZEUS_PR51_MERGE_INSTANT_UTC + timedelta(microseconds=1)
    assert cohort_pre_utc_fix(one_us_after) is False
    assert cohort_label(one_us_after) == "post_utc_fix"


def test_naive_datetime_is_rejected() -> None:
    """Naive input would force an implicit tz assumption — the
    bug class PR #51 closed for the live scheduler. Cohort helper
    refuses to reintroduce it.
    """
    naive = datetime(2026, 5, 4, 3, 57, 8)  # no tzinfo
    with pytest.raises(ValueError, match="tz-aware"):
        cohort_pre_utc_fix(naive)
    with pytest.raises(ValueError, match="tz-aware"):
        cohort_label(naive)


@pytest.mark.parametrize(
    "recorded_at,expected",
    [
        # PR #40 merged 2026-05-02T21:41:50Z → pre
        (datetime(2026, 5, 2, 21, 41, 50, tzinfo=timezone.utc), "pre_utc_fix"),
        # PR #44 merged 2026-05-02T23:57:42Z → pre
        (datetime(2026, 5, 2, 23, 57, 42, tzinfo=timezone.utc), "pre_utc_fix"),
        # PR #47 merged 2026-05-04T01:45:56Z → pre
        (datetime(2026, 5, 4, 1, 45, 56, tzinfo=timezone.utc), "pre_utc_fix"),
        # PR #49 merged 2026-05-04T03:29:47Z → pre
        (datetime(2026, 5, 4, 3, 29, 47, tzinfo=timezone.utc), "pre_utc_fix"),
        # PR #52 merged 2026-05-04T03:51:53Z → pre (5 min before #51)
        (datetime(2026, 5, 4, 3, 51, 53, tzinfo=timezone.utc), "pre_utc_fix"),
        # PR #53 merged 2026-05-04T07:40:14Z → post
        (datetime(2026, 5, 4, 7, 40, 14, tzinfo=timezone.utc), "post_utc_fix"),
        # Far past
        (datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc), "pre_utc_fix"),
        # Far future
        (datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc), "post_utc_fix"),
    ],
)
def test_real_pr_merge_instants_resolve_correctly(
    recorded_at: datetime, expected: str
) -> None:
    """Synthetic decisions placed at every recent PR merge instant
    bucket on the correct side of the boundary. PR #52 merged at
    03:51:53Z (5 min BEFORE PR #51's 03:57:08Z) — its label must
    be ``pre_utc_fix`` even though it's chronologically very close.
    """
    assert cohort_label(recorded_at) == expected


def test_non_utc_tzaware_input_is_compared_correctly() -> None:
    """A tz-aware datetime in any zone (not just UTC) compares
    correctly because Python's ``<`` is timezone-aware: the
    comparison normalizes to a single instant. PR #51 merge
    instant in CDT (-05:00) is ``2026-05-03T22:57:08-05:00``.
    """
    cdt = timezone(timedelta(hours=-5))
    same_instant_in_cdt = datetime(2026, 5, 3, 22, 57, 8, tzinfo=cdt)
    assert same_instant_in_cdt == ZEUS_PR51_MERGE_INSTANT_UTC
    assert cohort_label(same_instant_in_cdt) == "post_utc_fix"

    one_us_before_in_cdt = same_instant_in_cdt - timedelta(microseconds=1)
    assert cohort_label(one_us_before_in_cdt) == "pre_utc_fix"
