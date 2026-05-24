# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   (Backfill planner) + §11 (Backfill policies); docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR5).
"""Bounded backfill planner — PR5 (pure logic).

Backfill must be PARTITION-driven and BOUNDED. This planner turns a (source_id, role, window)
request into explicit ``BackfillTask`` units and REFUSES unbounded windows unless explicitly
allowed AND the role is not live. Two invariants it enforces:

  * A backfill task is NEVER live-authorized (``live_authorization`` is always False) — backfill
    writes can fill training/audit data but must never set live readiness (spec §"Data Type
    Taxonomy": backfill authority must never set live readiness).
  * An unbounded window (missing start or end) is refused unless ``allow_unbounded=True`` and
    the role is not 'live' — so catch-up cannot silently scan a giant range.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


import re

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UnboundedBackfillRefused(ValueError):
    """Raised when an unbounded backfill window is requested without explicit allowance."""


@dataclass(frozen=True)
class BackfillTask:
    """One bounded backfill unit. live_authorization is always False (enforced)."""

    source_id: str
    role: str
    partition_start: str
    partition_end: str
    max_rows: int = 100_000
    max_runtime_seconds: int = 600
    live_authorization: bool = field(default=False, init=False)  # never settable


def plan_backfill(
    source_id: str,
    role: str,
    partition_start: Optional[str],
    partition_end: Optional[str],
    *,
    allow_unbounded: bool = False,
    max_rows: int = 100_000,
    max_runtime_seconds: int = 600,
) -> list[BackfillTask]:
    """Plan bounded backfill tasks for a partition window.

    Refuses (raises ``UnboundedBackfillRefused``) when either bound is missing, unless
    ``allow_unbounded=True`` AND role != 'live'. A live role can never request an unbounded
    backfill (live data is forward-only; historical repair is a non-live concern).
    """
    if role == "live":
        # Backfill is by definition not the live-production path.
        raise UnboundedBackfillRefused(
            f"role='live' cannot request backfill for {source_id!r}; backfill is non-live by design"
        )

    if partition_start is None or partition_end is None:
        if not allow_unbounded:
            raise UnboundedBackfillRefused(
                f"unbounded backfill window for {source_id!r} (start={partition_start!r}, "
                f"end={partition_end!r}) refused; pass allow_unbounded=True for an explicit sweep"
            )
        # Even when allowed, an unbounded sweep yields no concrete task list here — the caller
        # must resolve real bounds first. We surface that rather than fabricate a range.
        raise UnboundedBackfillRefused(
            f"unbounded backfill for {source_id!r} allowed but no concrete bounds resolved; "
            f"resolve partition_start/partition_end before planning tasks"
        )

    # PR review #329 F11: the < comparison below is lexicographic, which is only correct for
    # ISO-8601 dates (YYYY-MM-DD sort == chronological). Block/cycle/numeric partition grains
    # would mis-order under lexicographic compare ("100" < "9"). PR5 supports DATE partitions
    # only; typed PartitionKey ordering for block/cycle grains is future work. Fail closed on
    # non-ISO bounds rather than silently mis-ordering.
    if not (_ISO_DATE_RE.match(partition_start) and _ISO_DATE_RE.match(partition_end)):
        raise UnboundedBackfillRefused(
            f"non-ISO-date partition bounds for {source_id!r} "
            f"(start={partition_start!r}, end={partition_end!r}); this planner supports "
            f"YYYY-MM-DD date partitions only (block/cycle grains need typed PartitionKey)"
        )

    if partition_end < partition_start:
        raise UnboundedBackfillRefused(
            f"backfill window end {partition_end!r} precedes start {partition_start!r}"
        )

    return [
        BackfillTask(
            source_id=source_id,
            role=role,
            partition_start=partition_start,
            partition_end=partition_end,
            max_rows=max_rows,
            max_runtime_seconds=max_runtime_seconds,
        )
    ]


def assert_backfill_not_live(task: BackfillTask) -> None:
    """Fail-closed guard: a backfill task must never carry live authorization."""
    if task.live_authorization:
        raise AssertionError(
            f"backfill task for {task.source_id!r} is live_authorized — backfill must never "
            f"set live readiness (spec data-type taxonomy)"
        )
