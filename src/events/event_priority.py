# Created: 2026-06-11
# Last reused or audited: 2026-06-18
# Authority basis: live Day0 and forecast events share one production execution
#   lane. Single shared location for opportunity-event priority constants — no
#   magic numbers at call sites.
"""Opportunity-event priority constants and the scope-aware claim-tier authority.

THE CATEGORY THIS MODULE MAKES UNCONSTRUCTABLE
----------------------------------------------
A claim-ordering decision encoded as a magic integer at a call site, and a
"Day0 is always the freshest alpha" assumption baked statically into the queue
tier.

THE STRUCTURAL DECISION
-----------------------
Day0 is the freshest *tradeable* alpha only when Day0 is a tradeable lane
(production scope ``forecast_plus_day0``). The claim tier is therefore
scope-aware for tests/replay, while production keeps Day0 tradeable.

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
# higher integer here only wins among events in the SAME tier — it can never
# promote a non-tradeable event past a tradeable one (that is the tier's job).
# ---------------------------------------------------------------------------

# Tradeable FORECAST_SNAPSHOT_READY families that are COMPLETE + LIVE_ELIGIBLE:
# these are the direct order-candidate events and emit at the top sub-sort.
PRIORITY_TRADEABLE: Final[int] = 100

# A FORECAST_SNAPSHOT_READY family that is NOT yet COMPLETE/LIVE_ELIGIBLE — a
# real future-target candidate, just not window-complete this cycle.
PRIORITY_FORECAST_INCOMPLETE: Final[int] = 0

# DAY0_EXTREME_UPDATED emitted while day0 is a TRADEABLE lane
# (edli_live_scope='forecast_plus_day0'): realized observation, freshest alpha.
PRIORITY_DAY0_TRADEABLE: Final[int] = 60

# Reserved low priority for non-tradeable Day0 scopes in tests or historical
# replay only. Production live scope is forecast_plus_day0.
PRIORITY_DAY0_NON_TRADEABLE: Final[int] = 10


def day0_emit_priority(*, day0_is_tradeable: bool) -> int:
    """Priority to stamp on a DAY0_EXTREME_UPDATED event at emit time.

    ``day0_is_tradeable`` is derived from ``edli_live_scope``. Production live
    uses ``forecast_plus_day0`` so Day0 can submit through the same live lane.
    """
    return PRIORITY_DAY0_TRADEABLE if day0_is_tradeable else PRIORITY_DAY0_NON_TRADEABLE


def day0_is_tradeable_for_scope(edli_live_scope: str | None) -> bool:
    """True iff a DAY0_EXTREME_UPDATED event could lawfully reach a submit path
    under ``edli_live_scope``.

    Single source of truth for the scope→tradeability mapping shared by the
    emitter (priority stamp) and the reactor (claim-tier selection). Any scope
    other than the Day0-tradeable lane is treated as non-tradeable for Day0.
    """
    return str(edli_live_scope or "") == "forecast_plus_day0"


# Distinguishing prefix on the ``source`` of an escalation-originated re-decision
# (redecide-block fix 2026-06-16). The escalation cancel job emits a
# FORECAST_SNAPSHOT_READY for a JUST-CANCELLED, ARMED family using a source that
# starts with this prefix; the claim-tier CASE keys off it (plus the event_type)
# to rank ONLY those escalation re-decisions at Tier 0. Ordinary continuous
# EDLI_REDECISION_PENDING is now also Tier 0, but via its own event-type clause:
# the screen owns current edge/rest/held admission, while this prefix remains the
# exact identity of the separate just-cancelled escalation path.
ESCALATION_CROSS_SOURCE_PREFIX: Final[str] = "escalation_cross-"


def claim_tier_expr_sql(*, day0_is_tradeable: bool) -> str:
    """The scope-aware claim-tier CASE EXPRESSION (no sort direction).

    This is the single tier authority as a bare ``CASE ... END`` that evaluates
    to the tier integer for a row. It is usable both as a SELECT column (e.g. to
    PARTITION a per-city round-robin window by tier) and — via
    :func:`claim_tier_case_sql`, which appends ``ASC`` — as an ORDER BY key. One
    source, two consumers; the ``ASC`` form can never drift from the column form.

    Tiers (lower integer = claimed first):
      0  ESCALATION-ORIGIN FORECAST_SNAPSHOT_READY (source starts with
         ``escalation_cross-``) — a JUST-CANCELLED, ARMED escalation family whose
         next certified decision crosses as TAKER_ESCALATED_AFTER_REST. Routed
         through the FSR path (not the dormant EDLI_REDECISION_PENDING type) so
         the full dispatch chain processes it without hard-block gaps. This is a
         confirmed-armed, settlement-proven +EV cross, NOT non-tradeable telemetry, so it ranks
         ABOVE the entire per-city round-robin and fires on the next cycle instead
         of waiting ~2-3h for the 49-deep rotation (redecide-block fix
         2026-06-16). Evaluated FIRST and INDEPENDENT of ``day0_is_tradeable`` —
         it is a bounded, self-extinguishing set (one event per confirmed cancel),
         never a flood, so it cannot starve the round-robin.
      0  EDLI_REDECISION_PENDING — continuous redecision rows admitted by the
         screen after current positive-entry/held-position pruning. These are
         live money-at-risk or confirmed-positive-value rechecks, so they must
         not wait behind the ordinary FSR discovery round-robin.
      0  DAY0_EXTREME_UPDATED  — ONLY when ``day0_is_tradeable`` (realized obs is
         the freshest actionable alpha and must not sit behind forecast backlog).
      1  FORECAST_SNAPSHOT_READY that is COMPLETE + LIVE_ELIGIBLE — the direct
         tradeable order candidates.
      2  Other decision-trigger events (incl. non-tradeable DAY0_EXTREME_UPDATED
         when NOT ``day0_is_tradeable``) — still actionable/dead-letterable but
         must never starve a tradeable forecast family.
      3  Market-channel cache-hydration events — rejected immediately; demoted
         so they cannot starve all FSR.

    When ``day0_is_tradeable`` is False the DAY0_EXTREME_UPDATED Tier-0 clause is
    OMITTED, so day0 falls through to the ELSE (Tier 2) — strictly below the
    tradeable FSR Tier 1. This is the live-incident fix; the True branch is
    byte-identical to the historical authority. The escalation-origin Tier-0
    clause is ALWAYS present (independent of the day0 scope).

    NON-REDECISION FAIRNESS IS UNTOUCHED: the escalation clause matches ONLY a
    FORECAST_SNAPSHOT_READY whose source begins with ``escalation_cross-``. A
    regular FSR (source ``forecast_snapshot_ready_trigger`` or ``cycle-*``) does
    NOT match and stays Tier 1. Continuous EDLI_REDECISION_PENDING now shares
    Tier 0 because admission is owned by the screen's current edge/rest/held
    pruning; ordinary FSR still uses the 2026-06-11 per-city round-robin law.
    """
    escalation_tier0_clause = (
        "WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'\n"
        "                 AND e.source LIKE '" + ESCALATION_CROSS_SOURCE_PREFIX + "%'\n"
        "                THEN 0\n              "
    )
    redecision_tier0_clause = (
        "WHEN e.event_type = 'EDLI_REDECISION_PENDING'\n"
        "                THEN 0\n              "
    )
    day0_tier0_clause = (
        "WHEN e.event_type = 'DAY0_EXTREME_UPDATED'\n                THEN 0\n              "
        if day0_is_tradeable
        else ""
    )
    return (
        "CASE\n              "
        + escalation_tier0_clause
        + redecision_tier0_clause
        + day0_tier0_clause
        + "WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'\n"
        "                 AND json_extract(e.payload_json, '$.coverage_completeness_status') = 'COMPLETE'\n"
        "                 AND json_extract(e.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'\n"
        "                THEN 1\n"
        "                WHEN e.event_type IN ('BEST_BID_ASK_CHANGED', 'BOOK_SNAPSHOT', 'NEW_MARKET_DISCOVERED')\n"
        "                THEN 3\n"
        "                ELSE 2\n"
        "              END"
    )


def claim_tier_case_sql(*, day0_is_tradeable: bool) -> str:
    """The scope-aware claim-tier CASE expression for ``fetch_pending`` ORDER BY.

    ONE ordering authority. The tier CASE is the cross-tier rank; ``e.priority
    DESC`` (appended by the caller) is the within-tier sub-sort. Derived from
    :func:`claim_tier_expr_sql` by appending the ``ASC`` sort direction, so the
    ORDER BY form and the column form are the same expression by construction.
    """
    return claim_tier_expr_sql(day0_is_tradeable=day0_is_tradeable) + " ASC"
