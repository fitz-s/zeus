# Created: 2026-07-06
# Last reused or audited: 2026-08-11
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

from collections.abc import Mapping
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


ECONOMIC_NOTIONAL_STORAGE_TOLERANCE_USD = Decimal("0.000000001")


def _venue_trade_facts_table(source_schema: str | None) -> str:
    """Resolve the only supported venue-fact schema names to SQL identifiers."""

    tables = {
        None: "venue_trade_facts",
        "main": "main.venue_trade_facts",
        "trades": "trades.venue_trade_facts",
    }
    if source_schema not in tables:
        raise ValueError(f"unsupported source_schema: {source_schema!r}")
    return tables[source_schema]


def economic_notional_storage_equal(left: object, right: object) -> bool:
    """Treat only sub-nanodollar decimal serialization drift as equal."""

    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    return bool(
        left_decimal is not None
        and right_decimal is not None
        and abs(left_decimal - right_decimal)
        <= ECONOMIC_NOTIONAL_STORAGE_TOLERANCE_USD
    )


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


@dataclass(frozen=True)
class LegacyPartialExitRepair:
    """One exact canonical fill proving one legacy event's economics."""

    legacy_event_id: str
    fill: EconomicExitFill


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def canonical_decimal_text(value: object) -> str:
    """Serialize one finite economic Decimal without passing through float."""

    result = _decimal(value)
    if result is None:
        raise PartialExitEconomicDebtError(
            f"partial EXIT economic value is not a finite decimal: {value!r}"
        )
    return format(result.normalize(), "f")


def _taker_sell_maker_leg_unit_price(
    raw_payload_json: object,
    *,
    venue_order_id: str,
    selected_token_id: str,
    quantity: Decimal,
) -> Decimal | None:
    """Derive exact taker-SELL VWAP when the top-level price is one leg.

    Polymarket user-channel taker trades can report the lowest matched leg in
    ``price`` while ``maker_orders`` carries every exact counterparty leg.  The
    latter must reproduce the full selected-token quantity before it can
    replace the top-level value.
    """

    try:
        raw = json.loads(str(raw_payload_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(raw, dict)
        or str(raw.get("trader_side") or "").upper() != "TAKER"
        or str(raw.get("side") or "").upper() != "SELL"
        or str(raw.get("taker_order_id") or "").lower()
        != str(venue_order_id or "").lower()
        or str(raw.get("asset_id") or "") != str(selected_token_id or "")
    ):
        return None
    legs = raw.get("maker_orders")
    if not isinstance(legs, list) or not legs:
        return None
    total_quantity = Decimal("0")
    total_notional = Decimal("0")
    for leg in legs:
        if not isinstance(leg, Mapping):
            return None
        leg_quantity = _decimal(leg.get("matched_amount", leg.get("matchedAmount")))
        leg_price = _decimal(leg.get("price"))
        leg_token_id = str(leg.get("asset_id") or "")
        leg_side = str(leg.get("side") or "").upper()
        if (
            leg_quantity is None
            or leg_price is None
            or leg_quantity <= 0
            or leg_price <= 0
            or leg_price >= 1
            or not leg_token_id
            or (leg_token_id == selected_token_id and leg_side != "BUY")
            or (leg_token_id != selected_token_id and leg_side != "SELL")
        ):
            return None
        selected_price = (
            leg_price if leg_token_id == selected_token_id else Decimal("1") - leg_price
        )
        if selected_price <= 0 or selected_price >= 1:
            return None
        total_quantity += leg_quantity
        total_notional += leg_quantity * selected_price
    if (
        total_quantity <= 0
        or total_notional <= 0
        or abs(total_quantity - quantity) > Decimal("0.000000001")
    ):
        return None
    return total_notional / total_quantity


def partial_exit_events_available(conn: sqlite3.Connection) -> bool:
    """Return whether this connection carries the canonical partial-exit journal."""

    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'position_events'"
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def canonical_trade_fact_cte(
    cte_name: str = "canonical_trade_fact",
    *,
    source_clause_sql: str = "",
    source_schema: str | None = None,
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

    source_table = _venue_trade_facts_table(source_schema)
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
                              FROM {source_table} fact
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
    source_schema: str | None = None,
    source_clause_sql: str = "",
) -> str:
    """Exclude every derived alias once its source economic fact exists.

    Tx-hash aggregate aliases are excluded when an exact child exists.  EDLI
    aliases are excluded when ``raw_fill_payload.source_trade_fact_id`` binds
    them to a positive source fact for the same command and venue order.
    ``source_clause_sql`` is a trusted AND predicate on ``source_fact``; as-of
    readers must constrain these alias sources as well as canonical revisions.
    """

    source_table = _venue_trade_facts_table(source_schema)
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
                         FROM {source_table} source_fact
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
                          {source_clause_sql}
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
    scope_params: list[object] = [position_id]
    if venue_order_id:
        order_clause = "AND cmd.venue_order_id = ?"
        scope_params.append(venue_order_id)
    try:
        # Resolve the narrow command identity before ranking trade-fact revisions.
        # The canonical window must never rank unrelated history: the alias
        # exclusion CTE evaluates the canonical CTE more than once, so an outer
        # position/order predicate would still repeat a full venue_trade_facts
        # scan for every lookup.
        command_rows = conn.execute(
            f"""
            SELECT command_id
              FROM venue_commands cmd
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND COALESCE(cmd.venue_order_id, '') <> ''
               {order_clause}
            ORDER BY cmd.command_id
            """,
            tuple(scope_params),
        ).fetchall()
        command_ids = [str(row[0] or "") for row in command_rows if str(row[0] or "")]
        if not command_ids:
            return []
        command_placeholders = ", ".join("?" for _ in command_ids)
        source_clause_sql = f"WHERE fact.command_id IN ({command_placeholders})"
        params: list[object] = [*command_ids, position_id]
        if venue_order_id:
            params.append(venue_order_id)
        rows = conn.execute(
            f"""
            WITH {canonical_trade_fact_cte(source_clause_sql=source_clause_sql)},
                 {economic_trade_fact_cte()}
            SELECT fact.command_id, fact.trade_id, fact.venue_order_id,
                   fact.filled_size, fact.fill_price, fact.raw_payload_json,
                   cmd.token_id
              FROM economic_trade_fact fact
              JOIN venue_commands cmd ON cmd.command_id = fact.command_id
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND LOWER(COALESCE(fact.venue_order_id, '')) =
                   LOWER(COALESCE(cmd.venue_order_id, ''))
               AND COALESCE(cmd.venue_order_id, '') <> ''
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
        canonical_order_id = str(row["venue_order_id"] or "")
        trade_id = str(row["trade_id"] or "")
        if not command_id or not canonical_order_id or not trade_id:
            raise PartialExitEconomicDebtError(
                f"partial EXIT canonical fill identity missing: position_id={position_id}"
            )
        maker_leg_unit_price = _taker_sell_maker_leg_unit_price(
            row["raw_payload_json"],
            venue_order_id=canonical_order_id,
            selected_token_id=str(row["token_id"] or ""),
            quantity=quantity,
        )
        if maker_leg_unit_price is not None:
            unit_price = maker_leg_unit_price
        fills.append(
            EconomicExitFill(
                identity=(
                    f"economic-fill:v2:{command_id}:"
                    f"{canonical_order_id.lower()}:{trade_id.lower()}"
                ),
                command_id=command_id,
                venue_order_id=canonical_order_id,
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

    if not position_id or not partial_exit_events_available(conn):
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
        if prior is not None:
            prior_quantity, prior_notional = prior
            delta_quantity = _decimal(payload.get("filled_shares"))
            delta_notional = _decimal(payload.get("filled_notional_usd"))
            correction_only = payload.get("economic_correction_only") is True
            valid_correction = (
                correction_only
                and quantity == prior_quantity
                and notional != prior_notional
                and delta_quantity == Decimal("0")
                and delta_notional == notional - prior_notional
            )
            valid_growth = (
                not correction_only
                and quantity > prior_quantity
                and notional > prior_notional
                and delta_quantity == quantity - prior_quantity
                and delta_notional == notional - prior_notional
            )
            if not (valid_correction or valid_growth):
                raise PartialExitEconomicDebtError(
                    "partial EXIT stable identity did not advance by its exact "
                    f"cumulative delta: position_id={position_id} identity={identity}"
                )
        cursors[identity] = (quantity, notional)
    return cursors


def partial_exit_realized_pnl_fold(
    conn: sqlite3.Connection,
    position_id: str,
    *,
    allow_unrepaired_legacy: bool = False,
) -> Decimal:
    """Fold persisted, stable-identity partial EXIT deltas exactly once.

    Legacy/minimal connections without ``position_events`` keep their historic
    settlement behavior: no partial contribution instead of a new runtime
    failure.  A present partial EXIT event without the new identity/economics
    envelope is typed debt and must be repaired from canonical venue facts.
    """

    if not position_id or not partial_exit_events_available(conn):
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
    repaired_legacy_event_ids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PartialExitEconomicDebtError(
                f"partial EXIT event payload malformed: position_id={position_id} event_id={row['event_id']}"
            ) from exc
        repaired_event_id = str(
            payload.get("repaired_legacy_event_id") or ""
        ).strip()
        if repaired_event_id:
            repaired_legacy_event_ids.add(repaired_event_id)
        parsed.append((row, payload))

    legacy_event_ids = {
        str(row["event_id"])
        for row, payload in parsed
        if str(row["caused_by"] or "") == "partial_exit_fill"
        and not str(payload.get("economic_fill_identity") or "").strip()
    }
    unknown_coverage = repaired_legacy_event_ids - legacy_event_ids
    if unknown_coverage:
        raise PartialExitEconomicDebtError(
            "partial EXIT repair references unknown legacy events: "
            f"position_id={position_id} event_ids={sorted(unknown_coverage)}"
        )

    total = Decimal("0")
    cursors: dict[str, tuple[Decimal, Decimal]] = {}
    for row, payload in parsed:
        identity = str(payload.get("economic_fill_identity") or "").strip()
        if not identity:
            if allow_unrepaired_legacy or str(row["event_id"]) in repaired_legacy_event_ids:
                continue
            raise PartialExitEconomicDebtError(
                f"partial EXIT economics repair required: position_id={position_id} event_id={row['event_id']}"
            )
        delta = _decimal(payload.get("realized_pnl_delta_usd"))
        quantity = _decimal(payload.get("filled_shares"))
        notional = _decimal(payload.get("filled_notional_usd"))
        cost = _decimal(payload.get("allocated_cost_basis_usd"))
        cumulative_quantity = _decimal(
            payload.get("economic_fill_cumulative_shares", quantity)
        )
        cumulative_notional = _decimal(
            payload.get("economic_fill_cumulative_notional_usd", notional)
        )
        correction_only = payload.get("economic_correction_only") is True
        values_present = all(
            value is not None
            for value in (
                delta,
                quantity,
                notional,
                cost,
                cumulative_quantity,
                cumulative_notional,
            )
        )
        valid_atom = False
        if values_present:
            if correction_only:
                valid_atom = (
                    quantity == 0
                    and notional != 0
                    and cost == 0
                    and delta == notional
                )
            else:
                valid_atom = (
                    quantity > 0
                    and notional > 0
                    and cost >= 0
                    and cumulative_quantity >= quantity
                    and cumulative_notional >= notional
                    and delta == notional - cost
                )
        if (
            not values_present
            or cumulative_quantity <= 0
            or cumulative_notional <= 0
            or not valid_atom
        ):
            raise PartialExitEconomicDebtError(
                f"partial EXIT event economics invalid: position_id={position_id} identity={identity}"
            )
        prior = cursors.get(identity)
        if prior is not None:
            prior_quantity, prior_notional = prior
            valid_correction = (
                correction_only
                and cumulative_quantity == prior_quantity
                and cumulative_notional != prior_notional
                and quantity == Decimal("0")
                and notional == cumulative_notional - prior_notional
            )
            valid_growth = (
                not correction_only
                and cumulative_quantity > prior_quantity
                and cumulative_notional > prior_notional
                and quantity == cumulative_quantity - prior_quantity
                and notional == cumulative_notional - prior_notional
            )
            if not (valid_correction or valid_growth):
                raise PartialExitEconomicDebtError(
                    "partial EXIT stable identity did not advance by its exact "
                    f"cumulative delta: position_id={position_id} identity={identity}"
                )
        elif correction_only:
            raise PartialExitEconomicDebtError(
                "partial EXIT economics correction lacks a prior stable identity: "
                f"position_id={position_id} identity={identity}"
            )
        cursors[identity] = (cumulative_quantity, cumulative_notional)
        total += delta
    return total


def legacy_partial_exit_repair_fills(
    conn: sqlite3.Connection,
    position_id: str,
) -> list[LegacyPartialExitRepair]:
    """Prove the exact canonical fills needed to repair old partial events.

    Old payloads recorded a quantity/price observation but not the stable
    economic identity.  Repair is permitted only when those old per-order
    quantities exactly equal the complete canonical venue-fact fold.  Anything
    else is typed debt; in particular, this never silently substitutes zero.
    """

    if not position_id or not partial_exit_events_available(conn):
        return []
    try:
        rows = conn.execute(
            """
            SELECT event_id, order_id, caused_by, payload_json
              FROM position_events
             WHERE position_id = ?
               AND caused_by IN ('partial_exit_fill', 'partial_exit_economics_repair')
             ORDER BY sequence_no, event_id
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PartialExitEconomicDebtError(
            f"partial EXIT repair lookup failed: position_id={position_id}: {exc}"
        ) from exc
    covered_legacy_event_ids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        repaired_event_id = str(
            payload.get("repaired_legacy_event_id") or ""
        ).strip()
        if repaired_event_id:
            covered_legacy_event_ids.add(repaired_event_id)

    legacy_by_order: dict[str, list[tuple[str, Decimal]]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PartialExitEconomicDebtError(
                f"partial EXIT repair payload malformed: position_id={position_id} event_id={row['event_id']}"
            ) from exc
        if payload.get("economic_fill_identity"):
            continue
        event_id = str(row["event_id"] or "").strip()
        if event_id in covered_legacy_event_ids:
            continue
        order_id = str(row["order_id"] or payload.get("order_id") or "").strip()
        quantity = _decimal(payload.get("filled_shares"))
        if not event_id or not order_id or quantity is None or quantity <= 0:
            raise PartialExitEconomicDebtError(
                f"partial EXIT repair identity/quantity missing: position_id={position_id} event_id={row['event_id']}"
            )
        legacy_by_order.setdefault(order_id, []).append((event_id, quantity))
    if not legacy_by_order:
        return []

    repaired = recorded_partial_exit_fill_cursors(conn, position_id)
    exact: list[LegacyPartialExitRepair] = []
    for order_id, legacy_events in legacy_by_order.items():
        fills = economic_exit_fills_for_position(
            conn, position_id, venue_order_id=order_id
        )
        available = [fill for fill in fills if fill.identity not in repaired]
        fill_index = 0
        for legacy_event_id, legacy_quantity in legacy_events:
            selected: list[EconomicExitFill] = []
            selected_quantity = Decimal("0")
            while fill_index < len(available) and selected_quantity < legacy_quantity:
                fill = available[fill_index]
                fill_index += 1
                selected.append(fill)
                selected_quantity += fill.quantity
            if not selected or selected_quantity != legacy_quantity:
                raise PartialExitEconomicDebtError(
                    "partial EXIT repair cannot prove exact fill identity/quantity: "
                    f"position_id={position_id} order_id={order_id} "
                    f"event_id={legacy_event_id} legacy={legacy_quantity} "
                    f"canonical={selected_quantity}"
                )
            exact.extend(
                LegacyPartialExitRepair(legacy_event_id=legacy_event_id, fill=fill)
                for fill in selected
            )
        if fill_index < len(available):
            raise PartialExitEconomicDebtError(
                "partial EXIT repair has canonical fills without a legacy event: "
                f"position_id={position_id} order_id={order_id}"
            )
    return exact
