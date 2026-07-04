# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   line 53 (self-trade guard: BUILD, nothing exists) +
#   docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W2 packet (self-trade guard lands INERT — no call site wired yet; W3's
#   solve wires it at envelope build).

"""Pure self-trade guard predicate (SCH-W2.2-SELF-TRADE).

A proposed Zeus order self-crosses when it would trade against Zeus's OWN
open resting order on the same token_id — a new BUY at a price at or above
our own resting SELL, or a new SELL at a price at or below our own resting
BUY. `check_self_trade` is a pure function: given the proposed order and an
explicit list of Zeus's own open resting orders, it returns a typed verdict.
It performs no I/O and reads no hidden DB state, matching the existing
`_entry_duplicate_same_token_component` gate shape in
`src/execution/executor.py`.

`load_own_open_resting_orders` is a thin, separate DB loader — the only
piece of this module that touches sqlite. It queries `venue_commands`
joined to the LATEST `venue_order_facts` row per command (venue_order_facts
is append-only; a naive `WHERE state = X` without taking the latest row
would treat a since-cancelled order as still open), filtered to the
canonical `OPEN_ORDER_FACT_STATES` (src/state/canonical_projections.py).
It returns `None` when the required tables are absent, signalling "own-open-
orders set unavailable" so a future call site can route that into
`check_self_trade(..., own_open_orders=None)` -> INDETERMINATE and fail
closed, rather than silently treating "no data" as "no risk".

Shared-wallet law: the operator co-trades on the SAME venue account and
their independently-placed orders are EXPECTED and must never be blocked by
this guard. This is satisfied by construction, not by an extra filter:
`venue_order_facts.command_id` is a foreign key into `venue_commands`, and
`venue_commands` rows are written exclusively by Zeus's own submission path
(src/state/venue_command_repo.py::insert_command). There is no ingestion
path that inserts a venue_order_facts row for an order Zeus did not itself
submit (the one adjacent carve-out, `external_operator_close` in
src/execution/exchange_reconcile.py, synthesizes a terminal SELL command
against a position Zeus ALREADY HELD when the operator closes it externally
— it does not track the operator's independently-placed resting orders).
So a query over venue_commands JOIN venue_order_facts intrinsically
enumerates ONLY Zeus-originated orders; the operator's own resting orders
are simply invisible to this table and cannot be seen or blocked here.

Scope is per-token_id (the same market outcome side), not per-family.
Family-level exclusivity (blocking a same-family re-entry while another
family member is live) is a separate concern already owned by
`src/strategy/family_exclusive_dedup.py`; conflating the two would
double-block legitimate opposite-token hedges (e.g. holding both YES and NO
legs of the same market via different token_ids).

Venue-side self-trade prevention: NONE exists. The installed Polymarket CLOB
SDK (py_clob_client 0.34.6) exposes no self-trade-prevention flag or
parameter, and `src/data/polymarket_client.py` does not implement one either
(grepped for self_trade/SelfTrade — zero hits). This local Zeus-side gate is
therefore the PRIMARY defense, not defense-in-depth on top of a venue-side
control. Even if the venue later added an account-global self-trade-
prevention setting, it would be unsuitable here anyway: it would apply to
the WHOLE shared account, including the operator's own orders, and would
interfere with expected operator co-trading — so the local, Zeus-order-only
guard remains correct even in that hypothetical.

This packet (W2.2) lands the predicate and loader only. No production call
site is wired; nothing else changes behavior.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from src.state.canonical_projections import OPEN_ORDER_FACT_STATES

_SIDES = ("BUY", "SELL")


class SelfTradeVerdict(str, Enum):
    CLEAR = "CLEAR"
    WOULD_SELF_CROSS = "WOULD_SELF_CROSS"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class RestingOrder:
    """One of Zeus's own open resting orders, as seen by the guard."""

    command_id: str
    token_id: str
    side: str
    price: Decimal | str | float


@dataclass(frozen=True)
class SelfTradeCheckResult:
    verdict: SelfTradeVerdict
    crossing_command_ids: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


def check_self_trade(
    *,
    token_id: str,
    side: str,
    price: Decimal | str | float,
    own_open_orders: list[RestingOrder] | None,
) -> SelfTradeCheckResult:
    """Pure predicate: would a proposed order self-cross Zeus's own resting orders?

    `own_open_orders=None` means the caller could not establish Zeus's own
    open-order set (e.g. required tables missing) and must be treated as
    INDETERMINATE, never as an empty (CLEAR) set.
    """
    if own_open_orders is None:
        return SelfTradeCheckResult(
            verdict=SelfTradeVerdict.INDETERMINATE,
            reason="own_open_orders_unavailable",
        )

    token = str(token_id or "").strip()
    if not token:
        return SelfTradeCheckResult(
            verdict=SelfTradeVerdict.INDETERMINATE,
            reason="missing_token_id",
        )

    candidate_side = str(side or "").strip().upper()
    if candidate_side not in _SIDES:
        return SelfTradeCheckResult(
            verdict=SelfTradeVerdict.INDETERMINATE,
            reason=f"invalid_side:{side!r}",
        )

    try:
        candidate_price = Decimal(str(price))
    except (InvalidOperation, ValueError, TypeError):
        return SelfTradeCheckResult(
            verdict=SelfTradeVerdict.INDETERMINATE,
            reason=f"invalid_price:{price!r}",
        )

    crossing_ids: list[str] = []
    for order in own_open_orders:
        if str(order.token_id or "").strip() != token:
            continue
        resting_side = str(order.side or "").strip().upper()
        if resting_side not in _SIDES or resting_side == candidate_side:
            continue
        try:
            resting_price = Decimal(str(order.price))
        except (InvalidOperation, ValueError, TypeError):
            # Malformed resting-order price is a data problem on that one
            # row, not grounds to fail the whole check closed; skip it.
            continue
        if candidate_side == "BUY" and resting_side == "SELL":
            crosses = candidate_price >= resting_price
        else:  # candidate_side == "SELL" and resting_side == "BUY"
            crosses = candidate_price <= resting_price
        if crosses:
            crossing_ids.append(order.command_id)

    if crossing_ids:
        return SelfTradeCheckResult(
            verdict=SelfTradeVerdict.WOULD_SELF_CROSS,
            crossing_command_ids=tuple(crossing_ids),
            reason="would_self_cross",
        )

    return SelfTradeCheckResult(verdict=SelfTradeVerdict.CLEAR, reason="clear")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def load_own_open_resting_orders(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    exclude_command_id: str | None = None,
) -> list[RestingOrder] | None:
    """Thin loader: Zeus's own open resting orders on `token_id`.

    Returns None (unavailable) if the required tables don't exist, so the
    caller can route that into `check_self_trade(..., own_open_orders=None)`
    and fail closed. Returns [] (not None) when tables exist but there is no
    open order on this token — that is a real, positive CLEAR signal.

    "Open" means the LATEST venue_order_facts row per command_id (by
    local_sequence — venue_order_facts is append-only) has a state in the
    canonical OPEN_ORDER_FACT_STATES.
    """
    if not _table_exists(conn, "venue_commands") or not _table_exists(
        conn, "venue_order_facts"
    ):
        return None

    open_state_placeholders = ",".join("?" for _ in OPEN_ORDER_FACT_STATES)
    query = f"""
        SELECT vc.command_id, vc.token_id, vc.side, vc.price
        FROM venue_commands vc
        JOIN venue_order_facts vof ON vof.command_id = vc.command_id
        WHERE vc.token_id = ?
          AND vof.state IN ({open_state_placeholders})
          AND upper(COALESCE(vc.state, '')) NOT IN (
                'CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED',
                'SUBMIT_REJECTED', 'FILLED'
          )
          AND vof.local_sequence = (
                SELECT MAX(vof2.local_sequence)
                FROM venue_order_facts vof2
                WHERE vof2.command_id = vof.command_id
          )
    """
    params: list[str] = [token_id, *sorted(OPEN_ORDER_FACT_STATES)]
    if exclude_command_id:
        query += " AND vc.command_id != ?"
        params.append(exclude_command_id)

    rows = conn.execute(query, params).fetchall()
    result: list[RestingOrder] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            command_id = str(row["command_id"])
            row_token_id = str(row["token_id"])
            row_side = str(row["side"])
            row_price = row["price"]
        else:
            command_id, row_token_id, row_side, row_price = row
        result.append(
            RestingOrder(
                command_id=command_id,
                token_id=row_token_id,
                side=row_side,
                price=row_price,
            )
        )
    return result
