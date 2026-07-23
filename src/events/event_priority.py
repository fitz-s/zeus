# Created: 2026-06-11
# Last reused or audited: 2026-06-18
# Authority basis: live Day0 and forecast events share one production execution
#   lane. Single shared location for opportunity-event priority constants — no
#   magic numbers at call sites.
"""Opportunity-event priority constants and the single-live claim-tier authority.

THE CATEGORY THIS MODULE MAKES UNCONSTRUCTABLE
----------------------------------------------
A claim-ordering decision encoded as a magic integer at a call site, and a
"Day0 is always the freshest alpha" assumption baked statically into the queue
tier.

THE STRUCTURAL DECISION
-----------------------
Day0 is live alpha. Every Day0 event uses the same priority and claim tier;
there is no dormant or non-tradeable Day0 lane.

The integer priority on each event (``opportunity_events.priority``) is a
SUB-SORT within a tier — it cannot reorder across tiers. Both surfaces (the
event's emitted ``priority`` and the queue's tier CASE) read from this one
module so the emission-priority half and the ordering half can never diverge.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Event-row priority constants (opportunity_events.priority).
#
# These are a SUB-SORT WITHIN a claim tier (see CLAIM_TIER_CASE_SQL below): the
# tier CASE dominates, then ``e.priority DESC`` breaks ties inside a tier. So a
# higher integer here only wins among events in the SAME tier.
# ---------------------------------------------------------------------------

# Tradeable FORECAST_SNAPSHOT_READY families that are COMPLETE + LIVE_ELIGIBLE:
# these are the direct order-candidate events and emit at the top sub-sort.
PRIORITY_TRADEABLE: Final[int] = 100

# A FORECAST_SNAPSHOT_READY family that is NOT yet COMPLETE/LIVE_ELIGIBLE — a
# real future-target candidate, just not window-complete this cycle.
PRIORITY_FORECAST_INCOMPLETE: Final[int] = 0

# DAY0_EXTREME_UPDATED: realized observation, freshest alpha.
PRIORITY_DAY0: Final[int] = 60


def claim_tier_expr_sql() -> str:
    """The claim-tier CASE EXPRESSION (no sort direction).

    This is the single tier authority as a bare ``CASE ... END`` that evaluates
    to the tier integer for a row. It is usable both as a SELECT column (e.g. to
    PARTITION a per-city round-robin window by tier) and — via
    :func:`claim_tier_case_sql`, which appends ``ASC`` — as an ORDER BY key. One
    source, two consumers; the ``ASC`` form can never drift from the column form.

    Tiers (lower integer = claimed first):
      0  EDLI_REDECISION_PENDING — continuous redecision rows admitted by the
         screen, by held-position monitoring, or by terminal-no-fill/rest-pull
         order management. These are live money-at-risk or confirmed-positive-value
         rechecks, so they must not wait behind the ordinary FSR discovery
         round-robin.
      0  DAY0_EXTREME_UPDATED — realized observation, freshest actionable alpha,
         must not sit behind forecast backlog.
      1  FORECAST_SNAPSHOT_READY that is COMPLETE + LIVE_ELIGIBLE — the direct
         tradeable order candidates.
      2  Other decision-trigger events — still actionable/dead-letterable but
         must never starve a tradeable forecast family.
      3  Market-channel cache-hydration events — rejected immediately; demoted
         so they cannot starve all FSR.

    NON-REDECISION FAIRNESS IS UNTOUCHED: ordinary FSR still uses the
    2026-06-11 per-city round-robin law. Order-management continuations use the
    standard EDLI_REDECISION_PENDING lane rather than a source-prefix FSR bypass.
    """
    redecision_tier0_clause = (
        "WHEN e.event_type = 'EDLI_REDECISION_PENDING'\n"
        "                THEN 0\n              "
    )
    return (
        "CASE\n              "
        + redecision_tier0_clause
        + "WHEN e.event_type = 'DAY0_EXTREME_UPDATED'\n                THEN 0\n              "
        + "WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'\n"
        "                 AND json_extract(e.payload_json, '$.coverage_completeness_status') = 'COMPLETE'\n"
        "                 AND json_extract(e.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'\n"
        "                THEN 1\n"
        "                WHEN e.event_type IN ('BEST_BID_ASK_CHANGED', 'BOOK_SNAPSHOT', 'NEW_MARKET_DISCOVERED')\n"
        "                THEN 3\n"
        "                ELSE 2\n"
        "              END"
    )


def claim_tier_case_sql() -> str:
    """The claim-tier CASE expression for ``fetch_pending`` ORDER BY.

    ONE ordering authority. The tier CASE is the cross-tier rank; ``e.priority
    DESC`` (appended by the caller) is the within-tier sub-sort. Derived from
    :func:`claim_tier_expr_sql` by appending the ``ASC`` sort direction, so the
    ORDER BY form and the column form are the same expression by construction.
    """
    return claim_tier_expr_sql() + " ASC"
