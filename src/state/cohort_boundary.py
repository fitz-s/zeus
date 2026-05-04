# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A1 + D-5/D-6 (report-time cohort split, helper importable from any reporter; no DB schema change).
"""PR #51 UTC-fix migration cohort boundary helpers.

PR #51 (`P0 scheduler-tz: pin APScheduler to UTC`, merged at
e62710e6 → ``2026-05-04T03:57:08Z``) flipped the live scheduler
from local-clock to UTC. Decisions and edges produced before
that instant ran on the legacy schedule; decisions after ran
on the UTC-pinned schedule. The two cohorts are not commensurable
for performance attribution — comparing them as a single
population masks the regime change and mislabels the alpha
signal during the migration window.

This module exposes the discriminator as a pure helper. Reporters
import it to bucket rows by regime; no DB column is added (the
boundary is a git-log fact derivable lazily at report time from
the existing ``recorded_at`` column on any attribution table).

Boundary semantics: the merge instant itself is INCLUSIVE on the
post-fix side. A decision recorded exactly at
``2026-05-04T03:57:08Z`` is in ``post_utc_fix`` on the assumption
that the UTC fix took effect at the merge instant. Decisions
recorded strictly before are ``pre_utc_fix``. Microsecond
precision matters: cron jobs near the boundary are rare but
possible, and an off-by-one cohort assignment would silently
bias the migration report.

Naive datetimes are rejected. The boundary is a UTC instant; an
unzoned input requires an implicit tz assumption that is itself
the bug class this module exists to detect (PR #51 closed exactly
that class for the live scheduler — the cohort helper refuses
to reintroduce it).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

ZEUS_PR51_MERGE_INSTANT_UTC: datetime = datetime(
    2026, 5, 4, 3, 57, 8, tzinfo=timezone.utc
)
"""PR #51 (`P0 scheduler-tz: pin APScheduler to UTC`) merge instant
in UTC. Verified via ``git log -1 e62710e6 --format=%cI`` →
``2026-05-03T22:57:08-05:00`` ≡ ``2026-05-04T03:57:08+00:00``.

This is the discriminator boundary for the cohort helpers below.
"""


CohortLabel = Literal["pre_utc_fix", "post_utc_fix"]


def cohort_pre_utc_fix(recorded_at_utc: datetime) -> bool:
    """Return ``True`` iff ``recorded_at_utc`` precedes the PR #51
    merge instant. Strict less-than: the merge instant itself is
    post-fix.

    Raises ``ValueError`` on naive datetime input.
    """
    if (
        recorded_at_utc.tzinfo is None
        or recorded_at_utc.tzinfo.utcoffset(recorded_at_utc) is None
    ):
        raise ValueError(
            "cohort_pre_utc_fix requires a tz-aware datetime; "
            f"got naive {recorded_at_utc!r}"
        )
    return recorded_at_utc < ZEUS_PR51_MERGE_INSTANT_UTC


def cohort_label(recorded_at_utc: datetime) -> CohortLabel:
    """Return ``"pre_utc_fix"`` or ``"post_utc_fix"`` for a
    recorded timestamp. See ``cohort_pre_utc_fix`` for boundary
    semantics. Raises ``ValueError`` on naive datetime input.
    """
    return "pre_utc_fix" if cohort_pre_utc_fix(recorded_at_utc) else "post_utc_fix"
