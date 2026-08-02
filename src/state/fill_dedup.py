# Created: 2026-07-06
# Last reused or audited: 2026-07-13
# Authority basis: money-path fill-aggregation correctness fix — venue_trade_facts
#   is an append-only WebSocket observation log; the SAME real fill appears as
#   MULTIPLE rows sharing trade_id (state progressing MATCHED->MINED->CONFIRMED,
#   local_sequence incrementing PER trade_id — src/state/venue_command_repo.py
#   _coerce_local_sequence, where_sql="trade_id = ?"). Correct aggregation dedups
#   to one row per (command_id, trade_id) taking the proof-strongest/latest
#   revision, THEN sums across distinct trade_ids.
# Authority basis (2026-07-13, docs/rebuild/local_ledger_excision_2026-07-12.md
#   LX-T4): consult adjudication requires venue_trade_facts' economic identity
#   (provider trade IDs x child fills x tx_hash/log identity x order/command
#   IDs) to have ONE home so a future derive-on-read reducer consumes
#   exactly-once economics without re-deriving the tx-hash-aggregate-exclusion
#   rule ad hoc. ``economic_trade_fact_cte`` was moved here VERBATIM from
#   ``src.execution.exchange_reconcile`` (module-private ``_economic_trade_fact_cte``)
#   — exchange_reconcile now imports both CTE builders under its existing
#   private names (zero behavior change, proved by its own test suite staying
#   green). ``alias_edge_cte`` is new: it exposes the trade_id <-> tx_hash <->
#   child-id alias graph explicitly (queryable, not just a filter).
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

A SECOND identity problem sits one layer up from lifecycle-revision dedup:
the SAME economic fill can appear as a tx-hash-keyed aggregate row (trade_id
== tx_hash) AND as one or more exact child trade rows sharing that tx_hash.
``economic_trade_fact_cte`` excludes the aggregate once an exact child
exists; ``alias_edge_cte`` exposes the underlying trade_id <-> tx_hash <->
child-id alias graph explicitly so a future reducer can walk it instead of
re-deriving the exclusion rule ad hoc.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import sqlite3


class PartialExitEconomicDebtError(RuntimeError):
    """Fail closed when one position's partial-exit economics lack proof.

    INV-47 SCOPE: exactly one ``position_id``; no unrelated entry or exit is
    blocked. DRAIN: a later canonical trade fact plus an authoritative unit
    basis lets ``repair_legacy_partial_exit_slices`` append the missing slice.
    RESET: every exact canonical fill identity has one economics event, so the
    fold succeeds without retaining a latch.
    """


@dataclass(frozen=True)
class EconomicExitFill:
    """One exactly-once canonical EXIT economic fill atom."""

    identity: str
    command_id: str
    venue_order_id: str
    trade_id: str
    quantity: Decimal
    unit_price: Decimal
    notional: Decimal


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _position_events_available(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'position_events'"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


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
                                   -- Stable execution order for the economic fold: a
                                   -- later re-observation (e.g. a REST re-confirmation)
                                   -- re-stamps observed_at and may carry a NULL
                                   -- venue_timestamp, so folding by the canonical row's
                                   -- observed_at can push an entry AFTER its own exits and
                                   -- fabricate an oversold error. Prefer the earliest venue
                                   -- (match) timestamp across the trade's revisions;
                                   -- when NO revision carries one, fall back to the
                                   -- earliest observed_at (the ORIGINAL observation, not
                                   -- the re-stamp). MIN() ignores NULLs. Additive column;
                                   -- canonical selection (proof_rank/local_sequence) is
                                   -- unchanged, so exchange_reconcile is unaffected.
                                   COALESCE(
                                       MIN(fact.venue_timestamp) OVER (
                                           PARTITION BY fact.command_id, fact.trade_id
                                       ),
                                       MIN(fact.observed_at) OVER (
                                           PARTITION BY fact.command_id, fact.trade_id
                                       )
                                   ) AS execution_ts,
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


def economic_trade_fact_cte(
    *,
    canonical_cte_name: str = "canonical_trade_fact",
    cte_name: str = "economic_trade_fact",
) -> str:
    """Exclude every derived alias once its source economic fact exists.

    Tx-hash aggregate aliases are excluded when an exact child exists.  EDLI
    aliases are excluded when ``raw_fill_payload.source_trade_fact_id`` binds
    them to a positive source fact for the same command and venue order.
    """

    return f"""
        {cte_name} AS (
            SELECT fact.*
              FROM {canonical_cte_name} fact
             WHERE NOT (
                    TRIM(COALESCE(fact.tx_hash, '')) != ''
                AND LOWER(TRIM(COALESCE(fact.trade_id, '')))
                    = LOWER(TRIM(fact.tx_hash))
                AND EXISTS (
                        SELECT 1
                          FROM {canonical_cte_name} exact
                         WHERE exact.command_id = fact.command_id
                           AND LOWER(TRIM(COALESCE(exact.tx_hash, '')))
                               = LOWER(TRIM(fact.tx_hash))
                           AND LOWER(TRIM(COALESCE(exact.trade_id, '')))
                               != LOWER(TRIM(COALESCE(fact.trade_id, '')))
                           AND UPPER(COALESCE(exact.state, ''))
                               IN ('MATCHED', 'MINED', 'CONFIRMED')
                           AND CAST(COALESCE(exact.filled_size, '0') AS REAL) > 0
                    )
                )
               AND NOT EXISTS (
                       SELECT 1
                         FROM venue_trade_facts source_fact
                        WHERE source_fact.trade_fact_id = CASE
                                  WHEN json_valid(fact.raw_payload_json)
                                  THEN CAST(json_extract(
                                      fact.raw_payload_json,
                                      '$.raw_fill_payload.source_trade_fact_id'
                                  ) AS INTEGER)
                              END
                          AND source_fact.command_id = fact.command_id
                          AND source_fact.venue_order_id = fact.venue_order_id
                          AND UPPER(COALESCE(source_fact.state, ''))
                              IN ('MATCHED', 'MINED', 'CONFIRMED')
                          AND CAST(COALESCE(source_fact.filled_size, '0') AS REAL) > 0
                    )
        )
    """


def alias_edge_cte(
    *,
    canonical_cte_name: str = "canonical_trade_fact",
    cte_name: str = "trade_fact_alias_edge",
) -> str:
    """Explicit trade_id <-> tx_hash <-> child-trade alias graph.

    One row per canonical trade fact (see :func:`canonical_trade_fact_cte`),
    tagged with an ``alias_role`` so a reducer can walk the graph instead of
    re-deriving the tx-hash-aggregate-exclusion rule ad hoc (the rule
    :func:`economic_trade_fact_cte` applies as a filter). Roles:

    - ``ALIASED_AGGREGATE``: ``trade_id == tx_hash`` (a tx-hash rollup) AND a
      distinct exact child trade_id sharing that tx_hash exists for the same
      command — this row is a duplicate VIEW of the same economic fill and
      must NOT be summed (excluded by ``economic_trade_fact_cte``).
    - ``STANDALONE``: ``trade_id == tx_hash`` with no sibling child — the
      aggregate IS the only observation of this fill, so it stays economic.
    - ``CHILD_EXACT``: the trade_id is distinct from its tx_hash (or the row
      has no tx_hash at all) — an economically-authoritative child row.

    Every row from ``canonical_cte_name`` appears exactly once here (this is
    a tag, not a filter) — ``economic_trade_fact_cte`` is equivalent to
    ``SELECT * FROM {cte_name} WHERE alias_role != 'ALIASED_AGGREGATE'``.
    """

    return f"""
        {cte_name} AS (
            SELECT
                fact.*,
                CASE
                    WHEN TRIM(COALESCE(fact.tx_hash, '')) != ''
                     AND LOWER(TRIM(COALESCE(fact.trade_id, '')))
                         = LOWER(TRIM(fact.tx_hash))
                     AND EXISTS (
                            SELECT 1
                              FROM {canonical_cte_name} sibling
                             WHERE sibling.command_id = fact.command_id
                               AND LOWER(TRIM(COALESCE(sibling.tx_hash, '')))
                                   = LOWER(TRIM(fact.tx_hash))
                               AND LOWER(TRIM(COALESCE(sibling.trade_id, '')))
                                   != LOWER(TRIM(COALESCE(fact.trade_id, '')))
                               AND UPPER(COALESCE(sibling.state, ''))
                                   IN ('MATCHED', 'MINED', 'CONFIRMED')
                               AND CAST(COALESCE(sibling.filled_size, '0') AS REAL) > 0
                        )
                    THEN 'ALIASED_AGGREGATE'
                    WHEN TRIM(COALESCE(fact.tx_hash, '')) != ''
                     AND LOWER(TRIM(COALESCE(fact.trade_id, '')))
                         = LOWER(TRIM(fact.tx_hash))
                    THEN 'STANDALONE'
                    ELSE 'CHILD_EXACT'
                END AS alias_role
              FROM {canonical_cte_name} fact
        )
    """


def economic_trade_facts_for_command(
    conn,
    command_id: str,
) -> list[dict]:
    """Return the exactly-once economic trade facts for one command.

    Queryable entry point for the alias graph (packaged for a future
    derive-on-read reducer): dedups lifecycle revisions
    (``canonical_trade_fact_cte``) then excludes tx-hash-aggregate aliases
    once an exact child exists (``economic_trade_fact_cte``). Every returned
    row contributes to that command's economics exactly once, fees included
    (``fee_paid_micro`` is a plain preserved column, not touched by either CTE).
    """

    sql = f"""
        WITH {canonical_trade_fact_cte(source_clause_sql="WHERE fact.command_id = ?")},
             {economic_trade_fact_cte()}
        SELECT * FROM economic_trade_fact ORDER BY trade_id
    """
    return [dict(row) for row in conn.execute(sql, (command_id,)).fetchall()]


def alias_edges_for_command(
    conn,
    command_id: str,
) -> list[dict]:
    """Return the full alias graph (all roles) for one command, for audit/tests."""

    sql = f"""
        WITH {canonical_trade_fact_cte(source_clause_sql="WHERE fact.command_id = ?")},
             {alias_edge_cte()}
        SELECT command_id, trade_id, tx_hash, state, filled_size, fill_price,
               fee_paid_micro, alias_role
          FROM trade_fact_alias_edge
         ORDER BY trade_id
    """
    return [dict(row) for row in conn.execute(sql, (command_id,)).fetchall()]


def economic_exit_fills_for_position(
    conn: sqlite3.Connection,
    position_id: str,
    *,
    venue_order_id: str = "",
) -> list[EconomicExitFill]:
    """Return exact canonical EXIT fills once, including every alias rule.

    This is the one economic-fill intake for partial exit booking, repair, and
    settlement.  It intentionally composes ``canonical_trade_fact_cte`` and
    ``economic_trade_fact_cte`` rather than reimplementing their MATCHED /
    CONFIRMED, tx aggregate, or EDLI source-fact alias rules.
    """

    if not position_id:
        return []
    order_clause = ""
    params: list[object] = [position_id]
    if venue_order_id:
        order_clause = "AND cmd.venue_order_id = ?"
        params.append(venue_order_id)
    try:
        rows = conn.execute(
            f"""
            WITH {canonical_trade_fact_cte()},
                 {economic_trade_fact_cte()}
            SELECT fact.command_id, fact.trade_id, fact.venue_order_id,
                   fact.filled_size, fact.fill_price
              FROM economic_trade_fact fact
              JOIN venue_commands cmd ON cmd.command_id = fact.command_id
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND UPPER(COALESCE(fact.state, '')) IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
               {order_clause}
             ORDER BY fact.execution_ts, fact.command_id, fact.trade_id
            """,
            tuple(params),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PartialExitEconomicDebtError(
            f"partial EXIT canonical fill lookup failed: position_id={position_id}: {exc}"
        ) from exc

    fills: list[EconomicExitFill] = []
    for row in rows:
        quantity = _decimal(row["filled_size"])
        unit_price = _decimal(row["fill_price"])
        if quantity is None or unit_price is None or quantity <= 0 or unit_price <= 0:
            raise PartialExitEconomicDebtError(
                f"partial EXIT canonical fill has invalid economics: position_id={position_id}"
            )
        command_id = str(row["command_id"] or "")
        trade_id = str(row["trade_id"] or "")
        if not command_id or not trade_id:
            raise PartialExitEconomicDebtError(
                f"partial EXIT canonical fill identity missing: position_id={position_id}"
            )
        fills.append(
            EconomicExitFill(
                identity=f"economic-fill:v1:{command_id}:{trade_id.lower()}",
                command_id=command_id,
                venue_order_id=str(row["venue_order_id"] or ""),
                trade_id=trade_id,
                quantity=quantity,
                unit_price=unit_price,
                notional=quantity * unit_price,
            )
        )
    return fills


def recorded_partial_exit_fill_cursors(
    conn: sqlite3.Connection,
    position_id: str,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Read already-booked canonical-fill cursors keyed by stable identity.

    A source trade's later MATCHED→CONFIRMED revision may increase a cumulative
    fill.  The cursor stores the prior cumulative quantity and notional so only
    the newly proven exact slice is booked on replay.
    """

    if not position_id or not _position_events_available(conn):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT event_id, caused_by, payload_json
              FROM position_events
             WHERE position_id = ?
               AND caused_by IN ('partial_exit_fill', 'partial_exit_economics_repair')
             ORDER BY sequence_no, event_id
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PartialExitEconomicDebtError(
            f"partial EXIT event cursor lookup failed: position_id={position_id}: {exc}"
        ) from exc

    cursors: dict[str, tuple[Decimal, Decimal]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PartialExitEconomicDebtError(
                f"partial EXIT event payload malformed: position_id={position_id} event_id={row['event_id']}"
            ) from exc
        identity = str(payload.get("economic_fill_identity") or "").strip()
        if not identity:
            continue
        quantity = _decimal(payload.get("economic_fill_cumulative_shares"))
        notional = _decimal(payload.get("economic_fill_cumulative_notional_usd"))
        if quantity is None or notional is None or quantity <= 0 or notional <= 0:
            raise PartialExitEconomicDebtError(
                f"partial EXIT cursor lacks cumulative economics: position_id={position_id} identity={identity}"
            )
        prior = cursors.get(identity)
        if prior is not None and prior != (quantity, notional):
            raise PartialExitEconomicDebtError(
                f"partial EXIT stable identity conflicts: position_id={position_id} identity={identity}"
            )
        cursors[identity] = (quantity, notional)
    return cursors


def partial_exit_realized_pnl_fold(
    conn: sqlite3.Connection,
    position_id: str,
) -> Decimal:
    """Fold persisted, stable-identity partial EXIT deltas exactly once.

    Legacy/minimal connections without ``position_events`` keep their historic
    settlement behavior: no partial contribution instead of a new runtime
    failure.  A present partial EXIT event without the new identity/economics
    envelope is typed debt and must be repaired from canonical venue facts.
    """

    if not position_id or not _position_events_available(conn):
        return Decimal("0")
    try:
        rows = conn.execute(
            """
            SELECT event_id, caused_by, payload_json
              FROM position_events
             WHERE position_id = ?
               AND caused_by IN ('partial_exit_fill', 'partial_exit_economics_repair')
             ORDER BY sequence_no, event_id
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PartialExitEconomicDebtError(
            f"partial EXIT fold lookup failed: position_id={position_id}: {exc}"
        ) from exc

    parsed: list[tuple[object, dict]] = []
    has_repair = False
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PartialExitEconomicDebtError(
                f"partial EXIT event payload malformed: position_id={position_id} event_id={row['event_id']}"
            ) from exc
        has_repair = has_repair or str(row["caused_by"] or "") == (
            "partial_exit_economics_repair"
        )
        parsed.append((row, payload))

    total = Decimal("0")
    seen: set[str] = set()
    for row, payload in parsed:
        identity = str(payload.get("economic_fill_identity") or "").strip()
        if not identity:
            if has_repair:
                continue
            raise PartialExitEconomicDebtError(
                f"partial EXIT economics repair required: position_id={position_id} event_id={row['event_id']}"
            )
        if identity in seen:
            raise PartialExitEconomicDebtError(
                f"partial EXIT duplicate stable identity: position_id={position_id} identity={identity}"
            )
        delta = _decimal(payload.get("realized_pnl_delta_usd"))
        quantity = _decimal(payload.get("filled_shares"))
        notional = _decimal(payload.get("filled_notional_usd"))
        cost = _decimal(payload.get("allocated_cost_basis_usd"))
        if (
            delta is None
            or quantity is None
            or notional is None
            or cost is None
            or quantity <= 0
            or notional <= 0
            or cost < 0
            or delta != notional - cost
        ):
            raise PartialExitEconomicDebtError(
                f"partial EXIT event economics invalid: position_id={position_id} identity={identity}"
            )
        seen.add(identity)
        total += delta
    return total


def legacy_partial_exit_repair_fills(
    conn: sqlite3.Connection,
    position_id: str,
) -> list[EconomicExitFill]:
    """Prove the exact canonical fills needed to repair old partial events.

    Old payloads recorded a quantity/price observation but not the stable
    economic identity.  Repair is permitted only when those old per-order
    quantities exactly equal the complete canonical venue-fact fold.  Anything
    else is typed debt; in particular, this never silently substitutes zero.
    """

    if not position_id or not _position_events_available(conn):
        return []
    try:
        rows = conn.execute(
            """
            SELECT event_id, order_id, payload_json
              FROM position_events
             WHERE position_id = ?
               AND caused_by = 'partial_exit_fill'
             ORDER BY sequence_no, event_id
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PartialExitEconomicDebtError(
            f"partial EXIT repair lookup failed: position_id={position_id}: {exc}"
        ) from exc
    legacy_by_order: dict[str, Decimal] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PartialExitEconomicDebtError(
                f"partial EXIT repair payload malformed: position_id={position_id} event_id={row['event_id']}"
            ) from exc
        if payload.get("economic_fill_identity"):
            continue
        order_id = str(row["order_id"] or payload.get("order_id") or "").strip()
        quantity = _decimal(payload.get("filled_shares"))
        if not order_id or quantity is None or quantity <= 0:
            raise PartialExitEconomicDebtError(
                f"partial EXIT repair identity/quantity missing: position_id={position_id} event_id={row['event_id']}"
            )
        legacy_by_order[order_id] = legacy_by_order.get(order_id, Decimal("0")) + quantity
    if not legacy_by_order:
        return []

    exact: list[EconomicExitFill] = []
    for order_id, legacy_quantity in legacy_by_order.items():
        fills = economic_exit_fills_for_position(
            conn, position_id, venue_order_id=order_id
        )
        canonical_quantity = sum((fill.quantity for fill in fills), Decimal("0"))
        if not fills or canonical_quantity != legacy_quantity:
            raise PartialExitEconomicDebtError(
                "partial EXIT repair cannot prove exact fill identity/quantity: "
                f"position_id={position_id} order_id={order_id} "
                f"legacy={legacy_quantity} canonical={canonical_quantity}"
            )
        exact.extend(fills)
    return exact
