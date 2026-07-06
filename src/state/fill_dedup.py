# Created: 2026-07-06
# Last reused or audited: 2026-07-06
# Authority basis: money-path fill-aggregation correctness fix — venue_trade_facts
#   is an append-only WebSocket observation log; the SAME real fill appears as
#   MULTIPLE rows sharing trade_id (state progressing MATCHED->MINED->CONFIRMED,
#   local_sequence incrementing PER trade_id — src/state/venue_command_repo.py
#   _coerce_local_sequence, where_sql="trade_id = ?"). Correct aggregation dedups
#   to one row per (command_id, trade_id) taking the proof-strongest/latest
#   revision, THEN sums across distinct trade_ids.
"""Shared canonical trade-fact dedup CTE for `venue_trade_facts` aggregation.

A bare ``SUM(filled_size)`` over ``venue_trade_facts`` over-counts by 1x-4x
because it sums every lifecycle revision of the same fill. A dedup that picks
the row with the largest ``local_sequence`` per command_id ALONE (i.e. not
also keyed by trade_id) is a *different* bug: it silently drops a command's
other ``trade_id``s, because ``local_sequence`` is scoped per ``trade_id``,
not per ``command_id`` — the command-wide max local_sequence belongs to only
ONE trade_id.

The correct pattern is this module's :func:`canonical_trade_fact_cte`: rank
by proof strength (CONFIRMED > MINED > MATCHED > any positive fill) then by
``local_sequence`` recency, ``PARTITION BY (command_id, trade_id)`` — one
canonical row per distinct trade_id, safe to ``SUM`` across a command.

This is the same ranking already used by
``src.execution.exchange_reconcile._canonical_trade_fact_cte`` and
``src.execution.command_recovery._canonical_trade_fact_cte`` (and inlined
again in ``src.state.venue_command_repo``). Those three existing copies are
left as-is (working code) — this module exists only so *new* call sites
across package boundaries (src/state, src/riskguard, scripts/) can share one
importable definition instead of growing a fifth copy.
"""

from __future__ import annotations


def canonical_trade_fact_cte(
    cte_name: str = "canonical_trade_fact",
    *,
    source_clause_sql: str = "",
) -> str:
    """Rank trade facts by proof strength before local_sequence recency.

    Returns a SQL CTE body (without the leading ``WITH``) that yields one row
    per ``(command_id, trade_id)``: the CONFIRMED/MINED/MATCHED/any-positive-
    fill revision with the highest ``local_sequence`` for that pair.

    ``source_clause_sql``, if given, is appended immediately after
    ``FROM venue_trade_facts fact`` inside the ranking subquery — typically a
    ``JOIN ... WHERE ...`` clause (referencing the ``fact`` alias) that scopes
    which trade facts are ranked. Callers may also apply filters afterward
    against the resulting CTE's columns (all original ``venue_trade_facts``
    columns are preserved via ``fact.*``, plus ``proof_rank`` /
    ``canonical_rank``).
    """

    return f"""
        {cte_name} AS (
            SELECT ranked.*
              FROM (
                    SELECT scored.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY command_id, trade_id
                               ORDER BY proof_rank DESC, local_sequence DESC
                           ) AS canonical_rank
                      FROM (
                            SELECT fact.*,
                                   CASE
                                       WHEN UPPER(COALESCE(fact.state, '')) = 'CONFIRMED'
                                            AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                       THEN 500
                                       WHEN UPPER(COALESCE(fact.state, '')) = 'MINED'
                                            AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                       THEN 450
                                       WHEN UPPER(COALESCE(fact.state, '')) = 'MATCHED'
                                            AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                       THEN 400
                                       WHEN CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                       THEN 300
                                       ELSE 100
                                   END AS proof_rank
                              FROM venue_trade_facts fact
                              {source_clause_sql}
                           ) scored
                   ) ranked
             WHERE ranked.canonical_rank = 1
        )
    """
